"""Per-channel bot session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from melody.commands.messages import format_no_previous, format_now_playing, format_queue_end
from melody.logging import get_logger
from melody.models import PlaybackStatus, Track
from melody.playback.engine import PlaybackEngine
from melody.playback.queue import QueueManager
from melody.playback.volume import DEFAULT_VOLUME_PERCENT, apply_volume_pcm, clamp_volume_percent
from melody.protocols import ISubsonicClient

logger = get_logger(__name__)

JoinChannelCallback = Callable[[], Awaitable[bool]]
IsInChannelCallback = Callable[[], Awaitable[bool]]
LeaveChannelCallback = Callable[[], Awaitable[None]]
SendMessageCallback = Callable[[str], Awaitable[bool]]
SendPcmCallback = Callable[[bytes], Awaitable[None]]
SendPcmBatchCallback = Callable[[list[bytes]], Awaitable[None]]
GetBufferSizeCallback = Callable[[], float]
ClearSendAudioCallback = Callable[[], Awaitable[None]]
WaitForAudioEncoderCallback = Callable[[], Awaitable[bool]]
OnShutdownCallback = Callable[[], Awaitable[None]]


class ChannelSession:
    """Independent queue and playback state for one Mumble channel."""

    def __init__(
        self,
        channel_id: int,
        channel_name: str,
        subsonic: ISubsonicClient,
        *,
        starting_volume_percent: int = DEFAULT_VOLUME_PERCENT,
        grace_period: float,
        ffmpeg_probesize: str = "32k",
        ffmpeg_analyzeduration: str = "500k",
        pcm_target_buffer_ms: int = 80,
        pcm_max_prebuffer_frames: int = 6,
        pcm_prebuffer_batch_size: int = 1,
        send_pcm: SendPcmCallback,
        send_pcm_batch: SendPcmBatchCallback,
        get_buffer_size: GetBufferSizeCallback,
        clear_send_audio: ClearSendAudioCallback | None = None,
        wait_for_audio_encoder: WaitForAudioEncoderCallback,
        join_channel: JoinChannelCallback,
        is_in_channel: IsInChannelCallback | None = None,
        leave_channel: LeaveChannelCallback,
        send_message: SendMessageCallback,
        on_shutdown: OnShutdownCallback,
    ) -> None:
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.queue = QueueManager()
        self._grace_period = grace_period
        self._join_channel = join_channel
        self._is_in_channel = is_in_channel
        self._leave_channel = leave_channel
        self._send_message = send_message
        self._request_destroy = on_shutdown
        self._wait_for_audio_encoder = wait_for_audio_encoder
        self._clear_send_audio = clear_send_audio
        self._human_count = 0
        self._grace_task: asyncio.Task[None] | None = None
        self._joined = False
        self._stop_drain_task: asyncio.Task[None] | None = None
        self._playback_start_task: asyncio.Task[None] | None = None
        self._clear_audio_task: asyncio.Task[None] | None = None
        self._shutting_down = False

        self.engine = PlaybackEngine(
            subsonic,
            self.queue,
            starting_volume_percent=starting_volume_percent,
            send_pcm=send_pcm,
            send_pcm_batch=send_pcm_batch,
            get_buffer_size=get_buffer_size,
            on_track_start=self._announce_now_playing,
            ffmpeg_probesize=ffmpeg_probesize,
            ffmpeg_analyzeduration=ffmpeg_analyzeduration,
            pcm_target_buffer_sec=pcm_target_buffer_ms / 1000.0,
            pcm_max_prebuffer_frames=pcm_max_prebuffer_frames,
            pcm_prebuffer_batch_size=pcm_prebuffer_batch_size,
        )

    async def _announce_now_playing(self, track: Track) -> None:
        await self.send_message(format_now_playing(track))

    async def send_message(self, message: str) -> bool:
        """Post to channel chat, retrying until joined or timeout."""
        if self._joined:
            try:
                result = await self._send_message(message)
                if result is not False:
                    return True
            except Exception:
                logger.exception(
                    "Channel message failed channel_id=%s",
                    self.channel_id,
                )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while loop.time() < deadline:
            await self.ensure_joined()
            try:
                result = await self._send_message(message)
                if result is not False:
                    return True
            except Exception:
                logger.exception(
                    "Channel message failed channel_id=%s",
                    self.channel_id,
                )
            await asyncio.sleep(0.1)
        logger.warning(
            "Channel message not delivered channel_id=%s after retries",
            self.channel_id,
        )
        return False

    async def ensure_joined(self) -> bool:
        if self._joined and self._is_in_channel is not None and await self._is_in_channel():
            self._cancel_grace_timer()
            return True
        if self._is_in_channel is not None and await self._is_in_channel():
            self._joined = True
            self._cancel_grace_timer()
            return True
        if await self._join_channel():
            self._joined = True
            self._cancel_grace_timer()
            return True
        else:
            logger.error(
                "Failed to join channel channel_id=%s channel_name=%s",
                self.channel_id,
                self.channel_name,
            )
            return False

    def mark_joined(self) -> None:
        """Mark session as joined without moving (only when join already verified)."""
        self._joined = True
        self._cancel_grace_timer()

    async def start_playback(self, *, announce: bool = True) -> None:
        await self.ensure_joined()
        if not await self._wait_for_audio_encoder():
            logger.error("Mumble Opus encoder not ready channel_id=%s", self.channel_id)
        await self.engine.play_current(announce=announce)

    def schedule_playback(self, *, announce: bool = True) -> None:
        """Start playback without blocking chat command handling."""
        if self._shutting_down:
            return
        if self._playback_start_task is not None and not self._playback_start_task.done():
            self._playback_start_task.cancel()
        self._playback_start_task = asyncio.create_task(
            self._run_scheduled_playback(announce=announce),
            name=f"playback-start-{self.channel_id}",
        )

    def prepare_for_shutdown(self) -> None:
        """Stop playback tasks immediately before the Mumble connection tears down."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._cancel_grace_timer()
        if self._playback_start_task is not None and not self._playback_start_task.done():
            self._playback_start_task.cancel()
        self.begin_stop(clear_all=True)

    async def _run_scheduled_playback(self, *, announce: bool = True) -> None:
        try:
            await self.start_playback(announce=announce)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled playback failed channel_id=%s", self.channel_id)

    def begin_stop(self, *, clear_all: bool = False) -> None:
        """Stop playback immediately without waiting for FFmpeg teardown."""
        self.engine.stop()
        if clear_all:
            self.queue.clear_all()
        if self._clear_send_audio is not None:
            if self._clear_audio_task is not None and not self._clear_audio_task.done():
                return
            self._clear_audio_task = asyncio.create_task(
                self._run_clear_audio(),
                name=f"clear-audio-{self.channel_id}",
            )

    async def _run_clear_audio(self) -> None:
        try:
            await self._clear_send_audio_safe()
        finally:
            self._clear_audio_task = None

    def cancel_playback_tasks(self) -> None:
        if self._playback_start_task is not None and not self._playback_start_task.done():
            self._playback_start_task.cancel()
        if self._stop_drain_task is not None and not self._stop_drain_task.done():
            self._stop_drain_task.cancel()

    async def fast_disconnect(self) -> None:
        """Stop playback and tear down FFmpeg before the Mumble connection closes."""
        self.prepare_for_shutdown()
        self.cancel_playback_tasks()
        await self._await_stop_teardown()
        self._joined = False

    def schedule_stop_drain(self) -> None:
        """Finish playback teardown in the background so chat commands stay responsive."""
        if self._stop_drain_task is not None and not self._stop_drain_task.done():
            return
        self._stop_drain_task = asyncio.create_task(
            self.finish_stop(),
            name=f"stop-drain-{self.channel_id}",
        )

    async def _clear_send_audio_safe(self) -> None:
        if self._clear_send_audio is None:
            return
        try:
            await asyncio.wait_for(self._clear_send_audio(), timeout=1.0)
        except TimeoutError:
            logger.warning("Clear send audio timed out channel_id=%s", self.channel_id)
        except Exception:
            logger.exception("Clear send audio failed channel_id=%s", self.channel_id)

    async def _await_stop_teardown(self) -> None:
        """Wait for any in-flight stop drain and finish FFmpeg teardown."""
        if self._stop_drain_task is not None and not self._stop_drain_task.done():
            self._stop_drain_task.cancel()
            try:
                await self._stop_drain_task
            except asyncio.CancelledError:
                pass
            self._stop_drain_task = None
        await self.finish_stop()

    async def finish_stop(self) -> None:
        """Wait for FFmpeg and clear any unsent Mumble audio."""
        try:
            pending = self.engine.pending_drain_count
            timeout = min(5.0, max(1.5, pending * 0.75))
            await self.engine.wait_stopped(timeout=timeout)
            await self._clear_send_audio_safe()
        except Exception:
            logger.exception("Playback teardown failed channel_id=%s", self.channel_id)
        finally:
            self._stop_drain_task = None

    async def stop_playback(self, *, clear_all: bool = False) -> None:
        self.begin_stop(clear_all=clear_all)
        if self._stop_drain_task is not None and not self._stop_drain_task.done():
            await self._stop_drain_task
        await self.finish_stop()

    async def replace_playback(self) -> None:
        """Stop playback and clear the queue; wait briefly for FFmpeg teardown."""
        self.engine.stop()
        self.queue.clear_all()
        await self.engine.wait_stopped(timeout=2.0)

    def pause(self) -> None:
        self.engine.pause()

    async def resume(self) -> None:
        self.engine.resume()
        if self.queue.current and self.engine.state.value in ("idle", "paused"):
            self.schedule_playback(announce=False)

    async def skip_next(self, *, notify: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.begin_stop()
        self.schedule_stop_drain()
        nxt = self.queue.advance()
        if nxt:
            await self._command_reply(notify, format_now_playing(nxt.track))
            self.schedule_playback(announce=False)
        else:
            await self._command_reply(notify, format_queue_end())

    async def skip_back(self, *, notify: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.begin_stop()
        self.schedule_stop_drain()
        prev = self.queue.go_back()
        if prev:
            await self._command_reply(notify, format_now_playing(prev.track))
            self.schedule_playback(announce=False)
        else:
            await self._command_reply(notify, format_no_previous())

    async def _command_reply(
        self,
        notify: Callable[[str], Awaitable[None]] | None,
        text: str,
    ) -> None:
        if notify is not None:
            await notify(text)
        else:
            await self.send_message(text)

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
        self.prepare_for_shutdown()
        if self._stop_drain_task is not None and not self._stop_drain_task.done():
            await self._stop_drain_task
        await self.finish_stop()
        self._joined = False
