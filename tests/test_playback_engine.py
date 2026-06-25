"""Tests for playback engine lifecycle and FFmpeg cleanup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from melody.models import PlaybackState, QueueItem, Track
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
async def test_wait_stopped_kills_active_transcoder_immediately() -> None:
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
    assert engine._task is None  # noqa: SLF001
    assert engine.state == PlaybackState.IDLE


@pytest.mark.asyncio
async def test_stop_playback_unblocks_while_play_item_active() -> None:
    blocked = asyncio.Event()

    async def slow_start(*_args, **_kwargs) -> None:
        await blocked.wait()

    fake = MagicMock()
    fake.stop = AsyncMock(return_value=0)
    fake.stderr_summary.return_value = ""
    fake.read_pcm_frames = MagicMock()

    queue = QueueManager()
    queue.play_now([QueueItem(track=Track(id="1", title="Song", artist="Artist"))])
    engine = PlaybackEngine(
        subsonic=MagicMock(stream_url=MagicMock(return_value="http://example.com/stream")),
        queue=queue,
        send_pcm=AsyncMock(),
        get_buffer_size=lambda: 0.0,
    )

    with patch("melody.playback.engine.FFmpegTranscoder", return_value=fake):
        started = asyncio.Event()

        async def slow_start(*_args, **_kwargs) -> None:
            started.set()
            await blocked.wait()

        fake.start_from_url = AsyncMock(side_effect=slow_start)
        play_task = asyncio.create_task(engine.play_current(announce=False))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        engine.stop()
        await engine.wait_stopped(timeout=1.0)

    fake.stop.assert_awaited()
    await play_task
    assert engine.state == PlaybackState.IDLE
