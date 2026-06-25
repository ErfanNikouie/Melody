"""Search service — resolves queries via Subsonic."""

from __future__ import annotations

from melody.logging import get_logger
from melody.models import CommandOptions, SearchMatch, SearchMode, SearchWeights
from melody.protocols import ISubsonicClient
from melody.subsonic.search import (
    DEFAULT_SEARCH_WEIGHTS,
    rank_albums_top,
    rank_playlists_top,
    rank_tracks_top,
    resolve_search,
)

logger = get_logger(__name__)


class SearchService:
    """Resolves user queries to ranked Subsonic matches."""

    def __init__(
        self,
        client: ISubsonicClient,
        weights: SearchWeights = DEFAULT_SEARCH_WEIGHTS,
        *,
        results_limit: int = 10,
    ) -> None:
        self._client = client
        self._weights = weights
        self._results_limit = results_limit

    async def resolve(self, query: str, options: CommandOptions) -> SearchMatch | None:
        if options.album:
            mode = SearchMode.ALBUM
        elif options.playlist:
            mode = SearchMode.PLAYLIST
        else:
            mode = SearchMode.TRACK

        backend = getattr(self._client, "base_url", "unknown")
        logger.info(
            "SearchService resolve query=%r mode=%s options(album=%s playlist=%s track=%s) backend=%s",
            query,
            mode.value,
            options.album,
            options.playlist,
            options.track,
            backend,
        )
        match = await resolve_search(self._client, query, mode, weights=self._weights)
        if match is None:
            logger.info("SearchService no match query=%r mode=%s", query, mode.value)
        else:
            logger.info(
                "SearchService matched kind=%s name=%r score=%s tracks=%s",
                match.kind,
                match.display_name,
                match.score,
                match.track_count,
            )
        return match

    async def search_top(
        self,
        query: str,
        options: CommandOptions,
        *,
        limit: int | None = None,
    ) -> list[SearchMatch]:
        """Return top ranked matches without resolving to a single play target."""
        if options.album:
            mode = SearchMode.ALBUM
        elif options.playlist:
            mode = SearchMode.PLAYLIST
        else:
            mode = SearchMode.TRACK

        effective_limit = limit if limit is not None else self._results_limit
        backend = getattr(self._client, "base_url", "unknown")
        logger.info(
            "SearchService search_top query=%r mode=%s limit=%s backend=%s",
            query,
            mode.value,
            effective_limit,
            backend,
        )

        if mode == SearchMode.TRACK:
            tracks = await self._client.search_tracks(query)
            return rank_tracks_top(query, tracks, self._weights, limit=effective_limit)
        if mode == SearchMode.PLAYLIST:
            playlists = await self._client.search_playlists(query)
            return rank_playlists_top(query, playlists, self._weights, limit=effective_limit)
        albums = await self._client.search_albums(query)
        return rank_albums_top(query, albums, self._weights, limit=effective_limit)
