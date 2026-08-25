"""Terminal behaviours that are easy to get wrong: retries, caching, filling mode."""

from __future__ import annotations

import pytest

from mt5_bridge.exceptions import SymbolUnavailable, TerminalConnectionError
from mt5_bridge.terminal import MT5Terminal
from tests.fakes import FakeMT5


def build(fake: FakeMT5, **kwargs: object) -> MT5Terminal:
    return MT5Terminal(
        fake, login=1, password="pw", server="Fake", sleep=lambda _: None, **kwargs
    )


class TestConnection:
    def test_connect_is_idempotent(self, fake_mt5: FakeMT5) -> None:
        terminal = build(fake_mt5)
        terminal.connect()
        terminal.connect()
        assert terminal.is_connected

    def test_retries_then_raises_a_clear_error(self, fake_mt5: FakeMT5) -> None:
        fake_mt5.fail_login = True
        terminal = build(fake_mt5)
        with pytest.raises(TerminalConnectionError, match="3 attempts"):
            terminal.connect()

    def test_recovers_if_login_starts_working(self, fake_mt5: FakeMT5) -> None:
        calls = {"n": 0}
        original_login = fake_mt5.login

        def flaky(*args: object, **kwargs: object) -> bool:
            calls["n"] += 1
            return original_login(*args, **kwargs) if calls["n"] >= 2 else False

        fake_mt5.login = flaky  # type: ignore[method-assign]
        terminal = build(fake_mt5)
        terminal.connect()
        assert terminal.is_connected

    def test_liveness_is_not_reverified_on_every_single_operation(
        self, fake_mt5: FakeMT5
    ) -> None:
        """Regression for the real latency bug: every bridge operation goes
        through `_session()` -> `connect()` -> `is_connected`, and a naive
        "always re-verify" implementation meant `terminal_info()` (a full
        native IPC round-trip) fired on every single request -- doubling the
        native calls needed for the hot quote path and, worse, for live
        order execution. Within `connection_check_interval`, repeated
        operations must reuse the last verification instead of re-checking.
        """
        calls = {"n": 0}
        original = fake_mt5.terminal_info

        def counted() -> object | None:
            calls["n"] += 1
            return original()

        fake_mt5.terminal_info = counted  # type: ignore[method-assign]
        terminal = build(fake_mt5, connection_check_interval=60.0)
        terminal.connect()
        assert calls["n"] == 0  # a fresh connect() need not re-verify itself

        for _ in range(5):
            terminal.tick("US100Cash")
        assert calls["n"] == 0  # five operations, zero extra liveness checks

    def test_liveness_is_reverified_once_the_check_interval_elapses(
        self, fake_mt5: FakeMT5
    ) -> None:
        """The staleness window has a real expiry, not an infinite cache:
        after `connection_check_interval` seconds, the next operation must
        pay for one real verification again -- proving this is a bounded
        trade-off (occasional re-check), not "never re-check at all"."""
        calls = {"n": 0}
        original = fake_mt5.terminal_info

        def counted() -> object | None:
            calls["n"] += 1
            return original()

        fake_mt5.terminal_info = counted  # type: ignore[method-assign]
        fake_clock = {"t": 0.0}
        terminal = build(
            fake_mt5,
            connection_check_interval=5.0,
            clock=lambda: fake_clock["t"],
        )
        terminal.connect()

        terminal.tick("US100Cash")
        assert calls["n"] == 0  # still within the window

        fake_clock["t"] += 5.1  # push past connection_check_interval
        terminal.tick("US100Cash")
        assert calls["n"] == 1  # exactly one re-verification, not zero

        terminal.tick("US100Cash")
        assert calls["n"] == 1  # and back to reusing it immediately after


class TestSymbolSpec:
    def test_spec_is_cached_within_ttl(self, fake_mt5: FakeMT5) -> None:
        calls = {"n": 0}
        original = fake_mt5.symbol_info

        def counted(symbol: str):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return original(symbol)

        fake_mt5.symbol_info = counted  # type: ignore[method-assign]
        terminal = build(fake_mt5, symbol_cache_ttl=60.0)

        for _ in range(5):
            terminal.symbol_spec("US100Cash")
        assert calls["n"] == 1  # hot quote path must not hammer the terminal

    def test_unknown_symbol_raises(self, fake_mt5: FakeMT5) -> None:
        terminal = build(fake_mt5)
        with pytest.raises(SymbolUnavailable):
            terminal.symbol_spec("NOPE")


class TestFillingMode:
    """Bit values here are the real MQL5 SYMBOL_FILLING_MODE flags (1=FOK,
    2=IOC) - not module attributes, since the real MetaTrader5 package does
    not expose SYMBOL_FILLING_FOK/IOC as constants. This distinction is the
    whole point of these tests: an earlier version of the fake exposed those
    as convenience attributes, which let every test pass while the same
    code crashed with AttributeError against the real broker."""

    def test_prefers_fok_when_supported(self, fake_mt5: FakeMT5) -> None:
        fake_mt5.symbol_info_obj.filling_mode = 1  # FOK bit
        terminal = build(fake_mt5)
        assert terminal.resolve_filling_mode("US100Cash") == fake_mt5.ORDER_FILLING_FOK

    def test_falls_back_to_ioc(self, fake_mt5: FakeMT5) -> None:
        fake_mt5.symbol_info_obj.filling_mode = 2  # IOC bit
        terminal = build(fake_mt5)
        assert terminal.resolve_filling_mode("US100Cash") == fake_mt5.ORDER_FILLING_IOC

    def test_falls_back_to_return_when_broker_advertises_neither(
        self, fake_mt5: FakeMT5
    ) -> None:
        fake_mt5.symbol_info_obj.filling_mode = 0
        terminal = build(fake_mt5)
        assert terminal.resolve_filling_mode("US100Cash") == fake_mt5.ORDER_FILLING_RETURN

    def test_resolving_never_touches_a_symbol_filling_attribute(
        self, fake_mt5: FakeMT5
    ) -> None:
        """Regression for the production AttributeError: asserts the real
        module's attribute surface (no SYMBOL_FILLING_* here) is sufficient -
        deleting them from the fake must not break resolve_filling_mode."""
        assert not hasattr(fake_mt5, "SYMBOL_FILLING_FOK")
        assert not hasattr(fake_mt5, "SYMBOL_FILLING_IOC")
        fake_mt5.symbol_info_obj.filling_mode = 1
        terminal = build(fake_mt5)
        terminal.resolve_filling_mode("US100Cash")  # must not raise


class TestTick:
    def test_missing_tick_raises_symbol_unavailable(self, fake_mt5: FakeMT5) -> None:
        fake_mt5.tick_is_none = True
        terminal = build(fake_mt5)
        with pytest.raises(SymbolUnavailable):
            terminal.tick("US100Cash")

    def test_symbol_is_selected_before_the_first_tick_request(
        self, fake_mt5: FakeMT5
    ) -> None:
        """Regression: a symbol that exists on the broker but was never
        selected into Market Watch makes MT5 return no tick at all
        (error -4, 'Terminal: Not found') - this bit US100Cash for real.
        `tick()` must select the symbol before asking for its price, not after."""
        fake_mt5.require_select_before_tick = True
        assert "US100Cash" not in fake_mt5.selected_symbols  # not selected yet

        terminal = build(fake_mt5)
        result = terminal.tick("US100Cash")  # must succeed, not raise

        assert result.bid == fake_mt5.tick_obj.bid
        assert "US100Cash" in fake_mt5.selected_symbols

    def test_candles_also_select_the_symbol_first(self, fake_mt5: FakeMT5) -> None:
        fake_mt5.require_select_before_tick = True
        terminal = build(fake_mt5)
        terminal.candles("US100Cash", fake_mt5.TIMEFRAME_M15, 3)
        assert "US100Cash" in fake_mt5.selected_symbols
