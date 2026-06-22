"""Search service — resolves queries via Subsonic."""

from __future__ import annotations

from melody.models import CommandOptions, SearchMatch, SearchMode, SearchWeights
from melody.protocols import ISubsonicClient
from melody.subsonic.search import DEFAULT_SEARCH_WEIGHTS, resolve_search


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
        return await resolve_search(self._client, query, mode, weights=self._weights)
