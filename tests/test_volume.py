"""Tests for PCM volume scaling and parsing."""

from __future__ import annotations

import array
import struct

from melody.playback.volume import (
    apply_volume_pcm,
    parse_volume_command,
    resolve_volume_percent,
)


def test_apply_volume_pcm_silence() -> None:
    pcm = struct.pack("<4h", 1000, -1000, 500, -500)
    assert apply_volume_pcm(pcm, 0.0) == b"\x00" * len(pcm)


def test_apply_volume_pcm_half() -> None:
    pcm = struct.pack("<2h", 1000, -2000)
    scaled = apply_volume_pcm(pcm, 0.5)
    samples = array.array("h")
    samples.frombytes(scaled)
    assert samples.tolist() == [500, -1000]


def test_apply_volume_pcm_unity_is_noop() -> None:
    pcm = struct.pack("<2h", 123, -456)
    assert apply_volume_pcm(pcm, 1.0) == pcm


def test_parse_volume_show() -> None:
    cmd = parse_volume_command(None)
    assert cmd is not None
    assert cmd.action == "show"


def test_parse_volume_absolute_and_relative() -> None:
    absolute = parse_volume_command("75")
    assert absolute is not None
    assert absolute.percent == 75
    assert parse_volume_command("75%").percent == 75
    assert parse_volume_command("up").delta == 10
    assert parse_volume_command("-5").delta == -5
    assert parse_volume_command("bad") is None


def test_resolve_volume_percent() -> None:
    cmd = parse_volume_command("80")
    assert cmd is not None
    assert resolve_volume_percent(50, cmd) == 80

    up = parse_volume_command("up")
    assert up is not None
    assert resolve_volume_percent(50, up) == 60
