"""Order flow. Every mutating route sits behind preview -> confirm -> execute."""

from __future__ import annotations

from fastapi import APIRouter

from ..dependencies import RequireApiKey, TradingDep
from ..models import (
    ClosePositionRequest,
    CloseResult,
    ExecuteOrderRequest,
    OrderIntent,
    OrderPreview,
    OrderResult,
)

router = APIRouter(tags=["trading"], dependencies=[RequireApiKey])


@router.post("/order/preview", response_model=OrderPreview)
def preview_order(intent: OrderIntent, trading: TradingDep) -> OrderPreview:
    """Quote and validate. Sends nothing to the broker."""
    return trading.preview(intent)


@router.post("/order/execute", response_model=OrderResult)
def execute_order(request: ExecuteOrderRequest, trading: TradingDep) -> OrderResult:
    """Requires a matching confirm_token from `/order/preview`."""
    return trading.execute(request)


@router.post("/position/close/preview", response_model=OrderPreview)
def preview_close(request: ClosePositionRequest, trading: TradingDep) -> OrderPreview:
    return trading.preview_close(request.ticket)


@router.post("/position/close", response_model=CloseResult)
def close_position(request: ClosePositionRequest, trading: TradingDep) -> CloseResult:
    return trading.close(request.ticket, request.confirm_token)
