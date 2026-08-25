"""Journal read API - what the agent queries before deciding again."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..dependencies import JournalDep, RequireApiKey

router = APIRouter(prefix="/journal", tags=["journal"], dependencies=[RequireApiKey])


@router.get("/recent")
def recent(
    journal: JournalDep,
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Most recent trade events, newest first. Includes rejections."""
    return journal.recent(symbol=symbol, limit=limit)
