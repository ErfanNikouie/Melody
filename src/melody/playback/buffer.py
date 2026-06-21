"""Global and per-track audio buffering."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator

from melody.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BITRATE_KBPS = 192
BYTES_PER_SECOND = DEFAULT_BITRATE_KBPS * 1024 // 8

_SENTINEL = object()


class GlobalBufferPool:
    """Tracks total buffered bytes across all active streams."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._used = 0
        self._lock = asyncio.Lock()

    @property
    def used_bytes(self) -> int:
        return self._used

    @property
    def available_bytes(self) -> int:
        return max(0, self._max_bytes - self._used)

    async def acquire(self, nbytes: int) -> bool:
        async with self._lock:
            if self._used + nbytes > self._max_bytes:
                return False
            self._used += nbytes
            return True

    async def release(self, nbytes: int) -> None:
        async with self._lock:
            self._used = max(0, self._used - nbytes)


class RollingAudioBuffer:
    """Bounded rolling buffer for encoded audio stream data."""

    def __init__(
        self,
        pool: GlobalBufferPool,
        *,
        start_seconds: float,
        max_spool_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._pool = pool
        self._start_bytes = int(start_seconds * BYTES_PER_SECOND)
        self._total = 0
        self._pool_allocated = 0
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._spool: tempfile.SpooledTemporaryFile[bytes] | None = None
        self._max_spool_bytes = max_spool_bytes
        self._ready = asyncio.Event()
        self._closed = False

    @property
    def total_bytes(self) -> int:
        return self._total

    @property
    def is_ready(self) -> bool:
        return self._total >= self._start_bytes

    async def wait_ready(self, timeout: float = 120.0) -> bool:
        if self.is_ready:
            return True
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except TimeoutError:
            logger.warning("Buffer start threshold not reached within timeout")
            return self._total > 0

    async def write(self, data: bytes) -> None:
        if self._closed or not data:
            return

        if await self._pool.acquire(len(data)):
            self._pool_allocated += len(data)
            await self._queue.put(data)
        else:
            await self._spill_to_disk(data)

        self._total += len(data)
        if self.is_ready:
            self._ready.set()

    async def _spill_to_disk(self, data: bytes) -> None:
        if self._spool is None:
            self._spool = tempfile.SpooledTemporaryFile(max_size=self._max_spool_bytes)
        self._spool.write(data)

    async def iter_chunks(self) -> AsyncIterator[bytes]:
        """Yield chunks as they arrive until stream is closed."""
        if self._spool is not None:
            self._spool.seek(0)
            while True:
                chunk = self._spool.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                break
            yield item  # type: ignore[misc]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._ready.set()
        await self._queue.put(_SENTINEL)
        if self._spool is not None:
            self._spool.close()
            self._spool = None
        if self._pool_allocated:
            await self._pool.release(self._pool_allocated)
            self._pool_allocated = 0


async def fill_buffer_from_stream(
    buffer: RollingAudioBuffer,
    stream: AsyncIterator[bytes],
) -> None:
    """Read stream chunks into buffer until stream ends."""
    try:
        async for chunk in stream:
            await buffer.write(chunk)
    finally:
        await buffer.close()
