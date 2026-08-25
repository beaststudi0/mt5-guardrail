"""Guards are pure logic, so they get pure tests - no clock, no I/O."""

from __future__ import annotations

import itertools

import pytest

from mt5_bridge.exceptions import ConfirmTokenInvalid, DailyLimitReached, TooManyAuthAttempts
from mt5_bridge.guards import (
    InMemoryAuthAttemptLimiter,
    InMemoryConfirmTokenStore,
    InMemoryOrderLimiter,
    TokenPayload,
)


class TestOrderLimiter:
    def test_allows_up_to_the_limit_then_blocks(self) -> None:
        limiter = InMemoryOrderLimiter(max_per_day=2)
        limiter.check_and_increment()
        limiter.check_and_increment()
        assert limiter.remaining() == 0
        with pytest.raises(DailyLimitReached):
            limiter.check_and_increment()

    def test_quota_resets_on_a_new_day(self) -> None:
        import datetime as dt

        days = itertools.cycle([dt.date(2026, 1, 1), dt.date(2026, 1, 1), dt.date(2026, 1, 2)])
        limiter = InMemoryOrderLimiter(max_per_day=1, today=lambda: next(days))

        limiter.check_and_increment()          # day 1, first order
        with pytest.raises(DailyLimitReached):
            limiter.check_and_increment()      # day 1, blocked
        limiter.check_and_increment()          # day 2, allowed again


class TestConfirmTokenStore:
    payload = TokenPayload(symbol="US100Cash", side="buy", volume=0.01)

    def test_round_trip(self) -> None:
        store = InMemoryConfirmTokenStore(ttl_seconds=60)
        store.redeem(store.issue(self.payload), self.payload)

    def test_token_is_single_use(self) -> None:
        store = InMemoryConfirmTokenStore(ttl_seconds=60)
        token = store.issue(self.payload)
        store.redeem(token, self.payload)
        with pytest.raises(ConfirmTokenInvalid, match="already used"):
            store.redeem(token, self.payload)

    def test_expired_token_is_refused(self) -> None:
        now = [0.0]
        store = InMemoryConfirmTokenStore(ttl_seconds=60, clock=lambda: now[0])
        token = store.issue(self.payload)
        now[0] = 61.0
        with pytest.raises(ConfirmTokenInvalid, match="expired"):
            store.redeem(token, self.payload)

    def test_token_cannot_authorise_a_different_volume(self) -> None:
        """The whole point: a 0.01 preview must never execute 0.10."""
        store = InMemoryConfirmTokenStore(ttl_seconds=60)
        token = store.issue(self.payload)
        bigger = TokenPayload(symbol="US100Cash", side="buy", volume=0.10)
        with pytest.raises(ConfirmTokenInvalid, match="does not match"):
            store.redeem(token, bigger)

    def test_unknown_token_is_refused(self) -> None:
        store = InMemoryConfirmTokenStore(ttl_seconds=60)
        with pytest.raises(ConfirmTokenInvalid):
            store.redeem("never-issued", self.payload)


class TestAuthAttemptLimiter:
    """Rate-limits repeated failed x-api-key attempts. Added for the
    public release: a bridge anyone can run is a bridge anyone can point
    a scanner at, and a static key deserves a lockout, not just a hope
    that it's long enough.
    """

    def test_not_locked_out_initially(self) -> None:
        limiter = InMemoryAuthAttemptLimiter()
        limiter.check_not_locked_out()  # must not raise

    def test_below_the_failure_threshold_stays_unlocked(self) -> None:
        limiter = InMemoryAuthAttemptLimiter(max_failures=3)
        limiter.record_failure()
        limiter.record_failure()
        limiter.check_not_locked_out()  # 2 failures, threshold is 3

    def test_reaching_the_threshold_locks_out(self) -> None:
        limiter = InMemoryAuthAttemptLimiter(max_failures=3, lockout_seconds=30.0)
        for _ in range(3):
            limiter.record_failure()
        with pytest.raises(TooManyAuthAttempts) as exc_info:
            limiter.check_not_locked_out()
        assert exc_info.value.retry_after_seconds > 0

    def test_a_success_resets_the_failure_count(self) -> None:
        """A caller that mistypes the key once and then gets it right must
        not be halfway to locked out from that one old failure."""
        limiter = InMemoryAuthAttemptLimiter(max_failures=3)
        limiter.record_failure()
        limiter.record_failure()
        limiter.record_success()
        limiter.record_failure()
        limiter.record_failure()
        limiter.check_not_locked_out()  # only 2 failures since the reset

    def test_lockout_expires_after_the_configured_duration(self) -> None:
        clock = {"t": 0.0}
        limiter = InMemoryAuthAttemptLimiter(
            max_failures=2, lockout_seconds=10.0, clock=lambda: clock["t"]
        )
        limiter.record_failure()
        limiter.record_failure()
        with pytest.raises(TooManyAuthAttempts):
            limiter.check_not_locked_out()

        clock["t"] = 10.1
        limiter.check_not_locked_out()  # must not raise once the lockout has passed

    def test_failures_outside_the_window_do_not_accumulate(self) -> None:
        """A failure from an hour ago should not count toward triggering a
        lockout now - only a burst *within* the configured window should."""
        clock = {"t": 0.0}
        limiter = InMemoryAuthAttemptLimiter(
            max_failures=3, window_seconds=10.0, lockout_seconds=30.0, clock=lambda: clock["t"]
        )
        limiter.record_failure()
        limiter.record_failure()
        clock["t"] = 11.0  # past the window: those two failures should be forgotten
        limiter.record_failure()
        limiter.check_not_locked_out()  # only 1 failure within the current window
