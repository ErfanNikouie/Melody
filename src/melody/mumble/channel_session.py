"""Per-channel bot session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from melody.logging import get_logger
from melody.playback.buffer import GlobalBufferPool
from melody.playback.engine import PlaybackEngine
from melody.playback.queue import QueueManager
from melody.protocols import ISubsonicClient

logger = get_logger(__name__)

JoinChannelCallback = Callable[[], Awaitable[None]]
LeaveChannelCallback = Callable[[], Awaitable[None]]
SendMessageCallback = Callable[[str], Awaitable[None]]
SendPcmCallback = Callable[[bytes], Awaitable[None]]
GetBufferSizeCallback = Callable[[], float]
OnShutdownCallback = Callable[[], Awaitable[None]]


class ChannelSession:
    """Independent queue and playback state for one Mumble channel."""

    def __init__(
        self,
        channel_id: int,
        channel_name: str,
        subsonic: ISubsonicClient,
        buffer_pool: GlobalBufferPool,
        *,
        start_seconds: float,
        grace_period: float,
        send_pcm: SendPcmCallback,
        get_buffer_size: GetBufferSizeCallback,
        join_channel: JoinChannelCallback,
        leave_channel: LeaveChannelCallback,
        send_message: SendMessageCallback,
        on_shutdown: OnShutdownCallback,
    ) -> None:
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.queue = QueueManager()
        self._grace_period = grace_period
        self._join_channel = join_channel
        self._leave_channel = leave_channel
        self._send_message = send_message
        self._request_destroy = on_shutdown
        self._human_count = 0
        self._grace_task: asyncio.Task[None] | None = None
        self._joined = False

        self.engine = PlaybackEngine(
            subsonic,
            self.queue,
            buffer_pool,
            start_seconds=start_seconds,
            send_pcm=send_pcm,
            get_buffer_size=get_buffer_size,
        )

    async def send_message(self, message: str) -> None:
        await self._send_message(message)

    async def ensure_joined(self) -> None:
        if self._joined:
            return
        await self._join_channel()
        self._joined = True
        self._cancel_grace_timer()

    def mark_joined(self) -> None:
        """Player already moved into the channel on connect."""
        self._joined = True
        self._cancel_grace_timer()

    async def start_playback(self) -> None:
        await self.ensure_joined()
        await self.engine.play_current()

    async def stop_playback(self, *, clear_all: bool = False) -> None:
        self.engine.stop()
        if clear_all:
            self.queue.clear_all()
        else:
            self.queue.clear()

    def pause(self) -> None:
        self.engine.pause()

    async def resume(self) -> None:
        self.engine.resume()
        if self.queue.current and self.engine.state.value in ("idle", "paused"):
            await self.engine.play_current()

    async def skip_next(self) -> None:
        self.engine.stop()
        nxt = self.queue.advance()
        if nxt:
            await self.send_message(f"Next: {nxt.track.display_name}")
            await self.engine.play_current()
        else:
            await self.send_message("Queue empty.")

    async def skip_back(self) -> None:
        self.engine.stop()
        prev = self.queue.go_back()
        if prev:
            await self.send_message(f"Back: {prev.track.display_name}")
            await self.engine.play_current()
        else:
            await self.send_message("No previous track.")

    def update_human_count(self, count: int) -> None:
        self._human_count = count
        if count > 0:
            self._cancel_grace_timer()
        elif self._joined:
            self._start_grace_timer()

    def _start_grace_timer(self) -> None:
        self._cancel_grace_timer()
        self._grace_task = asyncio.create_task(self._grace_disconnect())

    def _cancel_grace_timer(self) -> None:
        if self._grace_task and not self._grace_task.done():
            self._grace_task.cancel()
        self._grace_task = None

    async def _grace_disconnect(self) -> None:
        try:
            await asyncio.sleep(self._grace_period)
            logger.info(
                "Grace period expired channel_id=%s channel_name=%s",
                self.channel_id,
                self.channel_name,
            )
            await self._request_destroy()
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        self._cancel_grace_timer()
        await self.stop_playback(clear_all=True)
        if self._joined:
            await self._leave_channel()
            self._joined = False
