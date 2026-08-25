"""Central configuration. The ONLY place that reads environment variables.

Every other module receives a `Settings` instance via dependency injection,
which keeps them import-time pure and trivially testable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Validated at process start, so a bad config fails fast and loudly."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MT5_",
        extra="ignore",
    )

    # --- Bridge server ---
    bridge_api_key: SecretStr = Field(
        ..., min_length=16, description="Shared secret between bridge and clients"
    )
    bridge_host: str = "127.0.0.1"
    bridge_port: int = Field(default=8787, ge=1, le=65535)
    bridge_log: str = "mt5_bridge.log"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- MT5 terminal ---
    login: int = Field(..., gt=0)
    password: SecretStr
    server: str = Field(..., min_length=1)
    terminal_path: str | None = None
    connect_retries: int = Field(default=3, ge=1, le=10)
    connect_backoff_sec: float = Field(default=1.5, gt=0)

    # --- Safety guardrails ---
    # NoDecode tells pydantic-settings to hand the raw env-var string straight
    # to our `_split_symbols` validator below, instead of trying (and failing)
    # to JSON-decode "US100Cash" as if it were `["US100Cash"]`.
    allowed_symbols: Annotated[frozenset[str], NoDecode] = frozenset({"US100Cash"})
    max_lot_size: float = Field(default=0.10, gt=0)
    max_daily_orders: int = Field(default=20, ge=1)
    require_confirm_token: bool = True
    confirm_token_ttl: int = Field(default=60, ge=5, le=600)
    order_deviation: int = Field(default=20, ge=0)
    magic_number: int = 990011

    # --- Performance tuning ---
    symbol_cache_ttl: float = Field(
        default=30.0, gt=0, description="Seconds to cache immutable symbol metadata"
    )
    connection_check_interval: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Seconds to trust the terminal is still alive before paying for "
            "a real terminal_info() native round-trip again. Every bridge "
            "operation checks this on the way in, so a value that's too low "
            "re-adds the latency this setting exists to remove; a value "
            "that's too high delays how fast a genuinely dead terminal "
            "triggers a proactive reconnect (though any operation attempted "
            "meanwhile still fails cleanly on its own native call either way)."
        ),
    )

    # --- Journal (feature: learn from past trades) ---
    journal_enabled: bool = True
    journal_db: str = "trade_journal.sqlite3"

    # --- Notifications (feature: Discord/webhook alerts) ---
    webhook_url: SecretStr | None = None

    @field_validator("allowed_symbols", mode="before")
    @classmethod
    def _split_symbols(cls, value: object) -> object:
        """Accept `US100Cash,EURUSD` from a single env var."""
        if isinstance(value, str):
            return frozenset(s.strip() for s in value.split(",") if s.strip())
        return value

    @field_validator("terminal_path", "webhook_url", mode="before")
    @classmethod
    def _empty_string_is_none(cls, value: object) -> object:
        """An unset key in .env arrives as '' - treat it as absent."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so the .env file is parsed exactly once per process."""
    return Settings()  # type: ignore[call-arg]
