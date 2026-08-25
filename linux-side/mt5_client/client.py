"""The public facade. Thin: every method maps to one endpoint.

Trading safety is enforced *server-side*, so this client cannot weaken it even
if misused. What it adds is ergonomics: typed results, a discoverable base URL,
and errors an agent can branch on.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, Literal

from .config import ClientConfig, candidate_urls
from .exceptions import BridgeUnavailable
from .models import (
    AccountSnapshot,
    Candle,
    CloseResult,
    Health,
    OrderPreview,
    OrderResult,
    Position,
    SymbolSpec,
    Tick,
)
from .transport import Transport

log = logging.getLogger(__name__)

Side = Literal["buy", "sell"]
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class BridgeClient:
    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        self._transport = Transport(config)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_env(cls) -> BridgeClient:
        return cls(ClientConfig.from_env())

    @classmethod
    def autodiscover(cls, api_key: str, port: int = 8787, **kwargs: Any) -> BridgeClient:
        """Probe every plausible WSL->Windows URL and keep the first that answers.

        Saves the user from hand-editing /etc/resolv.conf lookups when their WSL
        networking mode changes.
        """
        errors: list[str] = []
        # Probing uses a short timeout so dead candidates fail fast; the kept
        # client is built from the caller's own kwargs. Overriding via a merged
        # dict (rather than a `timeout=` keyword) keeps a caller-supplied
        # `timeout` in kwargs from raising a duplicate-argument TypeError.
        probe_kwargs = {**kwargs, "timeout": 2.0}
        for url in candidate_urls(port):
            client = cls(ClientConfig(base_url=url, api_key=api_key, **probe_kwargs))
            try:
                if client.health().is_ready:
                    log.info("bridge discovered at %s", url)
                    return cls(ClientConfig(base_url=url, api_key=api_key, **kwargs))
            except Exception as exc:  # noqa: BLE001 - probing: any failure means "next"
                errors.append(f"{url}: {type(exc).__name__}")
            finally:
                client.close()

        raise BridgeUnavailable(
            "no MT5 bridge answered on any candidate URL.\n  "
            + "\n  ".join(errors)
            + "\nStart the Windows-side server, then check the Windows firewall."
        )

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> BridgeClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._transport.close()

    # -- read-only ---------------------------------------------------------
    def health(self) -> Health:
        return Health.from_dict(self._transport.get("/health"))

    def account(self) -> AccountSnapshot:
        return AccountSnapshot.from_dict(self._transport.get("/account"))

    def positions(self, symbol: str | None = None) -> list[Position]:
        params = {"symbol": symbol} if symbol else None
        rows = self._transport.get("/positions", params=params)
        return [Position.from_dict(row) for row in rows]

    def tick(self, symbol: str) -> Tick:
        return Tick.from_dict(self._transport.get(f"/market/{symbol}/tick"))

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        """Broker sizing limits. Call before preview_order to avoid guessing
        a volume that gets rejected (min lot sizes vary a lot by broker)."""
        return SymbolSpec.from_dict(self._transport.get(f"/market/{symbol}/spec"))

    def candles(self, symbol: str, timeframe: Timeframe = "M15", count: int = 100) -> list[Candle]:
        rows = self._transport.get(
            f"/market/{symbol}/candles", params={"timeframe": timeframe, "count": count}
        )
        return [Candle.from_dict(row) for row in rows]

    def journal(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Past trades, including rejections. The raw material for reflection."""
        params: dict[str, Any] = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        return self._transport.get("/journal/recent", params=params)

    # -- trading (two-step by design) --------------------------------------
    def preview_order(
        self,
        symbol: str,
        side: Side,
        volume: float,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "mt5-guardrail",
        reasoning: dict[str, object] | None = None,
    ) -> OrderPreview:
        """Validates and quotes. Sends nothing to the broker.

        `reasoning` is optional but not free to skip: it is the only place
        the caller's decision context (signal source, indicators referenced,
        confidence) reaches the journal. MT5 itself never records why an
        order was sent, only that it was - anything not passed here is lost
        the moment this call returns.
        """
        body = {
            "symbol": symbol,
            "side": side,
            "volume": volume,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "comment": comment,
            "reasoning": reasoning,
        }
        return OrderPreview.from_dict(self._transport.post("/order/preview", body))

    def execute_order(
        self,
        symbol: str,
        side: Side,
        volume: float,
        confirm_token: str,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "mt5-guardrail",
        reasoning: dict[str, object] | None = None,
    ) -> OrderResult:
        """Requires a token from `preview_order` describing this exact order.

        Call this only after a human has approved the previewed action.
        `reasoning` here is independent of whatever was passed to the
        matching preview - pass it again if it should be attached to the
        executed-order journal row too.
        """
        body = {
            "symbol": symbol,
            "side": side,
            "volume": volume,
            "confirm_token": confirm_token,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "comment": comment,
            "reasoning": reasoning,
        }
        return OrderResult.from_dict(self._transport.post("/order/execute", body))

    def preview_close(self, ticket: int) -> OrderPreview:
        return OrderPreview.from_dict(
            self._transport.post("/position/close/preview", {"ticket": ticket})
        )

    def close_position(self, ticket: int, confirm_token: str) -> CloseResult:
        return CloseResult.from_dict(
            self._transport.post(
                "/position/close", {"ticket": ticket, "confirm_token": confirm_token}
            )
        )
