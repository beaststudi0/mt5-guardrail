"""Liveness endpoint.

Requires the API key like every other route. An earlier version left this
unauthenticated on the assumption that an external load balancer or uptime
monitor would need a no-credential probe - but this bridge has exactly one
caller (the WSL client, which already sends x-api-key on every request via
transport.py's session headers), so that assumption never applied here.
Leaving it open only gave an unauthenticated way to fingerprint "a live
MT5 bridge is running and connected at this address" - reconnaissance
value with no offsetting benefit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from ..dependencies import RequireApiKey, TerminalDep
from ..models import HealthResponse

router = APIRouter(tags=["health"], dependencies=[RequireApiKey])


@router.get("/health", response_model=HealthResponse)
def health(terminal: TerminalDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        mt5_connected=terminal.is_connected,
        time=datetime.now(timezone.utc),
    )
