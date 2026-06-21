"""Melody application orchestrator."""

from __future__ import annotations

import asyncio
import signal

from melody.commands.handler import CommandHandler
from melody.commands.parser import CommandParser
from melody.config import Settings
from melody.logging import get_logger, setup_logging
from melody.mumble.client import MumbleClient
from melody.playback.buffer import GlobalBufferPool
from melody.services.search import SearchService
from melody.subsonic.client import SubsonicClient

logger = get_logger(__name__)


class MelodyApp:
    """Top-level application wiring all components."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._subsonic = SubsonicClient(
            settings.subsonic_url,
            settings.subsonic_username,
            settings.subsonic_password,
        )
        self._buffer_pool = GlobalBufferPool(settings.audio_buffer_max_bytes)
        self._parser = CommandParser(settings.prefixes)
        self._search = SearchService(self._subsonic)
        self._handler = CommandHandler(self._search)
        self._mumble = MumbleClient(
            settings,
            self._subsonic,
            self._parser,
            self._handler,
            self._buffer_pool,
        )
        self._shutdown_event = asyncio.Event()
        self._message_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._install_signal_handlers(loop)

        self._mumble.set_on_all_sessions_closed(self._on_all_sessions_closed)

        await self._mumble.connect(loop)
        if not self._mumble.is_connected:
            logger.error("Failed to connect to Mumble server")
            await self.shutdown()
            return

        logger.info("Connected to Mumble as %s", self._settings.mumble_username)
        self._message_task = asyncio.create_task(self._mumble.run_message_loop())

        await self._shutdown_event.wait()
        await self.shutdown()

    async def _on_all_sessions_closed(self) -> None:
        logger.info("No active channel sessions; disconnecting from Mumble")
        await self._mumble.disconnect()
        self._shutdown_event.set()

    async def shutdown(self) -> None:
        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
        await self._mumble.disconnect()
        await self._subsonic.close()
        logger.info("Melody shut down")

    def _install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: self._shutdown_event.set())
            except NotImplementedError:
                signal.signal(sig, lambda _s, _f: self._shutdown_event.set())


def create_app(settings: Settings | None = None) -> MelodyApp:
    setup_logging((settings or Settings()).log_level)  # type: ignore[call-arg]
    return MelodyApp(settings or Settings())  # type: ignore[call-arg]
