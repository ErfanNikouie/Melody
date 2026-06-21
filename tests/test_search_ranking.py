"""Tests for Subsonic search ranking."""

from __future__ import annotations

import pytest

from melody.models import CommandOptions, Playlist, Track
from melody.subsonic.search import (
    pick_best_match,
    rank_playlists,
    rank_tracks,
    resolve_search,
    score_playlist,
    score_track,
)


class FakeSubsonicClient:
    def __init__(
        self,
        tracks: list[Track] | None = None,
        playlists: list[Playlist] | None = None,
    ) -> None:
        self.tracks = tracks or []
        self.playlists = playlists or []

    async def search_tracks(self, query: str, limit: int = 20) -> list[Track]:
        return self.tracks

    async def search_playlists(self, query: str, limit: int = 20) -> list[Playlist]:
        return self.playlists

    async def get_playlist(self, playlist_id: str) -> Playlist:
        for pl in self.playlists:
            if pl.id == playlist_id:
                return pl
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
    assert match.score == 100


def test_pick_best_prefers_track_on_tie() -> None:
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
    track = rank_tracks("workout", [Track(id="1", title="Work", artist="X")])
    playlist = rank_playlists("workout", [Playlist(id="p1", name="Workout")])
    assert track is not None
    assert playlist is not None
    best = pick_best_match("workout", track, playlist)
    assert best is not None
    assert best.kind == "playlist"


@pytest.mark.asyncio
async def test_resolve_search_track_only() -> None:
    client = FakeSubsonicClient(
        tracks=[Track(id="1", title="Song", artist="Artist")],
    )
    match = await resolve_search(client, "song", CommandOptions(track=True))
    assert match is not None
    assert match.kind == "track"


@pytest.mark.asyncio
async def test_resolve_search_playlist_only() -> None:
    tracks = (Track(id="1", title="A", artist="B"),)
    client = FakeSubsonicClient(
        playlists=[Playlist(id="p1", name="Workout", tracks=tracks)],
    )
    match = await resolve_search(client, "workout", CommandOptions(playlist=True))
    assert match is not None
    assert match.kind == "playlist"
    assert match.playlist is not None
    assert len(match.playlist.tracks) == 1


@pytest.mark.asyncio
async def test_resolve_search_combined() -> None:
    client = FakeSubsonicClient(
        tracks=[Track(id="1", title="Random", artist="X")],
        playlists=[Playlist(id="p1", name="Workout")],
    )
    match = await resolve_search(client, "workout", CommandOptions())
    assert match is not None
    assert match.kind == "playlist"
