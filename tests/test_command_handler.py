"""Tests for command handler feedback routing."""

from __future__ import annotations

import asyncio

import pytest

from melody.commands.handler import CommandHandler
from melody.models import Album, CommandOptions, ParsedCommand, QueueItem, SearchMatch, Track
from melody.playback.queue import QueueManager
from melody.subsonic.errors import AlbumNotFoundError


class _Session:
    def __init__(self) -> None:
        self.channel_id = 1
        self.messages: list[str] = []
        self.volume = 100
        self.queue = QueueManager()
        self.stop_waited = False
        self.stop_drained = False
        self.replaced = False

    async def send_message(self, text: str) -> None:
        self.messages.append(text)

    @property
    def volume_percent(self) -> int:
        return self.volume

    def set_volume_percent(self, percent: int) -> None:
        self.volume = percent

    async def ensure_joined(self) -> bool:
        return True

    def begin_stop(self, *, clear_all: bool = False) -> None:
        self.stop_waited = True
        if clear_all:
            self.queue.clear_all()

    def schedule_stop_drain(self) -> None:
        self.stop_drained = True

    async def stop_playback(self, *, clear_all: bool = False) -> None:
        self.begin_stop(clear_all=clear_all)
        self.stop_drained = True

    async def replace_playback(self) -> None:
        self.replaced = True
        self.queue.clear_all()

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
        from melody.models import PlaybackState, PlaybackStatus

        return PlaybackStatus(state=PlaybackState.IDLE, track=None, elapsed_seconds=0.0, total_seconds=None)


class _Search:
    def __init__(
        self,
        match: SearchMatch | None = None,
        *,
        error: Exception | None = None,
        results: list[SearchMatch] | None = None,
    ) -> None:
        self._match = match
        self._error = error
        self._results = results or []

    async def resolve(self, query: str, options: CommandOptions) -> SearchMatch | None:
        if self._error is not None:
            raise self._error
        return self._match

    async def search_top(
        self,
        query: str,
        options: CommandOptions,
        *,
        limit: int | None = None,
    ) -> list[SearchMatch]:
        if self._error is not None:
            raise self._error
        return self._results


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
async def test_play_shows_searching_and_queues_track() -> None:
    track = Track(id="1", title="Song", artist="Artist")
    match = SearchMatch(kind="track", score=90, track=track)
    session = _Session()
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    handler = CommandHandler(search=_Search(match))
    await handler.handle(
        ParsedCommand(name="play", options=CommandOptions(), query="song"),
        session,  # type: ignore[arg-type]
        notify=notify,
    )

    assert not session.replaced
    assert not session.stop_waited
    assert notified[0] == "🔍 <b>Searching</b>…"
    assert any("Playing" in text for text in notified)
    assert session.queue.current is not None


@pytest.mark.asyncio
async def test_play_when_busy_shows_queued() -> None:
    track = Track(id="1", title="Song", artist="Artist")
    match = SearchMatch(kind="track", score=90, track=track)
    session = _Session()
    session.queue.enqueue([QueueItem(track=Track(id="0", title="Busy", artist="A"))])

    handler = CommandHandler(search=_Search(match))
    await handler.handle(
        ParsedCommand(name="play", options=CommandOptions(), query="song"),
        session,  # type: ignore[arg-type]
    )

    assert any("Queued" in text for text in session.messages)


@pytest.mark.asyncio
async def test_play_uses_prefetched_search_task() -> None:
    track = Track(id="1", title="Song", artist="Artist")
    match = SearchMatch(kind="track", score=90, track=track)
    session = _Session()
    search = _Search(match)
    handler = CommandHandler(search=search)
    task = asyncio.create_task(search.resolve("song", CommandOptions()))

    await handler.handle(
        ParsedCommand(name="play", options=CommandOptions(), query="song"),
        session,  # type: ignore[arg-type]
        search_task=task,
    )

    assert not session.replaced
    assert session.queue.current is not None
    assert session.queue.current.track.id == "1"


@pytest.mark.asyncio
async def test_search_returns_top_results() -> None:
    tracks = [
        SearchMatch(kind="track", score=90, track=Track(id=str(i), title=f"Song {i}", artist="A"))
        for i in range(3)
    ]
    session = _Session()
    handler = CommandHandler(search=_Search(results=tracks))
    await handler.handle(
        ParsedCommand(name="search", options=CommandOptions(), query="song"),
        session,  # type: ignore[arg-type]
    )

    assert any("Search results" in text for text in session.messages)
    assert any("Song 0" in text for text in session.messages)


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
    assert notified[0] == "🔍 <b>Searching</b>…"
    assert any("Playing" in text for text in notified)
    assert any("Playing" in text for text in session.messages)
    assert notified[-1] == session.messages[-1]


@pytest.mark.asyncio
async def test_exit_command_replies_before_release() -> None:
    session = _Session()
    handler = CommandHandler(search=object())  # type: ignore[arg-type]
    destroy = await handler.handle(
        ParsedCommand(name="exit", options=CommandOptions(), query=None),
        session,  # type: ignore[arg-type]
    )

    assert destroy
    assert session.messages == ["👋 <b>Left channel</b>"]


@pytest.mark.asyncio
async def test_stop_command_replies_before_background_drain() -> None:
    session = _Session()
    handler = CommandHandler(search=object())  # type: ignore[arg-type]
    await handler.handle(
        ParsedCommand(name="stop", options=CommandOptions(), query=None),
        session,  # type: ignore[arg-type]
    )

    assert session.stop_waited
    assert session.stop_drained
    assert session.messages == ["⏹️ <b>Stopped</b> — queue cleared"]
    assert session.queue.is_idle


@pytest.mark.asyncio
async def test_album_not_found_shows_search_error() -> None:
    session = _Session()
    handler = CommandHandler(search=_Search(error=AlbumNotFoundError("missing")))
    await handler.handle(
        ParsedCommand(name="play", options=CommandOptions(album=True), query="unknown"),
        session,  # type: ignore[arg-type]
    )
    assert session.messages
    assert any("Search failed" in text for text in session.messages)
    assert "Octo Fiesta" not in "".join(session.messages)
