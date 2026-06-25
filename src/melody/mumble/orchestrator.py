"""Orchestrates Melody coordinator and MelodyPlayer pool."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from melody.commands.handler import CommandHandler, SearchTask
from melody.commands.messages import format_command_failed, format_joining_channel
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
        pool.set_on_release(self._on_player_released)
        pool.set_on_player_created(self._ensure_player_listener)

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
        commands = self._parser.parse_all(message.message)
        if not commands:
            return

        notify: NotifyCallback | None = None
        if message.is_private:
            notify = lambda text, sid=message.sender_session: self._coordinator.whisper(sid, text)

        search_tasks = self._handler.start_search_tasks(commands)

        try:
            player = await self._acquire_player_for_message(message, notify=notify)
        except Exception as exc:
            await self._cancel_search_tasks(search_tasks)
            await self._notify_sender(message, notify, str(exc))
            return

        await self._run_commands(commands, player, message, notify=notify, search_tasks=search_tasks)

    async def _acquire_player_for_message(
        self,
        message: ParsedTextMessage,
        *,
        notify: NotifyCallback | None,
    ) -> PlayerBot:
        channel_id = message.sender_channel_id
        channel_name = message.sender_channel_name
        spawning = not self._pool.has_channel(channel_id)

        if spawning:
            progress = format_joining_channel(channel_name)
            if notify is not None:
                await notify(progress)
            else:
                await self._notify_sender(message, None, progress)

        started = time.monotonic()
        player, _created = await self._pool.acquire(channel_id, channel_name)
        logger.debug(
            "Player acquire channel_id=%s created=%s ms=%.0f",
            channel_id,
            _created,
            (time.monotonic() - started) * 1000,
        )
        self._ensure_player_listener(player)
        return player

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
                await self._handle_player_text(channel_id, message)
        except asyncio.CancelledError:
            pass
        finally:
            if self._player_tasks.get(channel_id) is asyncio.current_task():
                self._player_tasks.pop(channel_id, None)
            if self._player_queues.get(channel_id) is queue:
                self._player_queues.pop(channel_id, None)

    async def _handle_player_text(self, channel_id: int, message: ParsedTextMessage) -> None:
        if not is_player_channel_message(message, channel_id):
            return

        commands = self._parser.parse_all(message.message)
        if not commands:
            return

        search_tasks = self._handler.start_search_tasks(commands)

        try:
            player = await self._acquire_player_for_message(message, notify=None)
        except Exception as exc:
            await self._cancel_search_tasks(search_tasks)
            logger.warning("Failed to acquire player for channel message: %s", exc)
            await self._notify_sender(message, None, str(exc))
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
    ) -> None:
        tasks = search_tasks or {}
        lock = self._command_locks[player.channel_id]
        try:
            async with lock:
                for index, command in enumerate(commands):
                    started = time.monotonic()
                    destroy = await self._handler.handle(
                        command,
                        player.session,
                        notify=notify,
                        search_task=tasks.get(index),
                    )
                    logger.debug(
                        "Command %s channel_id=%s ms=%.0f",
                        command.name,
                        player.channel_id,
                        (time.monotonic() - started) * 1000,
                    )
                    if destroy:
                        await self._pool.release(player.channel_id)
                        return
                    player.session.update_human_count(
                        player.connection.count_humans_in(player.channel_id)
                    )
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

    def _ensure_player_listener(self, player: PlayerBot) -> None:
        if player.channel_id in self._player_queues:
            return

        queue: asyncio.Queue[ParsedTextMessage | object] = asyncio.Queue(maxsize=_PLAYER_QUEUE_MAX)
        self._player_queues[player.channel_id] = queue

        def on_player_text(msg: ParsedTextMessage) -> None:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                logger.warning(
                    "Dropped player text message channel_id=%s (queue full)",
                    player.channel_id,
                )

        player.connection.set_text_handler(on_player_text)
        self._player_tasks[player.channel_id] = asyncio.create_task(
            self._player_message_loop(player.channel_id, queue)
        )

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
