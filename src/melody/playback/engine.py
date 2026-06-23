"""Async playback engine for a single channel."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from melody.logging import get_logger
from melody.models import PlaybackState, PlaybackStatus, QueueItem, Track
from melody.playback.ffmpeg import FRAME_DURATION_SEC, FFmpegTranscoder
from melody.playback.pcm_pacer import PcmPacer
from melody.playback.queue import QueueManager
from melody.playback.volume import DEFAULT_VOLUME_PERCENT, apply_volume_pcm, clamp_volume_percent
from melody.protocols import ISubsonicClient

logger = get_logger(__name__)

SendPcmCallback = Callable[[bytes], Awaitable[None]]
SendPcmBatchCallback = Callable[[list[bytes]], Awaitable[None]]
GetBufferSizeCallback = Callable[[], float]
OnTrackStartCallback = Callable[[Track], Awaitable[None]]


class PlaybackEngine:
    """Streams tracks from Subsonic through FFmpeg to Mumble."""

    def __init__(
        self,
        subsonic: ISubsonicClient,
        queue: QueueManager,
        *,
        starting_volume_percent: int = DEFAULT_VOLUME_PERCENT,
        send_pcm: SendPcmCallback,
        send_pcm_batch: SendPcmBatchCallback | None = None,
        get_buffer_size: GetBufferSizeCallback,
        on_track_start: OnTrackStartCallback | None = None,
        ffmpeg_probesize: str = "32k",
        ffmpeg_analyzeduration: str = "500k",
        pcm_target_buffer_sec: float = 0.08,
        pcm_max_prebuffer_frames: int = 6,
        pcm_prebuffer_batch_size: int = 1,
    ) -> None:
        self._subsonic = subsonic
        self._queue = queue
        self._send_pcm = send_pcm
        self._send_pcm_batch = send_pcm_batch
        self._get_buffer_size = get_buffer_size
        self._on_track_start = on_track_start
        self._ffmpeg_probesize = ffmpeg_probesize
        self._ffmpeg_analyzeduration = ffmpeg_analyzeduration
        self._pcm_target_buffer_sec = pcm_target_buffer_sec
        self._pcm_max_prebuffer_frames = pcm_max_prebuffer_frames
        self._pcm_prebuffer_batch_size = pcm_prebuffer_batch_size
        self._state = PlaybackState.IDLE
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._announce_next_track = True
        self._volume = clamp_volume_percent(starting_volume_percent) / 100.0
        self._active_track: Track | None = None
        self._elapsed_seconds = 0.0

    @property
    def playback_status(self) -> PlaybackStatus:
        track = self._active_track
        if track is None and self._queue.current is not None:
            track = self._queue.current.track
        total: int | None = None
        if track is not None and track.duration > 0:
            total = track.duration
        return PlaybackStatus(
            track=track,
            state=self._state,
            elapsed_seconds=self._elapsed_seconds,
            total_seconds=total,
        )

    @property
    def volume_percent(self) -> int:
        return round(self._volume * 100)

    def set_volume_percent(self, percent: int) -> None:
        self._volume = clamp_volume_percent(percent) / 100.0

    def _scale_pcm(self, pcm: bytes) -> bytes:
        return apply_volume_pcm(pcm, self._volume)

    @property
    def state(self) -> PlaybackState:
        return self._state

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()
        self._active_track = None
        self._elapsed_seconds = 0.0
        if self._task and not self._task.done():
            self._task.cancel()

    async def wait_stopped(self, timeout: float = 5.0) -> None:
        """Wait for the playback task and any FFmpeg subprocess to finish."""
        if self._task is None or self._task.done():
            return
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            logger.warning("Playback task did not stop within %.1fs", timeout)
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    def pause(self) -> None:
        self._pause_event.clear()
        if self._state == PlaybackState.PLAYING:
            self._state = PlaybackState.PAUSED

    def resume(self) -> None:
        self._pause_event.set()
        if self._state == PlaybackState.PAUSED:
            self._state = PlaybackState.PLAYING

    async def play_current(self, *, announce: bool = True) -> None:
        if self._task and not self._task.done():
            self.stop()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._stop_event = asyncio.Event()
        self._pause_event.set()
        self._announce_next_track = announce
        self._task = asyncio.create_task(self._playback_loop())

    async def _playback_loop(self) -> None:
        while not self._stop_event.is_set():
            item = self._queue.current
            if item is None:
                self._state = PlaybackState.IDLE
                return

            if self._announce_next_track and self._on_track_start is not None:
                await self._on_track_start(item.track)
            self._announce_next_track = True

            try:
                await self._play_item(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Playback failed track_id=%s title=%s",
                    item.track.id,
                    item.track.title,
                )

            if self._stop_event.is_set():
                return

            nxt = self._queue.on_track_finished()
            if nxt is None:
                self._state = PlaybackState.IDLE
                return

    async def _play_item(self, item: QueueItem) -> None:
        track = item.track
        self._active_track = track
        self._elapsed_seconds = 0.0
        stream_url = self._subsonic.stream_url(track.id)
        self._state = PlaybackState.BUFFERING
        logger.info(
            "Starting playback track_id=%s title=%s url=%s",
            track.id,
            track.display_name,
            stream_url,
        )

        transcoder = FFmpegTranscoder()
        playback_started = time.monotonic()
        await transcoder.start_from_url(
            stream_url,
            probesize=self._ffmpeg_probesize,
            analyzeduration=self._ffmpeg_analyzeduration,
        )
        self._state = PlaybackState.PLAYING
        frames_sent = 0
        pcm_iter = transcoder.read_pcm_frames().__aiter__()
        first_frame_timeout = 30.0
        pacer = PcmPacer(
            self._get_buffer_size,
            frame_duration_sec=FRAME_DURATION_SEC,
            target_buffer_sec=self._pcm_target_buffer_sec,
        )
        loop = asyncio.get_running_loop()
        pending_batch: list[bytes] = []
        prebuffer_batch_size = self._pcm_prebuffer_batch_size
        max_prebuffer_frames = self._pcm_max_prebuffer_frames

        async def read_next_frame() -> bytes | None:
            try:
                if frames_sent == 0:
                    return await asyncio.wait_for(
                        pcm_iter.__anext__(),
                        timeout=first_frame_timeout,
                    )
                return await pcm_iter.__anext__()
            except StopAsyncIteration:
                return None
            except TimeoutError:
                logger.error(
                    "FFmpeg produced no PCM within %ss track_id=%s stderr=%s",
                    first_frame_timeout,
                    track.id,
                    transcoder.stderr_summary(),
                )
                return None

        async def flush_batch() -> None:
            nonlocal frames_sent
            if not pending_batch:
                return
            first_flush = frames_sent == 0
            if self._volume >= 1.0 - 1e-9:
                scaled = pending_batch
            else:
                scaled = [self._scale_pcm(chunk) for chunk in pending_batch]
            if self._send_pcm_batch and len(scaled) > 1:
                await self._send_pcm_batch(scaled)
            else:
                for chunk in scaled:
                    await self._send_pcm(chunk)
            frames_sent += len(pending_batch)
            self._elapsed_seconds = frames_sent * FRAME_DURATION_SEC
            if first_flush:
                logger.info(
                    "PCM playback started track_id=%s mumble_buffer=%.2fs first_pcm_ms=%.0f",
                    track.id,
                    self._get_buffer_size(),
                    (time.monotonic() - playback_started) * 1000,
                )
            pending_batch.clear()

        try:
            while not pacer.primed and not self._stop_event.is_set():
                if frames_sent >= max_prebuffer_frames:
                    pacer.force_prime(loop)
                    break
                frame = await read_next_frame()
                if frame is None:
                    break
                if self._stop_event.is_set():
                    break
                await self._pause_event.wait()
                pending_batch.append(frame)
                if len(pending_batch) >= prebuffer_batch_size:
                    await flush_batch()
                pacer.delay_before_next_frame(loop)

            while not self._stop_event.is_set():
                await pacer.wait(loop)
                frame = await read_next_frame()
                if frame is None:
                    break
                if self._stop_event.is_set():
                    break
                await self._pause_event.wait()
                pending_batch.append(frame)
                await flush_batch()
        finally:
            if pending_batch and not self._stop_event.is_set():
                await flush_batch()
            code = await transcoder.stop()
            if frames_sent == 0:
                logger.error(
                    "No PCM output for track_id=%s ffmpeg_code=%s stderr=%s",
                    track.id,
                    code,
                    transcoder.stderr_summary(),
                )
            else:
                logger.info(
                    "Finished playback track_id=%s pcm_frames=%s",
                    track.id,
                    frames_sent,
                )
            if code not in (0, -15, 255) and not self._stop_event.is_set():
                logger.warning(
                    "FFmpeg exited code=%s track_id=%s stderr=%s",
                    code,
                    track.id,
                    transcoder.stderr_summary(),
                )
