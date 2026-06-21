"""Handle parsed commands against channel sessions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from melody.logging import get_logger
from melody.models import ParsedCommand, QueueItem, RepeatMode, SearchMatch
from melody.protocols import IChannelSession
from melody.services.search import SearchService

logger = get_logger(__name__)

NotifyCallback = Callable[[str], Awaitable[None]]


class CommandHandler:
    """Executes bot commands for a channel session."""

    def __init__(self, search: SearchService) -> None:
        self._search = search

    async def handle(
        self,
        command: ParsedCommand,
        session: IChannelSession,
        *,
        notify: NotifyCallback | None = None,
    ) -> bool:
        """Handle command. Returns True if session should be destroyed."""
        name = command.name

        async def feedback(text: str) -> None:
            await session.send_message(text)
            if notify:
                await notify(text)

        if name in ("play", "queue") and not command.query:
            await feedback("Please provide a search query.")
            return False

        if name == "play":
            await session.ensure_joined()
            await self._handle_play(session, command, feedback)
            return False

        if name == "queue":
            await session.ensure_joined()
            await self._handle_queue(session, command, feedback)
            return False

        if name == "stop":
            await session.stop_playback(clear_all=True)
            await feedback("Stopped.")
            return False

        if name == "pause":
            session.pause()
            await feedback("Paused.")
            return False

        if name == "resume":
            await session.resume()
            await feedback("Resumed.")
            return False

        if name == "next":
            await session.skip_next()
            return False

        if name == "back":
            await session.skip_back()
            return False

        if name in ("quit", "exit"):
            return True

        return False

    async def _handle_play(
        self,
        session: IChannelSession,
        command: ParsedCommand,
        feedback: NotifyCallback,
    ) -> None:
        await session.stop_playback(clear_all=True)

        match = await self._search.resolve(command.query or "", command.options)
        if match is None:
            await feedback("No results found.")
            return

        items, playlist_id, source_tracks = self._match_to_queue_items(match)
        if not items:
            await feedback("No playable tracks found.")
            return

        if command.options.repeat:
            session.queue.set_repeat_mode(
                RepeatMode.ALL if match.kind == "playlist" else RepeatMode.TRACK
            )
        if command.options.shuffle:
            session.queue.set_shuffle(True)

        session.queue.play_now(
            items,
            source_playlist_id=playlist_id,
            source_tracks=source_tracks,
        )
        await session.start_playback()
        await feedback(f"Playing: {match.display_name}")

    async def _handle_queue(
        self,
        session: IChannelSession,
        command: ParsedCommand,
        feedback: NotifyCallback,
    ) -> None:
        match = await self._search.resolve(command.query or "", command.options)
        if match is None:
            await feedback("No results found.")
            return

        items, playlist_id, source_tracks = self._match_to_queue_items(match)
        if not items:
            await feedback("No playable tracks found.")
            return

        if command.options.repeat:
            session.queue.set_repeat_mode(
                RepeatMode.ALL if match.kind == "playlist" else RepeatMode.TRACK
            )
        if command.options.shuffle:
            session.queue.set_shuffle(True)

        was_idle = session.queue.is_idle
        session.queue.enqueue(
            items,
            source_playlist_id=playlist_id,
            source_tracks=source_tracks,
        )
        if was_idle:
            await session.start_playback()
        await feedback(f"Queued: {match.display_name}")

    def _match_to_queue_items(
        self,
        match: SearchMatch,
    ) -> tuple[list[QueueItem], str | None, list]:
        if match.track:
            if not match.track.id:
                return [], None, []
            return [QueueItem(track=match.track)], None, []

        if match.playlist and match.playlist.tracks:
            playlist_id = match.playlist.id
            tracks = [t for t in match.playlist.tracks if t.id]
            items = [QueueItem(track=t, source_playlist_id=playlist_id) for t in tracks]
            return items, playlist_id, tracks

        return [], None, []
