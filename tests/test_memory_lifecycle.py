"""Tests for memory/resource lifecycle fixes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from melody.config import Settings
from melody.mumble.connection import MumbleConnection
from melody.mumble.player_pool import PlayerPool
from melody.playback.ffmpeg import FFmpegTranscoder


def _minimal_env(**overrides: object) -> dict[str, str]:
    base = {
        "SUBSONIC_URL": "http://localhost:4533",
        "SUBSONIC_USERNAME": "user",
        "SUBSONIC_PASSWORD": "pass",
        "MUMBLE_HOST": "localhost",
        "MUMBLE_USERNAME": "Melody",
        "PLAYER_MODE": "pool",
        "PLAYER_POOL_SIZE": "2",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return base


@pytest.mark.asyncio
async def test_player_pool_rolls_back_failed_start() -> None:
    settings = Settings(**_minimal_env())  # type: ignore[arg-type]
    pool = PlayerPool(settings, subsonic=object())  # type: ignore[arg-type]
    pool.set_loop(asyncio.get_running_loop())
    released: list[int] = []

    async def on_release(channel_id: int) -> None:
        released.append(channel_id)

    pool.set_on_release(on_release)

    with patch.object(MumbleConnection, "start", AsyncMock(side_effect=OSError("refused"))):
        with pytest.raises(OSError, match="refused"):
            await pool.acquire(5, "Music")

    assert not pool.has_channel(5)
    assert pool.active_count == 0
    assert released == [5]
    assert pool._free_slots == [1, 2]  # noqa: SLF001


@pytest.mark.asyncio
async def test_player_pool_rolls_back_failed_join() -> None:
    settings = Settings(**_minimal_env())  # type: ignore[arg-type]
    pool = PlayerPool(settings, subsonic=object())  # type: ignore[arg-type]
    pool.set_loop(asyncio.get_running_loop())

    with (
        patch.object(MumbleConnection, "start", AsyncMock(return_value=None)),
        patch(
            "melody.mumble.channel_session.ChannelSession.ensure_joined",
            AsyncMock(return_value=False),
        ),
    ):
        with pytest.raises(RuntimeError, match="failed to join"):
            await pool.acquire(7, "Gaming")

    assert not pool.has_channel(7)
    assert pool._free_slots == [1, 2]  # noqa: SLF001


@pytest.mark.asyncio
async def test_connection_stop_clears_text_handler() -> None:
    connection = MumbleConnection("localhost", 64738, "bot", "", on_text=lambda _m: None)
    connection.set_text_handler(lambda _m: None)
    connection._mumble = None  # noqa: SLF001
    connection._thread = None  # noqa: SLF001

    await connection.stop()

    assert connection._on_text is None  # noqa: SLF001


def test_pyproject_requires_pymumble_2() -> None:
    import tomllib

    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    pymumble_dep = next(dep for dep in data["project"]["dependencies"] if "pymumble" in dep)
    assert "oopsbagel/pymumble" in pymumble_dep or ">=2" in pymumble_dep


@pytest.mark.asyncio
async def test_ffmpeg_transcoder_restarts_cleanly() -> None:
    transcoder = FFmpegTranscoder()
    first = AsyncMock()
    first.returncode = None
    first.stdout = None
    first.stderr = AsyncMock()
    first.stderr.readline = AsyncMock(return_value=b"")
    first.wait = AsyncMock(return_value=0)
    first.terminate = MagicMock()

    second = AsyncMock()
    second.returncode = None
    second.stdout = None
    second.stderr = AsyncMock()
    second.stderr.readline = AsyncMock(return_value=b"")
    second.wait = AsyncMock(return_value=0)
    second.terminate = MagicMock()

    with patch(
        "melody.playback.ffmpeg.asyncio.create_subprocess_exec",
        AsyncMock(side_effect=[first, second]),
    ):
        with patch("melody.playback.ffmpeg.find_ffmpeg", return_value="ffmpeg"):
            await transcoder.start_from_url("http://example.com/stream")
            assert transcoder._process is first  # noqa: SLF001
            await transcoder.start_from_url("http://example.com/other")
            first.terminate.assert_called_once()
            assert transcoder._process is second  # noqa: SLF001
