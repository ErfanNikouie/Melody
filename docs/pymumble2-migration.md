# pymumble 2 migration notes

Melody targets **[pymumble 2](https://github.com/oopsbagel/pymumble)** (`import mumble`). The PyPI package name is still `pymumble`, but the latest release on PyPI today is **1.6.1** (azlux fork, `pymumble_py3`). Until oopsbagel publishes 2.x to PyPI, Melody pins the dependency to the git repository in `pyproject.toml`; `pip install .` still resolves it through pip.

## Status

Migration is **complete** in Melody's adapter layer (`pymumble_util.py`, `connection.py`). Remaining follow-up is publishing pymumble 2 on PyPI so the git URL in `pyproject.toml` can become `pymumble>=2,<3`.

## API mapping (v1 → v2)

| Melody usage (v1) | pymumble 2 equivalent |
|-------------------|---------------------|
| `import pymumble_py3 as pymumble` | `import mumble` |
| `pymumble.Mumble(host, user, ...)` | `mumble.Mumble(host, user, client_type=1, ...)` |
| `mumble.set_receive_sound(1)` | `enable_audio=True` |
| `mumble.sound_output.*` | `mumble.send_audio.*` |
| `PYMUMBLE_CLBK_*` + `callbacks.set_callback` | `callbacks.<event>.set_handler` |
| `mumble.users.myself_session` | `mumble.users.my_session` |
| `user["channel_id"]` | `user.channel_id` |
| `myself.unmute()` / `undeafen()` | `myself.self_mute = False`, `myself.self_deaf = False` |
| `ConnectionRejectedError` | `mumble.errors.ConnectionRejectedError` |
| `MUMBLE_TLS` env flag | Removed — control connections are always TLS in v2 |
| — | Optional `MUMBLE_CERTFILE` / `MUMBLE_KEYFILE` for registered users |

## Melody adapter

- `src/melody/mumble/pymumble_util.py` — import, callbacks, text parsing
- `src/melody/mumble/connection.py` — thread lifecycle, voice, PCM send

## When pymumble 2 lands on PyPI

Replace the git URL in `pyproject.toml` with:

```
pymumble>=2,<3
```
