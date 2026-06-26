"""Orchestrates Melody coordinator and MelodyPlayer pool."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from melody.commands.handler import CommandHandler, SearchTask
from melody.commands.messages import (
    format_command_failed,
    format_joining_channel,
    format_already_leaving,
    format_leaving_channel,
)
from melody.commands.parser import CommandParser
from melody.config import Settings
from melody.logging import get_logger
from melody.models import ParsedCommand
from melody.mumble.coordinator import CoordinatorBot
from melody.mumble.player_pool import PlayerBot, PlayerPool
from melody.mumble.pymumble_util import ParsedTextMessage, is_player_channel_message

logger = get_logger(__name__)

NotifyCallback = Callable[[str], Awaitable[None]]

_OCCUPANCY_POLL_INTERVAL = 30.0
_PLAYER_QUEUE_MAX = 64
_SHUTDOWN = object()
_READ_ONLY_COMMANDS = frozenset({"list", "current", "help"})
_EXIT_COMMANDS = frozenset({"quit", "exit"})
_LOCK_FREE_COMMANDS = _READ_ONLY_COMMANDS | _EXIT_COMMANDS


class MumbleOrchestrator:
    """Single-process orchestrator for coordinator + player pool."""

    def __init__(
        self,
        settings: Settings,
        parser: CommandParser,
        handler: CommandHandler,
        pool: PlayerPool,
    ) -> None:
        self._settings = settings
        self._parser = parser
        self._handler = handler
        self._pool = pool
        self._coordinator = CoordinatorBot(
            settings,
            on_text=self._on_coordinator_text,
            has_player_in_channel=pool.is_ready,
        )
        self._player_queues: dict[int, asyncio.Queue[ParsedTextMessage | object]] = {}
        self._player_tasks: dict[int, asyncio.Task[None]] = {}
        self._occupancy_task: asyncio.Task[None] | None = None
        self._command_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._listener_connection_ids: dict[int, int] = {}
        pool.set_on_release(self._on_player_released)

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._pool.set_loop(loop)
        logger.info(
            "Waiting for Melody coordinator at %s:%s (reconnect enabled)",
            self._settings.mumble_host,
            self._settings.mumble_port,
        )
        await self._coordinator.start(loop)
        if not self._coordinator.connection.is_connected:
            raise RuntimeError(
                f"Melody coordinator failed to connect to "
                f"{self._settings.mumble_host}:{self._settings.mumble_port}"
            )
        logger.info("Melody coordinator connected as %s", self._settings.mumble_username)
        self._occupancy_task = asyncio.create_task(self._occupancy_poll_loop())

    async def stop(self) -> None:
        if self._occupancy_task is not None:
            self._occupancy_task.cancel()
            try:
                await self._occupancy_task
            except asyncio.CancelledError:
                pass
            self._occupancy_task = None
        for channel_id in list(self._player_tasks):
            await self._shutdown_player_listener(channel_id)
        await self._pool.stop_all()
        await self._coordinator.stop()

    @property
    def coordinator_connected(self) -> bool:
        return self._coordinator.connection.is_connected

    async def _on_player_released(self, channel_id: int) -> None:
        self._command_locks.pop(channel_id, None)
        self._listener_connection_ids.pop(channel_id, None)
        await self._shutdown_player_listener(channel_id)

    async def _shutdown_player_listener(self, channel_id: int) -> None:
        task = self._player_tasks.pop(channel_id, None)
        queue = self._player_queues.pop(channel_id, None)
        if queue is not None:
            try:
                queue.put_nowait(_SHUTDOWN)
            except asyncio.QueueFull:
                pass
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, TimeoutError):
                pass

    async def _on_coordinator_text(self, message: ParsedTextMessage) -> None:
        asyncio.create_task(
            self._dispatch_coordinator_text(message),
            name="coordinator-command",
        )

    async def _dispatch_coordinator_text(self, message: ParsedTextMessage) -> None:
        commands = self._parser.parse_all(message.message)
        if not commands:
            return

        channel_id = message.sender_channel_id
        if self._is_exit_noop(channel_id, commands):
            if all(command.name in _EXIT_COMMANDS for command in commands):
                if self._pool.is_releasing(channel_id):
                    await self._reply_in_channel(
                        message,
                        channel_id,
                        format_already_leaving(),
                    )
            return

        notify: NotifyCallback | None = None
        if message.is_private:
            notify = lambda text, sid=message.sender_session: self._coordinator.whisper(sid, text)

        search_tasks = self._handler.start_search_tasks(commands)

        try:
            player, _spawned = await self._acquire_player_for_message(message, notify=notify)
        except Exception as exc:
            await self._cancel_search_tasks(search_tasks)
            if message.is_private:
                await self._notify_sender(message, notify, str(exc))
            else:
                await self._reply_in_channel(
                    message,
                    channel_id,
                    str(exc),
                    notify=notify,
                )
            return

        await self._run_commands(
            commands,
            player,
            message,
            notify=notify,
            search_tasks=search_tasks,
            spawned=_spawned,
        )

    def _is_exit_noop(self, channel_id: int, commands: list[ParsedCommand]) -> bool:
        if not all(command.name in _EXIT_COMMANDS for command in commands):
            return False
        return not self._pool.has_channel(channel_id) or self._pool.is_releasing(channel_id)

    async def _resolve_player_for_message(self, message: ParsedTextMessage) -> PlayerBot:
        """Return an active player, spawning only when the channel has none."""
        channel_id = message.sender_channel_id
        if self._pool.is_releasing(channel_id):
            raise RuntimeError("MelodyPlayer is leaving this channel — try again in a moment")
        await self._pool.wait_until_released(channel_id)
        existing = await self._pool.get(channel_id)
        if existing is not None and existing.connection.is_connected:
            await self._ensure_player_listener(existing)
            return existing
        player, _spawned = await self._acquire_player_for_message(message, notify=None)
        return player

    async def _acquire_player_for_message(
        self,
        message: ParsedTextMessage,
        *,
        notify: NotifyCallback | None,
    ) -> tuple[PlayerBot, bool]:
        channel_id = message.sender_channel_id
        channel_name = message.sender_channel_name
        if self._pool.is_releasing(channel_id):
            raise RuntimeError("MelodyPlayer is leaving this channel — try again in a moment")
        await self._pool.wait_until_released(channel_id)
        spawning = not self._pool.has_channel(channel_id)

        if spawning:
            await self._coordinator.send_to_channel(
                channel_id,
                format_joining_channel(channel_name),
            )

        started = time.monotonic()
        player, _created = await self._pool.acquire(channel_id, channel_name)
        logger.debug(
            "Player acquire channel_id=%s created=%s ms=%.0f",
            channel_id,
            _created,
            (time.monotonic() - started) * 1000,
        )
        await self._ensure_player_listener(player)
        if _created:
            await self._wait_player_ready_for_chat(player)
        return player, _created

    async def _wait_player_ready_for_chat(self, player: PlayerBot) -> None:
        """Wait until the player can post channel chat after joining."""
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if not player.connection.is_connected:
                await asyncio.sleep(0.05)
                continue
            if await player.session.ensure_joined():
                if await player.connection.is_in_channel(player.channel_id):
                    return
            await asyncio.sleep(0.05)
        logger.warning(
            "Player not ready for channel chat channel_id=%s after join",
            player.channel_id,
        )

    async def _reply_in_channel(
        self,
        message: ParsedTextMessage,
        channel_id: int,
        text: str,
        *,
        notify: NotifyCallback | None = None,
        player: PlayerBot | None = None,
    ) -> None:
        if notify is not None:
            await notify(text)
            return
        if player is not None:
            await player.session.send_message(text)
            return
        await self._coordinator.send_to_channel(channel_id, text)

    async def _notify_sender(
        self,
        message: ParsedTextMessage,
        notify: NotifyCallback | None,
        text: str,
    ) -> None:
        if notify is not None:
            await notify(text)
        elif message.sender_session:
            await self._coordinator.whisper(message.sender_session, text)

    async def _player_message_loop(
        self,
        channel_id: int,
        queue: asyncio.Queue[ParsedTextMessage | object],
    ) -> None:
        try:
            while True:
                message = await queue.get()
                if message is _SHUTDOWN:
                    break
                if not isinstance(message, ParsedTextMessage):
                    continue
                asyncio.create_task(
                    self._dispatch_player_text(channel_id, message),
                    name=f"player-command-{channel_id}",
                )
        except asyncio.CancelledError:
            pass
        finally:
            if self._player_tasks.get(channel_id) is asyncio.current_task():
                self._player_tasks.pop(channel_id, None)
            if self._player_queues.get(channel_id) is queue:
                self._player_queues.pop(channel_id, None)

    async def _dispatch_player_text(self, channel_id: int, message: ParsedTextMessage) -> None:
        try:
            await self._handle_player_text(channel_id, message)
        except Exception:
            logger.exception(
                "Player listener failed channel_id=%s",
                channel_id,
            )

    async def _handle_player_text(self, channel_id: int, message: ParsedTextMessage) -> None:
        if not is_player_channel_message(message, channel_id):
            return

        commands = self._parser.parse_all(message.message)
        if not commands:
            return

        if self._is_exit_noop(channel_id, commands):
            if all(command.name in _EXIT_COMMANDS for command in commands):
                if self._pool.is_releasing(channel_id):
                    await self._reply_in_channel(
                        message,
                        channel_id,
                        format_already_leaving(),
                    )
            return

        search_tasks = self._handler.start_search_tasks(commands)

        try:
            if all(command.name in _EXIT_COMMANDS for command in commands):
                player = await self._pool.get(channel_id)
                if player is None:
                    return
            else:
                player = await self._resolve_player_for_message(message)
        except Exception as exc:
            await self._cancel_search_tasks(search_tasks)
            logger.warning("Failed to acquire player for channel message: %s", exc)
            await self._reply_in_channel(message, channel_id, str(exc))
            return

        notify: NotifyCallback | None = None
        if message.is_private:
            notify = lambda text, sid=message.sender_session, p=player: p.connection.whisper_user(
                sid, text
            )

        await self._run_commands(commands, player, message, notify=notify, search_tasks=search_tasks)

    async def _run_commands(
        self,
        commands: list[ParsedCommand],
        player: PlayerBot,
        message: ParsedTextMessage,
        *,
        notify: NotifyCallback | None,
        search_tasks: dict[int, SearchTask] | None = None,
        spawned: bool = False,
    ) -> None:
        tasks = search_tasks or {}
        lock = self._command_locks[player.channel_id]
        destroy = False
        channel_fallback: NotifyCallback | None = None
        if spawned:
            channel_id = player.channel_id
            channel_fallback = lambda text, cid=channel_id: self._coordinator.send_to_channel(
                cid, text
            )
        needs_lock = any(command.name not in _LOCK_FREE_COMMANDS for command in commands)
        try:
            if needs_lock and tasks:
                await self._await_search_tasks(tasks)
            if needs_lock:
                async with lock:
                    destroy = await self._execute_commands(
                        commands,
                        player,
                        message,
                        notify=notify,
                        tasks=tasks,
                        channel_fallback=channel_fallback,
                    )
            else:
                destroy = await self._execute_commands(
                    commands,
                    player,
                    message,
                    notify=notify,
                    tasks=tasks,
                    channel_fallback=channel_fallback,
                )
            if destroy:
                asyncio.create_task(
                    self._fast_release_player(player, message, notify=notify),
                    name=f"exit-{player.channel_id}",
                )
                return
        except Exception:
            logger.exception(
                "Command failed channel_id=%s from=%s",
                player.channel_id,
                message.sender_name,
            )
            if notify:
                await notify(format_command_failed())
            else:
                await player.session.send_message(format_command_failed())
        finally:
            await self._cancel_search_tasks(tasks)

    async def _execute_commands(
        self,
        commands: list[ParsedCommand],
        player: PlayerBot,
        message: ParsedTextMessage,
        *,
        notify: NotifyCallback | None,
        tasks: dict[int, SearchTask],
        channel_fallback: NotifyCallback | None = None,
    ) -> bool:
        destroy = False
        for index, command in enumerate(commands):
            started = time.monotonic()
            destroy = await self._handler.handle(
                command,
                player.session,
                notify=notify,
                search_task=tasks.get(index),
                channel_fallback=channel_fallback,
            )
            logger.debug(
                "Command %s channel_id=%s ms=%.0f",
                command.name,
                player.channel_id,
                (time.monotonic() - started) * 1000,
            )
            if destroy:
                break
        return destroy

    async def _fast_release_player(
        self,
        player: PlayerBot,
        message: ParsedTextMessage,
        *,
        notify: NotifyCallback | None,
    ) -> None:
        """Stop accepting commands, reply, and disconnect without blocking chat."""
        channel_id = player.channel_id
        player.connection.set_text_handler(None)
        asyncio.create_task(
            self._send_leaving_message(message, channel_id, notify=notify, player=player),
            name=f"leaving-{channel_id}",
        )
        asyncio.create_task(
            self._pool.release(channel_id),
            name=f"release-{channel_id}",
        )

    async def _send_leaving_message(
        self,
        message: ParsedTextMessage,
        channel_id: int,
        *,
        notify: NotifyCallback | None,
        player: PlayerBot,
    ) -> None:
        try:
            await asyncio.wait_for(
                self._reply_in_channel(
                    message,
                    channel_id,
                    format_leaving_channel(),
                    notify=notify,
                    player=player,
                ),
                timeout=2.0,
            )
        except TimeoutError:
            logger.warning("Leaving message timed out channel_id=%s", channel_id)
        except Exception:
            logger.exception("Leaving message failed channel_id=%s", channel_id)

    async def _refresh_player_occupancy(self, player: PlayerBot) -> None:
        try:
            count = await player.connection.count_humans_in_channel(player.channel_id)
            player.session.update_human_count(count)
        except Exception:
            logger.exception(
                "Occupancy refresh failed channel_id=%s",
                player.channel_id,
            )

    async def _ensure_player_listener(self, player: PlayerBot) -> None:
        channel_id = player.channel_id
        connection_id = id(player.connection)
        if (
            channel_id in self._player_queues
            and self._listener_connection_ids.get(channel_id) == connection_id
        ):
            return

        if channel_id in self._player_queues:
            await self._shutdown_player_listener(channel_id)

        queue: asyncio.Queue[ParsedTextMessage | object] = asyncio.Queue(maxsize=_PLAYER_QUEUE_MAX)
        self._player_queues[channel_id] = queue
        self._listener_connection_ids[channel_id] = connection_id

        def on_player_text(msg: ParsedTextMessage) -> None:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                logger.warning(
                    "Dropped player text message channel_id=%s (queue full)",
                    player.channel_id,
                )

        player.connection.set_text_handler(on_player_text)
        self._player_tasks[channel_id] = asyncio.create_task(
            self._player_message_loop(channel_id, queue),
            name=f"player-listener-{channel_id}",
        )

    async def _await_search_tasks(self, tasks: dict[int, SearchTask]) -> None:
        pending = [task for task in tasks.values() if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _cancel_search_tasks(self, tasks: dict[int, SearchTask]) -> None:
        if not tasks:
            return
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    async def _occupancy_poll_loop(self) -> None:
        """Release idle players when humans leave without sending another command."""
        try:
            while True:
                await asyncio.sleep(_OCCUPANCY_POLL_INTERVAL)
                await self._pool.refresh_all_occupancy()
        except asyncio.CancelledError:
            pass
