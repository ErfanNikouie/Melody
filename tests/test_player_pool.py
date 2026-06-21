"""Tests for player pool username logic."""

from __future__ import annotations

from melody.mumble.pymumble_util import sanitize_username_part


def test_sanitize_channel_name() -> None:
    assert sanitize_username_part("Music Room") == "Music_Room"
    assert sanitize_username_part("gaming!!!") == "gaming"
    assert sanitize_username_part("   ") == "channel"


def test_sanitize_truncates() -> None:
    long_name = "a" * 50
    assert len(sanitize_username_part(long_name)) == 24
