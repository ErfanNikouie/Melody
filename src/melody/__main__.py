"""Entry point for Melody."""

from __future__ import annotations

import asyncio
import sys

from melody.app import create_app
from melody.logging import setup_logging


def main() -> None:
    try:
        app = create_app()
    except Exception as exc:
        setup_logging("INFO")
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
