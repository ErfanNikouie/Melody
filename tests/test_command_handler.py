"""Tests for command handler feedback routing."""

from __future__ import annotations

import pytest

from melody.commands.handler import CommandHandler
from melody.models import Album, CommandOptions, ParsedCommand, SearchMatch, Track
from melody.playback.queue import PlaybackQueue
from melody.subsonic.errors import AlbumNotFoundError


class _Session:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.volume = 100
        self.queue = PlaybackQueue()

    async def send_message(self, text: str) -> None:
        self.messages.append(text)

    @property
    def volume_percent(self) -> int:
        return self.volume

    def set_volume_percent(self, percent: int) -> None:
        self.volume = percent

    async def ensure_joined(self) -> None:
        return None

    async def stop_playback(self, *, clear_all: bool = False) -> None:
        if clear_all:
            self.queue.clear()

    async def start_playback(self, *, announce: bool = True) -> None:
        return None

    def pause(self) -> None:
        return None

    async def resume(self) -> None:
        return None

    async def skip_next(self) -> None:
        return None

    async def skip_back(self) -> None:
        return None

    @property
    def playback_status(self):
        from melody.models import PlaybackStatus, PlaybackState

        return PlaybackStatus(state=PlaybackState.IDLE, track=None, elapsed_seconds=0.0, total_seconds=None)


class _Search:
    def __init__(self, match: SearchMatch | None = None, *, error: Exception | None = None) -> None:
        self._match = match
        self._error = error

    async def resolve(self, query: str, options: CommandOptions) -> SearchMatch | None:
        if self._error is not None:
            raise self._error
        return self._match


@pytest.mark.asyncio
async def test_feedback_whispers_when_notify_set() -> None:
    session = _Session()
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    handler = CommandHandler(search=object())  # type: ignore[arg-type]
    await handler.handle(
        ParsedCommand(name="play", options=CommandOptions(), query=None),
        session,  # type: ignore[arg-type]
        notify=notify,
    )

    assert notified
    assert "search query" in notified[0].lower()
    assert session.messages == []


@pytest.mark.asyncio
async def test_feedback_uses_channel_when_no_notify() -> None:
    session = _Session()

    handler = CommandHandler(search=object())  # type: ignore[arg-type]
    await handler.handle(
        ParsedCommand(name="play", options=CommandOptions(), query=None),
        session,  # type: ignore[arg-type]
    )

    assert session.messages
    assert "search query" in session.messages[0].lower()


@pytest.mark.asyncio
async def test_volume_show() -> None:
    session = _Session()
    handler = CommandHandler(search=object())  # type: ignore[arg-type]
    await handler.handle(
        ParsedCommand(name="volume", options=CommandOptions(), query=None),
        session,  # type: ignore[arg-type]
    )
    assert "50%" in session.messages[0] or "100%" in session.messages[0]
    assert "🔊" in session.messages[0]


@pytest.mark.asyncio
async def test_volume_set() -> None:
    session = _Session()
    handler = CommandHandler(search=object())  # type: ignore[arg-type]
    await handler.handle(
        ParsedCommand(name="volume", options=CommandOptions(), query="40"),
        session,  # type: ignore[arg-type]
    )
    assert session.volume == 40
    assert "40%" in session.messages[0]


@pytest.mark.asyncio
async def test_help_command() -> None:
    session = _Session()
    handler = CommandHandler(search=object(), command_prefix="m/")  # type: ignore[arg-type]
    await handler.handle(
        ParsedCommand(name="help", options=CommandOptions(), query=None),
        session,  # type: ignore[arg-type]
    )
    assert session.messages
    assert "play" in session.messages[0].lower()
    assert "m/play" in session.messages[0]


@pytest.mark.asyncio
async def test_album_play_announces_in_channel_when_whispered() -> None:
    track = Track(id="1", title="Time", artist="Pink Floyd", album="Dark Side")
    album = Album(id="a1", name="Dark Side", artist="Pink Floyd", tracks=(track,))
    match = SearchMatch(kind="album", score=90, album=album)
    session = _Session()
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    handler = CommandHandler(search=_Search(match))
    await handler.handle(
        ParsedCommand(name="play", options=CommandOptions(album=True), query="dark side"),
        session,  # type: ignore[arg-type]
        notify=notify,
    )

    assert notified
    assert session.messages
    assert "Playing" in notified[0]
    assert "Playing" in session.messages[0]
    assert notified[0] == session.messages[0]


@pytest.mark.asyncio
async def test_album_not_found_shows_search_error() -> None:
    session = _Session()
    handler = CommandHandler(search=_Search(error=AlbumNotFoundError("missing")))
    await handler.handle(
        ParsedCommand(name="play", options=CommandOptions(album=True), query="unknown"),
        session,  # type: ignore[arg-type]
    )
    assert session.messages
    assert "Search failed" in session.messages[0]
    assert "Octo Fiesta" in session.messages[0]
