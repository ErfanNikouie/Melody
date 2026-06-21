"""MelodyPlayer pool — one connection per active channel."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from melody.config import Settings
from melody.logging import get_logger
from melody.models import PlayerMode
from melody.mumble.channel_session import ChannelSession
from melody.mumble.connection import MumbleConnection
from melody.mumble.pymumble_util import sanitize_username_part
from melody.playback.buffer import GlobalBufferPool
from melody.protocols import ISubsonicClient

logger = get_logger(__name__)

ReleaseCallback = Callable[[int], Awaitable[None]]


class PlayerBot:
    """A dedicated MelodyPlayer connection bound to one channel."""

    def __init__(
        self,
        connection: MumbleConnection,
        channel_id: int,
        channel_name: str,
        session: ChannelSession,
    ) -> None:
        self.connection = connection
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.session = session

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self.connection.set_post_connect_channel(self.channel_id)
        await self.connection.start(loop)

    async def stop(self) -> None:
        await self.session.shutdown()
        await self.connection.stop()


class PlayerPool:
    """Assigns MelodyPlayer connections to channels and returns them to the pool."""

    def __init__(
        self,
        settings: Settings,
        subsonic: ISubsonicClient,
        buffer_pool: GlobalBufferPool,
        *,
        on_release: ReleaseCallback | None = None,
    ) -> None:
        self._settings = settings
        self._subsonic = subsonic
        self._buffer_pool = buffer_pool
        self._on_release = on_release
        self._lock = asyncio.Lock()
        self._active: dict[int, PlayerBot] = {}
        self._free_slots: list[int] = list(range(1, settings.player_pool_size + 1))
        self._slot_by_channel: dict[int, int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_on_release(self, callback: ReleaseCallback | None) -> None:
        self._on_release = callback

    @property
    def active_count(self) -> int:
        return len(self._active)

    async def acquire(self, channel_id: int, channel_name: str) -> PlayerBot:
        async with self._lock:
            existing = self._active.get(channel_id)
            if existing is not None:
                return existing

            username, password = self._resolve_credentials(channel_id, channel_name)
            connection = MumbleConnection(
                self._settings.mumble_host,
                self._settings.mumble_port,
                username,
                password,
                reconnect=True,
                stereo=True,
                on_text=None,
            )

            async def release_this() -> None:
                await self.release(channel_id)

            session = ChannelSession(
                channel_id,
                channel_name,
                self._subsonic,
                self._buffer_pool,
                start_seconds=self._settings.audio_buffer_start_seconds,
                grace_period=self._settings.disconnect_grace_period,
                send_pcm=connection.send_pcm,
                get_buffer_size=connection.get_buffer_size,
                join_channel=lambda cid=channel_id: connection.join_channel(cid),
                leave_channel=lambda: asyncio.sleep(0),
                send_message=lambda msg, cid=channel_id: connection.send_channel_message(cid, msg),
                on_shutdown=release_this,
            )

            player = PlayerBot(connection, channel_id, channel_name, session)
            self._active[channel_id] = player

        if self._loop is None:
            raise RuntimeError("PlayerPool loop not set")
        await player.start(self._loop)
        player.session.mark_joined()
        logger.info(
            "Player assigned user=%s channel_id=%s channel_name=%s active=%s",
            username,
            channel_id,
            channel_name,
            len(self._active),
        )
        return player

    async def release(self, channel_id: int) -> None:
        async with self._lock:
            player = self._active.pop(channel_id, None)
            if player is None:
                return
            if self._settings.player_mode_enum == PlayerMode.POOL:
                slot = self._slot_by_channel.pop(channel_id, None)
                if slot is not None:
                    self._free_slots.append(slot)
                    self._free_slots.sort()

        await player.stop()
        logger.info("Player released channel_id=%s active=%s", channel_id, len(self._active))
        if self._on_release:
            await self._on_release(channel_id)

    async def get(self, channel_id: int) -> PlayerBot | None:
        async with self._lock:
            return self._active.get(channel_id)

    async def stop_all(self) -> None:
        async with self._lock:
            channel_ids = list(self._active.keys())
        for channel_id in channel_ids:
            await self.release(channel_id)

    def _resolve_credentials(self, channel_id: int, channel_name: str) -> tuple[str, str]:
        prefix = self._settings.player_username_prefix
        if self._settings.player_mode_enum == PlayerMode.PER_CHANNEL:
            part = sanitize_username_part(channel_name)
            return f"{prefix}-{part}", self._settings.player_password

        if not self._free_slots:
            raise RuntimeError(
                f"No free MelodyPlayer slots (pool size={self._settings.player_pool_size}). "
                "Wait for a channel to finish or increase PLAYER_POOL_SIZE."
            )
        slot = self._free_slots.pop(0)
        self._slot_by_channel[channel_id] = slot
        return f"{prefix}-{slot}", self._settings.player_password
