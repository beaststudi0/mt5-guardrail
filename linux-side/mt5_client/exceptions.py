"""Client exceptions mirror the server's domain errors.

Callers can catch `ConfirmTokenRejected` specifically instead of parsing
strings out of a generic HTTP error - which is what makes an agent able to
react intelligently (e.g. re-preview and ask the human again).
"""

from __future__ import annotations


class BridgeClientError(Exception):
    """Base class for anything this client raises."""


class BridgeUnavailable(BridgeClientError):
    """The bridge is unreachable: not started, wrong URL, or firewall."""


class BridgeHTTPError(BridgeClientError):
    """Non-2xx response that has no more specific mapping."""

    def __init__(self, status_code: int, detail: str, payload: dict | None = None) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.payload = payload or {}


class BridgeUnauthorized(BridgeHTTPError):
    """Wrong or missing API key. Check both sides of the .env."""


class ConfirmTokenRejected(BridgeHTTPError):
    """Token missing, expired, replayed, or minted for a different order."""


class DailyLimitReached(BridgeHTTPError):
    """The server's circuit breaker tripped. Stop trading for today."""


class OrderRejected(BridgeHTTPError):
    """The broker refused the order. `retcode` carries MT5's numeric reason."""

    @property
    def retcode(self) -> int | None:
        value = self.payload.get("retcode")
        return int(value) if value is not None else None
