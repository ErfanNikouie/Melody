"""Mumble client with asyncio bridge."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pymumble_py3 as pymumble
from pymumble_py3.constants import PYMUMBLE_CLBK_TEXTMESSAGERECEIVED
from pymumble_py3.errors import DenyError

from melody.commands.handler import CommandHandler
from melody.commands.parser import CommandParser
from melody.config import Settings
from melody.logging import get_logger
from melody.mumble.channel_session import ChannelSession
from melody.playback.buffer import GlobalBufferPool
from melody.subsonic.interface import ISubsonicClient

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TextMessageEvent:
    message: str
    channel_id: int
    channel_name: str
    sender_name: str


class MumbleClient:
    """Thread-backed pymumble client bridged to asyncio."""

    def __init__(
        self,
        settings: Settings,
        subsonic: ISubsonicClient,
        parser: CommandParser,
        handler: CommandHandler,
        buffer_pool: GlobalBufferPool,
    ) -> None:
        self._settings = settings
        self._subsonic = subsonic
        self._parser = parser
        self._handler = handler
        self._buffer_pool = buffer_pool
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mumble: pymumble.Mumble | None = None
        self._thread: threading.Thread | None = None
        self._connected = asyncio.Event()
        self._text_queue: asyncio.Queue[TextMessageEvent] = asyncio.Queue()
        self._sessions: dict[int, ChannelSession] = {}
        self._session_lock = asyncio.Lock()
        self._bot_session_id: int | None = None
        self._on_all_sessions_closed: Callable[[], Awaitable[None]] | None = None

    def set_on_all_sessions_closed(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._on_all_sessions_closed = callback

    async def connect(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._run_mumble, daemon=True)
        self._thread.start()
        await self._connected.wait()

    def _run_mumble(self) -> None:
        host = self._settings.mumble_host
        port = self._settings.mumble_port
        user = self._settings.mumble_username
        password = self._settings.mumble_password

        if self._settings.mumble_tls:
            logger.warning(
                "MUMBLE_TLS is enabled but pymumble uses plain TCP; "
                "ensure MUMBLE_PORT points to a compatible endpoint"
            )

        try:
            self._mumble = pymumble.Mumble(host, user, port=port, password=password, stereo=True)
            self._mumble.set_callback(PYMUMBLE_CLBK_TEXTMESSAGERECEIVED, self._on_text_message)
            self._mumble.start()
            self._mumble.is_ready()
            self._bot_session_id = self._mumble.user_session
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)
            self._mumble.loop()
        except DenyError as exc:
            logger.error("Mumble connection denied: %s", exc)
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)
        except Exception:
            logger.exception("Mumble thread failed")
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)

    def _on_text_message(
        self,
        _mumble: pymumble.Mumble,
        sender: int,
        message: str,
        channel: int,
    ) -> None:
        if self._loop is None or self._mumble is None:
            return

        if self._bot_session_id is not None and sender == self._bot_session_id:
            return

        sender_name = str(sender)
        try:
            user = self._mumble.users[sender]
            sender_name = user["name"]
        except (KeyError, TypeError):
            pass

        try:
            channel_obj = self._mumble.channels[channel]
            channel_name = channel_obj["name"]
        except (KeyError, TypeError):
            channel_name = str(channel)

        event = TextMessageEvent(
            message=message,
            channel_id=channel,
            channel_name=channel_name,
            sender_name=sender_name,
        )
        self._loop.call_soon_threadsafe(self._text_queue.put_nowait, event)

    async def run_message_loop(self) -> None:
        while True:
            event = await self._text_queue.get()
            command = self._parser.parse(event.message)
            if command is None:
                continue

            session = await self._get_or_create_session(event.channel_id, event.channel_name)
            destroy = await self._handler.handle(command, session)
            if destroy:
                await self._destroy_session(event.channel_id)
            else:
                self._refresh_human_counts()

    async def _get_or_create_session(self, channel_id: int, channel_name: str) -> ChannelSession:
        async with self._session_lock:
            if channel_id in self._sessions:
                return self._sessions[channel_id]

            session = ChannelSession(
                channel_id,
                channel_name,
                self._subsonic,
                self._buffer_pool,
                start_seconds=self._settings.audio_buffer_start_seconds,
                grace_period=self._settings.disconnect_grace_period,
                send_pcm=self._send_pcm,
                get_buffer_size=self._get_buffer_size,
                join_channel=lambda cid=channel_id: self._join_channel(cid),
                leave_channel=lambda cid=channel_id: self._leave_channel(cid),
                send_message=lambda msg, cid=channel_id: self._send_channel_message(cid, msg),
                on_shutdown=lambda cid=channel_id: self._destroy_session(cid),
            )
            self._sessions[channel_id] = session
            self._refresh_human_counts()
            return session

    async def _destroy_session(self, channel_id: int) -> None:
        async with self._session_lock:
            session = self._sessions.pop(channel_id, None)
        if session:
            await session.shutdown()
        if not self._sessions and self._on_all_sessions_closed:
            await self._on_all_sessions_closed()

    def _refresh_human_counts(self) -> None:
        if self._mumble is None:
            return
        for channel_id, session in self._sessions.items():
            count = self._count_humans(channel_id)
            session.update_human_count(count)

    def _count_humans(self, channel_id: int) -> int:
        if self._mumble is None:
            return 0
        count = 0
        for user in self._mumble.users.values():
            if user["channel_id"] == channel_id and user["session"] != self._bot_session_id:
                count += 1
        return count

    async def _join_channel(self, channel_id: int) -> None:
        await asyncio.to_thread(self._join_channel_sync, channel_id)

    def _join_channel_sync(self, channel_id: int) -> None:
        if self._mumble is None:
            return
        try:
            channel = self._mumble.channels[channel_id]
            channel.move_in()
            logger.info("Joined channel_id=%s name=%s", channel_id, channel["name"])
        except Exception:
            logger.exception("Failed to join channel_id=%s", channel_id)

    async def _leave_channel(self, channel_id: int) -> None:
        await asyncio.to_thread(self._leave_channel_sync)

    def _leave_channel_sync(self) -> None:
        if self._mumble is None:
            return
        try:
            root = self._mumble.channels[0]
            root.move_in()
        except Exception:
            logger.exception("Failed to leave channel")

    async def _send_pcm(self, data: bytes) -> None:
        await asyncio.to_thread(self._send_pcm_sync, data)

    def _send_pcm_sync(self, data: bytes) -> None:
        if self._mumble is None:
            return
        self._mumble.sound_output.add_sound(data)

    def _get_buffer_size(self) -> float:
        if self._mumble is None:
            return 0.0
        return self._mumble.sound_output.get_buffer_size()

    async def _send_channel_message(self, channel_id: int, message: str) -> None:
        await asyncio.to_thread(self._send_channel_message_sync, channel_id, message)

    def _send_channel_message_sync(self, channel_id: int, message: str) -> None:
        if self._mumble is None:
            return
        try:
            channel = self._mumble.channels[channel_id]
            channel.send_text_message(message)
        except Exception:
            logger.exception("Failed to send message channel_id=%s", channel_id)

    async def disconnect(self) -> None:
        async with self._session_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.shutdown()

        if self._mumble is not None:
            await asyncio.to_thread(self._mumble.stop)

    @property
    def is_connected(self) -> bool:
        return self._mumble is not None and self._mumble.is_alive()
