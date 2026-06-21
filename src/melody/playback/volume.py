"""PCM volume scaling and volume command parsing."""

from __future__ import annotations

import array
from dataclasses import dataclass
from typing import Literal

MIN_VOLUME_PERCENT = 0
MAX_VOLUME_PERCENT = 100
DEFAULT_VOLUME_PERCENT = 100
VOLUME_STEP_PERCENT = 10


@dataclass(frozen=True, slots=True)
class VolumeCommand:
    """Parsed volume subcommand."""

    action: Literal["show", "set"]
    percent: int | None = None
    delta: int | None = None


def clamp_volume_percent(value: int) -> int:
    return max(MIN_VOLUME_PERCENT, min(MAX_VOLUME_PERCENT, value))


def apply_volume_pcm(pcm: bytes, volume: float) -> bytes:
    """Scale 16-bit little-endian PCM by volume (0.0–1.0)."""
    if not pcm or volume >= 1.0 - 1e-9:
        return pcm
    if volume <= 0.0:
        return b"\x00" * len(pcm)

    samples = array.array("h")
    samples.frombytes(pcm)
    for index, sample in enumerate(samples):
        scaled = int(sample * volume)
        if scaled > 32767:
            scaled = 32767
        elif scaled < -32768:
            scaled = -32768
        samples[index] = scaled
    return samples.tobytes()


def parse_volume_command(query: str | None) -> VolumeCommand | None:
    """Return a volume command, or None if the query is invalid."""
    if query is None or not query.strip():
        return VolumeCommand(action="show")

    token = query.strip().lower()
    if token in {"up", "louder", "+"}:
        return VolumeCommand(action="set", delta=VOLUME_STEP_PERCENT)
    if token in {"down", "quieter", "-"}:
        return VolumeCommand(action="set", delta=-VOLUME_STEP_PERCENT)

    if token.endswith("%"):
        token = token[:-1].strip()

    if token.startswith(("+", "-")) and len(token) > 1:
        try:
            delta = int(token)
        except ValueError:
            return None
        return VolumeCommand(action="set", delta=delta)

    try:
        percent = int(token)
    except ValueError:
        return None

    return VolumeCommand(action="set", percent=clamp_volume_percent(percent))


def resolve_volume_percent(current: int, command: VolumeCommand) -> int:
    if command.action == "show":
        return current
    if command.percent is not None:
        return command.percent
    if command.delta is not None:
        return clamp_volume_percent(current + command.delta)
    return current
