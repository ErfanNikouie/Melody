"""User-facing chat message formatting."""

from __future__ import annotations

from html import escape

from melody.models import PlaybackState, PlaybackStatus, QueueItem, Track

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
    """Stylized elapsed/total with a Unicode progress bar."""
    elapsed_text = format_duration(elapsed)
    if total is None or total <= 0:
        return (
            f'<span style="color:{_ACCENT_COLOR}">{elapsed_text}</span>'
            f' <span style="color:{_ACCENT_COLOR}">· live</span>'
        )

    pct = min(1.0, elapsed / total)
    filled = int(round(pct * width))
    bar = "█" * filled + "░" * (width - filled)
    total_text = format_duration(total)
    pct_text = int(round(pct * 100))
    return (
        f'<code>{bar}</code> '
        f'<span style="color:{_ACCENT_COLOR}">{elapsed_text} / {total_text}</span> '
        f'<span style="color:{_ACCENT_COLOR}">({pct_text}%)</span>'
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


def format_need_query() -> str:
    return "ℹ️ Please provide a search query."


def format_queue_empty() -> str:
    return "📭 <b>Queue is empty</b>"


def format_queue_end() -> str:
    return "📭 <b>Queue finished</b>"


def format_no_previous() -> str:
    return "⏮️ <b>No previous track</b>"


def format_queue_list(
    current: QueueItem | None,
    upcoming: tuple[QueueItem, ...],
    *,
    status: PlaybackStatus | None = None,
    max_items: int = 15,
) -> str:
    if current is None and not upcoming:
        return format_queue_empty()

    lines: list[str] = []
    total = (1 if current else 0) + len(upcoming)
    lines.append(
        f"🎵 <b>Queue</b> <span style=\"color:{_ACCENT_COLOR}\">"
        f"({total} track{'s' if total != 1 else ''})</span>"
    )
    lines.append("─" * 24)

    if status is not None and status.track is not None and status.is_active:
        lines.append(format_state_label(status.state))
        lines.append(
            f'<span style="color:{_NOW_PLAYING_COLOR}"><b>{format_track_line(status.track)}</b></span>'
        )
        lines.append(format_progress_line(status.elapsed_seconds, status.total_seconds))
        lines.append("─" * 24)
    elif current is not None:
        lines.append(
            f"▶️ <span style=\"color:{_NOW_PLAYING_COLOR}\"><b>{format_track_line(current.track)}</b></span>"
        )

    shown = 0
    for index, item in enumerate(upcoming, start=1):
        if shown >= max_items:
            remaining = len(upcoming) - shown
            lines.append(f"<span style=\"color:{_ACCENT_COLOR}\">… and {remaining} more</span>")
            break
        prefix = f"{index}."
        lines.append(f"<span style=\"color:{_ACCENT_COLOR}\">{prefix}</span> {format_track_line(item.track)}")
        shown += 1

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
        f"<code>{p}play [opts] query</code> — search &amp; play now",
        f"<code>{p}queue [opts] query</code> — search &amp; add to queue",
        f"<code>{p}current</code> — now playing &amp; progress",
        f"<code>{p}stop</code> — stop &amp; clear queue",
        f"<code>{p}pause</code> / <code>{p}resume</code>",
        f"<code>{p}next</code> / <code>{p}back</code> — skip tracks",
        f"<code>{p}list</code> — show queue &amp; progress",
        f"<code>{p}volume [0-100|up|down]</code>",
        f"<code>{p}quit</code> — leave channel",
        "─" * 28,
        "<b>Search options</b> <span style=\"color:{0}\">(play / queue)</span>".format(_ACCENT_COLOR),
        "<code>-t</code> tracks (default) · <code>-a</code> album · <code>-p</code> playlist",
        "<code>-r</code> repeat · <code>-s</code> shuffle",
        "─" * 28,
        "<b>Examples</b>",
        f"<code>{p}play never gonna give you up</code>",
        f"<code>{p}play -a dark side of the moon</code>",
        f"<code>{p}queue -p workout -r -s</code>",
        f"<code>{p}volume 50</code>",
    ]
    return "<br/>".join(lines)
