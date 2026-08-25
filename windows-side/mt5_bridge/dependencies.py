"""Dependency wiring.

The object graph is assembled exactly once, here. Tests override any node via
`app.dependency_overrides`, which is why nothing constructs its own collaborators.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from .config import Settings
from .guards import AuthAttemptLimiter
from .journal import Journal
from .terminal import MT5Terminal
from .trading import TradingService


def get_settings_from_app(request: Request) -> Settings:
    """Read from app state, never from the module-level cache.

    `create_app(settings)` must fully own the object graph; a dependency that
    reaches for the global `get_settings()` would silently re-read the real
    .env during tests and defeat the injection seam.
    """
    return request.app.state.settings


def get_terminal(request: Request) -> MT5Terminal:
    """Resolved from app state: one terminal connection per process."""
    return request.app.state.terminal


def get_trading_service(request: Request) -> TradingService:
    return request.app.state.trading


def get_journal(request: Request) -> Journal:
    return request.app.state.journal


def get_auth_limiter(request: Request) -> AuthAttemptLimiter:
    return request.app.state.auth_limiter


def verify_api_key(
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    limiter: Annotated[AuthAttemptLimiter, Depends(get_auth_limiter)],
    x_api_key: Annotated[str, Header(alias="x-api-key")] = "",
) -> None:
    """Constant-time comparison: a naive `==` leaks the key one byte at a time.

    Compared as bytes: `secrets.compare_digest` raises TypeError on non-ASCII
    str input, and a malformed header must produce a 401, not a 500.

    Checks `limiter` first, before ever touching the key comparison: once
    locked out, a caller gets 429s regardless of what key they send, so a
    lockout can't be probed for information by comparing response codes
    across different guessed keys.
    """
    limiter.check_not_locked_out()
    expected = settings.bridge_api_key.get_secret_value()
    if not secrets.compare_digest(x_api_key.encode("utf-8"), expected.encode("utf-8")):
        limiter.record_failure()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing x-api-key header")
    limiter.record_success()


RequireApiKey = Depends(verify_api_key)
TerminalDep = Annotated[MT5Terminal, Depends(get_terminal)]
TradingDep = Annotated[TradingService, Depends(get_trading_service)]
JournalDep = Annotated[Journal, Depends(get_journal)]
