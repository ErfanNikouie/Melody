"""Search service — resolves queries via Subsonic."""

from __future__ import annotations

from melody.models import CommandOptions, SearchMatch, SearchMode
from melody.protocols import ISubsonicClient
from melody.subsonic.search import resolve_search


class SearchService:
    """Resolves user queries to ranked Subsonic matches."""

    def __init__(self, client: ISubsonicClient) -> None:
        self._client = client

    async def resolve(self, query: str, options: CommandOptions) -> SearchMatch | None:
        if options.album:
            mode = SearchMode.ALBUM
        elif options.playlist:
            mode = SearchMode.PLAYLIST
        else:
            mode = SearchMode.TRACK
        return await resolve_search(self._client, query, mode)
