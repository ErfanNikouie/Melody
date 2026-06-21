"""Subsonic XML response parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from melody.models import Playlist, Track
from melody.subsonic.errors import SubsonicError

_NS = "{http://subsonic.org/restapi}"


def _find(parent: ET.Element, tag: str) -> ET.Element | None:
    el = parent.find(tag)
    if el is not None:
        return el
    return parent.find(f"{_NS}{tag}")


def _findall(parent: ET.Element, tag: str) -> list[ET.Element]:
    items = parent.findall(tag)
    if items:
        return items
    return parent.findall(f"{_NS}{tag}")


def _text(element: ET.Element | None, tag: str, default: str = "") -> str:
    if element is None:
        return default
    child = _find(element, tag)
    if child is None or child.text is None:
        return default
    return child.text


def _int(element: ET.Element | None, tag: str, default: int = 0) -> int:
    value = _text(element, tag)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_track(element: ET.Element) -> Track:
    return Track(
        id=_text(element, "id"),
        title=_text(element, "title"),
        artist=_text(element, "artist"),
        album=_text(element, "album"),
        duration=_int(element, "duration"),
    )


def parse_playlist_meta(element: ET.Element) -> Playlist:
    return Playlist(
        id=_text(element, "id"),
        name=_text(element, "name"),
        tracks=(),
    )


def parse_playlist(element: ET.Element) -> Playlist:
    entries = _findall(element, "entry")
    tracks = tuple(parse_track(e) for e in entries)
    return Playlist(
        id=_text(element, "id"),
        name=_text(element, "name"),
        tracks=tracks,
    )


def check_response_status(root: ET.Element) -> None:
    status = root.attrib.get("status")
    if status is None:
        status_el = _find(root, "status")
        status = status_el.text if status_el is not None else None
    if status == "ok":
        return
    error_el = _find(root, "error")
    code = error_el.get("code", "?") if error_el is not None else "?"
    message = error_el.get("message", "unknown error") if error_el is not None else "unknown error"
    raise SubsonicError(f"Subsonic error {code}: {message}")
