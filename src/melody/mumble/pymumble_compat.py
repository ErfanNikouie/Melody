"""Compatibility shims for unmaintained pymumble on modern Python."""

from __future__ import annotations

import socket
import ssl
from typing import Any


def install_ssl_wrap_socket_compat() -> None:
    """Restore ssl.wrap_socket for pymumble on Python 3.12+."""
    if hasattr(ssl, "wrap_socket"):
        return

    def wrap_socket(
        sock: socket.socket,
        keyfile: str | None = None,
        certfile: str | None = None,
        cert_reqs: int = ssl.CERT_NONE,
        ssl_version: int | None = None,
        **kwargs: Any,
    ) -> ssl.SSLSocket:
        del ssl_version, kwargs
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = cert_reqs
        if certfile:
            context.load_cert_chain(certfile, keyfile)
        return context.wrap_socket(sock)

    ssl.wrap_socket = wrap_socket  # type: ignore[attr-defined]
