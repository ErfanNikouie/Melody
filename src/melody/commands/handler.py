"""Handle parsed commands against channel sessions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from melody.commands.messages import (
    format_help,
    format_leaving_channel,
    format_need_query,
    format_no_playable,
    format_no_results,
    format_paused,
    format_playback_status,
    format_playing,
    format_queue_list,
    format_queued,
    format_resumed,
    format_search_failed,
    format_search_results,
    format_searching,
    format_stopped,
    format_volume,
    format_volume_usage,
)
from melody.logging import get_logger
from melody.models import ParsedCommand, QueueItem, RepeatMode, SearchMatch
from melody.playback.volume import parse_volume_command, resolve_volume_percent
from melody.protocols import IChannelSession
from melody.services.search import SearchService
from melody.subsonic.errors import AlbumNotFoundError, PlaylistNotFoundError, SubsonicError

logger = get_logger(__name__)

NotifyCallback = Callable[[str], Awaitable[None]]
SearchTask = asyncio.Task[SearchMatch | None]


class CommandHandler:
    """Executes bot commands for a channel session."""

    def __init__(
        self,
        search: SearchService,
        *,
        command_prefix: str = "m/",
        list_window_size: int = 50,
    ) -> None:
        self._search = search
        self._command_prefix = command_prefix
        self._list_window_size = list_window_size

    def start_search_tasks(self, commands: list[ParsedCommand]) -> dict[int, SearchTask]:
        """Start Subsonic resolve tasks in parallel with player acquisition."""
        tasks: dict[int, SearchTask] = {}
        for index, command in enumerate(commands):
            if command.name == "play" and command.query:
                tasks[index] = asyncio.create_task(
                    self._search.resolve(command.query, command.options),
                    name=f"search-{command.name}-{index}",
                )
        return tasks

    async def handle(
        self,
        command: ParsedCommand,
        session: IChannelSession,
        *,
        notify: NotifyCallback | None = None,
        search_task: SearchTask | None = None,
    ) -> bool:
        """Handle command. Returns True if session should be destroyed."""
        name = command.name

        async def feedback(text: str) -> None:
            if notify:
                await notify(text)
            else:
                await session.send_message(text)

        if name in ("play", "search") and not command.query:
            await feedback(format_need_query())
            return False

        if name == "play":
            await self._handle_play(session, command, feedback, notify=notify, search_task=search_task)
            return False

        if name == "search":
            await self._handle_search(session, command, feedback)
            return False

        if name == "stop":
            session.begin_stop(clear_all=True)
            await feedback(format_stopped())
            session.schedule_stop_drain()
            return False

        if name == "pause":
            session.pause()
            await feedback(format_paused())
            return False

        if name == "resume":
            await session.resume()
            await feedback(format_resumed())
            return False

        if name == "next":
            await session.skip_next()
            return False

        if name == "back":
            await session.skip_back()
            return False

        if name == "list":
            await self._handle_list(session, feedback)
            return False

        if name == "current":
            await feedback(format_playback_status(session.playback_status))
            return False

        if name == "volume":
            await self._handle_volume(session, command, feedback)
            return False

        if name == "help":
            await feedback(format_help(self._command_prefix))
            return False

        if name in ("quit", "exit"):
            await feedback(format_leaving_channel())
            return True

        return False

    async def _handle_volume(
        self,
        session: IChannelSession,
        command: ParsedCommand,
        feedback: NotifyCallback,
    ) -> None:
        volume_cmd = parse_volume_command(command.query)
        if volume_cmd is None:
            await feedback(format_volume_usage())
            return

        current = session.volume_percent
        if volume_cmd.action == "show":
            await feedback(format_volume(current))
            return

        new_level = resolve_volume_percent(current, volume_cmd)
        session.set_volume_percent(new_level)
        await feedback(format_volume(new_level))

    async def _handle_list(
        self,
        session: IChannelSession,
        feedback: NotifyCallback,
    ) -> None:
        queue = session.queue
        await feedback(
            format_queue_list(
                history=queue.history,
                current=queue.current,
                upcoming=queue.upcoming,
                status=session.playback_status,
                window_size=self._list_window_size,
            )
        )

    async def _handle_play(
        self,
        session: IChannelSession,
        command: ParsedCommand,
        feedback: NotifyCallback,
        *,
        notify: NotifyCallback | None = None,
        search_task: SearchTask | None = None,
    ) -> None:
        started = time.monotonic()
        await feedback(format_searching())

        match = await self._resolve_match(command, feedback, search_task=search_task)
        if match is None:
            return

        items, collection = self._match_to_queue_items(match)
        if not items:
            await feedback(format_no_playable())
            return

        self._apply_queue_options(session, command, match)

        was_idle = session.queue.is_idle
        session.queue.enqueue(items, **collection)
        if was_idle:
            await self._announce_playback(match, session, feedback, notify=notify)
            session.schedule_playback(announce=False)
        else:
            await feedback(format_queued(match.display_name, match.track_count))

        logger.debug(
            "Play command finished channel_id=%s total_ms=%.0f",
            session.channel_id,
            (time.monotonic() - started) * 1000,
        )

    async def _handle_search(
        self,
        session: IChannelSession,
        command: ParsedCommand,
        feedback: NotifyCallback,
    ) -> None:
        await feedback(format_searching())
        try:
            results = await self._search.search_top(command.query or "", command.options)
        except SubsonicError as exc:
            logger.error("Search failed query=%r: %s", command.query, exc)
            await feedback(format_search_failed())
            return

        if not results:
            await feedback(format_no_results())
            return

        await feedback(format_search_results(results))

    async def _resolve_match(
        self,
        command: ParsedCommand,
        feedback: NotifyCallback,
        *,
        search_task: SearchTask | None = None,
    ) -> SearchMatch | None:
        try:
            if search_task is not None:
                match = await search_task
            else:
                match = await self._search.resolve(command.query or "", command.options)
        except AlbumNotFoundError as exc:
            logger.warning("Album resolve failed query=%r: %s", command.query, exc)
            await feedback(format_search_failed())
            return None
        except PlaylistNotFoundError as exc:
            logger.warning("Playlist resolve failed query=%r: %s", command.query, exc)
            await feedback(format_search_failed())
            return None
        except SubsonicError as exc:
            logger.error("Search failed query=%r: %s", command.query, exc)
            await feedback(format_search_failed())
            return None

        if match is None:
            await feedback(format_no_results())
        return match

    async def _announce_playback(
        self,
        match: SearchMatch,
        session: IChannelSession,
        feedback: NotifyCallback,
        *,
        notify: NotifyCallback | None,
    ) -> None:
        """Post a playing announcement; album/playlist also go to channel chat when whispered."""
        msg = format_playing(match.display_name, match.track_count)
        if match.kind in ("album", "playlist"):
            await session.send_message(msg)
            if notify:
                await notify(msg)
        else:
            await feedback(msg)

    def _apply_queue_options(
        self,
        session: IChannelSession,
        command: ParsedCommand,
        match: SearchMatch,
    ) -> None:
        if command.options.repeat:
            session.queue.set_repeat_mode(
                RepeatMode.ALL if match.kind in ("playlist", "album") else RepeatMode.TRACK
            )
        if command.options.shuffle:
            session.queue.set_shuffle(True)

    def _match_to_queue_items(
        self,
        match: SearchMatch,
    ) -> tuple[list[QueueItem], dict[str, object]]:
        if match.track:
            if not match.track.id:
                return [], {}
            return [QueueItem(track=match.track)], {}

        if match.playlist and match.playlist.tracks:
            playlist_id = match.playlist.id
            tracks = [t for t in match.playlist.tracks if t.id]
            items = [QueueItem(track=t, source_playlist_id=playlist_id) for t in tracks]
            return items, {"source_playlist_id": playlist_id}

        if match.album and match.album.tracks:
            album_id = match.album.id
            tracks = [t for t in match.album.tracks if t.id]
            items = [QueueItem(track=t, source_album_id=album_id) for t in tracks]
            return items, {"source_album_id": album_id}

        return [], {}
