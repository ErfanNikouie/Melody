"""User-facing chat message formatting."""

from __future__ import annotations

from html import escape

from melody.models import PlaybackState, PlaybackStatus, QueueItem, SearchMatch, Track

_NOW_PLAYING_COLOR = "#6ab7ff"
_ACCENT_COLOR = "#b0bec5"
_PAUSED_COLOR = "#ffb74d"
_BUFFERING_COLOR = "#ffd54f"


def _e(text: str) -> str:
    return escape(text, quote=False)


def format_duration(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_progress_line(
    elapsed: float,
    total: int | None,
    *,
    width: int = 14,
) -> str:
    """Elapsed/total time display for now-playing views."""
    _ = width  # kept for call-site compatibility
    elapsed_text = format_duration(elapsed)
    if total is None or total <= 0:
        return f'<span style="color:{_ACCENT_COLOR}">{elapsed_text}</span>'
    total_text = format_duration(total)
    return (
        f'<span style="color:{_ACCENT_COLOR}">{elapsed_text} / {total_text}</span>'
    )


def format_state_label(state: PlaybackState) -> str:
    if state == PlaybackState.PLAYING:
        return f'▶️ <span style="color:{_NOW_PLAYING_COLOR}"><b>Playing</b></span>'
    if state == PlaybackState.PAUSED:
        return f'⏸️ <span style="color:{_PAUSED_COLOR}"><b>Paused</b></span>'
    if state == PlaybackState.BUFFERING:
        return f'⏳ <span style="color:{_BUFFERING_COLOR}"><b>Buffering</b></span>'
    return f'<span style="color:{_ACCENT_COLOR}"><b>Idle</b></span>'


def format_track_line(track: Track) -> str:
    return _e(track.display_name)


def format_playback_status(status: PlaybackStatus) -> str:
    """Now-playing block with state, title, and progress."""
    if status.track is None or not status.is_active:
        return format_nothing_playing()

    lines = [
        format_state_label(status.state),
        f'<span style="color:{_NOW_PLAYING_COLOR}"><b>{format_track_line(status.track)}</b></span>',
        format_progress_line(status.elapsed_seconds, status.total_seconds),
    ]
    return "<br/>".join(lines)


def format_nothing_playing() -> str:
    return f'💤 <span style="color:{_ACCENT_COLOR}"><b>Nothing playing</b></span>'


def format_now_playing(track: Track) -> str:
    return (
        f"▶️ <b>Now playing</b><br/>"
        f'<span style="color:{_NOW_PLAYING_COLOR}"><b>{format_track_line(track)}</b></span>'
    )


def format_playing(match_name: str, track_count: int | None = None) -> str:
    detail = _e(match_name)
    if track_count is not None and track_count > 1:
        return f"▶️ <b>Playing</b> {detail} <span style=\"color:{_ACCENT_COLOR}\">({track_count} tracks)</span>"
    return f"▶️ <b>Playing</b> {detail}"


def format_queued(match_name: str, track_count: int | None = None) -> str:
    detail = _e(match_name)
    if track_count is not None and track_count > 1:
        return f"➕ <b>Queued</b> {detail} <span style=\"color:{_ACCENT_COLOR}\">({track_count} tracks)</span>"
    return f"➕ <b>Queued</b> {detail}"


def format_stopped() -> str:
    return "⏹️ <b>Stopped</b> — queue cleared"


def format_left_channel() -> str:
    return "👋 <b>Left channel</b>"


def format_paused() -> str:
    return "⏸️ <b>Paused</b>"


def format_resumed() -> str:
    return "▶️ <b>Resumed</b>"


def format_volume(level: int) -> str:
    bar = _volume_bar(level)
    return f"🔊 <b>Volume</b> {level}% {bar}"


def format_volume_usage() -> str:
    return "ℹ️ <b>Usage:</b> <code>volume [0-100 | up | down | +N | -N]</code>"


def format_no_results() -> str:
    return "🔍 <b>No results found</b>"


def format_no_playable() -> str:
    return "⚠️ <b>No playable tracks found</b>"


def format_search_failed() -> str:
    return "⚠️ <b>Search failed</b>"


def format_searching() -> str:
    return "🔍 <b>Searching</b>…"


def format_need_query() -> str:
    return "ℹ️ Please provide a search query."


def format_joining_channel(channel_name: str) -> str:
    return f"⏳ <b>Joining</b> {_e(channel_name)}…"


def format_command_failed() -> str:
    return "⚠️ <b>Command failed</b>"


def format_queue_empty() -> str:
    return "📭 <b>Queue is empty</b>"


def format_queue_end() -> str:
    return "📭 <b>Queue finished</b>"


def format_no_previous() -> str:
    return "⏮️ <b>No previous track</b>"


def format_search_results(results: list[SearchMatch]) -> str:
    lines = [
        f'🔍 <b>Search results</b> <span style="color:{_ACCENT_COLOR}">({len(results)})</span>',
        "─" * 24,
    ]
    for index, match in enumerate(results, start=1):
        name = _e(match.display_name)
        if match.kind == "album":
            detail = f"💿 {name}"
            if match.track_count > 1:
                detail += f' <span style="color:{_ACCENT_COLOR}">({match.track_count} tracks)</span>'
        elif match.kind == "playlist":
            detail = f"📋 {name}"
            if match.track_count > 1:
                detail += f' <span style="color:{_ACCENT_COLOR}">({match.track_count} tracks)</span>'
        else:
            detail = name
        lines.append(f'<span style="color:{_ACCENT_COLOR}">{index}.</span> {detail}')
    return "<br/>".join(lines)


def _list_window_bounds(total: int, current_index: int, window_size: int) -> tuple[int, int]:
    """Return slice [start, end) indices centered on current when possible."""
    if total <= window_size:
        return 0, total

    before = (window_size - 1) // 2
    after = window_size - 1 - before
    start = current_index - before
    end = current_index + after + 1

    if start < 0:
        return 0, window_size
    if end > total:
        return total - window_size, total
    return start, end


def format_queue_list(
    *,
    history: tuple[QueueItem, ...] = (),
    current: QueueItem | None = None,
    upcoming: tuple[QueueItem, ...] = (),
    status: PlaybackStatus | None = None,
    window_size: int = 50,
) -> str:
    if current is None and not upcoming and not history:
        return format_queue_empty()

    played = len(history)
    total = played + (1 if current is not None else 0) + len(upcoming)

    lines: list[str] = []
    header = (
        f"🎵 <b>Queue</b> <span style=\"color:{_ACCENT_COLOR}\">"
        f"({total} track{'s' if total != 1 else ''}"
    )
    if played:
        header += f", {played} played"
    header += ")</span>"
    lines.append(header)

    anchor = len(history) if current is not None else 0
    window_start, window_end = _list_window_bounds(total, anchor, window_size)
    if window_end - window_start < total:
        shown = window_end - window_start
        lines[-1] += (
            f'<br/><span style="color:{_ACCENT_COLOR}">'
            f"Showing {shown} of {total}</span>"
        )

    entries: list[tuple[str, QueueItem | None]] = []
    for item in history:
        entries.append(("history", item))
    if current is not None:
        entries.append(("current", current))
    for item in upcoming:
        entries.append(("upcoming", item))

    lines.append("─" * 24)
    if window_start > 0:
        lines.append(f'<span style="color:{_ACCENT_COLOR}">… {window_start} earlier</span>')

    visible = entries[window_start:window_end]
    for offset, (kind, item) in enumerate(visible):
        position = window_start + offset
        track_number = position + 1
        if kind == "history" and item is not None:
            lines.append(
                f'<span style="color:{_ACCENT_COLOR}">✓ {track_number}.</span> '
                f"{format_track_line(item.track)}"
            )
            continue

        if kind == "current" and item is not None:
            if status is not None and status.track is not None and status.is_active:
                lines.append(format_state_label(status.state))
                lines.append(
                    f'<span style="color:{_ACCENT_COLOR}">{track_number}.</span> '
                    f'<span style="color:{_NOW_PLAYING_COLOR}"><b>{format_track_line(status.track)}</b></span>'
                )
                lines.append(format_progress_line(status.elapsed_seconds, status.total_seconds))
            else:
                lines.append(
                    f'<span style="color:{_ACCENT_COLOR}">{track_number}.</span> '
                    f'▶️ <span style="color:{_NOW_PLAYING_COLOR}"><b>{format_track_line(item.track)}</b></span>'
                )
            next_kind = visible[offset + 1][0] if offset + 1 < len(visible) else None
            if next_kind == "upcoming":
                lines.append("─" * 24)
            continue

        if kind == "upcoming" and item is not None:
            lines.append(
                f'<span style="color:{_ACCENT_COLOR}">{track_number}.</span> '
                f"{format_track_line(item.track)}"
            )

    if window_end < total:
        remaining = total - window_end
        lines.append(f'<span style="color:{_ACCENT_COLOR}">… {remaining} more</span>')

    return "<br/>".join(lines)


def _volume_bar(level: int) -> str:
    filled = max(0, min(10, round(level / 10)))
    return "█" * filled + "░" * (10 - filled)


def format_help(prefix: str = "m/") -> str:
    """Full command reference for the help command."""
    p = _e(prefix)
    lines = [
        "📖 <b>Melody — Commands</b>",
        f'<span style="color:{_ACCENT_COLOR}">Prefix: <code>{p}</code> · one command per line</span>',
        "─" * 28,
        "<b>Playback</b>",
        f"<code>{p}play [opts] query</code> — search &amp; add to queue",
        f"<code>{p}search [opts] query</code> — top search results (no playback)",
        f"<code>{p}current</code> — now playing &amp; progress",
        f"<code>{p}stop</code> — stop &amp; clear queue",
        f"<code>{p}pause</code> / <code>{p}resume</code>",
        f"<code>{p}next</code> / <code>{p}back</code> — skip tracks",
        f"<code>{p}list</code> — show queue &amp; progress",
        f"<code>{p}volume [0-100|up|down]</code>",
        f"<code>{p}quit</code> — leave channel",
        "─" * 28,
        "<b>Search options</b> <span style=\"color:{0}\">(play / search)</span>".format(_ACCENT_COLOR),
        "<code>-t</code> tracks (default) · <code>-a</code> album · <code>-p</code> playlist",
        "<code>-r</code> repeat · <code>-s</code> shuffle (play only)",
        "─" * 28,
        "<b>Examples</b>",
        f"<code>{p}play never gonna give you up</code>",
        f"<code>{p}play -a dark side of the moon</code>",
        f"<code>{p}search -p workout</code>",
        f"<code>{p}play -p workout -r -s</code>",
        f"<code>{p}volume 50</code>",
    ]
    return "<br/>".join(lines)
