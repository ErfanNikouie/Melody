"""Subsonic API exceptions."""

from __future__ import annotations


class SubsonicError(Exception):
    """Base error for Subsonic API failures."""


class SubsonicAuthError(SubsonicError):
    """Authentication failed."""


class TrackNotFoundError(SubsonicError):
    """Requested track was not found."""


class PlaylistNotFoundError(SubsonicError):
    """Requested playlist was not found."""


class AlbumNotFoundError(SubsonicError):
    """Requested album was not found."""


class StreamError(SubsonicError):
    """Streaming failed."""
