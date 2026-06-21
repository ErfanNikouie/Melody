"""Tests for PCM pacing."""

from __future__ import annotations

import asyncio

from melody.playback.pcm_pacer import PcmPacer


def test_prebuffer_phase_sends_without_delay() -> None:
    buffer = {"sec": 0.0}
    pacer = PcmPacer(lambda: buffer["sec"], frame_duration_sec=0.04, target_buffer_sec=0.12)
    loop = asyncio.new_event_loop()

    assert pacer.delay_before_next_frame(loop) == 0.0
    assert not pacer.primed

    buffer["sec"] = 0.12
    delay = pacer.delay_before_next_frame(loop)
    assert pacer.primed
    assert delay == 0.0


def test_paced_phase_targets_frame_duration() -> None:
    buffer = {"sec": 0.15}
    pacer = PcmPacer(lambda: buffer["sec"], frame_duration_sec=0.04, target_buffer_sec=0.12)
    loop = asyncio.new_event_loop()
    buffer["sec"] = 0.12
    pacer.delay_before_next_frame(loop)
    assert pacer.primed

    delay = pacer.delay_before_next_frame(loop)
    assert 0.035 <= delay <= 0.04


def test_low_buffer_skips_delay() -> None:
    buffer = {"sec": 0.12}
    pacer = PcmPacer(lambda: buffer["sec"], frame_duration_sec=0.04, target_buffer_sec=0.12)
    loop = asyncio.new_event_loop()
    pacer.delay_before_next_frame(loop)

    buffer["sec"] = 0.01
    assert pacer.delay_before_next_frame(loop) == 0.0
