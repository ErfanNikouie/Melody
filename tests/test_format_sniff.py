"""Tests for encoded audio format sniffing."""

from __future__ import annotations

from melody.playback.format_sniff import detect_encoded_format, ffmpeg_input_format


def test_detect_mp3_id3() -> None:
    assert detect_encoded_format(b"ID3\x04...") == "mp3"


def test_detect_flac() -> None:
    assert detect_encoded_format(b"fLaC\x00...") == "flac"


def test_detect_from_content_type() -> None:
    assert detect_encoded_format(b"", "audio/mpeg") == "mp3"


def test_content_type_overrides_ogg_magic() -> None:
    assert detect_encoded_format(b"OggS", "audio/mpeg") == "mp3"


def test_ffmpeg_input_format_skips_ogg() -> None:
    assert ffmpeg_input_format("ogg") is None
    assert ffmpeg_input_format("mp3") == "mp3"
