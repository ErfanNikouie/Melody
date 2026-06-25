"""Tests for playback engine lifecycle and FFmpeg cleanup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from melody.models import QueueItem, Track
from melody.playback.engine import PlaybackEngine
from melody.playback.queue import QueueManager


@pytest.mark.asyncio
async def test_cancel_during_ffmpeg_start_stops_transcoder() -> None:
    blocked = asyncio.Event()

    async def slow_start(*_args, **_kwargs) -> None:
        await blocked.wait()

    fake = MagicMock()
    fake.start_from_url = AsyncMock(side_effect=slow_start)
    fake.stop = AsyncMock(return_value=0)
    fake.stderr_summary.return_value = ""

    engine = PlaybackEngine(
        subsonic=MagicMock(stream_url=MagicMock(return_value="http://example.com/stream")),
        queue=QueueManager(),
        send_pcm=AsyncMock(),
        get_buffer_size=lambda: 0.0,
    )
    item = QueueItem(track=Track(id="1", title="Song", artist="Artist"))

    with patch("melody.playback.engine.FFmpegTranscoder", return_value=fake):
        task = asyncio.create_task(engine._play_item(item))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    fake.stop.assert_awaited()
    assert engine._active_transcoder is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_wait_stopped_timeout_stops_active_transcoder() -> None:
    engine = PlaybackEngine(
        subsonic=MagicMock(),
        queue=QueueManager(),
        send_pcm=AsyncMock(),
        get_buffer_size=lambda: 0.0,
    )
    fake_transcoder = MagicMock()
    fake_transcoder.stop = AsyncMock(return_value=0)
    engine._task = asyncio.create_task(asyncio.sleep(3600))  # noqa: SLF001
    engine._active_transcoder = fake_transcoder  # noqa: SLF001

    await engine.wait_stopped(timeout=0.01)

    fake_transcoder.stop.assert_awaited_once()
