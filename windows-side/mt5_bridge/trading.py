"""Trading use-cases. Knows nothing about HTTP, FastAPI, or environment variables.

Everything it needs arrives through the constructor, which is what makes the
whole flow unit-testable with fakes and reusable from a CLI or a scheduler.

The safety contract enforced here:

    preview(intent) -> confirm_token          (read-only, nothing sent)
    execute(intent + matching token)          (the only path that sends orders)

`execute` re-validates every limit rather than trusting that `preview` did,
because a token proves intent, not safety.
"""

from __future__ import annotations

import logging
from typing import Any

from .exceptions import (
    InvalidStopLevels,
    LiveAccountTradingBlocked,
    SymbolNotAllowed,
    VolumeOutOfRange,
)
from .guards import ConfirmTokenStore, OrderLimiter, ScaleInCooldownGuard, TokenPayload
from .journal import Journal, JournalEntry
from .models import (
    CloseResult,
    ExecuteOrderRequest,
    OrderIntent,
    OrderPreview,
    OrderResult,
    OrderSide,
)
from .terminal import MT5Terminal

log = logging.getLogger(__name__)


class TradingService:
    def __init__(
        self,
        terminal: MT5Terminal,
        *,
        token_store: ConfirmTokenStore,
        limiter: OrderLimiter,
        scale_in_guard: ScaleInCooldownGuard,
        journal: Journal,
        allowed_symbols: frozenset[str],
        max_lot_size: float,
        require_confirm_token: bool,
        confirm_token_ttl: int,
        deviation: int,
        magic_number: int,
        require_demo_account: bool = True,
    ) -> None:
        self._terminal = terminal
        self._tokens = token_store
        self._limiter = limiter
        self._scale_in_guard = scale_in_guard
        self._journal = journal
        self._allowed = allowed_symbols
        self._max_lot = max_lot_size
        self._require_token = require_confirm_token
        self._ttl = confirm_token_ttl
        self._deviation = deviation
        self._magic = magic_number
        self._require_demo_account = require_demo_account

    # -- validation --------------------------------------------------------
    def _assert_symbol_allowed(self, symbol: str) -> None:
        if symbol not in self._allowed:
            raise SymbolNotAllowed(symbol, self._allowed)

    def _assert_volume_valid(self, symbol: str, volume: float) -> None:
        """Check the configured ceiling *and* the broker's own step/min/max.

        Sending 0.013 lots to a broker with a 0.01 step is a silent rejection;
        catching it here produces an actionable error instead.
        """
        if volume > self._max_lot:
            raise VolumeOutOfRange(
                f"volume {volume} exceeds the configured ceiling of {self._max_lot}"
            )

        spec = self._terminal.symbol_spec(symbol)
        if not (spec.volume_min <= volume <= spec.volume_max):
            raise VolumeOutOfRange(
                f"volume {volume} outside broker range "
                f"[{spec.volume_min}, {spec.volume_max}] for {symbol}"
            )

        steps = round((volume - spec.volume_min) / spec.volume_step, 6)
        if abs(steps - round(steps)) > 1e-6:
            raise VolumeOutOfRange(
                f"volume {volume} is not a multiple of the broker step "
                f"{spec.volume_step} (min {spec.volume_min}) for {symbol}"
            )

    @staticmethod
    def _assert_stops_valid(
        side: OrderSide,
        entry: float,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> None:
        """A wrong-side SL/TP would come back from the broker as a bare
        'Invalid stops' rejection *after* submission; failing here instead
        names the offending level while nothing has been sent yet."""
        is_buy = side is OrderSide.BUY
        if stop_loss is not None and (stop_loss >= entry if is_buy else stop_loss <= entry):
            raise InvalidStopLevels(
                f"stop_loss {stop_loss} must be {'below' if is_buy else 'above'} "
                f"the {side.value} entry price {entry}"
            )
        if take_profit is not None and (take_profit <= entry if is_buy else take_profit >= entry):
            raise InvalidStopLevels(
                f"take_profit {take_profit} must be {'above' if is_buy else 'below'} "
                f"the {side.value} entry price {entry}"
            )

    def _entry_quote(
        self, symbol: str, side: OrderSide, reasoning: dict[str, Any] | None = None
    ) -> tuple[float, dict[str, Any]]:
        """One native tick call serves both the entry price and the journal
        context, so the audit trail always describes the exact quote used
        (and the terminal is hit once per operation instead of twice).

        `reasoning`, when the caller supplies one, is merged in under its own
        key - kept separate from the server-observed bid/ask/spread so a
        later reader (human or training pipeline) can always tell which half
        of the record is an objective fact and which half is the agent's own
        account of why it acted. This is the entire raw material a future
        model would learn from; nothing here is optional if the goal is ever
        to explain a loss rather than just log one.
        """
        tick = self._terminal.tick(symbol)
        price = tick.ask if side is OrderSide.BUY else tick.bid
        context: dict[str, Any] = {"bid": tick.bid, "ask": tick.ask, "spread": tick.spread}
        if reasoning:
            context["reasoning"] = reasoning
        return price, context

    # -- use-cases ---------------------------------------------------------
    def preview(self, intent: OrderIntent) -> OrderPreview:
        """Read-only. Validates, quotes, and mints a single-use token."""
        self._assert_symbol_allowed(intent.symbol)
        self._assert_volume_valid(intent.symbol, intent.volume)

        price, context = self._entry_quote(intent.symbol, intent.side, intent.reasoning)
        self._assert_stops_valid(intent.side, price, intent.stop_loss, intent.take_profit)
        token = self._tokens.issue(
            TokenPayload(symbol=intent.symbol, side=intent.side.value, volume=intent.volume)
        )

        self._journal.record(
            JournalEntry(
                event="preview",
                symbol=intent.symbol,
                side=intent.side.value,
                volume=intent.volume,
                price=price,
                stop_loss=intent.stop_loss,
                take_profit=intent.take_profit,
                context=context,
            )
        )
        return OrderPreview(
            symbol=intent.symbol,
            side=intent.side,
            volume=intent.volume,
            estimated_price=price,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            confirm_token=token,
            expires_in_seconds=self._ttl,
        )

    def _is_demo_account(self) -> bool:
        """MT5 reports 0 = demo, 1 = contest, 2 = real.

        Anything unreadable is treated as *not* demo, so the live-only guard
        fails safe (active) rather than silently switching itself off.
        """
        try:
            return int(self._terminal.account().get("trade_mode", -1)) == 0
        except (TypeError, ValueError):
            return False

    def execute(self, request: ExecuteOrderRequest) -> OrderResult:
        """The only method in this codebase that can move real money."""
        self._assert_symbol_allowed(request.symbol)
        self._assert_volume_valid(request.symbol, request.volume)

        # Both checks below need to know whether this is a demo account;
        # resolved once and reused so a live-account execute doesn't pay
        # for account() twice, and a demo execute (the common case) skips
        # the scale-in guard's positions() round-trip entirely.
        is_demo = self._is_demo_account()

        if self._require_demo_account and not is_demo:
            # Checked before token redemption and quota consumption, same
            # as symbol/volume above: a blocked attempt must not burn
            # either. Never applies to close() - see LiveAccountTradingBlocked.
            raise LiveAccountTradingBlocked()

        # Scale-in cooldown: live accounts only, demo is exempt by design.
        if not is_demo:
            self._scale_in_guard.check(
                symbol=request.symbol,
                is_demo=False,
                existing_positions=self._terminal.positions(request.symbol),
            )

        if self._require_token:
            self._tokens.redeem(
                request.confirm_token or "",
                TokenPayload(
                    symbol=request.symbol,
                    side=request.side.value,
                    volume=request.volume,
                ),
            )

        price, context = self._entry_quote(request.symbol, request.side, request.reasoning)
        self._assert_stops_valid(request.side, price, request.stop_loss, request.take_profit)
        payload = self._build_order_payload(request, price)

        # Quota is consumed last, when an order is genuinely about to be sent.
        # A rejected confirmation, a failed quote, or wrong-side stops must
        # not burn the day's budget.
        self._limiter.check_and_increment()

        try:
            result = self._terminal.send_order(payload)
        except Exception as exc:
            self._journal.record(
                JournalEntry(
                    event="rejected",
                    symbol=request.symbol,
                    side=request.side.value,
                    volume=request.volume,
                    price=price,
                    stop_loss=request.stop_loss,
                    take_profit=request.take_profit,
                    retcode=getattr(exc, "retcode", None),
                    error=str(exc),
                    context=context,
                )
            )
            raise

        self._journal.record(
            JournalEntry(
                event="executed",
                symbol=request.symbol,
                side=request.side.value,
                volume=result.volume,
                price=result.price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                ticket=result.order,
                retcode=result.retcode,
                context=context,
            )
        )
        log.info(
            "order executed symbol=%s side=%s volume=%s price=%s ticket=%s",
            request.symbol,
            request.side.value,
            result.volume,
            result.price,
            result.order,
        )
        return OrderResult(
            retcode=result.retcode,
            order=result.order,
            deal=result.deal,
            price=result.price,
            volume=result.volume,
            symbol=request.symbol,
            side=request.side,
        )

    def _build_order_payload(self, request: OrderIntent, price: float) -> dict[str, Any]:
        mt5 = self._terminal.constants
        payload: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": request.volume,
            "type": self._terminal.order_type_for(request.side),
            "price": price,
            "deviation": self._deviation,
            "magic": self._magic,
            "comment": request.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._terminal.resolve_filling_mode(request.symbol),
        }
        if request.stop_loss is not None:
            payload["sl"] = self._terminal.round_price(request.symbol, request.stop_loss)
        if request.take_profit is not None:
            payload["tp"] = self._terminal.round_price(request.symbol, request.take_profit)
        return payload

    # -- closing -----------------------------------------------------------
    def preview_close(self, ticket: int) -> OrderPreview:
        position = self._terminal.position_by_ticket(ticket)
        closing_side = position.side.opposite
        price, _ = self._entry_quote(position.symbol, closing_side)

        # The ticket is part of the payload: a token minted to close one
        # position must not be redeemable against another position that
        # happens to share the same symbol and volume.
        token = self._tokens.issue(
            TokenPayload(
                symbol=position.symbol,
                side="close",
                volume=position.volume,
                ticket=position.ticket,
            )
        )
        return OrderPreview(
            symbol=position.symbol,
            side=closing_side,
            volume=position.volume,
            estimated_price=price,
            stop_loss=None,
            take_profit=None,
            confirm_token=token,
            expires_in_seconds=self._ttl,
        )

    def close(self, ticket: int, confirm_token: str | None) -> CloseResult:
        # No require_demo_account check here, deliberately - see
        # LiveAccountTradingBlocked's docstring. A position on a live
        # account must always be closeable through this bridge, even if
        # require_demo_account is true; refusing to close it would strand
        # an open position in the market instead of protecting anything.
        position = self._terminal.position_by_ticket(ticket)

        if self._require_token:
            self._tokens.redeem(
                confirm_token or "",
                TokenPayload(
                    symbol=position.symbol,
                    side="close",
                    volume=position.volume,
                    ticket=position.ticket,
                ),
            )

        mt5 = self._terminal.constants
        closing_side = position.side.opposite
        price, context = self._entry_quote(position.symbol, closing_side)

        payload = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": self._terminal.order_type_for(closing_side),
            "position": position.ticket,
            "price": price,
            "deviation": self._deviation,
            "magic": self._magic,
            "comment": "mt5-guardrail-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._terminal.resolve_filling_mode(position.symbol),
        }
        # Closing reduces exposure, so it deliberately bypasses the daily
        # order limiter - a tripped circuit breaker must never trap an open
        # position in the market.
        try:
            result = self._terminal.send_order(payload)
        except Exception as exc:
            # Failed closes are journalled too: "I could not get out" is
            # exactly the kind of event a later reflection pass must see.
            self._journal.record(
                JournalEntry(
                    event="rejected",
                    symbol=position.symbol,
                    side=position.side.value,
                    volume=position.volume,
                    price=price,
                    ticket=position.ticket,
                    retcode=getattr(exc, "retcode", None),
                    error=str(exc),
                    context=context,
                )
            )
            raise

        self._journal.record(
            JournalEntry(
                event="closed",
                symbol=position.symbol,
                side=position.side.value,
                volume=position.volume,
                price=result.price,
                ticket=position.ticket,
                retcode=result.retcode,
                profit=position.profit,
                context=context,
            )
        )
        return CloseResult(
            retcode=result.retcode,
            closed_ticket=position.ticket,
            price=result.price,
            profit=position.profit,
        )
