"""Tests for orchestrator player listener lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from melody.commands.handler import CommandHandler
from melody.commands.parser import CommandParser
from melody.config import Settings
from melody.models import ParsedCommand, PlaybackState, PlaybackStatus
from melody.mumble.orchestrator import MumbleOrchestrator
from melody.mumble.player_pool import PlayerPool
from melody.mumble.pymumble_util import ParsedTextMessage
from melody.services.search import SearchService


def _minimal_env(**overrides: object) -> dict[str, str]:
    base = {
        "SUBSONIC_URL": "http://localhost:4533",
        "SUBSONIC_USERNAME": "user",
        "SUBSONIC_PASSWORD": "pass",
        "MUMBLE_HOST": "localhost",
        "MUMBLE_USERNAME": "Melody",
        "PLAYER_MODE": "per_channel",
        "PLAYER_POOL_SIZE": "2",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return base


def _make_player(channel_id: int = 5) -> MagicMock:
    player = MagicMock()
    player.channel_id = channel_id
    player.connection = MagicMock()
    player.connection.is_connected = True
    player.connection.set_text_handler = MagicMock()
    return player


def _make_orchestrator() -> MumbleOrchestrator:
    settings = Settings(**_minimal_env())  # type: ignore[arg-type]
    parser = CommandParser(settings.prefixes)
    handler = CommandHandler(SearchService(MagicMock()), command_prefix="/")
    pool = PlayerPool(settings, subsonic=MagicMock())
    return MumbleOrchestrator(settings, parser, handler, pool)


@pytest.mark.asyncio
async def test_ensure_player_listener_rebinds_after_stale_queue() -> None:
    orchestrator = _make_orchestrator()
    first = _make_player()
    second = _make_player()

    await orchestrator._ensure_player_listener(first)
    assert first.connection.set_text_handler.call_count == 1
    assert 5 in orchestrator._player_queues  # noqa: SLF001

    await orchestrator._ensure_player_listener(second)
    assert second.connection.set_text_handler.call_count == 1
    assert orchestrator._listener_connection_ids[5] == id(second.connection)  # noqa: SLF001


@pytest.mark.asyncio
async def test_ensure_player_listener_skips_when_same_connection() -> None:
    orchestrator = _make_orchestrator()
    player = _make_player()

    await orchestrator._ensure_player_listener(player)
    await orchestrator._ensure_player_listener(player)

    assert player.connection.set_text_handler.call_count == 1


@pytest.mark.asyncio
async def test_on_player_released_clears_listener_state() -> None:
    orchestrator = _make_orchestrator()
    player = _make_player()

    await orchestrator._ensure_player_listener(player)
    await orchestrator._on_player_released(5)

    assert 5 not in orchestrator._player_queues  # noqa: SLF001
    assert 5 not in orchestrator._listener_connection_ids  # noqa: SLF001
    assert 5 not in orchestrator._command_locks  # noqa: SLF001


@pytest.mark.asyncio
async def test_wait_until_released_waits_for_release_flag() -> None:
    settings = Settings(**_minimal_env())  # type: ignore[arg-type]
    pool = PlayerPool(settings, subsonic=MagicMock())

    async def clear_releasing() -> None:
        await asyncio.sleep(0.05)
        pool._releasing.discard(7)  # noqa: SLF001

    pool._releasing.add(7)  # noqa: SLF001
    asyncio.create_task(clear_releasing())
    await pool.wait_until_released(7, timeout=1.0)


@pytest.mark.asyncio
async def test_read_only_commands_skip_command_lock() -> None:
    orchestrator = _make_orchestrator()
    player = _make_player()
    player.session = MagicMock()
    player.session.send_message = AsyncMock()
    player.session.playback_status = PlaybackStatus(
        state=PlaybackState.IDLE,
        track=None,
    )

    message = ParsedTextMessage(
        sender_session=1,
        sender_name="user",
        message="/help",
        sender_channel_id=5,
        sender_channel_name="Music",
        is_private=False,
        target_channel_id=5,
    )

    lock = orchestrator._command_locks[5]
    await lock.acquire()
    try:
        task = asyncio.create_task(
            orchestrator._run_commands(
                [ParsedCommand(name="help", query=None, options=MagicMock())],
                player,
                message,
                notify=None,
            )
        )
        await asyncio.wait_for(task, timeout=1.0)
    finally:
        if lock.locked():
            lock.release()

    player.session.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_stop_is_not_blocked_by_in_flight_play_search() -> None:
    orchestrator = _make_orchestrator()
    player = _make_player()
    player.session = MagicMock()
    player.session.send_message = AsyncMock()
    player.session.queue = MagicMock()
    player.session.queue.is_idle = False
    player.session.channel_id = 5
    player.session.begin_stop = MagicMock()
    player.session.schedule_stop_drain = MagicMock()

    blocked = asyncio.Event()
    search_started = asyncio.Event()

    async def slow_search() -> None:
        search_started.set()
        await blocked.wait()

    search_task = asyncio.create_task(slow_search())
    play_message = ParsedTextMessage(
        sender_session=1,
        sender_name="user",
        message="/play song",
        sender_channel_id=5,
        sender_channel_name="Music",
        is_private=False,
        target_channel_id=5,
    )
    play_task = asyncio.create_task(
        orchestrator._run_commands(
            [ParsedCommand(name="play", query="song", options=MagicMock())],
            player,
            play_message,
            notify=None,
            search_tasks={0: search_task},  # type: ignore[arg-type]
        )
    )

    await asyncio.wait_for(search_started.wait(), timeout=1.0)

    stop_done = asyncio.Event()
    stop_message = ParsedTextMessage(
        sender_session=1,
        sender_name="user",
        message="/stop",
        sender_channel_id=5,
        sender_channel_name="Music",
        is_private=False,
        target_channel_id=5,
    )

    async def run_stop() -> None:
        await orchestrator._run_commands(
            [ParsedCommand(name="stop", query=None, options=MagicMock())],
            player,
            stop_message,
            notify=None,
        )
        stop_done.set()

    await asyncio.wait_for(run_stop(), timeout=1.0)
    assert stop_done.is_set()
    player.session.begin_stop.assert_called_once()

    blocked.set()
    await asyncio.wait_for(play_task, timeout=1.0)
