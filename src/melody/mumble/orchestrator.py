"""Orchestrates Melody coordinator and MelodyPlayer pool."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from melody.commands.handler import CommandHandler
from melody.commands.parser import CommandParser
from melody.config import Settings
from melody.logging import get_logger
from melody.models import ParsedCommand
from melody.mumble.coordinator import CoordinatorBot
from melody.mumble.player_pool import PlayerBot, PlayerPool
from melody.mumble.pymumble_util import ParsedTextMessage

logger = get_logger(__name__)

NotifyCallback = Callable[[str], Awaitable[None]]


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
        self._coordinator = CoordinatorBot(settings, on_text=self._on_coordinator_text)
        self._player_queues: dict[int, asyncio.Queue[ParsedTextMessage]] = {}
        pool.set_on_release(self._on_player_released)

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._pool.set_loop(loop)
        await self._coordinator.start(loop)
        if not self._coordinator.connection.is_connected:
            raise RuntimeError("Melody coordinator failed to connect")
        logger.info("Melody coordinator connected as %s", self._settings.mumble_username)

    async def stop(self) -> None:
        await self._pool.stop_all()
        await self._coordinator.stop()

    @property
    def coordinator_connected(self) -> bool:
        return self._coordinator.connection.is_connected

    async def _on_player_released(self, channel_id: int) -> None:
        self._player_queues.pop(channel_id, None)

    async def _on_coordinator_text(self, message: ParsedTextMessage) -> None:
        command = self._parser.parse(message.message)
        if command is None:
            return

        try:
            player = await self._pool.acquire(message.sender_channel_id, message.sender_channel_name)
        except RuntimeError as exc:
            await self._coordinator.whisper(message.sender_session, str(exc))
            return

        self._ensure_player_listener(player)
        await self._dispatch_command(
            command,
            player,
            notify=lambda text: self._coordinator.whisper(message.sender_session, text),
        )

    def _ensure_player_listener(self, player: PlayerBot) -> None:
        if player.channel_id in self._player_queues:
            return

        queue: asyncio.Queue[ParsedTextMessage] = asyncio.Queue()
        self._player_queues[player.channel_id] = queue

        def on_player_text(msg: ParsedTextMessage) -> None:
            queue.put_nowait(msg)

        player.connection._on_text = on_player_text  # noqa: SLF001
        asyncio.create_task(self._player_message_loop(player.channel_id, queue))

    async def _player_message_loop(
        self,
        channel_id: int,
        queue: asyncio.Queue[ParsedTextMessage],
    ) -> None:
        while channel_id in self._player_queues:
            message = await queue.get()
            command = self._parser.parse(message.message)
            if command is None:
                continue
            player = await self._pool.get(channel_id)
            if player is None:
                continue
            notify: NotifyCallback | None = None
            if message.is_private:
                notify = lambda text, sid=message.sender_session, p=player: p.connection.whisper_user(
                    sid, text
                )
            await self._dispatch_command(command, player, notify=notify)

    async def _dispatch_command(
        self,
        command: ParsedCommand,
        player: PlayerBot,
        *,
        notify: NotifyCallback | None = None,
    ) -> None:
        player.session.update_human_count(player.connection.count_humans_in(player.channel_id))
        destroy = await self._handler.handle(command, player.session, notify=notify)
        if destroy:
            await self._pool.release(player.channel_id)
        else:
            player.session.update_human_count(player.connection.count_humans_in(player.channel_id))
