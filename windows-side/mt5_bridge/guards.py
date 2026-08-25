"""Safety guardrails.

Each guard is defined as a `Protocol` first, then given an in-memory
implementation. Swapping to Redis for a multi-process deployment means writing
one new class - no changes anywhere else. That is the whole point of the
indirection; it is not ceremony.

A monotonic clock is injected so tests can fast-forward time without sleeping.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Protocol

from .exceptions import (
    ConfirmTokenInvalid,
    DailyLimitReached,
    ScaleInCooldownActive,
    TooManyAuthAttempts,
)
from .models import Position

Clock = Callable[[], float]
"""Returns monotonic seconds. Injected to keep time-based tests instant."""


# --------------------------------------------------------------------------
# Daily order limiter
# --------------------------------------------------------------------------
class OrderLimiter(Protocol):
    def check_and_increment(self) -> None:
        """Raise `DailyLimitReached` if today's quota is exhausted."""

    def remaining(self) -> int: ...


class InMemoryOrderLimiter:
    """Resets automatically at UTC midnight. Thread-safe."""

    def __init__(self, max_per_day: int, today: Callable[[], date] | None = None) -> None:
        self._max = max_per_day
        self._today = today or (lambda: datetime.now(timezone.utc).date())
        self._lock = threading.Lock()
        self._date: date | None = None
        self._count = 0

    def _roll_over_if_needed(self) -> None:
        current = self._today()
        if self._date != current:
            self._date = current
            self._count = 0

    def check_and_increment(self) -> None:
        with self._lock:
            self._roll_over_if_needed()
            if self._count >= self._max:
                raise DailyLimitReached(f"daily order limit reached ({self._max}/day)")
            self._count += 1

    def remaining(self) -> int:
        with self._lock:
            self._roll_over_if_needed()
            return max(0, self._max - self._count)


# --------------------------------------------------------------------------
# Two-step confirmation tokens
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TokenPayload:
    """The exact action a token authorises. Compared field-by-field on redeem,
    so a token minted for 0.01 lots can never execute 0.10 lots, and a token
    minted to close one ticket can never close a different one."""

    symbol: str
    side: str
    volume: float
    ticket: int | None = None  # set for close tokens, None for open orders


class ConfirmTokenStore(Protocol):
    def issue(self, payload: TokenPayload) -> str: ...
    def redeem(self, token: str, expected: TokenPayload) -> None:
        """Consume the token. Raise `ConfirmTokenInvalid` on any mismatch."""


class InMemoryConfirmTokenStore:
    """Single-use, short-lived tokens. Expired entries are swept on write."""

    def __init__(self, ttl_seconds: int, clock: Clock = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._store: dict[str, tuple[TokenPayload, float]] = {}

    def issue(self, payload: TokenPayload) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._sweep_expired()
            self._store[token] = (payload, self._clock())
        return token

    def redeem(self, token: str, expected: TokenPayload) -> None:
        with self._lock:
            entry = self._store.pop(token, None)  # single-use: pop, never peek

        if entry is None:
            raise ConfirmTokenInvalid("confirm_token is unknown or already used")

        payload, issued_at = entry
        if self._clock() - issued_at > self._ttl:
            raise ConfirmTokenInvalid(f"confirm_token expired (ttl={self._ttl}s)")
        if payload != expected:
            raise ConfirmTokenInvalid(
                "confirm_token does not match this order; "
                "preview and execute must describe the same action"
            )

    def _sweep_expired(self) -> None:
        """Called under lock. O(n) but n is tiny (tokens live ~60s)."""
        now = self._clock()
        stale = [t for t, (_, issued) in self._store.items() if now - issued > self._ttl]
        for token in stale:
            del self._store[token]


# --------------------------------------------------------------------------
# Scale-in cooldown - LIVE ACCOUNTS ONLY
# --------------------------------------------------------------------------
class ScaleInCooldownGuard:
    """Blocks opening a second position on the same symbol too soon after
    the first - live accounts only. Pass is_demo=True to skip the check
    entirely; demo accounts are exempt by design (this guard exists to
    slow down repeated live-account exposure to the same symbol, which
    is not a meaningful risk on demo).
    """

    def __init__(self, cooldown_seconds: int = 900) -> None:  # 900s = 15 min
        self._cooldown = cooldown_seconds

    def check(
        self, *, symbol: str, is_demo: bool, existing_positions: list[Position]
    ) -> None:
        if is_demo:
            return
        same_symbol = [p for p in existing_positions if p.symbol == symbol]
        if not same_symbol:
            return
        newest = max(p.time_open for p in same_symbol)
        elapsed = (datetime.now(timezone.utc) - newest).total_seconds()

        # MT5 reports position.time in the *broker's server* time, which is
        # commonly offset from UTC by a few hours and varies by broker (and
        # sometimes by DST rules within the same broker) - not UTC itself.
        # Comparing it directly against our clock skews `elapsed` by that
        # offset: a positive skew would stretch a 15-minute cooldown into
        # hours, a negative one would expire it instantly. Clamping keeps
        # the guard bounded and predictable either way: a future-dated
        # timestamp is treated as "just opened" (full cooldown), and the
        # wait can never exceed the configured window.
        if elapsed < 0:
            elapsed = 0.0
        remaining = self._cooldown - int(elapsed)
        if remaining > 0:
            raise ScaleInCooldownActive(symbol, min(remaining, self._cooldown))


# --------------------------------------------------------------------------
# Auth attempt limiter
# --------------------------------------------------------------------------
class AuthAttemptLimiter(Protocol):
    def check_not_locked_out(self) -> None:
        """Raise `TooManyAuthAttempts` if currently locked out."""

    def record_failure(self) -> None: ...
    def record_success(self) -> None: ...


class InMemoryAuthAttemptLimiter:
    """Throttles repeated failed `x-api-key` attempts.

    Scoped globally, not per-caller: this bridge protects one MT5 account
    behind one shared key, so there is no meaningful per-client identity to
    track separately, and the caller could be spoofed anyway. A burst of
    failed auth attempts is either a misconfigured legitimate caller or
    someone scanning/guessing - either way, the right response is the same:
    slow down, don't distinguish. This is defense-in-depth on top of an
    already-unguessable key (`secrets.compare_digest` against a value the
    operator is expected to generate with real entropy); it exists for
    anyone who ends up with a weaker key than they should, not because the
    intended key is expected to be crackable.
    """

    def __init__(
        self,
        max_failures: int = 10,
        window_seconds: float = 60.0,
        lockout_seconds: float = 30.0,
        clock: Clock = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failure_times: list[float] = []
        self._locked_until: float | None = None

    def check_not_locked_out(self) -> None:
        with self._lock:
            if self._locked_until is not None and self._clock() < self._locked_until:
                remaining = int(self._locked_until - self._clock()) + 1
                raise TooManyAuthAttempts(remaining)
            if self._locked_until is not None:
                self._locked_until = None  # lockout has expired, clear it

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._failure_times = [t for t in self._failure_times if now - t < self._window]
            self._failure_times.append(now)
            if len(self._failure_times) >= self._max_failures:
                self._locked_until = now + self._lockout
                self._failure_times = []

    def record_success(self) -> None:
        with self._lock:
            self._failure_times = []
            self._locked_until = None
