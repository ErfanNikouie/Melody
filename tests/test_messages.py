"""Tests for chat message formatting."""

from __future__ import annotations

from melody.commands.messages import (
    format_now_playing,
    format_playing,
    format_queue_list,
    format_volume,
)
from melody.models import QueueItem, Track


def test_format_now_playing_uses_html() -> None:
    track = Track(id="1", title="Song", artist="Artist")
    text = format_now_playing(track)
    assert "▶️" in text
    assert "Artist" in text
    assert "<b>" in text
    assert "color:" in text


def test_format_playing_includes_track_count() -> None:
    text = format_playing("Workout", track_count=12)
    assert "12 tracks" in text


def test_format_queue_list_highlights_current() -> None:
    current = QueueItem(track=Track(id="1", title="Now", artist="A"))
    upcoming = (
        QueueItem(track=Track(id="2", title="Next", artist="B")),
    )
    text = format_queue_list(current, upcoming)
    assert "▶️" in text
    assert "Now" in text
    assert "Next" in text
    assert "1." in text


def test_format_volume_bar() -> None:
    text = format_volume(50)
    assert "50%" in text
    assert "█" in text
