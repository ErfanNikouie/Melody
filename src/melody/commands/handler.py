"""Handle parsed commands against channel sessions."""

from __future__ import annotations

from melody.logging import get_logger
from melody.models import ParsedCommand, QueueItem, RepeatMode, SearchMatch
from melody.subsonic.interface import ISubsonicClient
from melody.subsonic.search import resolve_search

logger = get_logger(__name__)


class CommandHandler:
    """Executes bot commands for a channel session."""

    def __init__(self, subsonic: ISubsonicClient) -> None:
        self._subsonic = subsonic

    async def handle(
        self,
        command: ParsedCommand,
        session: object,
    ) -> bool:
        """Handle command. Returns True if session should be destroyed."""
        from melody.mumble.channel_session import ChannelSession

        assert isinstance(session, ChannelSession)
        name = command.name

        if name in ("play", "queue") and not command.query:
            await session.send_message("Please provide a search query.")
            return False

        if name == "play":
            await session.ensure_joined()
            await self._handle_play(session, command)
            return False

        if name == "queue":
            await session.ensure_joined()
            await self._handle_queue(session, command)
            return False

        if name == "stop":
            await session.stop_playback(clear_all=True)
            await session.send_message("Stopped.")
            return False

        if name == "pause":
            session.pause()
            await session.send_message("Paused.")
            return False

        if name == "resume":
            await session.resume()
            await session.send_message("Resumed.")
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

    async def _handle_play(self, session: object, command: ParsedCommand) -> None:
        from melody.mumble.channel_session import ChannelSession

        assert isinstance(session, ChannelSession)
        await session.stop_playback(clear_all=True)

        match = await resolve_search(self._subsonic, command.query or "", command.options)
        if match is None:
            await session.send_message("No results found.")
            return

        items, playlist_id, source_tracks = self._match_to_queue_items(match)
        if not items:
            await session.send_message("No playable tracks found.")
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
        await session.send_message(f"Playing: {match.display_name}")

    async def _handle_queue(self, session: object, command: ParsedCommand) -> None:
        from melody.mumble.channel_session import ChannelSession

        assert isinstance(session, ChannelSession)
        match = await resolve_search(self._subsonic, command.query or "", command.options)
        if match is None:
            await session.send_message("No results found.")
            return

        items, playlist_id, source_tracks = self._match_to_queue_items(match)
        if not items:
            await session.send_message("No playable tracks found.")
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
        await session.send_message(f"Queued: {match.display_name}")

    def _match_to_queue_items(
        self,
        match: SearchMatch,
    ) -> tuple[list[QueueItem], str | None, list]:
        if match.track:
            return [QueueItem(track=match.track)], None, []

        if match.playlist and match.playlist.tracks:
            playlist_id = match.playlist.id
            tracks = list(match.playlist.tracks)
            items = [QueueItem(track=t, source_playlist_id=playlist_id) for t in tracks]
            return items, playlist_id, tracks

        return [], None, []
