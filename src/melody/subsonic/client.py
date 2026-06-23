"""aiohttp-based Subsonic client for any Open Subsonic-compatible server."""

from __future__ import annotations

import hashlib
import secrets
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import aiohttp

from melody.logging import get_logger
from melody.models import Album, Playlist, Track
from melody.subsonic.errors import (
    AlbumNotFoundError,
    PlaylistNotFoundError,
    StreamError,
    SubsonicAuthError,
    SubsonicError,
    TrackNotFoundError,
)
from melody.protocols import ISubsonicClient
from melody.subsonic.xml_utils import (
    _findall,
    check_response_status,
    find_search_result3,
    parse_album,
    parse_playlist,
    parse_playlist_meta,
    parse_search_albums,
    parse_search_tracks,
    parse_track,
    search_result3_child_counts,
)

logger = get_logger(__name__)

API_VERSION = "1.16.1"
CLIENT_NAME = "Melody"
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15, sock_read=60)


class SubsonicClient(ISubsonicClient):
    """Subsonic REST client using token authentication."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._session = session
        self._owns_session = session is None

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
        return self._session

    def _auth_params(self) -> dict[str, str]:
        salt = secrets.token_hex(8)
        token = hashlib.md5(f"{self._password}{salt}".encode()).hexdigest()  # noqa: S324
        return {
            "u": self._username,
            "t": token,
            "s": salt,
            "v": API_VERSION,
            "c": CLIENT_NAME,
            "f": "xml",
        }

    def _rest_url(self, endpoint: str, extra: dict[str, Any] | None = None) -> str:
        params = self._auth_params()
        if extra:
            params.update({k: str(v) for k, v in extra.items() if v is not None})
        query = urlencode(params)
        return f"{self._base_url}/rest/{endpoint}?{query}"

    async def _fetch_xml(self, endpoint: str, extra: dict[str, Any] | None = None) -> ET.Element:
        url = self._rest_url(endpoint, extra)
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status == 401:
                    raise SubsonicAuthError("Subsonic authentication failed")
                if resp.status >= 400:
                    body = await resp.text()
                    raise SubsonicError(f"HTTP {resp.status} from {endpoint}: {body[:200]}")
                text = await resp.text()
        except aiohttp.ClientError as exc:
            raise SubsonicError(f"Network error calling {endpoint}: {exc}") from exc

        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise SubsonicError(f"Invalid XML from {endpoint}") from exc

        check_response_status(root)
        return root

    async def _search3(
        self,
        query: str,
        *,
        song_count: int = 20,
        album_count: int = 20,
        artist_count: int = 0,
    ) -> ET.Element | None:
        """Call search3 with non-zero song/album counts (Octo Fiesta recommendation)."""
        root = await self._fetch_xml(
            "search3.view",
            {
                "query": query,
                "songCount": song_count,
                "albumCount": album_count,
                "artistCount": artist_count,
            },
        )
        return find_search_result3(root)

    def _log_search3_result(
        self,
        query: str,
        kind: str,
        result: ET.Element | None,
        parsed_count: int,
    ) -> None:
        if result is None:
            logger.warning(
                "search3 query=%r kind=%s backend=%s: missing searchResult3 element",
                query,
                kind,
                self._base_url,
            )
            return

        child_counts = search_result3_child_counts(result)
        logger.info(
            "search3 query=%r kind=%s backend=%s parsed=%s xml=%s",
            query,
            kind,
            self._base_url,
            parsed_count,
            child_counts,
        )
        if parsed_count == 0 and child_counts:
            logger.warning(
                "search3 query=%r kind=%s backend=%s: XML had children %s but parsed 0 %s — "
                "check response format",
                query,
                kind,
                self._base_url,
                child_counts,
                kind,
            )

    async def search_tracks(self, query: str, limit: int = 20) -> list[Track]:
        result = await self._search3(query, song_count=limit, album_count=limit)
        if result is None:
            self._log_search3_result(query, "track", None, 0)
            return []

        tracks = parse_search_tracks(result)[:limit]
        self._log_search3_result(query, "track", result, len(tracks))
        for index, track in enumerate(tracks[:5]):
            logger.debug("  track[%s] id=%s name=%r", index, track.id, track.display_name)
        return tracks

    async def search_albums(self, query: str, limit: int = 20) -> list[Album]:
        result = await self._search3(query, song_count=limit, album_count=limit)
        if result is None:
            self._log_search3_result(query, "album", None, 0)
            return []

        albums = parse_search_albums(result)[:limit]
        self._log_search3_result(query, "album", result, len(albums))
        for index, album in enumerate(albums[:5]):
            logger.debug(
                "  album[%s] id=%s name=%r artist=%r song_count=%s",
                index,
                album.id,
                album.name,
                album.artist,
                album.song_count,
            )
        return albums

    async def search_playlists(self, query: str, limit: int = 20) -> list[Playlist]:
        root = await self._fetch_xml("getPlaylists.view")
        playlists_el = root.find(".//{http://subsonic.org/restapi}playlists")
        if playlists_el is None:
            playlists_el = root.find(".//playlists")
        if playlists_el is None:
            return []

        query_lower = query.lower()
        matches: list[Playlist] = []
        for pl_el in _findall(playlists_el, "playlist"):
            playlist = parse_playlist_meta(pl_el)
            if query_lower in playlist.name.lower():
                matches.append(playlist)
            if len(matches) >= limit:
                break
        logger.info(
            "getPlaylists filter query=%r backend=%s count=%s",
            query,
            self._base_url,
            len(matches),
        )
        return matches

    async def get_playlist(self, playlist_id: str) -> Playlist:
        try:
            root = await self._fetch_xml("getPlaylist.view", {"id": playlist_id})
        except SubsonicError as exc:
            raise PlaylistNotFoundError(str(exc)) from exc

        playlist_el = root.find(".//{http://subsonic.org/restapi}playlist")
        if playlist_el is None:
            playlist_el = root.find(".//playlist")
        if playlist_el is None:
            raise PlaylistNotFoundError(f"Playlist {playlist_id} not found")
        playlist = parse_playlist(playlist_el)
        logger.info(
            "getPlaylist id=%s name=%r backend=%s track_count=%s",
            playlist_id,
            playlist.name,
            self._base_url,
            len(playlist.tracks),
        )
        return playlist

    async def get_album(self, album_id: str) -> Album:
        try:
            root = await self._fetch_xml("getAlbum.view", {"id": album_id})
        except SubsonicError as exc:
            raise AlbumNotFoundError(str(exc)) from exc

        album_el = root.find(".//{http://subsonic.org/restapi}album")
        if album_el is None:
            album_el = root.find(".//album")
        if album_el is None:
            raise AlbumNotFoundError(f"Album {album_id} not found")
        album = parse_album(album_el)
        logger.info(
            "getAlbum id=%s name=%r backend=%s track_count=%s",
            album_id,
            album.name,
            self._base_url,
            len(album.tracks),
        )
        if not album.tracks:
            logger.warning(
                "getAlbum id=%s name=%r returned 0 tracks backend=%s",
                album_id,
                album.name,
                self._base_url,
            )
        return album

    async def get_song(self, song_id: str) -> Track:
        try:
            root = await self._fetch_xml("getSong.view", {"id": song_id})
        except SubsonicError as exc:
            raise TrackNotFoundError(str(exc)) from exc

        song_el = root.find(".//{http://subsonic.org/restapi}song")
        if song_el is None:
            song_el = root.find(".//song")
        if song_el is None:
            raise TrackNotFoundError(f"Song {song_id} not found")
        return parse_track(song_el)

    def stream_url(self, song_id: str) -> str:
        return self._rest_url(
            "stream.view",
            {"id": song_id, "maxBitRate": 320, "format": "mp3"},
        )

    async def open_stream(self, song_id: str) -> tuple[str, AsyncIterator[bytes]]:
        """Return (Content-Type, audio byte stream)."""
        url = self.stream_url(song_id)
        session = await self._get_session()

        try:
            resp = await session.get(url)
        except aiohttp.ClientError as exc:
            raise StreamError(f"Stream network error for song {song_id}: {exc}") from exc

        if resp.status == 401:
            await resp.release()
            raise SubsonicAuthError("Stream authentication failed")
        if resp.status >= 400:
            body = await resp.text()
            await resp.release()
            raise StreamError(f"Stream HTTP {resp.status} for song {song_id}: {body[:200]}")

        content_type = resp.headers.get("Content-Type", "")
        if "xml" in content_type or "json" in content_type:
            body = await resp.text()
            await resp.release()
            raise StreamError(f"Stream returned error body: {body[:300]}")

        async def body() -> AsyncIterator[bytes]:
            total = 0
            try:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    if chunk:
                        total += len(chunk)
                        yield chunk
                if total == 0:
                    raise StreamError(f"Stream returned no audio data for song {song_id}")
                logger.debug(
                    "Stream complete song_id=%s bytes=%s content_type=%s",
                    song_id,
                    total,
                    content_type,
                )
            finally:
                await resp.release()

        return content_type, body()

    async def stream(self, song_id: str) -> AsyncIterator[bytes]:
        _, audio = await self.open_stream(song_id)
        try:
            async for chunk in audio:
                yield chunk
        finally:
            closer = getattr(audio, "aclose", None)
            if closer is not None:
                await closer()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
