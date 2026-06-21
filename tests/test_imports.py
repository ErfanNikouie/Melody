"""Verify the package has no import cycles."""

from __future__ import annotations

import importlib
import pkgutil
import sys

import melody

# Import order follows dependency layers (core → outer). A cycle causes a partial init.
_LAYERED_MODULES = (
    "melody.models",
    "melody.protocols",
    "melody.config",
    "melody.logging",
    "melody.subsonic.errors",
    "melody.subsonic.xml_utils",
    "melody.subsonic.interface",
    "melody.subsonic.search",
    "melody.subsonic.client",
    "melody.playback.queue",
    "melody.playback.buffer",
    "melody.playback.ffmpeg",
    "melody.playback.engine",
    "melody.services.search",
    "melody.commands.parser",
    "melody.commands.handler",
    "melody.mumble.pymumble_util",
    "melody.mumble.connection",
    "melody.mumble.channel_session",
    "melody.mumble.player_pool",
    "melody.mumble.coordinator",
    "melody.mumble.orchestrator",
    "melody.app",
)


def test_no_import_cycles_layered() -> None:
    """Import modules in dependency order; failure indicates a circular import."""
    for name in _LAYERED_MODULES:
        importlib.import_module(name)


def test_no_import_cycles_walk_packages() -> None:
    """Import every melody submodule (pymumble is lazy-loaded, not required here)."""
    prefix = melody.__name__ + "."
    skip = {f"{prefix}__main__"}  # entrypoint; tested via layered import of app
    for module_info in pkgutil.walk_packages(melody.__path__, prefix):
        if module_info.name in skip:
            continue
        importlib.import_module(module_info.name)

    assert "melody.commands.handler" in sys.modules
    assert "melody.mumble.orchestrator" in sys.modules
    assert "melody.mumble.channel_session" in sys.modules
