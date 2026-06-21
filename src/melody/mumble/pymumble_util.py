"""Lazy pymumble import and text-message parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ROOT_CHANNEL_ID = 0


@dataclass(frozen=True, slots=True)
class ParsedTextMessage:
    """Normalized Mumble text message."""

    sender_session: int
    sender_name: str
    message: str
    sender_channel_id: int
    sender_channel_name: str
    is_private: bool
    target_channel_id: int | None


def load_pymumble() -> tuple[Any, Any, Any, Any, type[Exception]]:
    """Return (pymumble module, text callback id, connected id, disconnected id, reject error)."""
    import pymumble_py3 as pymumble
    from pymumble_py3.constants import (
        PYMUMBLE_CLBK_CONNECTED,
        PYMUMBLE_CLBK_DISCONNECTED,
        PYMUMBLE_CLBK_TEXTMESSAGERECEIVED,
    )
    from pymumble_py3.errors import ConnectionRejectedError

    return (
        pymumble,
        PYMUMBLE_CLBK_TEXTMESSAGERECEIVED,
        PYMUMBLE_CLBK_CONNECTED,
        PYMUMBLE_CLBK_DISCONNECTED,
        ConnectionRejectedError,
    )


def parse_text_message(mumble: Any, mess: Any) -> ParsedTextMessage | None:
    """Parse pymumble TextMessage protobuf into a normalized event."""
    if not mess.HasField("actor"):
        return None

    sender_session = int(mess.actor)
    text = mess.message or ""
    if not text.strip():
        return None

    is_private = len(mess.session) > 0
    channel_targets = list(mess.channel_id)

    sender_name = str(sender_session)
    sender_channel_id = ROOT_CHANNEL_ID
    try:
        user = mumble.users[sender_session]
        sender_name = user.get("name", sender_name)
        sender_channel_id = int(user.get("channel_id", ROOT_CHANNEL_ID))
    except (KeyError, TypeError):
        pass

    sender_channel_name = _channel_name(mumble, sender_channel_id)
    target_channel_id: int | None = None
    if channel_targets:
        target_channel_id = int(channel_targets[0])
    elif not is_private:
        target_channel_id = sender_channel_id

    return ParsedTextMessage(
        sender_session=sender_session,
        sender_name=sender_name,
        message=text,
        sender_channel_id=sender_channel_id,
        sender_channel_name=sender_channel_name,
        is_private=is_private,
        target_channel_id=target_channel_id,
    )


def _channel_name(mumble: Any, channel_id: int) -> str:
    try:
        return str(mumble.channels[channel_id]["name"])
    except (KeyError, TypeError):
        return str(channel_id)


def sanitize_username_part(name: str, *, max_length: int = 24) -> str:
    """Sanitize a channel name for use in MelodyPlayer-{name} usernames."""
    cleaned = re.sub(r"[^\w\-]", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "channel"
    return cleaned[:max_length]
