"""TradingService: the only code in this bridge that can move real money.

Every safety property the module's own docstrings claim gets a test here --
not because the claims were doubted, but because a docstring is not a
guarantee, and this file previously had none at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_bridge.exceptions import (
    ConfirmTokenInvalid,
    DailyLimitReached,
    InvalidStopLevels,
    OrderRejected,
    PositionNotFound,
    ScaleInCooldownActive,
    SymbolNotAllowed,
    VolumeOutOfRange,
)
from mt5_bridge.guards import (
    InMemoryConfirmTokenStore,
    InMemoryOrderLimiter,
    ScaleInCooldownGuard,
)
from mt5_bridge.models import ExecuteOrderRequest, OrderIntent, OrderSide
from mt5_bridge.terminal import MT5Terminal
from mt5_bridge.trading import TradingService
from tests.fakes import FakeMT5, RecordingJournal

SYMBOL = "US100Cash"


def build_service(
    fake_mt5: FakeMT5,
    *,
    journal: RecordingJournal | None = None,
    max_lot_size: float = 1.0,
    max_daily_orders: int = 3,
    require_confirm_token: bool = True,
    confirm_token_ttl: int = 60,
    cooldown_seconds: int = 900,
) -> tuple[TradingService, RecordingJournal]:
    terminal = MT5Terminal(fake_mt5, login=1, password="pw", server="Fake", sleep=lambda _: None)
    recorded = journal if journal is not None else RecordingJournal()
    service = TradingService(
        terminal,
        token_store=InMemoryConfirmTokenStore(confirm_token_ttl),
        limiter=InMemoryOrderLimiter(max_daily_orders),
        scale_in_guard=ScaleInCooldownGuard(cooldown_seconds=cooldown_seconds),
        journal=recorded,
        allowed_symbols=frozenset({SYMBOL}),
        max_lot_size=max_lot_size,
        require_confirm_token=require_confirm_token,
        confirm_token_ttl=confirm_token_ttl,
        deviation=20,
        magic_number=123456,
    )
    return service, recorded


def buy_intent(**overrides: object) -> OrderIntent:
    base = dict(symbol=SYMBOL, side=OrderSide.BUY, volume=0.01, comment="test")
    return OrderIntent(**{**base, **overrides})  # type: ignore[arg-type]


def execute_request(confirm_token: str = "", **overrides: object) -> ExecuteOrderRequest:
    base = dict(
        symbol=SYMBOL,
        side=OrderSide.BUY,
        volume=0.01,
        comment="test",
        confirm_token=confirm_token,
        reasoning={
            "signal_source": "example breakout indicator",
            "agency_reference": "decision-log entry #1042",
        },
    )
    return ExecuteOrderRequest(**{**base, **overrides})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# preview()
# --------------------------------------------------------------------------
class TestPreview:
    def test_successful_preview_returns_a_token_and_the_live_quote(self, fake_mt5: FakeMT5) -> None:
        service, journal = build_service(fake_mt5)
        preview = service.preview(buy_intent())

        assert preview.confirm_token
        assert preview.estimated_price == fake_mt5.tick_obj.ask  # BUY fills at ask
        assert preview.symbol == SYMBOL
        assert preview.volume == 0.01

    def test_preview_journals_the_attempt(self, fake_mt5: FakeMT5) -> None:
        service, journal = build_service(fake_mt5)
        service.preview(buy_intent())

        assert len(journal.entries) == 1
        assert journal.entries[0].event == "preview"

    def test_symbol_not_allowed_is_rejected_before_any_terminal_call(
        self, fake_mt5: FakeMT5
    ) -> None:
        service, journal = build_service(fake_mt5)
        with pytest.raises(SymbolNotAllowed):
            service.preview(buy_intent(symbol="EURUSD"))
        assert fake_mt5.selected_symbols == set()  # never even reached the terminal
        assert journal.entries == []

    def test_volume_over_the_configured_ceiling_is_rejected(self, fake_mt5: FakeMT5) -> None:
        service, _ = build_service(fake_mt5, max_lot_size=0.05)
        with pytest.raises(VolumeOutOfRange, match="ceiling"):
            service.preview(buy_intent(volume=0.10))

    def test_volume_outside_the_brokers_own_range_is_rejected(self, fake_mt5: FakeMT5) -> None:
        fake_mt5.symbol_info_obj.volume_max = 5.0
        service, _ = build_service(fake_mt5, max_lot_size=100.0)
        with pytest.raises(VolumeOutOfRange, match=r"\[0.01, 5.0\]"):
            service.preview(buy_intent(volume=10.0))

    def test_volume_not_a_multiple_of_the_brokers_step_is_rejected(
        self, fake_mt5: FakeMT5
    ) -> None:
        fake_mt5.symbol_info_obj.volume_step = 0.01
        service, _ = build_service(fake_mt5)
        with pytest.raises(VolumeOutOfRange, match="multiple of the broker step"):
            service.preview(buy_intent(volume=0.013))

    def test_stop_loss_on_the_wrong_side_is_caught_before_submission(
        self, fake_mt5: FakeMT5
    ) -> None:
        """A BUY's stop must sit below entry; entry is fake_mt5.tick_obj.ask."""
        service, journal = build_service(fake_mt5)
        bad_sl = fake_mt5.tick_obj.ask + 1.0  # above entry for a BUY: wrong side
        with pytest.raises(InvalidStopLevels, match="below"):
            service.preview(buy_intent(stop_loss=bad_sl))
        # The quote was already fetched (needed to validate against), but
        # nothing should have been sent to the broker and nothing journalled
        # as a preview for an order that was never actually valid.
        assert fake_mt5.sent_orders == []

    def test_take_profit_on_the_wrong_side_is_caught_before_submission(
        self, fake_mt5: FakeMT5
    ) -> None:
        service, _ = build_service(fake_mt5)
        bad_tp = fake_mt5.tick_obj.ask - 1.0  # below entry for a BUY: wrong side
        with pytest.raises(InvalidStopLevels, match="above"):
            service.preview(buy_intent(take_profit=bad_tp))

    def test_sell_side_stop_validation_is_mirrored(self, fake_mt5: FakeMT5) -> None:
        """SELL fills at bid; a SELL's stop must sit ABOVE entry (the
        opposite of BUY) -- this direction is easy to get backwards, so it
        gets its own explicit test rather than trusting BUY coverage alone.
        """
        service, _ = build_service(fake_mt5)
        bad_sl_for_sell = fake_mt5.tick_obj.bid - 1.0  # below entry: wrong side for SELL
        with pytest.raises(InvalidStopLevels, match="above"):
            service.preview(buy_intent(side=OrderSide.SELL, stop_loss=bad_sl_for_sell))


# --------------------------------------------------------------------------
# execute()
# --------------------------------------------------------------------------
class TestExecute:
    def _valid_token(self, service: TradingService, **intent_overrides: object) -> str:
        return service.preview(buy_intent(**intent_overrides)).confirm_token

    def test_successful_execute_sends_the_order_and_journals_it_as_executed(
        self, fake_mt5: FakeMT5
    ) -> None:
        service, journal = build_service(fake_mt5)
        token = self._valid_token(service)

        result = service.execute(execute_request(confirm_token=token))

        assert result.retcode == fake_mt5.TRADE_RETCODE_DONE
        assert len(fake_mt5.sent_orders) == 1
        assert journal.entries[-1].event == "executed"
        assert journal.entries[-1].ticket == result.order

    def test_execute_without_a_token_is_rejected_when_confirm_token_required(
        self, fake_mt5: FakeMT5
    ) -> None:
        service, _ = build_service(fake_mt5, require_confirm_token=True)
        with pytest.raises(ConfirmTokenInvalid):
            service.execute(execute_request(confirm_token=""))
        assert fake_mt5.sent_orders == []  # nothing reached the broker

    def test_execute_works_without_a_token_when_confirm_token_not_required(
        self, fake_mt5: FakeMT5
    ) -> None:
        service, _ = build_service(fake_mt5, require_confirm_token=False)
        result = service.execute(execute_request(confirm_token=""))
        assert result.retcode == fake_mt5.TRADE_RETCODE_DONE

    def test_token_minted_for_a_different_volume_cannot_execute_this_order(
        self, fake_mt5: FakeMT5
    ) -> None:
        """Proves TokenPayload's field-by-field comparison actually works
        end-to-end: a token for 0.01 lots must not authorise 0.02 lots."""
        service, _ = build_service(fake_mt5)
        token = self._valid_token(service, volume=0.01)

        with pytest.raises(ConfirmTokenInvalid):
            service.execute(execute_request(confirm_token=token, volume=0.02))
        assert fake_mt5.sent_orders == []

    def test_token_is_single_use(self, fake_mt5: FakeMT5) -> None:
        service, _ = build_service(fake_mt5)
        token = self._valid_token(service)

        service.execute(execute_request(confirm_token=token))  # first use: fine
        with pytest.raises(ConfirmTokenInvalid):
            service.execute(execute_request(confirm_token=token))  # replay: rejected

    def test_daily_limit_is_enforced(self, fake_mt5: FakeMT5) -> None:
        service, _ = build_service(fake_mt5, max_daily_orders=1)
        service.execute(execute_request(confirm_token=self._valid_token(service)))

        with pytest.raises(DailyLimitReached):
            service.execute(execute_request(confirm_token=self._valid_token(service)))
        assert len(fake_mt5.sent_orders) == 1  # the rejected attempt never reached the broker

    def test_a_rejected_confirmation_does_not_consume_the_daily_quota(
        self, fake_mt5: FakeMT5
    ) -> None:
        """The module's own docstring promises this ('Quota is consumed
        last... A rejected confirmation... must not burn the day's
        budget') -- this proves it rather than trusting the comment."""
        service, _ = build_service(fake_mt5, max_daily_orders=1)

        with pytest.raises(ConfirmTokenInvalid):
            service.execute(execute_request(confirm_token="bogus-token"))

        # The quota must still be fully available for a real attempt.
        token = self._valid_token(service)
        result = service.execute(execute_request(confirm_token=token))
        assert result.retcode == fake_mt5.TRADE_RETCODE_DONE

    def test_broker_rejection_raises_and_journals_as_rejected_not_executed(
        self, fake_mt5: FakeMT5
    ) -> None:
        service, journal = build_service(fake_mt5)
        token = self._valid_token(service)
        fake_mt5.reject_orders = True

        with pytest.raises(OrderRejected):
            service.execute(execute_request(confirm_token=token))

        assert journal.entries[-1].event == "rejected"
        assert journal.entries[-1].event != "executed"

    def test_demo_account_skips_the_scale_in_guard_entirely(self, fake_mt5: FakeMT5) -> None:
        """Demo accounts keep unrestricted Auto-Execute by design. A
        second position on the same symbol, opened seconds after the
        first, must not be blocked on demo."""
        fake_mt5.trade_mode = 0  # demo
        service, _ = build_service(fake_mt5, max_daily_orders=5)
        fake_mt5.positions.append(_existing_position(seconds_ago=1))

        token = self._valid_token(service)
        result = service.execute(execute_request(confirm_token=token))  # must not raise
        assert result.retcode == fake_mt5.TRADE_RETCODE_DONE

    def test_live_account_blocks_scale_in_within_the_cooldown_window(
        self, fake_mt5: FakeMT5
    ) -> None:
        fake_mt5.trade_mode = 2  # real
        service, journal = build_service(fake_mt5, cooldown_seconds=900)
        fake_mt5.positions.append(_existing_position(seconds_ago=60))  # well within 900s

        token = self._valid_token(service)
        with pytest.raises(ScaleInCooldownActive):
            service.execute(execute_request(confirm_token=token))
        assert fake_mt5.sent_orders == []
        # Rejected by the guard before ever quoting/journalling this attempt.
        assert not any(e.event in ("executed", "rejected") for e in journal.entries)

    def test_live_account_allows_scale_in_once_the_cooldown_has_elapsed(
        self, fake_mt5: FakeMT5
    ) -> None:
        fake_mt5.trade_mode = 2  # real
        service, _ = build_service(fake_mt5, cooldown_seconds=900)
        fake_mt5.positions.append(_existing_position(seconds_ago=901))  # just past cooldown

        token = self._valid_token(service)
        result = service.execute(execute_request(confirm_token=token))  # must not raise
        assert result.retcode == fake_mt5.TRADE_RETCODE_DONE

    def test_live_account_scale_in_guard_only_looks_at_the_same_symbol(
        self, fake_mt5: FakeMT5
    ) -> None:
        fake_mt5.trade_mode = 2  # real
        service, _ = build_service(fake_mt5)
        other_symbol_position = _existing_position(seconds_ago=1)
        other_symbol_position.symbol = "EURUSD"
        fake_mt5.positions.append(other_symbol_position)

        token = self._valid_token(service)
        result = service.execute(execute_request(confirm_token=token))  # must not raise
        assert result.retcode == fake_mt5.TRADE_RETCODE_DONE


# --------------------------------------------------------------------------
# close() / preview_close()
# --------------------------------------------------------------------------
class TestClose:
    def test_preview_close_of_an_unknown_ticket_raises(self, fake_mt5: FakeMT5) -> None:
        service, _ = build_service(fake_mt5)
        with pytest.raises(PositionNotFound):
            service.preview_close(999_999)

    def test_close_uses_the_opposite_side_of_the_open_position(self, fake_mt5: FakeMT5) -> None:
        """A BUY position must be closed with a SELL order, and vice versa
        -- getting this backwards would open a NEW position instead of
        closing the existing one."""
        fake_mt5.positions.append(_existing_position(seconds_ago=0))
        service, _ = build_service(fake_mt5, require_confirm_token=False)

        service.close(fake_mt5.positions[0].ticket, confirm_token=None)

        assert fake_mt5.sent_orders[-1]["type"] == fake_mt5.ORDER_TYPE_SELL

    def test_close_bypasses_the_daily_order_limiter(self, fake_mt5: FakeMT5) -> None:
        """Docstring promise: 'a tripped circuit breaker must never trap an
        open position in the market' -- close must work even with an
        already-exhausted daily quota."""
        fake_mt5.positions.append(_existing_position(seconds_ago=0))
        service, _ = build_service(fake_mt5, max_daily_orders=1, require_confirm_token=False)

        service._limiter.check_and_increment()  # exhaust the quota via a normal open
        assert service._limiter.remaining() == 0

        result = service.close(fake_mt5.positions[0].ticket, confirm_token=None)  # must not raise
        assert result.closed_ticket == fake_mt5.positions[0].ticket

    def test_close_journals_as_closed(self, fake_mt5: FakeMT5) -> None:
        fake_mt5.positions.append(_existing_position(seconds_ago=0))
        service, journal = build_service(fake_mt5, require_confirm_token=False)

        service.close(fake_mt5.positions[0].ticket, confirm_token=None)
        assert journal.entries[-1].event == "closed"

    def test_failed_close_journals_as_rejected_not_closed(self, fake_mt5: FakeMT5) -> None:
        fake_mt5.positions.append(_existing_position(seconds_ago=0))
        fake_mt5.reject_orders = True
        service, journal = build_service(fake_mt5, require_confirm_token=False)

        with pytest.raises(OrderRejected):
            service.close(fake_mt5.positions[0].ticket, confirm_token=None)
        assert journal.entries[-1].event == "rejected"

    def test_close_requires_a_valid_token_when_confirm_token_is_required(
        self, fake_mt5: FakeMT5
    ) -> None:
        fake_mt5.positions.append(_existing_position(seconds_ago=0))
        service, _ = build_service(fake_mt5, require_confirm_token=True)

        with pytest.raises(ConfirmTokenInvalid):
            service.close(fake_mt5.positions[0].ticket, confirm_token=None)
        assert fake_mt5.sent_orders == []

    def test_a_close_token_cannot_be_redeemed_against_a_different_ticket(
        self, fake_mt5: FakeMT5
    ) -> None:
        """A token minted to close ticket A must not close ticket B, even
        if they share a symbol and volume -- the ticket itself is part of
        what the token authorises."""
        pos_a = _existing_position(seconds_ago=0)
        pos_a.ticket = 111
        pos_b = _existing_position(seconds_ago=0)
        pos_b.ticket = 222
        fake_mt5.positions.extend([pos_a, pos_b])
        service, _ = build_service(fake_mt5, require_confirm_token=True)

        token_for_a = service.preview_close(111).confirm_token
        with pytest.raises(ConfirmTokenInvalid):
            service.close(222, confirm_token=token_for_a)


class TestReasoningIsRequiredOnExecute:
    """This validation exists to close a real failure mode: an agent
    executing an order on a vague, unverifiable justification (see
    models.py's `_require_signal_source_and_agency_reference` for the
    full rationale). It had no test coverage at all -- exactly the kind
    of rule that regresses silently if models.py is ever refactored,
    since nothing here depends on TradingService at all: these are
    pydantic-level checks on ExecuteOrderRequest itself.
    """

    def test_missing_reasoning_is_rejected(self) -> None:
        with pytest.raises(Exception, match="signal_source"):
            ExecuteOrderRequest(
                symbol=SYMBOL, side=OrderSide.BUY, volume=0.01, reasoning={}
            )

    def test_empty_signal_source_is_rejected(self) -> None:
        with pytest.raises(Exception, match="signal_source"):
            execute_request(reasoning={"signal_source": "", "agency_reference": "ref"})

    def test_missing_agency_reference_is_rejected(self) -> None:
        """The specific gap this closes: signal_source alone
        ('instructed to trade') is not enough to catch a request with no
        checkable decision record behind it."""
        with pytest.raises(Exception, match="agency_reference"):
            execute_request(reasoning={"signal_source": "instructed to trade"})

    def test_agency_reference_that_is_only_whitespace_is_rejected(self) -> None:
        with pytest.raises(Exception, match="agency_reference"):
            execute_request(
                reasoning={"signal_source": "indicator", "agency_reference": "   "}
            )

    def test_valid_reasoning_is_accepted(self) -> None:
        request = execute_request(
            reasoning={
                "signal_source": "example breakout indicator",
                "agency_reference": "decision-log entry #1042",
            }
        )
        assert request.reasoning["agency_reference"]


def _existing_position(*, seconds_ago: float):  # type: ignore[no-untyped-def]
    """A position whose `time_open` (via the raw MT5 `time` field) is
    `seconds_ago` seconds in the past, for scale-in cooldown tests."""
    from tests.fakes import _Position

    when = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return _Position(time=when.timestamp())
