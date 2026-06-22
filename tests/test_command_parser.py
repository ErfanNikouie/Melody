"""Tests for command parsing."""

from __future__ import annotations

import pytest

from melody.commands.parser import CommandParser
from melody.models import CommandOptions


@pytest.fixture
def parser() -> CommandParser:
    return CommandParser(["m/", "melody/", "/"])


def test_play_with_m_prefix(parser: CommandParser) -> None:
    cmd = parser.parse("m/play never gonna give you up")
    assert cmd is not None
    assert cmd.name == "play"
    assert cmd.query == "never gonna give you up"
    assert cmd.options == CommandOptions()


def test_queue_with_melody_prefix(parser: CommandParser) -> None:
    cmd = parser.parse("melody/queue --playlist workout")
    assert cmd is not None
    assert cmd.name == "queue"
    assert cmd.options.playlist is True
    assert cmd.query == "workout"


def test_play_track_option_after_query(parser: CommandParser) -> None:
    cmd = parser.parse("/play queen bohemian rhapsody -t")
    assert cmd is not None
    assert cmd.options.track is True
    assert cmd.query == "queen bohemian rhapsody"


def test_play_track_option_before_query(parser: CommandParser) -> None:
    cmd = parser.parse("m/play -t queen bohemian rhapsody")
    assert cmd is not None
    assert cmd.options.track is True
    assert cmd.query == "queen bohemian rhapsody"


def test_multiple_options(parser: CommandParser) -> None:
    cmd = parser.parse("m/queue -r -s workout mix")
    assert cmd is not None
    assert cmd.options.repeat is True
    assert cmd.options.shuffle is True
    assert cmd.query == "workout mix"


def test_stop_no_query(parser: CommandParser) -> None:
    cmd = parser.parse("m/stop")
    assert cmd is not None
    assert cmd.name == "stop"
    assert cmd.query is None


def test_unknown_command_returns_none(parser: CommandParser) -> None:
    assert parser.parse("m/skip track") is None


def test_no_prefix_returns_none(parser: CommandParser) -> None:
    assert parser.parse("play something") is None


def test_longest_prefix_wins() -> None:
    p = CommandParser(["m/", "melody/"])
    cmd = p.parse("melody/play test")
    assert cmd is not None
    assert cmd.name == "play"


def test_quit_and_exit(parser: CommandParser) -> None:
    assert parser.parse("m/quit") is not None
    assert parser.parse("/exit") is not None


def test_volume_command(parser: CommandParser) -> None:
    cmd = parser.parse("m/volume 75")
    assert cmd is not None
    assert cmd.name == "volume"
    assert cmd.query == "75"

    show = parser.parse("m/volume")
    assert show is not None
    assert show.name == "volume"
    assert show.query is None


def test_list_command(parser: CommandParser) -> None:
    cmd = parser.parse("m/list")
    assert cmd is not None
    assert cmd.name == "list"


def test_album_option(parser: CommandParser) -> None:
    cmd = parser.parse("m/play -a dark side of the moon")
    assert cmd is not None
    assert cmd.options.album is True
    assert cmd.query == "dark side of the moon"


def test_parse_all_multiline(parser: CommandParser) -> None:
    commands = parser.parse_all("m/play song one\nm/volume 50\nm/list")
    assert [c.name for c in commands] == ["play", "volume", "list"]
    assert commands[0].query == "song one"
    assert commands[1].query == "50"


def test_parse_all_skips_non_commands(parser: CommandParser) -> None:
    commands = parser.parse_all("hello\nm/stop\nworld")
    assert len(commands) == 1
    assert commands[0].name == "stop"


def test_help_command(parser: CommandParser) -> None:
    cmd = parser.parse("m/help")
    assert cmd is not None
    assert cmd.name == "help"
    assert cmd.query is None
