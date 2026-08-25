"""Domain exceptions.

These are raised by the service layer, which knows nothing about HTTP.
The API layer translates them into status codes exactly once
(see `mt5_bridge.api.errors`). This keeps the trading logic reusable from a
CLI, a scheduled job, or a test - not just from a web request.
"""

from __future__ import annotations


class BridgeError(Exception):
    """Base class for every error this package raises deliberately."""


class TerminalConnectionError(BridgeError):
    """Could not reach or authenticate against the MT5 terminal."""


class SymbolNotAllowed(BridgeError):
    """Symbol is outside the configured whitelist."""

    def __init__(self, symbol: str, allowed: frozenset[str]) -> None:
        super().__init__(f"symbol {symbol!r} is not in the allowed list {sorted(allowed)}")
        self.symbol = symbol


class SymbolUnavailable(BridgeError):
    """Symbol is allowed, but the terminal cannot quote it right now."""


class VolumeOutOfRange(BridgeError):
    """Requested lot size violates broker limits or the configured ceiling."""


class InvalidStopLevels(BridgeError):
    """Stop-loss / take-profit on the wrong side of the entry price.

    Caught locally instead of letting the broker answer with a bare
    'Invalid stops' rejection after the order was already submitted.
    """


class DailyLimitReached(BridgeError):
    """Too many orders sent today - a circuit breaker against runaway bots."""


class TooManyAuthAttempts(BridgeError):
    """Too many failed x-api-key attempts in a short window - temporarily
    locked out. Defense-in-depth against a weak key being brute-forced or
    scanned for; does not distinguish a misconfigured legitimate caller
    from an attacker, since a shared static key has no other identity to
    tell them apart by."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"too many failed auth attempts - try again in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class ConfirmTokenInvalid(BridgeError):
    """Missing, expired, or mismatched confirmation token."""


class PositionNotFound(BridgeError):
    """No open position matches the given ticket."""


class OrderRejected(BridgeError):
    """The broker refused the order. `retcode` is MT5's numeric reason."""

    def __init__(self, message: str, retcode: int | None = None) -> None:
        super().__init__(message)
        self.retcode = retcode


class ScaleInCooldownActive(BridgeError):
    """Same symbol opened another position too recently - live accounts
    only (demo accounts are exempt; see ScaleInCooldownGuard)."""

    def __init__(self, symbol: str, seconds_remaining: int) -> None:
        super().__init__(
            f"scale-in cooldown active for {symbol}: wait {seconds_remaining}s "
            "more, or get explicit Owner confirmation before opening another "
            "position on this symbol"
        )
        self.symbol = symbol
        self.seconds_remaining = seconds_remaining
