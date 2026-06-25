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
from melody.protocols import ISubsonicClient

logger = get_logger(__name__)

ReleaseCallback = Callable[[int], Awaitable[None]]
PlayerCreatedCallback = Callable[["PlayerBot"], None]


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
        *,
        on_release: ReleaseCallback | None = None,
    ) -> None:
        self._settings = settings
        self._subsonic = subsonic
        self._on_release = on_release
        self._on_player_created: PlayerCreatedCallback | None = None
        self._lock = asyncio.Lock()
        self._active: dict[int, PlayerBot] = {}
        self._free_slots: list[int] = list(range(1, settings.player_pool_size + 1))
        self._slot_by_channel: dict[int, int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._occupancy_timers: dict[int, asyncio.TimerHandle] = {}

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_on_release(self, callback: ReleaseCallback | None) -> None:
        self._on_release = callback

    def set_on_player_created(self, callback: PlayerCreatedCallback | None) -> None:
        self._on_player_created = callback

    @property
    def active_count(self) -> int:
        return len(self._active)

    def has_channel(self, channel_id: int) -> bool:
        return channel_id in self._active

    def is_ready(self, channel_id: int) -> bool:
        """True when a player is connected and can handle channel chat."""
        player = self._active.get(channel_id)
        if player is None:
            return False
        return player.connection.is_connected

    async def refresh_occupancy(self, channel_id: int) -> None:
        player = self._active.get(channel_id)
        if player is None:
            return
        count = await asyncio.to_thread(
            player.connection.count_humans_in,
            player.channel_id,
        )
        player.session.update_human_count(count)

    async def refresh_all_occupancy(self) -> None:
        async with self._lock:
            channel_ids = list(self._active.keys())
        for channel_id in channel_ids:
            await self.refresh_occupancy(channel_id)

    async def acquire(self, channel_id: int, channel_name: str) -> tuple[PlayerBot, bool]:
        created = False
        async with self._lock:
            existing = self._active.get(channel_id)
            if existing is not None:
                player = existing
            else:
                created = True
                player = None
                username, password = self._resolve_credentials(channel_id, channel_name)
                connection = MumbleConnection(
                    self._settings.mumble_host,
                    self._settings.mumble_port,
                    username,
                    password,
                    reconnect=True,
                    stereo=True,
                    certfile=self._settings.mumble_certfile,
                    keyfile=self._settings.mumble_keyfile,
                    on_text=None,
                    on_users_changed=self._occupancy_callback(channel_id),
                )

                async def release_this() -> None:
                    await self.release(channel_id)

                session = ChannelSession(
                    channel_id,
                    channel_name,
                    self._subsonic,
                    starting_volume_percent=self._settings.starting_volume,
                    grace_period=self._settings.disconnect_grace_period,
                    ffmpeg_probesize=self._settings.ffmpeg_probesize,
                    ffmpeg_analyzeduration=self._settings.ffmpeg_analyzeduration,
                    pcm_target_buffer_ms=self._settings.pcm_target_buffer_ms,
                    pcm_max_prebuffer_frames=self._settings.pcm_max_prebuffer_frames,
                    pcm_prebuffer_batch_size=self._settings.pcm_prebuffer_batch_size,
                    send_pcm=connection.send_pcm,
                    send_pcm_batch=connection.send_pcm_batch,
                    get_buffer_size=connection.get_buffer_size,
                    clear_send_audio=connection.clear_send_audio,
                    wait_for_audio_encoder=connection.wait_for_audio_encoder,
                    join_channel=lambda cid=channel_id: connection.join_channel(cid),
                    is_in_channel=lambda cid=channel_id: connection.is_in_channel(cid),
                    leave_channel=_noop_leave,
                    send_message=lambda msg, cid=channel_id: connection.send_channel_message(cid, msg),
                    on_shutdown=release_this,
                )

                player = PlayerBot(connection, channel_id, channel_name, session)
                self._active[channel_id] = player
                if self._on_player_created is not None:
                    self._on_player_created(player)

        if self._loop is None:
            raise RuntimeError("PlayerPool loop not set")

        if existing is not None:
            if not existing.connection.is_connected:
                await existing.session.ensure_joined()
            return existing, False

        try:
            await player.start(self._loop)
            if not await player.session.ensure_joined():
                raise RuntimeError(f"MelodyPlayer failed to join channel {channel_name}")
        except Exception:
            logger.exception(
                "Player startup failed channel_id=%s channel_name=%s",
                channel_id,
                channel_name,
            )
            await self._rollback_failed_acquire(channel_id, player)
            raise
        logger.info(
            "Player assigned user=%s channel_id=%s channel_name=%s active=%s",
            player.connection.username,
            channel_id,
            channel_name,
            len(self._active),
        )
        await self.refresh_occupancy(channel_id)
        return player, True

    async def release(self, channel_id: int) -> None:
        async with self._lock:
            player = self._active.pop(channel_id, None)
            if player is None:
                return
            self._return_slot(channel_id)

        player.connection.set_text_handler(None)
        self._cancel_occupancy_timer(channel_id)
        if self._on_release:
            await self._on_release(channel_id)
        await player.stop()
        logger.info("Player released channel_id=%s active=%s", channel_id, len(self._active))

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

    async def _rollback_failed_acquire(self, channel_id: int, player: PlayerBot) -> None:
        async with self._lock:
            if self._active.get(channel_id) is player:
                self._active.pop(channel_id, None)
            self._return_slot(channel_id)

        player.connection.set_text_handler(None)
        self._cancel_occupancy_timer(channel_id)
        if self._on_release:
            await self._on_release(channel_id)

        try:
            await player.stop()
        except Exception:
            logger.exception("Failed cleaning up player after startup error channel_id=%s", channel_id)

    def _return_slot(self, channel_id: int) -> None:
        if self._settings.player_mode_enum != PlayerMode.POOL:
            return
        slot = self._slot_by_channel.pop(channel_id, None)
        if slot is None or slot in self._free_slots:
            return
        self._free_slots.append(slot)
        self._free_slots.sort()

    def _occupancy_callback(self, channel_id: int) -> Callable[[], None]:
        def on_users_changed() -> None:
            loop = self._loop
            if loop is None:
                return
            loop.call_soon_threadsafe(lambda: self._debounce_occupancy_refresh(channel_id))

        return on_users_changed

    def _debounce_occupancy_refresh(self, channel_id: int) -> None:
        loop = self._loop
        if loop is None:
            return
        self._cancel_occupancy_timer(channel_id)

        def fire() -> None:
            self._occupancy_timers.pop(channel_id, None)
            asyncio.create_task(self.refresh_occupancy(channel_id))

        self._occupancy_timers[channel_id] = loop.call_later(2.0, fire)

    def _cancel_occupancy_timer(self, channel_id: int) -> None:
        handle = self._occupancy_timers.pop(channel_id, None)
        if handle is not None:
            handle.cancel()


async def _noop_leave() -> None:
    """MelodyPlayer disconnect is handled by MumbleConnection.stop()."""
