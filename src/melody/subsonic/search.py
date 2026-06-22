"""Search result ranking for Subsonic queries."""

from __future__ import annotations

import re

from melody.models import Album, Playlist, SearchMatch, SearchMode, Track
from melody.protocols import ISubsonicClient

_TOKEN_RE = re.compile(r"\w+")


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def score_track(query: str, track: Track) -> int:
    """Score a track against a query. Higher is better."""
    q = _normalize(query)
    title = _normalize(track.title)
    artist = _normalize(track.artist)
    combined = f"{artist} {title}".strip()

    if q == title or q == combined:
        return 100
    if artist and title and q == f"{artist} {title}":
        return 100
    if combined == q:
        return 80
    if title.startswith(q) or combined.startswith(q):
        return 60

    q_tokens = _tokens(q)
    if not q_tokens:
        return 0
    target_tokens = _tokens(combined)
    overlap = len(q_tokens & target_tokens) / len(q_tokens)
    return int(40 * overlap)


def score_playlist(query: str, playlist: Playlist) -> int:
    """Score a playlist against a query. Higher is better."""
    q = _normalize(query)
    name = _normalize(playlist.name)

    if q == name:
        return 100
    if name.startswith(q):
        return 60

    q_tokens = _tokens(q)
    if not q_tokens:
        return 0
    name_tokens = _tokens(name)
    overlap = len(q_tokens & name_tokens) / len(q_tokens)
    return int(40 * overlap)


def score_album(query: str, album: Album) -> int:
    """Score an album against a query. Higher is better."""
    q = _normalize(query)
    name = _normalize(album.name)
    artist = _normalize(album.artist)
    combined = f"{artist} {name}".strip() if artist else name

    if q == name or q == combined:
        return 100
    if combined.startswith(q) or name.startswith(q):
        return 60

    q_tokens = _tokens(q)
    if not q_tokens:
        return 0
    target_tokens = _tokens(combined)
    overlap = len(q_tokens & target_tokens) / len(q_tokens)
    return int(40 * overlap)


def rank_tracks(query: str, tracks: list[Track]) -> SearchMatch | None:
    if not tracks:
        return None
    best = max(tracks, key=lambda t: score_track(query, t))
    return SearchMatch(kind="track", score=score_track(query, best), track=best)


def rank_playlists(query: str, playlists: list[Playlist]) -> SearchMatch | None:
    if not playlists:
        return None
    best = max(playlists, key=lambda p: score_playlist(query, p))
    return SearchMatch(kind="playlist", score=score_playlist(query, best), playlist=best)


def rank_albums(query: str, albums: list[Album]) -> SearchMatch | None:
    if not albums:
        return None
    best = max(albums, key=lambda a: score_album(query, a))
    return SearchMatch(kind="album", score=score_album(query, best), album=best)


def pick_best_match(
    query: str,
    track_match: SearchMatch | None,
    playlist_match: SearchMatch | None,
) -> SearchMatch | None:
    """Pick the best overall match; prefer tracks on tie."""
    if track_match is None and playlist_match is None:
        return None
    if track_match is None:
        return playlist_match
    if playlist_match is None:
        return track_match
    if track_match.score > playlist_match.score:
        return track_match
    if playlist_match.score > track_match.score:
        return playlist_match
    return track_match


async def resolve_search(
    client: ISubsonicClient,
    query: str,
    mode: SearchMode,
) -> SearchMatch | None:
    """Search Subsonic and return the best ranked match."""
    if not query or not query.strip():
        return None

    query = query.strip()

    if mode == SearchMode.PLAYLIST:
        playlists = await client.search_playlists(query)
        match = rank_playlists(query, playlists)
        if match and match.playlist:
            full = await client.get_playlist(match.playlist.id)
            return SearchMatch(
                kind="playlist",
                score=match.score,
                playlist=full,
            )
        return match

    if mode == SearchMode.ALBUM:
        albums = await client.search_albums(query)
        match = rank_albums(query, albums)
        if match and match.album:
            full = await client.get_album(match.album.id)
            return SearchMatch(
                kind="album",
                score=match.score,
                album=full,
            )
        return match

    tracks = await client.search_tracks(query)
    return rank_tracks(query, tracks)
