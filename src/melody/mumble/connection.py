"""Reconnecting pymumble connection wrapper."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from melody.logging import get_logger
from melody.mumble.pymumble_util import ParsedTextMessage, load_pymumble, parse_text_message

logger = get_logger(__name__)

TextHandler = Callable[[ParsedTextMessage], None]
ConnectedHandler = Callable[[], None]
DisconnectedHandler = Callable[[], None]


class MumbleConnection:
    """One Mumble user session with automatic reconnect."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        reconnect: bool = True,
        stereo: bool = True,
        on_text: TextHandler | None = None,
        on_connected: ConnectedHandler | None = None,
        on_disconnected: DisconnectedHandler | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._reconnect = reconnect
        self._stereo = stereo
        self._on_text = on_text
        self._on_connected_cb = on_connected
        self._on_disconnected_cb = on_disconnected
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mumble: Any = None
        self._thread: threading.Thread | None = None
        self._ready = asyncio.Event()
        self._bot_session_id: int | None = None
        self._deny_error: Any = None
        self._post_connect_channel: int | None = None

    @property
    def username(self) -> str:
        return self._username

    @property
    def is_connected(self) -> bool:
        return self._mumble is not None and self._mumble.is_alive()

    @property
    def session_id(self) -> int | None:
        return self._bot_session_id

    def set_post_connect_channel(self, channel_id: int | None) -> None:
        """Channel to join automatically after each successful connect."""
        self._post_connect_channel = channel_id

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"mumble-{self._username}",
            daemon=True,
        )
        self._thread.start()
        await self._ready.wait()

    async def stop(self) -> None:
        if self._mumble is not None:
            self._mumble.exit = True
            await asyncio.to_thread(self._mumble.stop)

    def _run(self) -> None:
        try:
            pymumble, text_cb, connected_cb, disconnected_cb, deny_error = load_pymumble()
            self._deny_error = deny_error
        except Exception:
            logger.exception("Failed to load pymumble for user=%s", self._username)
            if self._loop:
                self._loop.call_soon_threadsafe(self._ready.set)
            return

        try:
            self._mumble = pymumble.Mumble(
                self._host,
                self._username,
                port=self._port,
                password=self._password,
                reconnect=self._reconnect,
                stereo=self._stereo,
            )
            self._mumble.set_callback(text_cb, self._handle_text)
            self._mumble.set_callback(connected_cb, self._handle_connected)
            self._mumble.set_callback(disconnected_cb, self._handle_disconnected)
            self._mumble.start()
            self._mumble.is_ready()
            self._bot_session_id = self._mumble.user_session
            if self._loop:
                self._loop.call_soon_threadsafe(self._ready.set)
            self._mumble.loop()
        except deny_error as exc:
            logger.error("Mumble connection denied user=%s: %s", self._username, exc)
            if self._loop:
                self._loop.call_soon_threadsafe(self._ready.set)
        except Exception:
            logger.exception("Mumble thread failed user=%s", self._username)
            if self._loop:
                self._loop.call_soon_threadsafe(self._ready.set)

    def _handle_connected(self, _mumble: Any) -> None:
        self._bot_session_id = _mumble.user_session
        logger.info("Mumble connected user=%s session=%s", self._username, self._bot_session_id)
        if self._post_connect_channel is not None:
            self._join_channel_sync(self._post_connect_channel)
        if self._on_connected_cb:
            self._on_connected_cb()

    def _handle_disconnected(self, _mumble: Any) -> None:
        logger.warning("Mumble disconnected user=%s (will reconnect=%s)", self._username, self._reconnect)
        if self._on_disconnected_cb:
            self._on_disconnected_cb()

    def _handle_text(self, _mumble: Any, mess: Any) -> None:
        if self._loop is None or self._mumble is None or self._on_text is None:
            return
        parsed = parse_text_message(self._mumble, mess)
        if parsed is None:
            return
        if parsed.sender_session == self._bot_session_id:
            return
        self._loop.call_soon_threadsafe(self._on_text, parsed)

    async def join_channel(self, channel_id: int) -> None:
        self._post_connect_channel = channel_id
        await asyncio.to_thread(self._join_channel_sync, channel_id)

    def _join_channel_sync(self, channel_id: int) -> None:
        if self._mumble is None:
            return
        try:
            channel = self._mumble.channels[channel_id]
            channel.move_in()
            logger.info("Joined channel user=%s channel_id=%s name=%s", self._username, channel_id, channel["name"])
        except Exception:
            logger.exception("Failed to join channel user=%s channel_id=%s", self._username, channel_id)

    async def move_to_root(self) -> None:
        await self.join_channel(0)

    async def send_channel_message(self, channel_id: int, message: str) -> None:
        await asyncio.to_thread(self._send_channel_message_sync, channel_id, message)

    def _send_channel_message_sync(self, channel_id: int, message: str) -> None:
        if self._mumble is None:
            return
        try:
            channel = self._mumble.channels[channel_id]
            channel.send_text_message(message)
        except Exception:
            logger.exception("Failed to send channel message user=%s channel_id=%s", self._username, channel_id)

    async def whisper_user(self, session_id: int, message: str) -> None:
        await asyncio.to_thread(self._whisper_user_sync, session_id, message)

    def _whisper_user_sync(self, session_id: int, message: str) -> None:
        if self._mumble is None:
            return
        try:
            user = self._mumble.users[session_id]
            user.send_text_message(message)
        except Exception:
            logger.exception("Failed to whisper user=%s to session=%s", self._username, session_id)

    async def send_pcm(self, data: bytes) -> None:
        await asyncio.to_thread(self._send_pcm_sync, data)

    def _send_pcm_sync(self, data: bytes) -> None:
        if self._mumble is None:
            return
        self._mumble.sound_output.add_sound(data)

    def get_buffer_size(self) -> float:
        if self._mumble is None:
            return 0.0
        return self._mumble.sound_output.get_buffer_size()

    def count_humans_in(self, channel_id: int) -> int:
        if self._mumble is None:
            return 0
        count = 0
        for user in self._mumble.users.values():
            if user["channel_id"] == channel_id and user["session"] != self._bot_session_id:
                count += 1
        return count

    def channel_name(self, channel_id: int) -> str:
        if self._mumble is None:
            return str(channel_id)
        try:
            return str(self._mumble.channels[channel_id]["name"])
        except (KeyError, TypeError):
            return str(channel_id)
