"""Search result ranking for Subsonic queries."""

from __future__ import annotations

import math
import re
from typing import Protocol

from melody.models import Album, Playlist, SearchMatch, SearchMode, SearchWeights, Track
from melody.protocols import ISubsonicClient
from melody.logging import get_logger
from melody.subsonic.errors import AlbumNotFoundError, PlaylistNotFoundError

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"\w+")

DEFAULT_SEARCH_WEIGHTS = SearchWeights()


class _HasPopularity(Protocol):
    play_count: int
    user_rating: int


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def relevance_score_track(query: str, track: Track) -> int:
    """Score a track against a query (0–100). Higher is better."""
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


def relevance_score_playlist(query: str, playlist: Playlist) -> int:
    """Score a playlist against a query (0–100)."""
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


def relevance_score_album(query: str, album: Album) -> int:
    """Score an album against a query (0–100)."""
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


def popularity_norm_play_count(play_count: int, max_play_count: int) -> float:
    """Log-scaled play count in 0.0–1.0."""
    if play_count <= 0 or max_play_count <= 0:
        return 0.0
    return math.log1p(play_count) / math.log1p(max_play_count)


def popularity_norm_rating(user_rating: int) -> float:
    """Subsonic user rating (1–5) in 0.0–1.0."""
    if user_rating <= 0:
        return 0.0
    return min(1.0, user_rating / 5.0)


def popularity_score(item: _HasPopularity, max_play_count: int) -> float:
    """Combined popularity signal in 0.0–1.0."""
    play = popularity_norm_play_count(item.play_count, max_play_count)
    rating = popularity_norm_rating(item.user_rating)
    if play > 0 and rating > 0:
        return 0.7 * play + 0.3 * rating
    return max(play, rating)


def playlist_popularity_score(playlist: Playlist, max_song_count: int) -> float:
    """Playlists rarely expose play counts; use size as a weak signal."""
    if playlist.song_count <= 0 or max_song_count <= 0:
        return 0.0
    return math.log1p(playlist.song_count) / math.log1p(max_song_count)


def combined_score(relevance: int, popularity: float, weights: SearchWeights = DEFAULT_SEARCH_WEIGHTS) -> int:
    """Merge relevance (0–100) and popularity (0–1) using configured weights."""
    rel_w = weights.relevance_percent / 100.0
    pop_w = weights.popularity_percent / 100.0
    return int(round(relevance * rel_w + popularity * 100.0 * pop_w))


def _max_play_count(items: list[_HasPopularity]) -> int:
    return max((item.play_count for item in items), default=0)


def rank_tracks(
    query: str,
    tracks: list[Track],
    weights: SearchWeights = DEFAULT_SEARCH_WEIGHTS,
) -> SearchMatch | None:
    if not tracks:
        return None
    max_play = _max_play_count(tracks)

    def score(track: Track) -> int:
        rel = relevance_score_track(query, track)
        pop = popularity_score(track, max_play)
        return combined_score(rel, pop, weights)

    best = max(tracks, key=score)
    return SearchMatch(kind="track", score=score(best), track=best)


def rank_playlists(
    query: str,
    playlists: list[Playlist],
    weights: SearchWeights = DEFAULT_SEARCH_WEIGHTS,
) -> SearchMatch | None:
    if not playlists:
        return None
    max_songs = max((p.song_count for p in playlists), default=0)

    def score(playlist: Playlist) -> int:
        rel = relevance_score_playlist(query, playlist)
        pop = playlist_popularity_score(playlist, max_songs)
        return combined_score(rel, pop, weights)

    best = max(playlists, key=score)
    return SearchMatch(kind="playlist", score=score(best), playlist=best)


def rank_albums(
    query: str,
    albums: list[Album],
    weights: SearchWeights = DEFAULT_SEARCH_WEIGHTS,
) -> SearchMatch | None:
    if not albums:
        return None
    max_play = _max_play_count(albums)

    def score(album: Album) -> int:
        rel = relevance_score_album(query, album)
        pop = popularity_score(album, max_play)
        return combined_score(rel, pop, weights)

    best = max(albums, key=score)
    return SearchMatch(kind="album", score=score(best), album=best)


# Backwards-compatible aliases for tests
score_track = relevance_score_track
score_playlist = relevance_score_playlist
score_album = relevance_score_album


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


_OCTO_FIESTA_HINT = (
    "Streaming-provider albums/playlists only work when SUBSONIC_URL points at "
    "Octo Fiesta (e.g. http://host:5274), not Navidrome directly."
)


def _log_candidates(kind: str, query: str, items: list[Album] | list[Playlist] | list[Track]) -> None:
    for index, item in enumerate(items[:8]):
        if isinstance(item, Album):
            logger.debug(
                "  %s[%s] id=%s name=%r artist=%r song_count=%s",
                kind,
                index,
                item.id,
                item.name,
                item.artist,
                item.song_count,
            )
        elif isinstance(item, Playlist):
            logger.debug("  %s[%s] id=%s name=%r song_count=%s", kind, index, item.id, item.name, item.song_count)
        elif isinstance(item, Track):
            logger.debug("  %s[%s] id=%s name=%r", kind, index, item.id, item.display_name)


async def resolve_search(
    client: ISubsonicClient,
    query: str,
    mode: SearchMode,
    weights: SearchWeights = DEFAULT_SEARCH_WEIGHTS,
) -> SearchMatch | None:
    """Search Subsonic and return the best ranked match."""
    if not query or not query.strip():
        return None

    query = query.strip()
    backend = getattr(client, "base_url", "unknown")
    logger.info("Search start query=%r mode=%s backend=%s", query, mode.value, backend)

    if mode == SearchMode.PLAYLIST:
        playlists = await client.search_playlists(query)
        logger.info("Search playlist candidates=%s query=%r", len(playlists), query)
        _log_candidates("playlist", query, playlists)
        if not playlists:
            logger.warning("Search playlist returned 0 results query=%r backend=%s", query, backend)
            return None
        match = rank_playlists(query, playlists, weights)
        if match and match.playlist:
            logger.info(
                "Search playlist best_match=%r id=%s score=%s",
                match.playlist.name,
                match.playlist.id,
                match.score,
            )
            try:
                full = await client.get_playlist(match.playlist.id)
            except PlaylistNotFoundError:
                logger.error(
                    "getPlaylist failed id=%s query=%r backend=%s — %s",
                    match.playlist.id,
                    query,
                    backend,
                    _OCTO_FIESTA_HINT,
                )
                raise
            if not full.tracks:
                logger.warning(
                    "getPlaylist id=%s name=%r returned 0 tracks backend=%s",
                    full.id,
                    full.name,
                    backend,
                )
            return SearchMatch(
                kind="playlist",
                score=match.score,
                playlist=full,
            )
        return match

    if mode == SearchMode.ALBUM:
        albums = await client.search_albums(query)
        logger.info("Search album candidates=%s query=%r", len(albums), query)
        _log_candidates("album", query, albums)
        if not albums:
            logger.warning(
                "Search album returned 0 results query=%r backend=%s — %s",
                query,
                backend,
                _OCTO_FIESTA_HINT,
            )
            return None
        match = rank_albums(query, albums, weights)
        if match and match.album:
            logger.info(
                "Search album best_match=%r id=%s score=%s",
                match.album.display_name,
                match.album.id,
                match.score,
            )
            try:
                full = await client.get_album(match.album.id)
            except AlbumNotFoundError:
                logger.error(
                    "getAlbum failed id=%s query=%r backend=%s — album was in search but "
                    "details missing. %s",
                    match.album.id,
                    query,
                    backend,
                    _OCTO_FIESTA_HINT,
                )
                raise
            if not full.tracks:
                logger.warning(
                    "getAlbum id=%s name=%r returned 0 playable tracks backend=%s — %s",
                    full.id,
                    full.display_name,
                    backend,
                    _OCTO_FIESTA_HINT,
                )
            return SearchMatch(
                kind="album",
                score=match.score,
                album=full,
            )
        return match

    tracks = await client.search_tracks(query)
    logger.info("Search track candidates=%s query=%r", len(tracks), query)
    _log_candidates("track", query, tracks)
    if not tracks:
        logger.warning("Search track returned 0 results query=%r backend=%s", query, backend)
        return None
    match = rank_tracks(query, tracks, weights)
    if match and match.track:
        logger.info(
            "Search track best_match=%r id=%s score=%s",
            match.track.display_name,
            match.track.id,
            match.score,
        )
    return match
