"""The one and only place that touches the native MetaTrader5 API.

Two design decisions worth calling out:

1. The `MetaTrader5` module is *injected*, not imported at module scope.
   The real package is Windows-only (COM/DLL), so importing it at the top of
   this file would make the entire codebase unimportable on Linux - including
   the test suite. Injection buys us a fake terminal in tests for free.

2. A single re-entrant lock wraps native calls, because the MT5 client library
   is not safe to call from multiple threads. FastAPI still serves requests
   concurrently; only the short native section serialises. Read-mostly symbol
   metadata is cached with a TTL so the lock is not a bottleneck on the hot
   quote path.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from types import ModuleType
from typing import Any, Callable, Iterator
from contextlib import contextmanager

from .exceptions import (
    OrderRejected,
    PositionNotFound,
    SymbolUnavailable,
    TerminalConnectionError,
)
from .models import Candle, OrderSide, Position, Tick

log = logging.getLogger(__name__)


def load_mt5_module() -> ModuleType:
    """Import the real terminal binding. Fails clearly on the wrong platform."""
    try:
        import MetaTrader5  # noqa: PLC0415  (intentionally deferred)
    except ImportError as exc:  # pragma: no cover - platform specific
        raise TerminalConnectionError(
            "The 'MetaTrader5' package is unavailable. It is Windows-only and "
            "must run as native Windows Python, not inside WSL."
        ) from exc
    return MetaTrader5


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    """Immutable-per-session broker facts about a symbol."""

    name: str
    digits: int
    point: float
    volume_min: float
    volume_max: float
    volume_step: float
    filling_mode: int


class _TtlCache:
    """Tiny TTL cache. Avoids pulling in a dependency for ~20 lines."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._data: dict[str, tuple[Any, float]] = {}

    def get_or_set(self, key: str, factory: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and self._clock() - hit[1] < self._ttl:
                return hit[0]
        # Built outside the lock: the factory may block on terminal I/O. Two
        # racing threads may both load; last write wins, which is harmless for
        # the idempotent lookups cached here.
        value = factory()
        with self._lock:
            # Stamped *after* the build, so a slow factory still gets a full TTL.
            self._data[key] = (value, self._clock())
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class MT5Terminal:
    """A thread-safe, reconnecting facade over the native terminal."""

    def __init__(
        self,
        mt5: ModuleType,
        *,
        login: int,
        password: str,
        server: str,
        terminal_path: str | None = None,
        retries: int = 3,
        backoff_sec: float = 1.5,
        symbol_cache_ttl: float = 30.0,
        connection_check_interval: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mt5 = mt5
        self._login = login
        self._password = password
        self._server = server
        self._path = terminal_path
        self._retries = retries
        self._backoff = backoff_sec
        self._sleep = sleep
        self._clock = clock
        self._connection_check_interval = connection_check_interval

        self._connected = False
        self._last_verified = 0.0
        self._connect_lock = threading.Lock()
        self._call_lock = threading.RLock()
        self._symbols = _TtlCache(symbol_cache_ttl)

    # -- lifecycle ---------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        """Cheap on the common path: trusts the last real verification
        within `connection_check_interval` seconds instead of paying for a
        native `terminal_info()` round-trip on every call. `_session()` (and
        therefore every single bridge operation -- tick, candles, account,
        positions, send_order) calls `connect()`, which calls this, so an
        "always re-verify" implementation silently doubled the native IPC
        round-trips for every request, including live order execution: the
        one operation where added latency matters most. A genuinely dead
        terminal is still caught -- not by this check re-verifying instantly,
        but because the actual operation's own native call (symbol_info_tick,
        order_send, ...) already fails cleanly and raises when the terminal
        is truly gone, which every caller already handles. This just stops
        re-confirming liveness on every request when nothing has changed.
        """
        if not self._connected:
            return False
        if self._clock() - self._last_verified < self._connection_check_interval:
            return True
        try:
            # `terminal_info` is a native call, so it takes the call lock like
            # every other one - a health probe racing an order_send would
            # otherwise hit the non-thread-safe MT5 library concurrently.
            with self._call_lock:
                alive = self._mt5.terminal_info() is not None
        except Exception:  # noqa: BLE001 - a dead terminal can raise anything
            alive = False
        if alive:
            self._last_verified = self._clock()
        else:
            self._connected = False
        return alive

    def connect(self) -> None:
        """Idempotent. Retries with linear backoff before giving up."""
        with self._connect_lock:
            if self.is_connected:
                return

            last_error: Any = None
            for attempt in range(1, self._retries + 1):
                kwargs = {"path": self._path} if self._path else {}
                with self._call_lock:
                    ok = self._mt5.initialize(**kwargs)
                    if ok:
                        ok = self._mt5.login(
                            self._login, password=self._password, server=self._server
                        )
                    if not ok:
                        last_error = self._mt5.last_error()
                if ok:
                    self._connected = True
                    self._last_verified = self._clock()
                    self._symbols.clear()  # broker specs may differ per session
                    log.info(
                        "connected to MT5 (login=%s server=%s attempt=%d)",
                        self._login,
                        self._server,
                        attempt,
                    )
                    return

                log.warning(
                    "MT5 connect attempt %d/%d failed: %s", attempt, self._retries, last_error
                )
                # `initialize` may have succeeded even though `login` failed;
                # tear the half-open session down before retrying so failed
                # attempts do not leak IPC connections into the terminal.
                with self._call_lock:
                    try:
                        self._mt5.shutdown()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass
                if attempt < self._retries:
                    self._sleep(self._backoff * attempt)

            self._connected = False
            raise TerminalConnectionError(
                f"could not connect to MT5 after {self._retries} attempts: {last_error}"
            )

    def shutdown(self) -> None:
        with self._connect_lock:
            if self._connected:
                with self._call_lock:
                    self._mt5.shutdown()
                self._connected = False

    @contextmanager
    def _session(self) -> Iterator[None]:
        """Guarantees a live connection, then serialises the native call."""
        self.connect()
        with self._call_lock:
            yield

    # -- symbol metadata ---------------------------------------------------
    def symbol_spec(self, symbol: str) -> SymbolSpec:
        """Cached: broker specs do not change intraday, but quotes do."""

        def _load() -> SymbolSpec:
            with self._session():
                # `symbol_select` is required for symbols hidden from Market Watch.
                self._mt5.symbol_select(symbol, True)
                info = self._mt5.symbol_info(symbol)
            if info is None:
                raise SymbolUnavailable(f"terminal does not expose symbol {symbol!r}")
            return SymbolSpec(
                name=info.name,
                digits=info.digits,
                point=info.point,
                volume_min=info.volume_min,
                volume_max=info.volume_max,
                volume_step=info.volume_step,
                filling_mode=info.filling_mode,
            )

        return self._symbols.get_or_set(symbol, _load)

    def resolve_filling_mode(self, symbol: str) -> int:
        """Pick a filling mode the broker actually accepts for this symbol.

        Hard-coding IOC is the single most common cause of silently rejected
        orders on brokers such as XM, so we negotiate it instead of guessing.

        The bit values below are NOT read from `mt5.SYMBOL_FILLING_FOK` /
        `mt5.SYMBOL_FILLING_IOC` - the Python MetaTrader5 package does not
        expose those as module attributes (only `ORDER_FILLING_*`, used for
        the outgoing request, exist). Referencing the non-existent symbol-side
        constants caused a real `AttributeError` in production - every single
        execute call crashed with it, 100% reproducible - while it went
        completely undetected in tests because the fake MT5 module used for
        testing had defined those same constants itself. The bit values here
        are documented directly by MetaQuotes for `SYMBOL_FILLING_MODE`:
        bit 0 (1) = FOK supported, bit 1 (2) = IOC supported.
        """
        _FOK_BIT = 1
        _IOC_BIT = 2

        mask = self.symbol_spec(symbol).filling_mode
        mt5 = self._mt5
        if mask & _FOK_BIT:
            return mt5.ORDER_FILLING_FOK
        if mask & _IOC_BIT:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def round_price(self, symbol: str, price: float) -> float:
        return round(price, self.symbol_spec(symbol).digits)

    # -- market data -------------------------------------------------------
    def tick(self, symbol: str) -> Tick:
        # `symbol_spec` selects the symbol into Market Watch as a side effect
        # (cached, so repeated calls are cheap). MT5 returns "Terminal: Not
        # found" (-4) from `symbol_info_tick` for a symbol that exists on the
        # broker but was never selected - the select must happen first.
        digits = self.symbol_spec(symbol).digits
        with self._session():
            raw = self._mt5.symbol_info_tick(symbol)
            # `last_error` is itself a native call - read it inside the locked
            # session, or a concurrent call could overwrite it (or race the
            # library) before we get here.
            error = self._mt5.last_error() if raw is None else None
        if raw is None:
            raise SymbolUnavailable(f"no live tick for {symbol!r}: {error}")
        return Tick(
            symbol=symbol,
            bid=raw.bid,
            ask=raw.ask,
            spread=round(raw.ask - raw.bid, digits),
            time=datetime.fromtimestamp(raw.time, tz=timezone.utc),
        )

    def candles(self, symbol: str, timeframe: int, count: int) -> list[Candle]:
        """Feeds the technical-analysis layer. Newest bar last."""
        self.symbol_spec(symbol)  # ensure selected first, same reasoning as tick()
        with self._session():
            rows = self._mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rows is None or len(rows) == 0:
            raise SymbolUnavailable(f"no candle history for {symbol!r}")
        return [
            Candle(
                time=datetime.fromtimestamp(int(r["time"]), tz=timezone.utc),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                tick_volume=int(r["tick_volume"]),
            )
            for r in rows
        ]

    def account(self) -> dict[str, Any]:
        with self._session():
            info = self._mt5.account_info()
            error = self._mt5.last_error() if info is None else None
        if info is None:
            raise TerminalConnectionError(f"account_info failed: {error}")
        return info._asdict()

    # -- positions ---------------------------------------------------------
    def positions(self, symbol: str | None = None) -> list[Position]:
        with self._session():
            raw = self._mt5.positions_get(symbol=symbol) if symbol else self._mt5.positions_get()
        return [self._to_position(p) for p in (raw or [])]

    def position_by_ticket(self, ticket: int) -> Position:
        with self._session():
            raw = self._mt5.positions_get(ticket=ticket)
        if not raw:
            raise PositionNotFound(f"no open position with ticket {ticket}")
        return self._to_position(raw[0])

    def _to_position(self, raw: Any) -> Position:
        is_buy = raw.type == self._mt5.POSITION_TYPE_BUY
        return Position(
            ticket=raw.ticket,
            symbol=raw.symbol,
            side=OrderSide.BUY if is_buy else OrderSide.SELL,
            volume=raw.volume,
            price_open=raw.price_open,
            price_current=raw.price_current,
            stop_loss=raw.sl or None,
            take_profit=raw.tp or None,
            profit=raw.profit,
            time_open=datetime.fromtimestamp(raw.time, tz=timezone.utc),
        )

    # -- order execution ---------------------------------------------------
    def send_order(self, request: dict[str, Any]) -> Any:
        """Submit and translate MT5's retcode into an exception on failure."""
        with self._session():
            result = self._mt5.order_send(request)
            failed = result is None or result.retcode != self._mt5.TRADE_RETCODE_DONE
            last_error = self._mt5.last_error() if failed else None

        if result is None:
            raise OrderRejected(f"order_send returned nothing: {last_error}")
        if result.retcode != self._mt5.TRADE_RETCODE_DONE:
            reason = getattr(result, "comment", "") or str(last_error)
            raise OrderRejected(
                f"broker rejected the order (retcode={result.retcode}): {reason}",
                retcode=result.retcode,
            )
        return result

    # -- constants passthrough --------------------------------------------
    def order_type_for(self, side: OrderSide) -> int:
        return self._mt5.ORDER_TYPE_BUY if side is OrderSide.BUY else self._mt5.ORDER_TYPE_SELL

    @property
    def constants(self) -> ModuleType:
        """Escape hatch for the few places that need raw MT5 enums."""
        return self._mt5
