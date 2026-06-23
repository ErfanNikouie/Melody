"""Melody coordinator — sits in root and receives whispers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from melody.config import Settings
from melody.logging import get_logger
from melody.mumble.connection import MumbleConnection
from melody.mumble.pymumble_util import ROOT_CHANNEL_ID, ParsedTextMessage

logger = get_logger(__name__)

TextCallback = Callable[[ParsedTextMessage], Awaitable[None]]
HasPlayerCallback = Callable[[int], bool]


class CoordinatorBot:
    """Melody coordinator user: stays in root, receives whispers and optional root chat."""

    def __init__(
        self,
        settings: Settings,
        *,
        on_text: TextCallback,
        has_player_in_channel: HasPlayerCallback | None = None,
    ) -> None:
        self._settings = settings
        self._on_text = on_text
        self._has_player_in_channel = has_player_in_channel or (lambda _cid: False)
        self._text_queue: asyncio.Queue[ParsedTextMessage] = asyncio.Queue()
        self._connection = MumbleConnection(
            settings.mumble_host,
            settings.mumble_port,
            settings.mumble_username,
            settings.mumble_password,
            reconnect=True,
            stereo=False,
            on_text=self._enqueue_text,
        )
        self._connection.set_post_connect_channel(ROOT_CHANNEL_ID)
        self._task: asyncio.Task[None] | None = None

    @property
    def connection(self) -> MumbleConnection:
        return self._connection

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        await self._connection.start(loop)
        self._task = asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._connection.stop()

    async def whisper(self, session_id: int, message: str) -> None:
        await self._connection.whisper_user(session_id, message)

    def _enqueue_text(self, message: ParsedTextMessage) -> None:
        self._text_queue.put_nowait(message)

    async def _process_loop(self) -> None:
        while True:
            message = await self._text_queue.get()
            if not self._should_accept(message):
                continue
            try:
                await self._on_text(message)
            except Exception:
                logger.exception(
                    "Coordinator failed handling message from=%s",
                    message.sender_name,
                )

    def _should_accept(self, message: ParsedTextMessage) -> bool:
        if message.is_private:
            return True
        if (
            self._settings.coordinator_accept_root_messages
            and message.target_channel_id == ROOT_CHANNEL_ID
        ):
            # MelodyPlayer in root handles root channel chat once it is active.
            if self._has_player_in_channel(ROOT_CHANNEL_ID):
                return False
            return True
        return False
