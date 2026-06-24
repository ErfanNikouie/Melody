"""Per-channel playback queue."""

from __future__ import annotations

import random
from collections import deque

from melody.models import QueueItem, RepeatMode, Track

# Cap history so long sessions do not retain every played track forever.
MAX_QUEUE_HISTORY = 200
# Cap upcoming tracks so repeated queue commands cannot grow RAM without bound.
MAX_UPCOMING = 500


class QueueManager:
    """Manages history, current track, and upcoming queue for one channel."""

    def __init__(self) -> None:
        self._history: deque[QueueItem] = deque(maxlen=MAX_QUEUE_HISTORY)
        self._current: QueueItem | None = None
        self._upcoming: deque[QueueItem] = deque()
        self._repeat_mode = RepeatMode.OFF
        self._shuffle_enabled = False
        self._source_playlist_id: str | None = None
        self._source_album_id: str | None = None

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
    def source_playlist_id(self) -> str | None:
        return self._source_playlist_id

    @property
    def source_album_id(self) -> str | None:
        return self._source_album_id

    @property
    def is_idle(self) -> bool:
        return self._current is None and not self._upcoming

    @property
    def needs_repeat_refill(self) -> bool:
        """True when repeat-all is on but upcoming is empty and a source id exists."""
        return (
            self._repeat_mode == RepeatMode.ALL
            and not self._upcoming
            and self._current is None
            and (self._source_album_id is not None or self._source_playlist_id is not None)
        )

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
        source_album_id: str | None = None,
    ) -> QueueItem | None:
        """Replace queue with new items; return first item to play."""
        self.clear()
        self.clear_history()
        self._source_playlist_id = source_playlist_id
        self._source_album_id = source_album_id
        if not items:
            return None
        self._current = items[0]
        self._extend_upcoming(items[1:])
        if self._shuffle_enabled:
            self._shuffle_upcoming()
        return self._current

    def enqueue(
        self,
        items: list[QueueItem],
        *,
        source_playlist_id: str | None = None,
        source_album_id: str | None = None,
    ) -> QueueItem | None:
        """Add items to queue. Start playback if idle."""
        if source_playlist_id:
            self._source_playlist_id = source_playlist_id
        if source_album_id:
            self._source_album_id = source_album_id

        if not items:
            return self._current

        if self._current is None:
            self._current = items[0]
            self._extend_upcoming(items[1:])
            if self._shuffle_enabled:
                self._shuffle_upcoming()
            return self._current

        self._extend_upcoming(items)
        if self._shuffle_enabled:
            self._shuffle_upcoming()
        return self._current

    def refill_after_repeat(self, items: list[QueueItem]) -> QueueItem | None:
        """Refill upcoming from Subsonic for repeat-all; return new current track."""
        self._extend_upcoming(items)
        if self._shuffle_enabled:
            self._shuffle_upcoming()
        if self._upcoming:
            self._current = self._upcoming.popleft()
            return self._current
        return None

    def clear(self) -> None:
        self._upcoming.clear()
        self._current = None
        self._source_playlist_id = None
        self._source_album_id = None

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
            self._current = None
            return None

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
            self._current = None
            return None

        self._current = None
        return None

    def _extend_upcoming(self, items: list[QueueItem]) -> None:
        if not items:
            return
        room = MAX_UPCOMING - len(self._upcoming)
        if room <= 0:
            return
        self._upcoming.extend(items[:room])

    def _shuffle_upcoming(self) -> None:
        upcoming = list(self._upcoming)
        random.shuffle(upcoming)
        self._upcoming = deque(upcoming)
