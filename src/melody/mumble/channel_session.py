"""Per-channel bot session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from melody.commands.messages import format_no_previous, format_now_playing, format_queue_end
from melody.logging import get_logger
from melody.models import PlaybackStatus, Track
from melody.playback.buffer import GlobalBufferPool
from melody.playback.engine import PlaybackEngine
from melody.playback.queue import QueueManager
from melody.playback.volume import DEFAULT_VOLUME_PERCENT, apply_volume_pcm, clamp_volume_percent
from melody.protocols import ISubsonicClient

logger = get_logger(__name__)

JoinChannelCallback = Callable[[], Awaitable[None]]
LeaveChannelCallback = Callable[[], Awaitable[None]]
SendMessageCallback = Callable[[str], Awaitable[None]]
SendPcmCallback = Callable[[bytes], Awaitable[None]]
SendPcmBatchCallback = Callable[[list[bytes]], Awaitable[None]]
GetBufferSizeCallback = Callable[[], float]
WaitForAudioEncoderCallback = Callable[[], Awaitable[bool]]
OnShutdownCallback = Callable[[], Awaitable[None]]


class ChannelSession:
    """Independent queue and playback state for one Mumble channel."""

    def __init__(
        self,
        channel_id: int,
        channel_name: str,
        subsonic: ISubsonicClient,
        buffer_pool: GlobalBufferPool,
        *,
        start_seconds: float,
        starting_volume_percent: int = DEFAULT_VOLUME_PERCENT,
        grace_period: float,
        send_pcm: SendPcmCallback,
        send_pcm_batch: SendPcmBatchCallback,
        get_buffer_size: GetBufferSizeCallback,
        wait_for_audio_encoder: WaitForAudioEncoderCallback,
        join_channel: JoinChannelCallback,
        leave_channel: LeaveChannelCallback,
        send_message: SendMessageCallback,
        on_shutdown: OnShutdownCallback,
    ) -> None:
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.queue = QueueManager()
        self._grace_period = grace_period
        self._join_channel = join_channel
        self._leave_channel = leave_channel
        self._send_message = send_message
        self._request_destroy = on_shutdown
        self._wait_for_audio_encoder = wait_for_audio_encoder
        self._human_count = 0
        self._grace_task: asyncio.Task[None] | None = None
        self._joined = False

        self.engine = PlaybackEngine(
            subsonic,
            self.queue,
            buffer_pool,
            start_seconds=start_seconds,
            starting_volume_percent=starting_volume_percent,
            send_pcm=send_pcm,
            send_pcm_batch=send_pcm_batch,
            get_buffer_size=get_buffer_size,
            on_track_start=self._announce_now_playing,
        )

    async def _announce_now_playing(self, track: Track) -> None:
        await self.send_message(format_now_playing(track))

    async def send_message(self, message: str) -> None:
        await self._send_message(message)

    async def ensure_joined(self) -> None:
        if self._joined:
            return
        await self._join_channel()
        self._joined = True
        self._cancel_grace_timer()

    def mark_joined(self) -> None:
        """Player already moved into the channel on connect."""
        self._joined = True
        self._cancel_grace_timer()

    async def start_playback(self, *, announce: bool = True) -> None:
        await self.ensure_joined()
        if not await self._wait_for_audio_encoder():
            logger.error("Mumble Opus encoder not ready channel_id=%s", self.channel_id)
        await self.engine.play_current(announce=announce)

    async def stop_playback(self, *, clear_all: bool = False) -> None:
        self.engine.stop()
        if clear_all:
            self.queue.clear_all()
        else:
            self.queue.clear()

    def pause(self) -> None:
        self.engine.pause()

    async def resume(self) -> None:
        self.engine.resume()
        if self.queue.current and self.engine.state.value in ("idle", "paused"):
            await self.engine.play_current(announce=False)

    async def skip_next(self) -> None:
        self.engine.stop()
        nxt = self.queue.advance()
        if nxt:
            await self.send_message(format_now_playing(nxt.track))
            await self.engine.play_current(announce=False)
        else:
            await self.send_message(format_queue_end())

    async def skip_back(self) -> None:
        self.engine.stop()
        prev = self.queue.go_back()
        if prev:
            await self.send_message(format_now_playing(prev.track))
            await self.engine.play_current(announce=False)
        else:
            await self.send_message(format_no_previous())

    @property
    def volume_percent(self) -> int:
        return self.engine.volume_percent

    def set_volume_percent(self, percent: int) -> None:
        self.engine.set_volume_percent(percent)

    @property
    def playback_status(self) -> PlaybackStatus:
        return self.engine.playback_status

    def update_human_count(self, count: int) -> None:
        self._human_count = count
        if count > 0:
            self._cancel_grace_timer()
        elif self._joined:
            self._start_grace_timer()

    def _start_grace_timer(self) -> None:
        self._cancel_grace_timer()
        self._grace_task = asyncio.create_task(self._grace_disconnect())

    def _cancel_grace_timer(self) -> None:
        if self._grace_task and not self._grace_task.done():
            self._grace_task.cancel()
        self._grace_task = None

    async def _grace_disconnect(self) -> None:
        try:
            await asyncio.sleep(self._grace_period)
            logger.info(
                "Grace period expired channel_id=%s channel_name=%s",
                self.channel_id,
                self.channel_name,
            )
            await self._request_destroy()
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        self._cancel_grace_timer()
        await self.stop_playback(clear_all=True)
        if self._joined:
            await self._leave_channel()
            self._joined = False
