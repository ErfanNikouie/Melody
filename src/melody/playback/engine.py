"""Async playback engine for a single channel."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from melody.logging import get_logger
from melody.models import PlaybackState, QueueItem
from melody.playback.buffer import GlobalBufferPool, RollingAudioBuffer, fill_buffer_from_stream
from melody.playback.ffmpeg import FFmpegTranscoder
from melody.playback.queue import QueueManager
from melody.protocols import ISubsonicClient

logger = get_logger(__name__)

SendPcmCallback = Callable[[bytes], Awaitable[None]]
GetBufferSizeCallback = Callable[[], float]


class PlaybackEngine:
    """Streams tracks from Subsonic through FFmpeg to Mumble."""

    def __init__(
        self,
        subsonic: ISubsonicClient,
        queue: QueueManager,
        buffer_pool: GlobalBufferPool,
        *,
        start_seconds: float,
        send_pcm: SendPcmCallback,
        get_buffer_size: GetBufferSizeCallback,
    ) -> None:
        self._subsonic = subsonic
        self._queue = queue
        self._pool = buffer_pool
        self._start_seconds = start_seconds
        self._send_pcm = send_pcm
        self._get_buffer_size = get_buffer_size
        self._state = PlaybackState.IDLE
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()

    @property
    def state(self) -> PlaybackState:
        return self._state

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()
        if self._task and not self._task.done():
            self._task.cancel()

    def pause(self) -> None:
        self._pause_event.clear()
        if self._state == PlaybackState.PLAYING:
            self._state = PlaybackState.PAUSED

    def resume(self) -> None:
        self._pause_event.set()
        if self._state == PlaybackState.PAUSED:
            self._state = PlaybackState.PLAYING

    async def play_current(self) -> None:
        if self._task and not self._task.done():
            self.stop()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._stop_event = asyncio.Event()
        self._pause_event.set()
        self._task = asyncio.create_task(self._playback_loop())

    async def _playback_loop(self) -> None:
        while not self._stop_event.is_set():
            item = self._queue.current
            if item is None:
                self._state = PlaybackState.IDLE
                return

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
        self._state = PlaybackState.BUFFERING
        logger.info(
            "Starting playback track_id=%s title=%s url=%s",
            track.id,
            track.display_name,
            self._subsonic.stream_url(track.id),
        )

        buffer = RollingAudioBuffer(self._pool, start_seconds=self._start_seconds)
        stream = self._subsonic.stream(track.id)
        fill_task = asyncio.create_task(fill_buffer_from_stream(buffer, stream))

        ready = await buffer.wait_ready(timeout=120.0)
        if not ready:
            logger.error("No audio buffered for track_id=%s", track.id)
            await buffer.close()
            fill_task.cancel()
            return

        transcoder = FFmpegTranscoder()
        await transcoder.start()

        async def writer() -> None:
            try:
                async for chunk in buffer.iter_chunks():
                    if self._stop_event.is_set():
                        break
                    await transcoder.write(chunk)
                await fill_task
            except asyncio.CancelledError:
                fill_task.cancel()
                raise
            finally:
                await transcoder.close_input()

        writer_task = asyncio.create_task(writer())
        self._state = PlaybackState.PLAYING

        try:
            async for frame in transcoder.read_pcm_frames():
                if self._stop_event.is_set():
                    break
                await self._pause_event.wait()
                while self._get_buffer_size() > 0.5:
                    await asyncio.sleep(0.01)
                await self._send_pcm(frame)
        finally:
            writer_task.cancel()
            try:
                await writer_task
            except asyncio.CancelledError:
                pass
            code = await transcoder.wait()
            await transcoder.stop()
            await buffer.close()
            if code not in (0, -15, 255) and not self._stop_event.is_set():
                logger.warning(
                    "FFmpeg exited code=%s track_id=%s stderr=%s",
                    code,
                    track.id,
                    transcoder.stderr_summary(),
                )
