"""Shared domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RepeatMode(str, Enum):
    OFF = "off"
    TRACK = "track"
    ALL = "all"


class PlaybackState(str, Enum):
    IDLE = "idle"
    BUFFERING = "buffering"
    PLAYING = "playing"
    PAUSED = "paused"


class PlayerMode(str, Enum):
    """How MelodyPlayer usernames are assigned."""

    POOL = "pool"
    PER_CHANNEL = "per_channel"


class SearchMode(str, Enum):
    """Search target for Subsonic queries."""

    TRACK = "track"
    PLAYLIST = "playlist"


class SearchMode(str, Enum):
    """Search target for Subsonic queries."""

    TRACK = "track"
    PLAYLIST = "playlist"


@dataclass(frozen=True, slots=True)
class Track:
    id: str
    title: str
    artist: str
    album: str = ""
    duration: int = 0  # seconds

    @property
    def display_name(self) -> str:
        if self.artist:
            return f"{self.artist} — {self.title}"
        return self.title


@dataclass(frozen=True, slots=True)
class Playlist:
    id: str
    name: str
    tracks: tuple[Track, ...] = field(default_factory=tuple)

    @property
    def track_count(self) -> int:
        return len(self.tracks)


@dataclass(frozen=True, slots=True)
class QueueItem:
    track: Track
    source_playlist_id: str | None = None


@dataclass(frozen=True, slots=True)
class SearchMatch:
    kind: str  # "track" or "playlist"
    score: int
    track: Track | None = None
    playlist: Playlist | None = None

    @property
    def display_name(self) -> str:
        if self.track:
            return self.track.display_name
        if self.playlist:
            return self.playlist.name
        return "unknown"


@dataclass(frozen=True, slots=True)
class CommandOptions:
    track: bool = False
    playlist: bool = False
    repeat: bool = False
    shuffle: bool = False


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    options: CommandOptions
    query: str | None = None
