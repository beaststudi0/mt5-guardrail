"""Shared fixtures. Every test gets an isolated app with a fake terminal."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mt5_bridge.app import create_app
from mt5_bridge.config import Settings
from mt5_bridge.guards import (
    InMemoryConfirmTokenStore,
    InMemoryOrderLimiter,
    ScaleInCooldownGuard,
)
from mt5_bridge.journal import NullJournal
from mt5_bridge.terminal import MT5Terminal
from mt5_bridge.trading import TradingService
from tests.fakes import FakeMT5

API_KEY = "test-key-at-least-16-chars"


@pytest.fixture
def settings() -> Settings:
    """Fully isolated from the real environment.

    `_env_file=None` stops pydantic-settings from reading whatever `.env`
    happens to sit in the current working directory - without this, running
    `pytest` from `C:\\mt5-bridge` picks up the *real* `.env`, including
    `MT5_REQUIRE_CONFIRM_TOKEN=false` if that was ever set for live trading.
    That single leaked value silently disabled the confirm-token check in
    every test here. `require_confirm_token=True` is also stated explicitly
    rather than left to the class default, so the test's intent survives
    even if that default ever changes.
    """
    return Settings(
        _env_file=None,
        bridge_api_key=API_KEY,
        login=1,
        password="pw",
        server="Fake-Demo",
        allowed_symbols=frozenset({"US100Cash"}),
        max_lot_size=0.10,
        max_daily_orders=3,
        require_confirm_token=True,
        confirm_token_ttl=60,
        journal_enabled=False,
    )


@pytest.fixture
def fake_mt5() -> FakeMT5:
    return FakeMT5()


@pytest.fixture
def terminal(fake_mt5: FakeMT5) -> MT5Terminal:
    return MT5Terminal(
        fake_mt5,
        login=1,
        password="pw",
        server="Fake-Demo",
        sleep=lambda _: None,  # no real waiting in retry tests
    )


@pytest.fixture
def trading(settings: Settings, terminal: MT5Terminal) -> TradingService:
    return TradingService(
        terminal,
        token_store=InMemoryConfirmTokenStore(settings.confirm_token_ttl),
        limiter=InMemoryOrderLimiter(settings.max_daily_orders),
        scale_in_guard=ScaleInCooldownGuard(cooldown_seconds=900),
        journal=NullJournal(),
        allowed_symbols=settings.allowed_symbols,
        max_lot_size=settings.max_lot_size,
        require_confirm_token=settings.require_confirm_token,
        confirm_token_ttl=settings.confirm_token_ttl,
        deviation=settings.order_deviation,
        magic_number=settings.magic_number,
    )


@pytest.fixture
def client(settings: Settings, terminal: MT5Terminal) -> TestClient:
    app = create_app(settings, terminal=terminal, journal=NullJournal())
    with TestClient(app) as c:
        c.headers.update({"x-api-key": API_KEY})
        yield c
