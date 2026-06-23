"""Melody application orchestrator."""

from __future__ import annotations

import asyncio
import signal

from melody.commands.handler import CommandHandler
from melody.commands.parser import CommandParser
from melody.config import Settings
from melody.logging import get_logger, setup_logging
from melody.mumble.orchestrator import MumbleOrchestrator
from melody.mumble.player_pool import PlayerPool
from melody.models import SearchWeights
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
        self._parser = CommandParser(settings.prefixes)
        self._search = SearchService(
            self._subsonic,
            weights=SearchWeights(
                relevance_percent=settings.search_relevance_percent,
                popularity_percent=settings.search_popularity_percent,
            ),
        )
        prefix = settings.prefixes[-1] if settings.prefixes else "m/"
        self._handler = CommandHandler(self._search, command_prefix=prefix)
        self._pool = PlayerPool(settings, self._subsonic)
        self._orchestrator = MumbleOrchestrator(
            settings,
            self._parser,
            self._handler,
            self._pool,
        )
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._install_signal_handlers(loop)

        try:
            await self._orchestrator.start(loop)
        except RuntimeError:
            logger.error("Failed to start Melody coordinator")
            await self.shutdown()
            return

        logger.info(
            "Melody running (coordinator=%s, player_mode=%s, subsonic=%s)",
            self._settings.mumble_username,
            self._settings.player_mode,
            self._settings.subsonic_url,
        )
        await self._shutdown_event.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        await self._orchestrator.stop()
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
