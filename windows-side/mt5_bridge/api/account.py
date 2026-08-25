"""Account and open-position views. Read-only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..dependencies import RequireApiKey, TerminalDep
from ..models import AccountSnapshot, Position

router = APIRouter(tags=["account"], dependencies=[RequireApiKey])


@router.get("/account", response_model=AccountSnapshot)
def account(terminal: TerminalDep) -> Any:
    return terminal.account()


@router.get("/positions", response_model=list[Position])
def positions(terminal: TerminalDep, symbol: str | None = None) -> list[Position]:
    return terminal.positions(symbol)
