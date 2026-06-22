"""Tests for command handler feedback routing."""

from __future__ import annotations

import pytest

from melody.commands.handler import CommandHandler
from melody.models import CommandOptions, ParsedCommand


class _Session:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.volume = 100

    async def send_message(self, text: str) -> None:
        self.messages.append(text)

    @property
    def volume_percent(self) -> int:
        return self.volume

    def set_volume_percent(self, percent: int) -> None:
        self.volume = percent


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
