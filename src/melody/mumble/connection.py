"""Reconnecting pymumble connection wrapper."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any

from melody.logging import get_logger
from melody.mumble.pymumble_util import (
    ParsedTextMessage,
    bind_callbacks,
    get_session_id,
    load_pymumble,
    parse_text_message,
)

logger = get_logger(__name__)

TextHandler = Callable[[ParsedTextMessage], None]
ConnectedHandler = Callable[[], None]
DisconnectedHandler = Callable[[], None]

_MESSAGE_RETRIES = 3
_MESSAGE_RETRY_DELAY = 0.15
_CHANNEL_JOIN_RETRIES = 10
_CHANNEL_JOIN_DELAY = 0.03


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
        self._post_connect_channel: int | None = None
        self._encoder_ready = False

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
            pymumble, connection_rejected_error = load_pymumble()
        except Exception:
            logger.exception("Failed to load pymumble for user=%s", self._username)
            if self._loop:
                self._loop.call_soon_threadsafe(self._ready.set)
            return

        try:
            logger.info(
                "Connecting to Mumble user=%s host=%s port=%s reconnect=%s",
                self._username,
                self._host,
                self._port,
                self._reconnect,
            )
            self._mumble = pymumble.Mumble(
                self._host,
                self._username,
                port=self._port,
                password=self._password,
                reconnect=self._reconnect,
                stereo=self._stereo,
            )
            if self._stereo:
                # pymumble only creates sound_output when receive_sound is enabled.
                self._mumble.set_receive_sound(1)
                self._mumble.set_codec_profile("audio")
            bind_callbacks(
                self._mumble,
                on_text=self._handle_text,
                on_connected=self._handle_connected,
                on_disconnected=self._handle_disconnected,
            )
            self._mumble.start()
            # Do not call is_ready() here: pymumble releases its ready lock on a
            # failed connect attempt too, which would let us exit this thread,
            # kill reconnect, and surface ConnectionRejectedError in the child.
            self._mumble.join()
            if not self._ready.is_set():
                logger.error(
                    "Mumble connection ended without connecting user=%s host=%s port=%s",
                    self._username,
                    self._host,
                    self._port,
                )
                if self._loop:
                    self._loop.call_soon_threadsafe(self._ready.set)
        except connection_rejected_error as exc:
            logger.error(
                "Mumble connection denied user=%s host=%s port=%s: %s",
                self._username,
                self._host,
                self._port,
                exc,
            )
            if self._loop and not self._ready.is_set():
                self._loop.call_soon_threadsafe(self._ready.set)
        except Exception:
            logger.exception(
                "Mumble thread failed user=%s host=%s port=%s",
                self._username,
                self._host,
                self._port,
            )
            if self._loop and not self._ready.is_set():
                self._loop.call_soon_threadsafe(self._ready.set)

    def _handle_connected(self) -> None:
        if self._mumble is not None:
            self._bot_session_id = get_session_id(self._mumble)
        logger.info("Mumble connected user=%s session=%s", self._username, self._bot_session_id)
        if self._post_connect_channel is not None:
            self._join_channel_sync(self._post_connect_channel)
        self._ensure_voice_ready()
        if self._on_connected_cb:
            self._on_connected_cb()
        if self._loop and not self._ready.is_set():
            self._loop.call_soon_threadsafe(self._ready.set)

    def _ensure_voice_ready(self) -> None:
        """Player bots must be able to transmit audio in Mumble."""
        if not self._stereo or self._mumble is None:
            return
        try:
            if not self._has_sound_output():
                logger.warning("Voice setup skipped user=%s (sound_output missing)", self._username)
                return
            if getattr(self._mumble, "server_max_bandwidth", None) is not None:
                self._mumble.set_bandwidth(128000)
            sound_out = self._mumble.sound_output
            sound_out.set_audio_per_packet(0.04)
            myself = self._mumble.users.myself
            if myself is None:
                logger.warning("Voice setup skipped user=%s (myself unknown)", self._username)
                return
            myself.unmute()
            myself.undeafen()
            myself.unsuppress()
            try:
                myself.register()
            except Exception:
                pass
            encoder_ready = self._mumble.sound_output.encoder is not None
            if encoder_ready:
                self._encoder_ready = True
            logger.info(
                "Voice ready user=%s encoder_ready=%s buffer=%.2fs",
                self._username,
                encoder_ready,
                self.get_buffer_size(),
            )
        except Exception:
            logger.exception("Failed to prepare voice for user=%s", self._username)

    def _has_sound_output(self) -> bool:
        if self._mumble is None:
            return False
        return getattr(self._mumble, "sound_output", None) is not None

    async def wait_for_audio_encoder(self, timeout: float = 10.0) -> bool:
        """Wait until pymumble has a working Opus encoder (CodecVersion received)."""
        if not self._stereo:
            return False
        if self._encoder_ready:
            return True
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if await asyncio.to_thread(self._has_audio_encoder):
                await asyncio.to_thread(self._ensure_voice_ready)
                self._encoder_ready = True
                return True
            await asyncio.sleep(0.05)
        return False

    def _has_audio_encoder(self) -> bool:
        if not self._has_sound_output():
            return False
        return self._mumble.sound_output.encoder is not None

    def _handle_disconnected(self) -> None:
        self._encoder_ready = False
        logger.warning("Mumble disconnected user=%s (will reconnect=%s)", self._username, self._reconnect)
        if self._on_disconnected_cb:
            self._on_disconnected_cb()

    def _handle_text(self, mess: Any) -> None:
        if self._loop is None or self._mumble is None or self._on_text is None:
            return
        parsed = parse_text_message(self._mumble, mess)
        if parsed is None:
            return
        if parsed.sender_session == self._bot_session_id:
            return
        self._loop.call_soon_threadsafe(self._on_text, parsed)

    async def is_in_channel(self, channel_id: int) -> bool:
        return await asyncio.to_thread(self._is_in_channel_sync, channel_id)

    def _is_in_channel_sync(self, channel_id: int) -> bool:
        return self.current_channel_id == channel_id

    async def join_channel(self, channel_id: int) -> bool:
        self._post_connect_channel = channel_id
        if await self.is_in_channel(channel_id):
            return True
        return await asyncio.to_thread(self._join_channel_sync, channel_id)

    def _join_channel_sync(self, channel_id: int) -> bool:
        if self._mumble is None:
            logger.warning(
                "Cannot join channel user=%s channel_id=%s (not connected)",
                self._username,
                channel_id,
            )
            return False

        if self._is_in_channel_sync(channel_id):
            return True

        last_exc: Exception | None = None
        for attempt in range(1, _CHANNEL_JOIN_RETRIES + 1):
            try:
                channel = self._mumble.channels[channel_id]
                channel.move_in()
                current = self.current_channel_id
                if current != channel_id:
                    logger.warning(
                        "Join channel mismatch user=%s expected=%s actual=%s",
                        self._username,
                        channel_id,
                        current,
                    )
                logger.info(
                    "Joined channel user=%s channel_id=%s name=%s",
                    self._username,
                    channel_id,
                    channel["name"],
                )
                return True
            except KeyError as exc:
                last_exc = exc
                if attempt < _CHANNEL_JOIN_RETRIES:
                    time.sleep(_CHANNEL_JOIN_DELAY)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Join channel attempt %s/%s failed user=%s channel_id=%s: %s",
                    attempt,
                    _CHANNEL_JOIN_RETRIES,
                    self._username,
                    channel_id,
                    exc,
                )
                if attempt < _CHANNEL_JOIN_RETRIES:
                    time.sleep(_CHANNEL_JOIN_DELAY)

        logger.error(
            "Failed to join channel user=%s channel_id=%s after %s attempts: %s",
            self._username,
            channel_id,
            _CHANNEL_JOIN_RETRIES,
            last_exc,
        )
        return False

    @property
    def current_channel_id(self) -> int | None:
        if self._mumble is None:
            return None
        try:
            myself = self._mumble.users.myself
            if myself is None:
                return None
            return int(myself["channel_id"])
        except (KeyError, TypeError, AttributeError):
            return None

    async def move_to_root(self) -> None:
        await self.join_channel(0)

    async def send_channel_message(self, channel_id: int, message: str) -> bool:
        return await asyncio.to_thread(self._send_channel_message_sync, channel_id, message)

    def _send_channel_message_sync(self, channel_id: int, message: str) -> bool:
        if self._mumble is None or not self.is_connected:
            logger.warning(
                "Cannot send channel message user=%s channel_id=%s (not connected)",
                self._username,
                channel_id,
            )
            return False
        last_exc: Exception | None = None
        for attempt in range(1, _MESSAGE_RETRIES + 1):
            try:
                channel = self._mumble.channels[channel_id]
                channel.send_text_message(message)
                logger.debug(
                    "Channel message sent user=%s channel_id=%s len=%s",
                    self._username,
                    channel_id,
                    len(message),
                )
                return True
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Channel message attempt %s/%s failed user=%s channel_id=%s: %s",
                    attempt,
                    _MESSAGE_RETRIES,
                    self._username,
                    channel_id,
                    exc,
                )
                if attempt < _MESSAGE_RETRIES:
                    time.sleep(_MESSAGE_RETRY_DELAY)
        logger.error(
            "Failed to send channel message user=%s channel_id=%s after %s attempts: %s",
            self._username,
            channel_id,
            _MESSAGE_RETRIES,
            last_exc,
        )
        return False

    async def whisper_user(self, session_id: int, message: str) -> bool:
        return await asyncio.to_thread(self._whisper_user_sync, session_id, message)

    def _whisper_user_sync(self, session_id: int, message: str) -> bool:
        if self._mumble is None or not self.is_connected:
            logger.warning(
                "Cannot whisper user=%s to session=%s (not connected)",
                self._username,
                session_id,
            )
            return False
        last_exc: Exception | None = None
        for attempt in range(1, _MESSAGE_RETRIES + 1):
            try:
                user = self._mumble.users[session_id]
                user.send_text_message(message)
                logger.debug(
                    "Whisper sent user=%s to session=%s len=%s",
                    self._username,
                    session_id,
                    len(message),
                )
                return True
            except KeyError:
                logger.warning(
                    "Cannot whisper user=%s to session=%s (user not in channel)",
                    self._username,
                    session_id,
                )
                return False
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Whisper attempt %s/%s failed user=%s to session=%s: %s",
                    attempt,
                    _MESSAGE_RETRIES,
                    self._username,
                    session_id,
                    exc,
                )
                if attempt < _MESSAGE_RETRIES:
                    time.sleep(_MESSAGE_RETRY_DELAY)
        logger.error(
            "Failed to whisper user=%s to session=%s after %s attempts: %s",
            self._username,
            session_id,
            _MESSAGE_RETRIES,
            last_exc,
        )
        return False

    async def send_pcm(self, data: bytes) -> None:
        await asyncio.to_thread(self._send_pcm_sync, data)

    async def send_pcm_batch(self, chunks: list[bytes]) -> None:
        if not chunks:
            return
        await asyncio.to_thread(self._send_pcm_batch_sync, chunks)

    def _send_pcm_batch_sync(self, chunks: list[bytes]) -> None:
        if self._mumble is None or not self._has_sound_output():
            return
        if self._mumble.sound_output.encoder is None:
            logger.warning("Dropped PCM batch user=%s (Opus encoder not ready)", self._username)
            return
        for data in chunks:
            self._mumble.sound_output.add_sound(data)

    def _send_pcm_sync(self, data: bytes) -> None:
        if self._mumble is None or not self._has_sound_output():
            return
        if self._mumble.sound_output.encoder is None:
            logger.warning("Dropped PCM user=%s (Opus encoder not ready)", self._username)
            return
        self._mumble.sound_output.add_sound(data)

    def get_buffer_size(self) -> float:
        if self._mumble is None or not self._has_sound_output():
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
