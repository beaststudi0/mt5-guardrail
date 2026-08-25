"""Data contracts.

Deliberately free of business rules that depend on runtime configuration
(e.g. "is this lot size too big?"). Those live in the service layer, where the
limits are injected. Models here only enforce what is *always* true.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> OrderSide:
        return OrderSide.SELL if self is OrderSide.BUY else OrderSide.BUY


class Timeframe(str, Enum):
    """Supported candle intervals (used by the technical-analysis feature)."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------
class OrderIntent(BaseModel):
    """What the caller *wants* to do. Not yet validated against live limits."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    side: OrderSide
    volume: float = Field(gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    comment: str = Field(default="mt5-guardrail", max_length=31)

    @field_validator("comment")
    @classmethod
    def _comment_is_printable_ascii(cls, value: str) -> str:
        """The comment is passed straight through to the broker's order
        record. max_length alone is not enough: a control character (newline,
        NUL, etc.) in a value that travels into a downstream terminal, log, or
        the broker's own systems is a classic injection vector. MT5 order
        comments are plain short ASCII labels in practice, so restrict to
        printable ASCII and reject anything else rather than silently letting
        it through. This is defense-in-depth - the caller is already
        authenticated, but an authenticated caller can still be a compromised
        or buggy bot.
        """
        if not value.isascii() or not value.isprintable():
            raise ValueError(
                "comment must be printable ASCII only (no control characters) "
                "- it is written verbatim into the broker order record"
            )
        return value
    reasoning: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Why this trade, in the caller's own structured terms - signal "
            "source, indicators referenced, confidence, market read. Stored "
            "verbatim in the journal alongside the server-observed market "
            "facts (bid/ask/spread), so a later training pass has both what "
            "the market actually looked like and what the agent believed. "
            "Optional, but every field left out here is a feature that can "
            "never be reconstructed later - MT5 does not remember why an "
            "order was sent, only that it was."
        ),
    )


class ExecuteOrderRequest(OrderIntent):
    """Unlike preview (exploratory, reasoning optional), execute is the one
    call that actually moves money - and the one whose reasoning becomes a
    permanent journal row. Making it required here, not just requested in
    the bot's instructions, is a deliberate choice: repeated plain-language
    reminders to attach --reasoning were not followed reliably in practice,
    so the requirement now lives where it cannot be forgotten - the trade
    simply does not execute without it, the same way it does not execute
    without a valid confirm_token.
    """

    confirm_token: str | None = None
    reasoning: dict[str, Any] = Field(
        description=(
            "Required on execute (unlike the base class's optional default) "
            "- must include non-empty 'signal_source' and 'agency_reference' "
            "at minimum. This is not extra paperwork: it is the only place "
            "the decision context behind a real trade is ever recorded, and "
            "it cannot be reconstructed after the fact if omitted."
        )
    )

    @field_validator("reasoning")
    @classmethod
    def _require_signal_source_and_agency_reference(
        cls, value: dict[str, Any]
    ) -> dict[str, Any]:
        """Both fields exist to close the same failure mode: an agent
        executing a real order on a vague or unverifiable justification.
        `signal_source` alone is not enough - an agent can satisfy a
        "must give a reason" check with something as thin as "instructed
        to trade," which asserts that *some* reasoning happened without
        naming anything a human could actually go check. `agency_reference`
        closes that gap: it must point at a specific, checkable decision
        record (a message ID, a log entry, a named indicator run) rather
        than a free-text assertion. Together they make every executed
        trade traceable back to a concrete, auditable origin instead of
        "the agent decided to."
        """
        source = value.get("signal_source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(
                "reasoning.signal_source is required and must be a non-empty "
                "string - e.g. which signal, indicator, or decision process "
                "this trade was based on. An empty {} does not satisfy this."
            )
        ref = value.get("agency_reference")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError(
                "reasoning.agency_reference is required and must be a "
                "non-empty string naming a specific, checkable decision "
                "record this trade is based on (e.g. a message ID, log "
                "entry, or indicator run - not a free-text assertion like "
                "'instructed to trade' with nothing a human could verify)."
            )
        return value


class ClosePositionRequest(BaseModel):
    ticket: int = Field(gt=0)
    confirm_token: str | None = None


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    mt5_connected: bool
    time: datetime


class Tick(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread: float
    time: datetime


class SymbolSpecResponse(BaseModel):
    """Broker facts a caller needs before sizing an order - avoids guessing
    a volume like 0.01 that may be below this symbol's real minimum."""

    symbol: str
    digits: int
    volume_min: float
    volume_max: float
    volume_step: float


class Candle(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int


class AccountSnapshot(BaseModel):
    login: int
    currency: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    leverage: int


class Position(BaseModel):
    ticket: int
    symbol: str
    side: OrderSide
    volume: float
    price_open: float
    price_current: float
    stop_loss: float | None
    take_profit: float | None
    profit: float
    time_open: datetime


class OrderPreview(BaseModel):
    """Read-only. Nothing has been sent to the broker at this point."""

    symbol: str
    side: OrderSide
    volume: float
    estimated_price: float
    stop_loss: float | None
    take_profit: float | None
    confirm_token: str
    expires_in_seconds: int


class OrderResult(BaseModel):
    retcode: int
    order: int
    deal: int
    price: float
    volume: float
    symbol: str
    side: OrderSide


class CloseResult(BaseModel):
    retcode: int
    closed_ticket: int
    price: float
    profit: float
