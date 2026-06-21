"""Subsonic client interface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from melody.models import Playlist, Track


class ISubsonicClient(Protocol):
    """Backend-agnostic Subsonic API client."""

    async def search_tracks(self, query: str, limit: int = 20) -> list[Track]: ...

    async def search_playlists(self, query: str, limit: int = 20) -> list[Playlist]: ...

    async def get_playlist(self, playlist_id: str) -> Playlist: ...

    async def get_song(self, song_id: str) -> Track: ...

    def stream_url(self, song_id: str) -> str: ...

    async def stream(self, song_id: str) -> AsyncIterator[bytes]: ...

    async def close(self) -> None: ...
