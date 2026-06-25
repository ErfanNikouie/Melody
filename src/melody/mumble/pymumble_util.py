"""Lazy pymumble import and text-message parsing."""

from __future__ import annotations

import re
from collections.abc import Callable
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


def load_pymumble() -> tuple[Any, type[Exception]]:
    """Return (pymumble module, connection-rejected exception class)."""
    import mumble as pymumble
    from mumble.errors import ConnectionRejectedError

    return pymumble, ConnectionRejectedError


def bind_callbacks(
    mumble: Any,
    *,
    on_text: Callable[[Any], None],
    on_connected: Callable[[], None],
    on_disconnected: Callable[[], None],
    on_users_changed: Callable[[], None] | None = None,
) -> None:
    """Register pymumble event handlers."""
    mumble.callbacks.text_message_received.set_handler(on_text)
    mumble.callbacks.connected.set_handler(on_connected)
    mumble.callbacks.disconnected.set_handler(on_disconnected)
    if on_users_changed is not None:
        mumble.callbacks.user_updated.set_handler(
            lambda _user, changes: on_users_changed() if "channel_id" in changes else None
        )
        mumble.callbacks.user_removed.set_handler(lambda _user, _msg: on_users_changed())


def disable_incoming_audio(mumble: Any) -> None:
    """Transmit-only mode: do not decode or buffer other users' voice in RAM."""
    mumble.callbacks.sound_received.set_handler(lambda _user, _chunk: None)

    def on_user_created(user: Any) -> None:
        sound = getattr(user, "sound", None)
        if sound is not None:
            sound.set_receive_sound(False)

    mumble.callbacks.user_created.set_handler(on_user_created)
    for user in mumble.users.by_session().values():
        on_user_created(user)


def clear_callbacks(mumble: Any) -> None:
    """Best-effort removal of callbacks that can retain Melody objects."""
    try:
        mumble.callbacks.text_message_received.clear_handler()
        mumble.callbacks.connected.clear_handler()
        mumble.callbacks.disconnected.clear_handler()
        mumble.callbacks.user_updated.clear_handler()
        mumble.callbacks.user_removed.clear_handler()
        mumble.callbacks.user_created.clear_handler()
        mumble.callbacks.sound_received.clear_handler()
    except Exception:
        # Teardown should never fail just because pymumble's callback object is already gone.
        pass


def get_session_id(mumble: Any) -> int | None:
    """Return this bot's Mumble session id, if known."""
    session = mumble.users.my_session
    return int(session) if session is not None else None


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
        sender_name = user.name
        sender_channel_id = int(user.channel_id)
    except (KeyError, TypeError, AttributeError):
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


def is_player_channel_message(message: ParsedTextMessage, channel_id: int) -> bool:
    """True if the MelodyPlayer in channel_id should handle this text."""
    if message.is_private:
        return True
    if message.target_channel_id == channel_id:
        return True
    return message.sender_channel_id == channel_id


def sanitize_username_part(name: str, *, max_length: int = 24) -> str:
    """Sanitize a channel name for use in MelodyPlayer-{name} usernames."""
    cleaned = re.sub(r"[^\w\-]", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "channel"
    return cleaned[:max_length]
