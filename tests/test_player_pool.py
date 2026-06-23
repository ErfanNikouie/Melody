"""Tests for player pool username logic."""

from __future__ import annotations

import pytest

from melody.mumble.channel_session import ChannelSession
from melody.mumble.pymumble_util import ParsedTextMessage, is_player_channel_message, sanitize_username_part


def test_sanitize_channel_name() -> None:
    assert sanitize_username_part("Music Room") == "Music_Room"
    assert sanitize_username_part("gaming!!!") == "gaming"
    assert sanitize_username_part("   ") == "channel"


def test_sanitize_truncates() -> None:
    long_name = "a" * 50
    assert len(sanitize_username_part(long_name)) == 24


@pytest.mark.asyncio
async def test_ensure_joined_sets_joined_when_move_succeeds() -> None:
    session = _make_test_session(join_channel=lambda: _async_true())

    await session.ensure_joined()
    assert session._joined  # noqa: SLF001


@pytest.mark.asyncio
async def test_ensure_joined_stays_unjoined_when_move_fails() -> None:
    session = _make_test_session(join_channel=lambda: _async_false())

    await session.ensure_joined()
    assert not session._joined  # noqa: SLF001


def test_is_player_channel_message_accepts_channel_chat() -> None:
    channel_id = 5
    channel_msg = ParsedTextMessage(
        sender_session=1,
        sender_name="user",
        message="m/list",
        sender_channel_id=channel_id,
        sender_channel_name="Music",
        is_private=False,
        target_channel_id=channel_id,
    )
    assert is_player_channel_message(channel_msg, channel_id)

    other_channel = ParsedTextMessage(
        sender_session=1,
        sender_name="user",
        message="m/list",
        sender_channel_id=99,
        sender_channel_name="Other",
        is_private=False,
        target_channel_id=99,
    )
    assert not is_player_channel_message(other_channel, channel_id)


def test_is_player_channel_message_accepts_whispers() -> None:
    whisper = ParsedTextMessage(
        sender_session=1,
        sender_name="user",
        message="m/help",
        sender_channel_id=5,
        sender_channel_name="Music",
        is_private=True,
        target_channel_id=None,
    )
    assert is_player_channel_message(whisper, 5)


async def _async_true() -> bool:
    return True


async def _async_false() -> bool:
    return False


def _make_test_session(*, join_channel) -> ChannelSession:
    return ChannelSession(
        5,
        "Music",
        subsonic=object(),  # type: ignore[arg-type]
        buffer_pool=object(),  # type: ignore[arg-type]
        start_seconds=1.0,
        grace_period=60.0,
        send_pcm=lambda _: None,  # type: ignore[arg-type, return-value]
        send_pcm_batch=lambda _: None,  # type: ignore[arg-type, return-value]
        get_buffer_size=lambda: 0.0,
        wait_for_audio_encoder=lambda: True,  # type: ignore[arg-type, return-value]
        join_channel=join_channel,
        leave_channel=lambda: None,  # type: ignore[arg-type, return-value]
        send_message=lambda _: None,  # type: ignore[arg-type, return-value]
        on_shutdown=lambda: None,  # type: ignore[arg-type, return-value]
    )
