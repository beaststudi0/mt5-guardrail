"""Typed client for the MT5 bridge, for use from WSL (where MetaTrader5 cannot run).

    from mt5_client import BridgeClient

    with BridgeClient.from_env() as client:
        preview = client.preview_order("US100Cash", "buy", 0.01)
        # ... a human confirms ...
        client.execute_order("US100Cash", "buy", 0.01, preview.confirm_token)
"""

from .client import BridgeClient
from .config import ClientConfig
from .exceptions import (
    BridgeClientError,
    BridgeHTTPError,
    BridgeUnauthorized,
    BridgeUnavailable,
    ConfirmTokenRejected,
    DailyLimitReached,
    OrderRejected,
)
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

__version__ = "2.0.0"
__all__ = [
    "AccountSnapshot",
    "BridgeClient",
    "BridgeClientError",
    "BridgeHTTPError",
    "BridgeUnauthorized",
    "BridgeUnavailable",
    "Candle",
    "ClientConfig",
    "CloseResult",
    "ConfirmTokenRejected",
    "DailyLimitReached",
    "Health",
    "OrderPreview",
    "OrderRejected",
    "OrderResult",
    "Position",
    "SymbolSpec",
    "Tick",
]
