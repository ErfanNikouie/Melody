"""Tests for chat message formatting."""

from __future__ import annotations

from melody.commands.messages import (
    format_duration,
    format_help,
    format_now_playing,
    format_playback_status,
    format_playing,
    format_progress_line,
    format_queue_list,
    format_volume,
)
from melody.models import PlaybackState, PlaybackStatus, QueueItem, Track


def test_format_duration() -> None:
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"


def test_format_progress_line_with_total() -> None:
    text = format_progress_line(90, 180, width=10)
    assert text == '<span style="color:#b0bec5">1:30 / 3:00</span>'
    assert "█" not in text
    assert "%" not in text


def test_format_playback_status_playing() -> None:
    status = PlaybackStatus(
        track=Track(id="1", title="Song", artist="Artist", duration=200),
        state=PlaybackState.PLAYING,
        elapsed_seconds=50,
        total_seconds=200,
    )
    text = format_playback_status(status)
    assert "Playing" in text
    assert "Artist" in text
    assert "0:50" in text


def test_format_queue_list_with_status() -> None:
    track = Track(id="1", title="Now", artist="A", duration=120)
    status = PlaybackStatus(
        track=track,
        state=PlaybackState.PAUSED,
        elapsed_seconds=30,
        total_seconds=120,
    )
    text = format_queue_list(
        current=QueueItem(track=track),
        upcoming=(QueueItem(track=Track(id="2", title="Next", artist="B")),),
        status=status,
    )
    assert "Paused" in text
    assert "0:30 / 2:00" in text
    assert "Next" in text


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
    text = format_queue_list(current=current, upcoming=upcoming)
    assert "▶️" in text
    assert "Now" in text
    assert "Next" in text
    assert "1." in text
    assert "2." in text


def test_format_queue_list_shows_history_and_full_queue() -> None:
    history = (
        QueueItem(track=Track(id="0", title="Past", artist="Z")),
        QueueItem(track=Track(id="1", title="Earlier", artist="Y")),
    )
    current = QueueItem(track=Track(id="2", title="Now", artist="A"))
    upcoming = tuple(
        QueueItem(track=Track(id=str(i), title=f"Track {i}", artist="B"))
        for i in range(3, 20)
    )
    text = format_queue_list(history=history, current=current, upcoming=upcoming)
    assert "2 played" in text
    assert "Past" in text
    assert "Earlier" in text
    assert "Now" in text
    assert "Track 19" in text
    assert "Showing" not in text
    assert "✓ 1." in text
    assert "✓ 2." in text
    assert "3." in text


def test_format_queue_list_windows_near_end() -> None:
    history = tuple(
        QueueItem(track=Track(id=str(i), title=f"Past {i}", artist="Z"))
        for i in range(40)
    )
    current = QueueItem(track=Track(id="40", title="Current", artist="A"))
    upcoming = tuple(
        QueueItem(track=Track(id=str(i), title=f"Next {i}", artist="B"))
        for i in range(41, 60)
    )
    text = format_queue_list(
        history=history,
        current=current,
        upcoming=upcoming,
        window_size=50,
    )
    assert "Showing 50 of 60" in text
    assert "Past 39" in text
    assert "Current" in text
    assert "Next 59" in text
    assert "Past 0" not in text
    assert "10 earlier" in text


def test_format_queue_list_windows_near_start() -> None:
    history = ()
    current = QueueItem(track=Track(id="0", title="Current", artist="A"))
    upcoming = tuple(
        QueueItem(track=Track(id=str(i), title=f"Next {i}", artist="B"))
        for i in range(1, 100)
    )
    text = format_queue_list(current=current, upcoming=upcoming, window_size=50)
    assert "Showing 50 of 100" in text
    assert "Current" in text
    assert "Next 49" in text
    assert "Next 99" not in text
    assert "50 more" in text


def test_format_volume_bar() -> None:
    text = format_volume(50)
    assert "50%" in text
    assert "█" in text


def test_format_help_lists_commands() -> None:
    text = format_help("m/")
    assert "play" in text
    assert "help" not in text.lower() or "Commands" in text
    assert "m/play" in text
    assert "-a" in text
