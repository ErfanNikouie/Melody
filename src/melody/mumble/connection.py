"""Reconnecting pymumble connection wrapper."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from melody.logging import get_logger
from melody.mumble.pymumble_util import (
    ParsedTextMessage,
    bind_callbacks,
    clear_callbacks,
    disable_incoming_audio,
    get_session_id,
    load_pymumble,
    parse_text_message,
)

logger = get_logger(__name__)

TextHandler = Callable[[ParsedTextMessage], None]
ConnectedHandler = Callable[[], None]
DisconnectedHandler = Callable[[], None]
UsersChangedHandler = Callable[[], None]

_MESSAGE_RETRIES = 3
_MESSAGE_RETRY_DELAY = 0.15
_CHANNEL_JOIN_RETRIES = 10
_CHANNEL_JOIN_DELAY = 0.03
_PCM_STOP = object()


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
        certfile: str | None = None,
        keyfile: str | None = None,
        client_type: int = 1,
        on_text: TextHandler | None = None,
        on_connected: ConnectedHandler | None = None,
        on_disconnected: DisconnectedHandler | None = None,
        on_users_changed: UsersChangedHandler | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._reconnect = reconnect
        self._stereo = stereo
        self._certfile = certfile
        self._keyfile = keyfile
        self._client_type = client_type
        self._on_text = on_text
        self._on_connected_cb = on_connected
        self._on_disconnected_cb = on_disconnected
        self._on_users_changed_cb = on_users_changed
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mumble: Any = None
        self._thread: threading.Thread | None = None
        self._ready = asyncio.Event()
        self._bot_session_id: int | None = None
        self._post_connect_channel: int | None = None
        self._encoder_ready = False
        self._accept_events = False
        self._sync_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"mumble-sync-{username[:24]}",
        )
        self._pcm_queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._pcm_writer: threading.Thread | None = None

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

    def set_text_handler(self, handler: TextHandler | None) -> None:
        """Set or clear the application-level text handler."""
        self._on_text = handler

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._ready.clear()
        self._accept_events = True
        self._thread = threading.Thread(
            target=self._run,
            name=f"mumble-{self._username}",
            daemon=True,
        )
        self._thread.start()
        await self._ready.wait()

    async def stop(self) -> None:
        self._accept_events = False
        self._on_text = None
        self._stop_pcm_writer()
        if self._mumble is not None:
            clear_callbacks(self._mumble)
            self._mumble.exit = True
            await asyncio.to_thread(self._mumble.stop)
        if self._thread is not None and self._thread.is_alive():
            await asyncio.to_thread(self._thread.join, 3.0)
            if self._thread.is_alive():
                logger.error("Mumble thread did not stop user=%s", self._username)
            else:
                self._thread = None
        else:
            self._thread = None
        if self._thread is None:
            self._mumble = None
        self._encoder_ready = False
        self._loop = None
        self._on_connected_cb = None
        self._on_disconnected_cb = None
        self._on_users_changed_cb = None
        self._sync_executor.shutdown(wait=False, cancel_futures=True)

    def _start_pcm_writer(self) -> None:
        if not self._stereo:
            return
        if self._pcm_writer is not None and self._pcm_writer.is_alive():
            return
        while True:
            try:
                self._pcm_queue.get_nowait()
            except queue.Empty:
                break
        self._pcm_writer = threading.Thread(
            target=self._pcm_writer_loop,
            name=f"pcm-{self._username}",
            daemon=True,
        )
        self._pcm_writer.start()

    def _pcm_writer_loop(self) -> None:
        while True:
            item = self._pcm_queue.get()
            if item is _PCM_STOP:
                break
            if isinstance(item, list):
                self._send_pcm_batch_sync(item)
            elif isinstance(item, (bytes, bytearray)):
                self._send_pcm_sync(bytes(item))

    def _stop_pcm_writer(self) -> None:
        if self._pcm_writer is not None:
            self._pcm_queue.put(_PCM_STOP)
            self._pcm_writer.join(timeout=2.0)
            if self._pcm_writer.is_alive():
                logger.warning("PCM writer did not stop user=%s", self._username)
            self._pcm_writer = None
        self._drain_pcm_queue()

    async def _run_sync(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run pymumble work on a dedicated single-thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._sync_executor, lambda: fn(*args))

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
                enable_audio=self._stereo,
                certfile=self._certfile,
                keyfile=self._keyfile,
                client_type=self._client_type,
            )
            if self._stereo:
                self._mumble.set_codec_profile("audio")
            bind_callbacks(
                self._mumble,
                on_text=self._handle_text,
                on_connected=self._handle_connected,
                on_disconnected=self._handle_disconnected,
                on_users_changed=self._on_users_changed_cb,
            )
            self._mumble.start()
            # Do not call wait_until_connected() here: pymumble releases its ready
            # lock on a failed connect attempt too, which would let us exit this
            # thread, kill reconnect, and surface ConnectionRejectedError.
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
        if not self._accept_events:
            return
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
            if not self._has_send_audio():
                logger.warning("Voice setup skipped user=%s (send_audio missing)", self._username)
                return
            if getattr(self._mumble, "server_max_bandwidth", None) is not None:
                self._mumble.set_bandwidth(128000)
            sound_out = self._mumble.send_audio
            sound_out.set_audio_per_packet(0.04)
            myself = self._mumble.users.myself
            if myself is None:
                logger.warning("Voice setup skipped user=%s (myself unknown)", self._username)
                return
            disable_incoming_audio(self._mumble)
            myself.self_mute = False
            myself.self_deaf = False
            myself.suppress = False
            try:
                myself.register()
            except Exception:
                pass
            encoder_ready = self._mumble.send_audio.encoder is not None
            if encoder_ready:
                self._encoder_ready = True
            self._start_pcm_writer()
            logger.info(
                "Voice ready user=%s encoder_ready=%s buffer=%.2fs",
                self._username,
                encoder_ready,
                self.get_buffer_size(),
            )
        except Exception:
            logger.exception("Failed to prepare voice for user=%s", self._username)

    def _has_send_audio(self) -> bool:
        if self._mumble is None:
            return False
        return getattr(self._mumble, "send_audio", None) is not None

    async def wait_for_audio_encoder(self, timeout: float = 10.0) -> bool:
        """Wait until pymumble has a working Opus encoder (CodecVersion received)."""
        if not self._stereo:
            return False
        if self._encoder_ready:
            return True
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self._run_sync(self._has_audio_encoder):
                await self._run_sync(self._ensure_voice_ready)
                self._encoder_ready = True
                return True
            await asyncio.sleep(0.05)
        return False

    def _has_audio_encoder(self) -> bool:
        if not self._has_send_audio():
            return False
        return self._mumble.send_audio.encoder is not None

    def _handle_disconnected(self) -> None:
        self._encoder_ready = False
        if not self._accept_events:
            return
        logger.warning("Mumble disconnected user=%s (will reconnect=%s)", self._username, self._reconnect)
        if self._on_disconnected_cb:
            self._on_disconnected_cb()

    def _handle_text(self, mess: Any) -> None:
        handler = self._on_text
        loop = self._loop
        if (
            not self._accept_events
            or loop is None
            or self._mumble is None
            or handler is None
        ):
            return
        parsed = parse_text_message(self._mumble, mess)
        if parsed is None:
            return
        if parsed.sender_session == self._bot_session_id:
            return
        if self._accept_events:
            loop.call_soon_threadsafe(handler, parsed)

    async def is_in_channel(self, channel_id: int) -> bool:
        return await self._run_sync(self._is_in_channel_sync, channel_id)

    def _is_in_channel_sync(self, channel_id: int) -> bool:
        return self.current_channel_id == channel_id

    async def join_channel(self, channel_id: int) -> bool:
        self._post_connect_channel = channel_id
        if await self.is_in_channel(channel_id):
            return True
        return await self._run_sync(self._join_channel_sync, channel_id)

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
            return int(myself.channel_id)
        except (KeyError, TypeError, AttributeError):
            return None

    async def move_to_root(self) -> None:
        await self.join_channel(0)

    async def send_channel_message(self, channel_id: int, message: str) -> bool:
        return await self._run_sync(self._send_channel_message_sync, channel_id, message)

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
        return await self._run_sync(self._whisper_user_sync, session_id, message)

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
        if not self._accept_events or not self._stereo:
            return
        self._pcm_queue.put(data)

    async def send_pcm_batch(self, chunks: list[bytes]) -> None:
        if not chunks or not self._accept_events or not self._stereo:
            return
        self._pcm_queue.put(chunks)

    def _send_pcm_batch_sync(self, chunks: list[bytes]) -> None:
        if self._mumble is None or not self._has_send_audio():
            return
        if self._mumble.send_audio.encoder is None:
            logger.warning("Dropped PCM batch user=%s (Opus encoder not ready)", self._username)
            return
        for data in chunks:
            self._mumble.send_audio.add_sound(data)

    def _send_pcm_sync(self, data: bytes) -> None:
        if self._mumble is None or not self._has_send_audio():
            return
        if self._mumble.send_audio.encoder is None:
            logger.warning("Dropped PCM user=%s (Opus encoder not ready)", self._username)
            return
        self._mumble.send_audio.add_sound(data)

    def get_buffer_size(self) -> float:
        if self._mumble is None or not self._has_send_audio():
            return 0.0
        return self._mumble.send_audio.get_buffer_size()

    async def clear_send_audio(self) -> None:
        self._drain_pcm_queue()
        self._clear_send_audio_sync()

    def _drain_pcm_queue(self) -> None:
        while True:
            try:
                self._pcm_queue.get_nowait()
            except queue.Empty:
                break

    def _clear_send_audio_sync(self) -> None:
        if self._mumble is None or not self._has_send_audio():
            return
        self._mumble.send_audio.clear_buffer()

    def count_humans_in(self, channel_id: int) -> int:
        if self._mumble is None:
            return 0
        count = 0
        for user in self._mumble.users.by_session().values():
            if user.channel_id == channel_id and user.session != self._bot_session_id:
                count += 1
        return count

    async def count_humans_in_channel(self, channel_id: int) -> int:
        return await self._run_sync(self.count_humans_in, channel_id)

    def channel_name(self, channel_id: int) -> str:
        if self._mumble is None:
            return str(channel_id)
        try:
            return str(self._mumble.channels[channel_id]["name"])
        except (KeyError, TypeError):
            return str(channel_id)
