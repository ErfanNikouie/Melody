"""PCM pacing to keep pymumble's outgoing audio queue filled steadily."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

# Keep ~150 ms queued in pymumble so send_audio never starves between frames.
TARGET_BUFFER_SEC = 0.15
MAX_BUFFER_SEC = 0.35
RESYNC_LAG_SEC = 0.25


class PcmPacer:
    """Schedule PCM frames on a monotonic clock with adaptive buffer backpressure."""

    def __init__(
        self,
        get_buffer_size: Callable[[], float],
        *,
        frame_duration_sec: float,
        target_buffer_sec: float = TARGET_BUFFER_SEC,
        max_buffer_sec: float = MAX_BUFFER_SEC,
    ) -> None:
        self._get_buffer_size = get_buffer_size
        self._frame_duration = frame_duration_sec
        self._target_buffer = target_buffer_sec
        self._max_buffer = max_buffer_sec
        self._next_frame_at: float | None = None
        self._primed = False

    @property
    def primed(self) -> bool:
        return self._primed

    def delay_before_next_frame(self, loop: asyncio.AbstractEventLoop) -> float:
        """Seconds to wait before reading/sending the next frame."""
        buffer_sec = self._get_buffer_size()
        now = loop.time()

        if not self._primed:
            if buffer_sec >= self._target_buffer:
                self._primed = True
                self._next_frame_at = now
            return 0.0

        if self._next_frame_at is None:
            self._next_frame_at = now + self._frame_duration
        else:
            self._next_frame_at += self._frame_duration

        delay = self._next_frame_at - now

        if buffer_sec < self._target_buffer * 0.5:
            # Queue ran dry — feed immediately and resync the clock.
            self._next_frame_at = now + self._frame_duration
            return 0.0

        if buffer_sec > self._max_buffer:
            delay = max(delay, self._frame_duration * 0.5)

        if delay < -RESYNC_LAG_SEC:
            self._next_frame_at = now + self._frame_duration
            return 0.0

        return max(0.0, delay)

    async def wait(self, loop: asyncio.AbstractEventLoop) -> None:
        delay = self.delay_before_next_frame(loop)
        if delay > 0:
            await asyncio.sleep(delay)

    def force_prime(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start paced delivery even if the outbound buffer never reported full."""
        self._primed = True
        self._next_frame_at = loop.time()
