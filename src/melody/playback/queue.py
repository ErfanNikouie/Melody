"""Per-channel playback queue."""

from __future__ import annotations

import random
from collections import deque

from melody.models import QueueItem, RepeatMode, Track


class QueueManager:
    """Manages history, current track, and upcoming queue for one channel."""

    def __init__(self) -> None:
        self._history: list[QueueItem] = []
        self._current: QueueItem | None = None
        self._upcoming: deque[QueueItem] = deque()
        self._repeat_mode = RepeatMode.OFF
        self._shuffle_enabled = False
        self._source_playlist_id: str | None = None
        self._source_playlist_tracks: list[Track] = []
        self._source_album_id: str | None = None
        self._source_album_tracks: list[Track] = []

    @property
    def current(self) -> QueueItem | None:
        return self._current

    @property
    def upcoming(self) -> tuple[QueueItem, ...]:
        return tuple(self._upcoming)

    @property
    def history(self) -> tuple[QueueItem, ...]:
        return tuple(self._history)

    @property
    def repeat_mode(self) -> RepeatMode:
        return self._repeat_mode

    @property
    def shuffle_enabled(self) -> bool:
        return self._shuffle_enabled

    @property
    def is_idle(self) -> bool:
        return self._current is None and not self._upcoming

    def set_repeat(self, enabled: bool) -> None:
        self._repeat_mode = RepeatMode.TRACK if enabled else RepeatMode.OFF

    def set_repeat_mode(self, mode: RepeatMode) -> None:
        self._repeat_mode = mode

    def set_shuffle(self, enabled: bool) -> None:
        self._shuffle_enabled = enabled
        if enabled:
            self._shuffle_upcoming()

    def disable_repeat(self) -> None:
        self._repeat_mode = RepeatMode.OFF

    def play_now(
        self,
        items: list[QueueItem],
        *,
        source_playlist_id: str | None = None,
        source_tracks: list[Track] | None = None,
        source_album_id: str | None = None,
        source_album_tracks: list[Track] | None = None,
    ) -> QueueItem | None:
        """Replace queue with new items; return first item to play."""
        self.clear()
        self._source_playlist_id = source_playlist_id
        self._source_playlist_tracks = list(source_tracks or [])
        self._source_album_id = source_album_id
        self._source_album_tracks = list(source_album_tracks or [])
        if not items:
            return None
        self._current = items[0]
        self._upcoming.extend(items[1:])
        if self._shuffle_enabled:
            self._shuffle_upcoming()
        return self._current

    def enqueue(
        self,
        items: list[QueueItem],
        *,
        source_playlist_id: str | None = None,
        source_tracks: list[Track] | None = None,
        source_album_id: str | None = None,
        source_album_tracks: list[Track] | None = None,
    ) -> QueueItem | None:
        """Add items to queue. Start playback if idle."""
        if source_playlist_id:
            self._source_playlist_id = source_playlist_id
        if source_tracks:
            self._source_playlist_tracks = list(source_tracks)
        if source_album_id:
            self._source_album_id = source_album_id
        if source_album_tracks:
            self._source_album_tracks = list(source_album_tracks)

        if not items:
            return self._current

        if self._current is None:
            self._current = items[0]
            self._upcoming.extend(items[1:])
            if self._shuffle_enabled:
                self._shuffle_upcoming()
            return self._current

        self._upcoming.extend(items)
        if self._shuffle_enabled:
            self._shuffle_upcoming()
        return self._current

    def clear(self) -> None:
        self._upcoming.clear()
        self._current = None
        self._source_playlist_id = None
        self._source_playlist_tracks = []
        self._source_album_id = None
        self._source_album_tracks = []

    def clear_all(self) -> None:
        """Clear queue, current, and history."""
        self.clear()
        self.clear_history()
        self.disable_repeat()

    def clear_history(self) -> None:
        self._history.clear()

    def advance(self) -> QueueItem | None:
        """Move to next track (next command). Returns new current or None."""
        if self._current is not None:
            self._history.append(self._current)

        if self._upcoming:
            self._current = self._upcoming.popleft()
            return self._current

        if self._repeat_mode == RepeatMode.TRACK and self._history:
            self._current = self._history[-1]
            return self._current

        if self._repeat_mode == RepeatMode.ALL:
            self._refill_from_source()
            if self._upcoming:
                self._current = self._upcoming.popleft()
                return self._current

        self._current = None
        return None

    def go_back(self) -> QueueItem | None:
        """Return to previous track."""
        if not self._history:
            return self._current

        if self._current is not None:
            self._upcoming.appendleft(self._current)

        self._current = self._history.pop()
        return self._current

    def on_track_finished(self) -> QueueItem | None:
        """Called when current track finishes naturally."""
        finished = self._current
        if finished is not None:
            self._history.append(finished)

        if self._repeat_mode == RepeatMode.TRACK and finished is not None:
            self._current = finished
            return self._current

        if self._upcoming:
            self._current = self._upcoming.popleft()
            return self._current

        if self._repeat_mode == RepeatMode.ALL:
            self._refill_from_source()
            if self._upcoming:
                self._current = self._upcoming.popleft()
                return self._current

        self._current = None
        return None

    def _refill_from_source(self) -> None:
        if self._source_album_tracks:
            items = [
                QueueItem(track=t, source_album_id=self._source_album_id)
                for t in self._source_album_tracks
            ]
            self._upcoming.extend(items)
            if self._shuffle_enabled:
                self._shuffle_upcoming()
            return
        self._refill_from_playlist()

    def _refill_from_playlist(self) -> None:
        if not self._source_playlist_tracks:
            return
        items = [
            QueueItem(track=t, source_playlist_id=self._source_playlist_id)
            for t in self._source_playlist_tracks
        ]
        self._upcoming.extend(items)
        if self._shuffle_enabled:
            self._shuffle_upcoming()

    def _shuffle_upcoming(self) -> None:
        upcoming = list(self._upcoming)
        random.shuffle(upcoming)
        self._upcoming = deque(upcoming)
