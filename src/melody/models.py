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


@dataclass(frozen=True, slots=True)
class PlaybackStatus:
    """Snapshot of what is playing and how far along it is."""

    track: Track | None
    state: PlaybackState
    elapsed_seconds: float = 0.0
    total_seconds: int | None = None  # None when duration unknown

    @property
    def is_active(self) -> bool:
        return self.track is not None and self.state != PlaybackState.IDLE


class PlayerMode(str, Enum):
    """How MelodyPlayer usernames are assigned."""

    POOL = "pool"
    PER_CHANNEL = "per_channel"


class SearchMode(str, Enum):
    """Search target for Subsonic queries."""

    TRACK = "track"
    PLAYLIST = "playlist"
    ALBUM = "album"


@dataclass(frozen=True, slots=True)
class SearchWeights:
    """How relevance and popularity combine when ranking search results (sum to 100)."""

    relevance_percent: int = 85
    popularity_percent: int = 15


@dataclass(frozen=True, slots=True)
class Track:
    id: str
    title: str
    artist: str
    album: str = ""
    duration: int = 0  # seconds
    play_count: int = 0
    user_rating: int = 0  # 0–5 from Subsonic

    @property
    def display_name(self) -> str:
        if self.artist:
            return f"{self.artist} — {self.title}"
        return self.title


@dataclass(frozen=True, slots=True)
class Album:
    id: str
    name: str
    artist: str = ""
    tracks: tuple[Track, ...] = field(default_factory=tuple)
    play_count: int = 0
    song_count: int = 0
    user_rating: int = 0

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def display_name(self) -> str:
        if self.artist:
            return f"{self.artist} — {self.name}"
        return self.name


@dataclass(frozen=True, slots=True)
class Playlist:
    id: str
    name: str
    tracks: tuple[Track, ...] = field(default_factory=tuple)
    song_count: int = 0

    @property
    def track_count(self) -> int:
        return len(self.tracks)


@dataclass(frozen=True, slots=True)
class QueueItem:
    track: Track
    source_playlist_id: str | None = None
    source_album_id: str | None = None


@dataclass(frozen=True, slots=True)
class SearchMatch:
    kind: str  # "track", "playlist", or "album"
    score: int
    track: Track | None = None
    playlist: Playlist | None = None
    album: Album | None = None

    @property
    def display_name(self) -> str:
        if self.track:
            return self.track.display_name
        if self.playlist:
            return self.playlist.name
        if self.album:
            return self.album.display_name
        return "unknown"

    @property
    def track_count(self) -> int:
        if self.track:
            return 1
        if self.playlist:
            return self.playlist.track_count
        if self.album:
            return self.album.track_count
        return 0


@dataclass(frozen=True, slots=True)
class CommandOptions:
    track: bool = False
    playlist: bool = False
    album: bool = False
    repeat: bool = False
    shuffle: bool = False


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    options: CommandOptions
    query: str | None = None
