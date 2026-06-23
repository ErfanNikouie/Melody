"""Tests for queue management."""

from __future__ import annotations

from melody.models import QueueItem, RepeatMode, Track
from melody.playback.queue import QueueManager


def _track(suffix: str) -> Track:
    return Track(id=suffix, title=f"Track {suffix}", artist="Artist")


def _item(suffix: str) -> QueueItem:
    return QueueItem(track=_track(suffix))


def test_play_now_clears_and_sets_current() -> None:
    q = QueueManager()
    q.enqueue([_item("old")])
    first = q.play_now([_item("1"), _item("2")])
    assert first is not None
    assert first.track.id == "1"
    assert len(q.upcoming) == 1


def test_enqueue_starts_when_idle() -> None:
    q = QueueManager()
    current = q.enqueue([_item("1"), _item("2")])
    assert current is not None
    assert current.track.id == "1"
    assert len(q.upcoming) == 1


def test_enqueue_appends_when_playing() -> None:
    q = QueueManager()
    q.play_now([_item("1")])
    q.enqueue([_item("2")])
    assert q.current is not None
    assert q.current.track.id == "1"
    assert len(q.upcoming) == 1


def test_advance_moves_to_next() -> None:
    q = QueueManager()
    q.play_now([_item("1"), _item("2"), _item("3")])
    nxt = q.advance()
    assert nxt is not None
    assert nxt.track.id == "2"
    assert len(q.history) == 1


def test_back_returns_previous() -> None:
    q = QueueManager()
    q.play_now([_item("1"), _item("2")])
    q.advance()
    back = q.go_back()
    assert back is not None
    assert back.track.id == "1"
    assert q.current is not None
    assert q.current.track.id == "1"


def test_clear_all_resets_everything() -> None:
    q = QueueManager()
    q.play_now([_item("1"), _item("2")])
    q.set_repeat(True)
    q.advance()
    q.clear_all()
    assert q.is_idle
    assert q.repeat_mode == RepeatMode.OFF
    assert len(q.history) == 0


def test_repeat_track_on_finish() -> None:
    q = QueueManager()
    q.play_now([_item("1")])
    q.set_repeat_mode(RepeatMode.TRACK)
    nxt = q.on_track_finished()
    assert nxt is not None
    assert nxt.track.id == "1"


def test_repeat_all_refills_playlist() -> None:
    tracks = [_track("a"), _track("b")]
    items = [QueueItem(track=t, source_playlist_id="pl1") for t in tracks]
    q = QueueManager()
    q.play_now(items, source_playlist_id="pl1", source_tracks=tracks)
    q.set_repeat_mode(RepeatMode.ALL)
    q.on_track_finished()  # a finished -> now playing b
    nxt = q.on_track_finished()  # b finished -> playlist refills -> now playing a
    assert nxt is not None
    assert nxt.track.id == "a"
    assert q.current is not None
    assert q.current.track.id == "a"


def test_shuffle_only_upcoming() -> None:
    q = QueueManager()
    q.play_now([_item("1"), _item("2"), _item("3"), _item("4")])
    current_id = q.current.track.id if q.current else ""
    q.set_shuffle(True)
    assert q.current is not None
    assert q.current.track.id == current_id
    upcoming_ids = {i.track.id for i in q.upcoming}
    assert upcoming_ids == {"2", "3", "4"}


def test_history_is_capped() -> None:
    from melody.playback.queue import MAX_QUEUE_HISTORY

    q = QueueManager()
    total = MAX_QUEUE_HISTORY + 25
    q.play_now([_item(str(i)) for i in range(total)])
    for _ in range(total - 1):
        q.on_track_finished()
    assert len(q.history) == MAX_QUEUE_HISTORY
    assert q.history[0].track.id == str(25)
