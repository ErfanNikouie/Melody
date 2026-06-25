# pymumble 2 migration notes

Melody currently targets **pymumble 1.x** (`pymumble_py3`) and pins `pymumble>=1.6.1,<2` in `pyproject.toml`. A full migration to [pymumble 2](https://sr.ht/~oopsbagel/pymumble/) is a separate effort; the upstream project notes the API is still evolving before 2.0.0.

## Why not migrate in the memory pass

- Import path changes: `pymumble_py3` → `mumble`
- Callback constants become a `CALLBACK` enum
- `sound_output` → `send_audio`; `set_receive_sound()` removed (audio on by default)
- Context-manager lifecycle (`with Mumble(...) as m:`) is encouraged
- Encrypted UDP (AES-OCB2) and protocol 1.5.735 support

Melody's adapter lives in:

- `src/melody/mumble/pymumble_util.py` — import, callbacks, text parsing
- `src/melody/mumble/connection.py` — thread lifecycle, voice, PCM send

## API mapping (v1 → v2)

| Melody usage (v1) | pymumble 2 equivalent |
|-------------------|---------------------|
| `import pymumble_py3 as pymumble` | `from mumble import Mumble` |
| `pymumble.Mumble(host, user, port=..., password=..., reconnect=..., stereo=...)` | `Mumble(host, user, port=..., password=..., ...)` — verify ctor kwargs |
| `mumble.set_receive_sound(1)` | Default on; `Mumble(enable_audio=True)` or omit |
| `mumble.sound_output.add_sound(data)` | `mumble.send_audio.add_sound(data)` |
| `mumble.sound_output.get_buffer_size()` | `mumble.send_audio.get_buffer_size()` (verify name) |
| `mumble.sound_output.set_audio_per_packet(0.04)` | Same on `send_audio` (verify) |
| `PYMUMBLE_CLBK_*` + `callbacks.set_callback` | `CALLBACK.*` enum |
| `mumble.users.myself`, `mumble.channels[id]` | Likely similar; verify dict vs object API |
| `channel.send_text_message(msg)` | Unchanged pattern in v2 examples |
| `ConnectionRejectedError` | Verify exception module path |

## Suggested migration steps

1. Add a version-detecting shim in `pymumble_util.py` (`load_pymumble()` returns unified facade).
2. Port `bind_callbacks` / `clear_callbacks` to v2 `CALLBACK` enum.
3. Port `MumbleConnection._ensure_voice_ready` and PCM send paths to `send_audio`.
4. Run integration tests against a local Murmur with pool + coordinator bots.
5. Drop `pymumble_compat.py` SSL shim if v2 no longer needs it on Python 3.12+.
6. Bump dependency to `pymumble>=2,<3` only after manual soak testing.

## Risk

Upstream states that until 2.0.0 is released, **nothing is stable**. Pinning `<2` avoids accidental incompatible installs while Melody still imports `pymumble_py3`.
