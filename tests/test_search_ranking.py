"""Tests for Subsonic search ranking."""

from __future__ import annotations

import pytest

from melody.models import Album, Playlist, SearchMode, Track
from melody.subsonic.search import (
    rank_albums,
    rank_playlists,
    rank_tracks,
    resolve_search,
    score_album,
    score_playlist,
    score_track,
)


class FakeSubsonicClient:
    def __init__(
        self,
        tracks: list[Track] | None = None,
        playlists: list[Playlist] | None = None,
        albums: list[Album] | None = None,
    ) -> None:
        self.tracks = tracks or []
        self.playlists = playlists or []
        self.albums = albums or []

    async def search_tracks(self, query: str, limit: int = 20) -> list[Track]:
        return self.tracks

    async def search_albums(self, query: str, limit: int = 20) -> list[Album]:
        return self.albums

    async def search_playlists(self, query: str, limit: int = 20) -> list[Playlist]:
        return self.playlists

    async def get_playlist(self, playlist_id: str) -> Playlist:
        for pl in self.playlists:
            if pl.id == playlist_id:
                return pl
        raise ValueError("not found")

    async def get_album(self, album_id: str) -> Album:
        for album in self.albums:
            if album.id == album_id:
                return album
        raise ValueError("not found")

    async def get_song(self, song_id: str) -> Track:
        raise NotImplementedError

    def stream_url(self, song_id: str) -> str:
        return f"http://example/stream/{song_id}"

    async def stream(self, song_id: str):
        yield b""

    async def close(self) -> None:
        pass


def test_exact_title_match_scores_highest() -> None:
    track = Track(id="1", title="Never Gonna Give You Up", artist="Rick Astley")
    assert score_track("never gonna give you up", track) == 100
    assert score_track("rick astley never gonna give you up", track) == 100


def test_fuzzy_match_lower_than_exact() -> None:
    exact = Track(id="1", title="Bohemian Rhapsody", artist="Queen")
    fuzzy = Track(id="2", title="Bohemian Something", artist="Queen")
    assert score_track("bohemian rhapsody", exact) > score_track("bohemian rhapsody", fuzzy)


def test_rank_tracks_returns_best() -> None:
    tracks = [
        Track(id="1", title="Work Song", artist="Artist"),
        Track(id="2", title="Workout Mix", artist="DJ"),
    ]
    match = rank_tracks("workout", tracks)
    assert match is not None
    assert match.track is not None
    assert match.track.id == "2"


def test_rank_playlists_exact() -> None:
    playlists = [
        Playlist(id="p1", name="Chill"),
        Playlist(id="p2", name="Workout"),
    ]
    match = rank_playlists("workout", playlists)
    assert match is not None
    assert match.playlist is not None
    assert match.playlist.id == "p2"
    assert match.score == 85  # 100% relevance × 85% weight


def test_rank_albums_exact() -> None:
    albums = [
        Album(id="a1", name="Random Access Memories", artist="Daft Punk"),
        Album(id="a2", name="Discovery", artist="Daft Punk"),
    ]
    match = rank_albums("discovery", albums)
    assert match is not None
    assert match.album is not None
    assert match.album.id == "a2"


def test_score_album_artist_and_title() -> None:
    album = Album(id="1", name="The Dark Side of the Moon", artist="Pink Floyd")
    assert score_album("pink floyd the dark side of the moon", album) == 100


def test_pick_best_prefers_track_on_tie() -> None:
    from melody.subsonic.search import pick_best_match

    track_match = rank_tracks("test", [Track(id="1", title="Test", artist="A")])
    playlist_match = rank_playlists("test", [Playlist(id="p1", name="Test")])
    assert track_match is not None
    assert playlist_match is not None
    track_match = type(track_match)(kind="track", score=50, track=track_match.track)
    playlist_match = type(playlist_match)(kind="playlist", score=50, playlist=playlist_match.playlist)
    best = pick_best_match("test", track_match, playlist_match)
    assert best is not None
    assert best.kind == "track"


def test_pick_best_higher_score_wins() -> None:
    from melody.subsonic.search import pick_best_match

    track = rank_tracks("workout", [Track(id="1", title="Work", artist="X")])
    playlist = rank_playlists("workout", [Playlist(id="p1", name="Workout")])
    assert track is not None
    assert playlist is not None
    best = pick_best_match("workout", track, playlist)
    assert best is not None
    assert best.kind == "playlist"


@pytest.mark.asyncio
async def test_resolve_search_track_mode() -> None:
    client = FakeSubsonicClient(
        tracks=[Track(id="1", title="Song", artist="Artist")],
    )
    match = await resolve_search(client, "song", SearchMode.TRACK)
    assert match is not None
    assert match.kind == "track"


@pytest.mark.asyncio
async def test_resolve_search_playlist_mode() -> None:
    tracks = (Track(id="1", title="A", artist="B"),)
    client = FakeSubsonicClient(
        playlists=[Playlist(id="p1", name="Workout", tracks=tracks)],
    )
    match = await resolve_search(client, "workout", SearchMode.PLAYLIST)
    assert match is not None
    assert match.kind == "playlist"
    assert match.playlist is not None
    assert len(match.playlist.tracks) == 1


@pytest.mark.asyncio
async def test_resolve_search_album_mode() -> None:
    tracks = (Track(id="1", title="Speak to Me", artist="Pink Floyd"),)
    client = FakeSubsonicClient(
        albums=[Album(id="a1", name="Dark Side", artist="Pink Floyd", tracks=tracks)],
    )
    match = await resolve_search(client, "dark side", SearchMode.ALBUM)
    assert match is not None
    assert match.kind == "album"
    assert match.album is not None
    assert len(match.album.tracks) == 1


@pytest.mark.asyncio
async def test_resolve_search_defaults_to_track() -> None:
    client = FakeSubsonicClient(
        tracks=[Track(id="1", title="Workout Mix", artist="DJ")],
        playlists=[Playlist(id="p1", name="Workout")],
    )
    match = await resolve_search(client, "workout", SearchMode.TRACK)
    assert match is not None
    assert match.kind == "track"
    assert match.track is not None
    assert match.track.id == "1"


def test_popularity_breaks_relevance_tie() -> None:
    tracks = [
        Track(id="1", title="Hit Song", artist="A", play_count=5),
        Track(id="2", title="Hit Song", artist="B", play_count=8000),
    ]
    match = rank_tracks("hit song", tracks)
    assert match is not None
    assert match.track is not None
    assert match.track.id == "2"


def test_relevance_beats_popularity() -> None:
    tracks = [
        Track(id="1", title="Bohemian Rhapsody", artist="Queen", play_count=0),
        Track(id="2", title="Random", artist="Obscure", play_count=999_999),
    ]
    match = rank_tracks("bohemian rhapsody", tracks)
    assert match is not None
    assert match.track is not None
    assert match.track.id == "1"


def test_combined_score_uses_weights() -> None:
    from melody.models import SearchWeights
    from melody.subsonic.search import combined_score

    default = SearchWeights()
    assert combined_score(100, 0.0, default) == 85
    assert combined_score(100, 1.0, default) == 100

    pop_heavy = SearchWeights(relevance_percent=50, popularity_percent=50)
    assert combined_score(100, 1.0, pop_heavy) == 100
    assert combined_score(80, 0.0, pop_heavy) == 40
