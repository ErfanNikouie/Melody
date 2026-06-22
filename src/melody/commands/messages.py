"""User-facing chat message formatting."""

from __future__ import annotations

from html import escape

from melody.models import QueueItem, Track

_NOW_PLAYING_COLOR = "#6ab7ff"
_ACCENT_COLOR = "#b0bec5"


def _e(text: str) -> str:
    return escape(text, quote=False)


def format_track_line(track: Track) -> str:
    return _e(track.display_name)


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
    max_items: int = 15,
) -> str:
    if current is None and not upcoming:
        return format_queue_empty()

    lines: list[str] = []
    total = (1 if current else 0) + len(upcoming)
    lines.append(f"🎵 <b>Queue</b> <span style=\"color:{_ACCENT_COLOR}\">({total} track{'s' if total != 1 else ''})</span>")
    lines.append("─" * 24)

    if current is not None:
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
        f"<code>{p}stop</code> — stop &amp; clear queue",
        f"<code>{p}pause</code> / <code>{p}resume</code>",
        f"<code>{p}next</code> / <code>{p}back</code> — skip tracks",
        f"<code>{p}list</code> — show queue",
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
