"""Process entrypoint. Run on native Windows Python, never inside WSL."""

from __future__ import annotations

import sys

import uvicorn

from mt5_bridge.app import configure_logging, create_app
from mt5_bridge.config import get_settings


def main() -> int:
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - config errors must be readable
        print(f"Configuration error: {exc}", file=sys.stderr)
        print("Check your .env file against .env.example.", file=sys.stderr)
        return 1

    configure_logging(settings)
    uvicorn.run(
        create_app(settings),
        host=settings.bridge_host,
        port=settings.bridge_port,
        log_config=None,  # our logging config already owns the handlers
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
