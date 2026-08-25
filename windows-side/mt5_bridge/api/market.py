"""Market data. Feeds both the agent's reasoning and the TA layer."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..dependencies import RequireApiKey, TerminalDep
from ..models import Candle, SymbolSpecResponse, Tick, Timeframe

router = APIRouter(prefix="/market", tags=["market"], dependencies=[RequireApiKey])

# Mapping lives here rather than in `terminal` so the domain stays free of
# transport-level naming choices.
_TIMEFRAME_ATTR: dict[Timeframe, str] = {
    Timeframe.M1: "TIMEFRAME_M1",
    Timeframe.M5: "TIMEFRAME_M5",
    Timeframe.M15: "TIMEFRAME_M15",
    Timeframe.M30: "TIMEFRAME_M30",
    Timeframe.H1: "TIMEFRAME_H1",
    Timeframe.H4: "TIMEFRAME_H4",
    Timeframe.D1: "TIMEFRAME_D1",
}


@router.get("/{symbol}/tick", response_model=Tick)
def tick(symbol: str, terminal: TerminalDep) -> Tick:
    return terminal.tick(symbol)


@router.get("/{symbol}/spec", response_model=SymbolSpecResponse)
def symbol_spec(symbol: str, terminal: TerminalDep) -> SymbolSpecResponse:
    """Broker-side limits for this symbol. Call this before preview_order to
    size a volume that will not be rejected - lot minimums vary a lot between
    brokers and even between accounts on the same broker."""
    spec = terminal.symbol_spec(symbol)
    return SymbolSpecResponse(
        symbol=spec.name,
        digits=spec.digits,
        volume_min=spec.volume_min,
        volume_max=spec.volume_max,
        volume_step=spec.volume_step,
    )


@router.get("/{symbol}/candles", response_model=list[Candle])
def candles(
    symbol: str,
    terminal: TerminalDep,
    timeframe: Timeframe = Timeframe.M15,
    count: int = Query(default=100, ge=1, le=5000),
) -> list[Candle]:
    native_tf = getattr(terminal.constants, _TIMEFRAME_ATTR[timeframe])
    return terminal.candles(symbol, native_tf, count)
