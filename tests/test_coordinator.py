"""Tests for coordinator message acceptance."""

from __future__ import annotations

from melody.config import Settings
from melody.mumble.coordinator import CoordinatorBot
from melody.mumble.pymumble_util import ROOT_CHANNEL_ID, ParsedTextMessage


def _root_channel_message(text: str) -> ParsedTextMessage:
    return ParsedTextMessage(
        sender_session=1,
        sender_name="user",
        message=text,
        sender_channel_id=ROOT_CHANNEL_ID,
        sender_channel_name="Root",
        is_private=False,
        target_channel_id=ROOT_CHANNEL_ID,
    )


def _whisper_message(text: str) -> ParsedTextMessage:
    return ParsedTextMessage(
        sender_session=1,
        sender_name="user",
        message=text,
        sender_channel_id=ROOT_CHANNEL_ID,
        sender_channel_name="Root",
        is_private=True,
        target_channel_id=None,
    )


def test_coordinator_accepts_root_chat_when_no_player() -> None:
    bot = CoordinatorBot(
        Settings(  # type: ignore[call-arg]
            SUBSONIC_URL="http://localhost:4533",
            SUBSONIC_USERNAME="u",
            SUBSONIC_PASSWORD="p",
            MUMBLE_HOST="localhost",
            MUMBLE_USERNAME="Melody",
        ),
        on_text=lambda _m: None,  # type: ignore[arg-type, return-value]
        has_player_in_channel=lambda _cid: False,
    )
    assert bot._should_accept(_root_channel_message("m/help"))  # noqa: SLF001


def test_coordinator_defers_root_chat_when_player_active() -> None:
    bot = CoordinatorBot(
        Settings(  # type: ignore[call-arg]
            SUBSONIC_URL="http://localhost:4533",
            SUBSONIC_USERNAME="u",
            SUBSONIC_PASSWORD="p",
            MUMBLE_HOST="localhost",
            MUMBLE_USERNAME="Melody",
        ),
        on_text=lambda _m: None,  # type: ignore[arg-type, return-value]
        has_player_in_channel=lambda cid: cid == ROOT_CHANNEL_ID,
    )
    assert not bot._should_accept(_root_channel_message("m/volume 50"))  # noqa: SLF001


def test_coordinator_still_accepts_whispers_when_player_active() -> None:
    bot = CoordinatorBot(
        Settings(  # type: ignore[call-arg]
            SUBSONIC_URL="http://localhost:4533",
            SUBSONIC_USERNAME="u",
            SUBSONIC_PASSWORD="p",
            MUMBLE_HOST="localhost",
            MUMBLE_USERNAME="Melody",
        ),
        on_text=lambda _m: None,  # type: ignore[arg-type, return-value]
        has_player_in_channel=lambda cid: cid == ROOT_CHANNEL_ID,
    )
    assert bot._should_accept(_whisper_message("m/help"))  # noqa: SLF001
