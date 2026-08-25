"""A fake MetaTrader5 module.

Its existence is the payoff of injecting the terminal binding: the entire
suite - including the HTTP layer - runs on Linux, in CI, with no broker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# --- constants mirrored from the real package -----------------------------
# IMPORTANT: only define attributes that genuinely exist on the real
# MetaTrader5 module. A prior version of this fake defined SYMBOL_FILLING_FOK
# and SYMBOL_FILLING_IOC here "for convenience" - the real package has no
# such attributes (only ORDER_FILLING_* exist, used for the outgoing order
# request). That single fictional convenience let every test pass while
# production crashed with AttributeError on every real execute call. If it
# is not a real attribute of the real module, it does not belong here.
TRADE_ACTION_DEAL = 1
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_REJECT = 10006
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H4 = 16388
TIMEFRAME_D1 = 16408

# Raw SYMBOL_FILLING_MODE bitmask bits (MQL5 spec, not module attributes -
# there is no Python-side constant for these on the real module either).
_SYMBOL_FILLING_FOK_BIT = 1
_SYMBOL_FILLING_IOC_BIT = 2


@dataclass
class _SymbolInfo:
    name: str = "US100Cash"
    digits: int = 2
    point: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 50.0
    volume_step: float = 0.01
    filling_mode: int = _SYMBOL_FILLING_IOC_BIT


@dataclass
class _Tick:
    bid: float = 19_900.0
    ask: float = 19_900.5
    time: int = field(default_factory=lambda: int(time.time()))


@dataclass
class _OrderResult:
    retcode: int = TRADE_RETCODE_DONE
    order: int = 555_001
    deal: int = 777_001
    price: float = 19_900.5
    volume: float = 0.01
    comment: str = "done"


@dataclass
class _Position:
    ticket: int = 555_001
    symbol: str = "US100Cash"
    type: int = POSITION_TYPE_BUY
    volume: float = 0.01
    price_open: float = 19_900.5
    price_current: float = 19_950.0
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 49.5
    time: float = 0.0


class _AccountInfo:
    def __init__(self, trade_mode: int = 0) -> None:
        # 0 = ACCOUNT_TRADE_MODE_DEMO, 2 = ACCOUNT_TRADE_MODE_REAL
        self.trade_mode = trade_mode

    def _asdict(self) -> dict[str, Any]:
        return {
            "login": 318_285_067,
            "currency": "USD",
            "balance": 10_000.0,
            "equity": 10_049.5,
            "margin": 200.0,
            "margin_free": 9_849.5,
            "leverage": 500,
            "trade_mode": self.trade_mode,
        }


class RecordingJournal:
    """Collects every entry passed to `record()` for assertions. Unlike
    `NullJournal` (which exists to keep callers branch-free when
    journalling is off), this exists specifically so tests can verify
    *what* got journalled -- event type, price, retcode, whether a
    rejection was recorded distinctly from a fill -- not just that
    `execute()` returned successfully.
    """

    def __init__(self) -> None:
        self.entries: list[Any] = []

    def record(self, entry: Any) -> None:
        self.entries.append(entry)

    def recent(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:  # noqa: ARG002
        return []


class FakeMT5:
    """Stateful enough to exercise real flows; dumb enough to stay readable."""

    def __init__(self) -> None:
        self.initialized = False
        self.logged_in = False
        self.symbol_info_obj = _SymbolInfo()
        self.tick_obj = _Tick()
        self.positions: list[_Position] = []
        self.sent_orders: list[dict[str, Any]] = []
        self.selected_symbols: set[str] = set()
        self.trade_mode = 0  # tests flip to 2 (real) to exercise the live-only guard

        # knobs for failure-path tests
        self.fail_login = False
        self.reject_orders = False
        self.tick_is_none = False
        self.require_select_before_tick = False

        # mirror the constants so `terminal.constants` works unchanged
        for name, value in globals().items():
            if name.isupper():
                setattr(self, name, value)

    # -- lifecycle
    def initialize(self, **_: Any) -> bool:
        self.initialized = True
        return True

    def login(self, login: int, password: str, server: str) -> bool:  # noqa: ARG002
        self.logged_in = not self.fail_login
        return self.logged_in

    def shutdown(self) -> None:
        self.initialized = False
        self.logged_in = False

    def terminal_info(self) -> object | None:
        return object() if self.logged_in else None

    def last_error(self) -> tuple[int, str]:
        return (-1, "fake error")

    # -- market data
    def symbol_select(self, symbol: str, enable: bool) -> bool:
        if enable:
            self.selected_symbols.add(symbol)
        else:
            self.selected_symbols.discard(symbol)
        return True

    def symbol_info(self, symbol: str) -> _SymbolInfo | None:
        return self.symbol_info_obj if symbol == self.symbol_info_obj.name else None

    def symbol_info_tick(self, symbol: str) -> _Tick | None:
        if self.tick_is_none:
            return None
        # Mirrors real MT5: a symbol that exists but was never selected into
        # Market Watch returns no tick, error (-4, "Terminal: Not found").
        if self.require_select_before_tick and symbol not in self.selected_symbols:
            return None
        return self.tick_obj

    def copy_rates_from_pos(self, symbol: str, tf: int, start: int, count: int) -> list[dict]:  # noqa: ARG002
        base = int(time.time())
        return [
            {
                "time": base + i * 60,
                "open": 19_900.0 + i,
                "high": 19_910.0 + i,
                "low": 19_890.0 + i,
                "close": 19_905.0 + i,
                "tick_volume": 100 + i,
            }
            for i in range(count)
        ]

    def account_info(self) -> _AccountInfo:
        return _AccountInfo(self.trade_mode)

    # -- positions & orders
    def positions_get(self, **kwargs: Any) -> tuple[_Position, ...]:
        ticket = kwargs.get("ticket")
        symbol = kwargs.get("symbol")
        found = self.positions
        if ticket is not None:
            found = [p for p in found if p.ticket == ticket]
        if symbol is not None:
            found = [p for p in found if p.symbol == symbol]
        return tuple(found)

    def order_send(self, request: dict[str, Any]) -> _OrderResult:
        self.sent_orders.append(request)
        if self.reject_orders:
            return _OrderResult(retcode=TRADE_RETCODE_REJECT, comment="Unsupported filling mode")
        return _OrderResult(volume=request["volume"], price=request["price"])
