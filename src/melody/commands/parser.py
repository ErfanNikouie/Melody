"""Parse Mumble text chat commands."""

from __future__ import annotations

from melody.models import CommandOptions, ParsedCommand

KNOWN_COMMANDS = frozenset(
    {
        "play",
        "queue",
        "stop",
        "pause",
        "resume",
        "next",
        "back",
        "volume",
        "quit",
        "exit",
    }
)

OPTION_MAP: dict[str, str] = {
    "-t": "track",
    "--track": "track",
    "-p": "playlist",
    "--playlist": "playlist",
    "-r": "repeat",
    "--repeat": "repeat",
    "-s": "shuffle",
    "--shuffle": "shuffle",
}


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

    def _match_prefix(self, text: str) -> str | None:
        lower = text.lower()
        for prefix in self._prefixes:
            if lower.startswith(prefix.lower()):
                return text[: len(prefix)]
        return None

    def _extract_options(self, tokens: list[str]) -> tuple[CommandOptions, list[str]]:
        flags = {"track": False, "playlist": False, "repeat": False, "shuffle": False}
        query_tokens: list[str] = []

        for token in tokens:
            key = OPTION_MAP.get(token.lower())
            if key:
                flags[key] = True
            else:
                query_tokens.append(token)

        return CommandOptions(**flags), query_tokens
