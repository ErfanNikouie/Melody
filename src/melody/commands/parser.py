"""Parse Mumble text chat commands."""

from __future__ import annotations

import re
from html import unescape

from melody.models import CommandOptions, ParsedCommand

KNOWN_COMMANDS = frozenset(
    {
        "play",
        "search",
        "stop",
        "pause",
        "resume",
        "next",
        "back",
        "list",
        "current",
        "volume",
        "help",
        "quit",
        "exit",
    }
)

OPTION_MAP: dict[str, str] = {
    "-t": "track",
    "--track": "track",
    "-p": "playlist",
    "--playlist": "playlist",
    "-a": "album",
    "--album": "album",
    "-r": "repeat",
    "--repeat": "repeat",
    "-s": "shuffle",
    "--shuffle": "shuffle",
}

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


class CommandParser:
    """Parse prefixed chat commands into structured commands."""

    def __init__(self, prefixes: list[str]) -> None:
        self._prefixes = sorted(prefixes, key=len, reverse=True)

    def parse(self, message: str) -> ParsedCommand | None:
        """Return ParsedCommand if message is a bot command, else None."""
        text = message.strip()
        if not text:
            return None

        prefix = self._match_prefix(text)
        if prefix is None:
            return None

        remainder = text[len(prefix) :].strip()
        if not remainder:
            return None

        tokens = remainder.split()
        command_name = tokens[0].lower()
        if command_name not in KNOWN_COMMANDS:
            return None

        option_tokens = tokens[1:]
        options, query_tokens = self._extract_options(option_tokens)
        query = " ".join(query_tokens).strip() or None

        return ParsedCommand(name=command_name, options=options, query=query)

    def parse_all(self, message: str) -> list[ParsedCommand]:
        """Parse each non-empty line as a separate command."""
        commands: list[ParsedCommand] = []
        for line in _split_message_lines(message):
            command = self.parse(line)
            if command is None:
                command = self._parse_bare_command_line(line)
            if command is not None:
                commands.append(command)
        return commands

    def _parse_bare_command_line(self, line: str) -> ParsedCommand | None:
        """Parse lines like ``volume 50`` when sent without a prefix on the next line."""
        tokens = line.split()
        if not tokens:
            return None
        command_name = tokens[0].lower()
        if command_name not in KNOWN_COMMANDS:
            return None
        options, query_tokens = self._extract_options(tokens[1:])
        query = " ".join(query_tokens).strip() or None
        return ParsedCommand(name=command_name, options=options, query=query)

    def _match_prefix(self, text: str) -> str | None:
        lower = text.lower()
        for prefix in self._prefixes:
            if lower.startswith(prefix.lower()):
                return text[: len(prefix)]
        return None

    def _extract_options(self, tokens: list[str]) -> tuple[CommandOptions, list[str]]:
        flags = {
            "track": False,
            "playlist": False,
            "album": False,
            "repeat": False,
            "shuffle": False,
        }
        query_tokens: list[str] = []

        for token in tokens:
            key = OPTION_MAP.get(token.lower())
            if key:
                flags[key] = True
            else:
                query_tokens.append(token)

        return CommandOptions(**flags), query_tokens


def _split_message_lines(message: str) -> list[str]:
    """Split a Mumble message into logical lines (plain, HTML, or CRLF)."""
    text = unescape(message)
    text = _BR_RE.sub("\n", text)
    for separator in ("\r\n", "\r", "\u2028", "\u2029"):
        text = text.replace(separator, "\n")
    return [line.strip() for line in text.split("\n") if line.strip()]
