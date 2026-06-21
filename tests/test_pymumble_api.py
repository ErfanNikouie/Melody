"""Guard against pymumble API mismatches (no libopus required)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _pymumble_path(relative: str) -> Path:
    spec = importlib.util.find_spec("pymumble_py3")
    if spec is None or spec.origin is None:
        pytest.skip("pymumble_py3 is not installed")
    return Path(spec.origin).parent / relative


def test_connection_rejected_error_exists() -> None:
    source = _pymumble_path("errors.py").read_text(encoding="utf-8")
    assert "class ConnectionRejectedError" in source
    assert "class DenyError" not in source


def test_callback_constants_exist() -> None:
    source = _pymumble_path("constants.py").read_text(encoding="utf-8")
    for name in (
        "PYMUMBLE_CLBK_CONNECTED",
        "PYMUMBLE_CLBK_DISCONNECTED",
        "PYMUMBLE_CLBK_TEXTMESSAGERECEIVED",
        "PYMUMBLE_CONN_STATE_FAILED",
    ):
        assert name in source, f"missing pymumble constant {name}"


def test_callbacks_use_callbacks_object() -> None:
    source = _pymumble_path("mumble.py").read_text(encoding="utf-8")
    assert "self.callbacks = callbacks.CallBacks()" in source
    assert "def set_callback" not in source.split("class Mumble", maxsplit=1)[-1]


def test_load_pymumble_imports_without_deny_error() -> None:
    from melody.mumble.pymumble_util import load_pymumble

    try:
        _pymumble, reject_error = load_pymumble()
    except Exception as exc:
        if "Opus" in str(exc):
            pytest.skip("libopus not available in this environment")
        raise
    assert reject_error.__name__ == "ConnectionRejectedError"
