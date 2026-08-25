"""Typed responses. Plain dataclasses: no pydantic dependency on the client side.

`from_dict` ignores unknown keys, so a newer bridge that adds fields will not
break an older client. That forward-compatibility is deliberate.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, TypeVar

T = TypeVar("T", bound="_FromDict")


class _FromDict:
    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in data.items() if k in known})  # type: ignore[arg-type]


def _parse_time(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class Health(_FromDict):
    status: str
    mt5_connected: bool
    time: str

    @property
    def is_ready(self) -> bool:
        return self.status == "ok" and self.mt5_connected


@dataclass(frozen=True, slots=True)
class Tick(_FromDict):
    symbol: str
    bid: float
    ask: float
    spread: float
    time: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True, slots=True)
class SymbolSpec(_FromDict):
    """Broker-side sizing limits. Fetch this before guessing a volume."""

    symbol: str
    digits: int
    volume_min: float
    volume_max: float
    volume_step: float


@dataclass(frozen=True, slots=True)
class Candle(_FromDict):
    time: str
    open: float
    high: float
    low: float
    close: float
    tick_volume: int

    @property
    def timestamp(self) -> datetime:
        return _parse_time(self.time)


@dataclass(frozen=True, slots=True)
class AccountSnapshot(_FromDict):
    login: int
    currency: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    leverage: int


@dataclass(frozen=True, slots=True)
class Position(_FromDict):
    ticket: int
    symbol: str
    side: str
    volume: float
    price_open: float
    price_current: float
    stop_loss: float | None
    take_profit: float | None
    profit: float


@dataclass(frozen=True, slots=True)
class OrderPreview(_FromDict):
    symbol: str
    side: str
    volume: float
    estimated_price: float
    stop_loss: float | None
    take_profit: float | None
    confirm_token: str
    expires_in_seconds: int

    def summary(self) -> str:
        """One line a human can approve or reject in a Discord message."""
        parts = [
            f"{self.side.upper()} {self.volume} {self.symbol} @ ~{self.estimated_price}"
        ]
        if self.stop_loss:
            parts.append(f"SL {self.stop_loss}")
        if self.take_profit:
            parts.append(f"TP {self.take_profit}")
        return " | ".join(parts)


@dataclass(frozen=True, slots=True)
class OrderResult(_FromDict):
    retcode: int
    order: int
    deal: int
    price: float
    volume: float
    symbol: str
    side: str


@dataclass(frozen=True, slots=True)
class CloseResult(_FromDict):
    retcode: int
    closed_ticket: int
    price: float
    profit: float
