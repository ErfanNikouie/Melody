"""Guard against pymumble API mismatches (no libopus required)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _mumble_path(relative: str) -> Path:
    spec = importlib.util.find_spec("mumble")
    if spec is None or spec.origin is None:
        pytest.skip("mumble (pymumble 2) is not installed")
    return Path(spec.origin).parent / relative


def test_connection_rejected_error_exists() -> None:
    source = _mumble_path("errors.py").read_text(encoding="utf-8")
    assert "class ConnectionRejectedError" in source


def test_callbacks_use_handler_objects() -> None:
    source = _mumble_path("callbacks.py").read_text(encoding="utf-8")
    assert "class Callbacks" in source
    assert "text_message_received" in source
    assert "def set_handler" in source
    assert "def clear_handler" in source


def test_send_audio_replaces_sound_output() -> None:
    source = _mumble_path("mumble.py").read_text(encoding="utf-8")
    assert "self.send_audio = SendAudio" in source
    assert "sound_output" not in source


def test_stereo_players_enable_audio() -> None:
    """pymumble 2 allocates send_audio when enable_audio is True."""
    source = (
        Path(__file__).resolve().parents[1] / "src" / "melody" / "mumble" / "connection.py"
    ).read_text(encoding="utf-8")
    assert "enable_audio=self._stereo" in source
    assert "client_type=self._client_type" in source


def test_clear_callbacks_helper_exists() -> None:
    calls: list[str] = []

    class _Callback:
        def clear_handler(self) -> None:
            calls.append("cleared")

    class _Callbacks:
        text_message_received = _Callback()
        connected = _Callback()
        disconnected = _Callback()

    from melody.mumble.pymumble_util import clear_callbacks

    mumble = type("M", (), {"callbacks": _Callbacks()})()
    clear_callbacks(mumble)
    assert calls == ["cleared", "cleared", "cleared"]


def test_load_pymumble_imports_mumble_module() -> None:
    from melody.mumble.pymumble_util import load_pymumble

    try:
        pymumble, reject_error = load_pymumble()
    except Exception as exc:
        if "Opus" in str(exc):
            pytest.skip("libopus not available in this environment")
        raise
    assert pymumble.Mumble is not None
    assert reject_error.__name__ == "ConnectionRejectedError"
