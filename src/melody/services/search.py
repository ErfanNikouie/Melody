"""Search service — resolves queries via Subsonic."""

from __future__ import annotations

from melody.logging import get_logger
from melody.models import CommandOptions, SearchMatch, SearchMode, SearchWeights
from melody.protocols import ISubsonicClient
from melody.subsonic.search import DEFAULT_SEARCH_WEIGHTS, resolve_search

logger = get_logger(__name__)


class SearchService:
    """Resolves user queries to ranked Subsonic matches."""

    def __init__(
        self,
        client: ISubsonicClient,
        weights: SearchWeights = DEFAULT_SEARCH_WEIGHTS,
    ) -> None:
        self._client = client
        self._weights = weights

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
