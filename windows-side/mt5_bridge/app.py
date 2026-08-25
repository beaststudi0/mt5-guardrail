"""Application factory.

`create_app()` takes optional collaborators. Production passes none and gets a
real terminal; tests pass fakes and get an identical app with no MT5 anywhere.
That single seam is what makes the HTTP layer testable on Linux.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from .api import account, health, journal as journal_api, market, trading as trading_api
from .api.errors import register_error_handlers
from .config import Settings, get_settings
from .guards import (
    AuthAttemptLimiter,
    ConfirmTokenStore,
    InMemoryAuthAttemptLimiter,
    InMemoryConfirmTokenStore,
    InMemoryOrderLimiter,
    OrderLimiter,
    ScaleInCooldownGuard,
)
from .journal import Journal, NullJournal, SqliteJournal
from .terminal import MT5Terminal, load_mt5_module
from .trading import TradingService

log = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(settings.bridge_log, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def build_terminal(settings: Settings) -> MT5Terminal:
    return MT5Terminal(
        load_mt5_module(),
        login=settings.login,
        password=settings.password.get_secret_value(),
        server=settings.server,
        terminal_path=settings.terminal_path,
        retries=settings.connect_retries,
        backoff_sec=settings.connect_backoff_sec,
        symbol_cache_ttl=settings.symbol_cache_ttl,
        connection_check_interval=settings.connection_check_interval,
    )


def build_journal(settings: Settings) -> Journal:
    return SqliteJournal(settings.journal_db) if settings.journal_enabled else NullJournal()


def create_app(
    settings: Settings | None = None,
    *,
    terminal: MT5Terminal | None = None,
    journal: Journal | None = None,
    token_store: ConfirmTokenStore | None = None,
    limiter: OrderLimiter | None = None,
    scale_in_guard: ScaleInCooldownGuard | None = None,
    auth_limiter: AuthAttemptLimiter | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    resolved_terminal = terminal or build_terminal(settings)
    resolved_journal = journal or build_journal(settings)
    resolved_tokens = token_store or InMemoryConfirmTokenStore(settings.confirm_token_ttl)
    resolved_limiter = limiter or InMemoryOrderLimiter(settings.max_daily_orders)
    resolved_scale_in_guard = scale_in_guard or ScaleInCooldownGuard(cooldown_seconds=900)
    resolved_auth_limiter = auth_limiter or InMemoryAuthAttemptLimiter()

    trading_service = TradingService(
        resolved_terminal,
        token_store=resolved_tokens,
        limiter=resolved_limiter,
        scale_in_guard=resolved_scale_in_guard,
        journal=resolved_journal,
        allowed_symbols=settings.allowed_symbols,
        max_lot_size=settings.max_lot_size,
        require_confirm_token=settings.require_confirm_token,
        confirm_token_ttl=settings.confirm_token_ttl,
        deviation=settings.order_deviation,
        magic_number=settings.magic_number,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Connect eagerly so a bad password fails at boot, not on the first trade.
        resolved_terminal.connect()
        if not settings.require_confirm_token:
            # This has sat unnoticed in .env before: it blends into a single
            # info-level summary line easy to skim past. A dedicated WARNING
            # is much harder to miss on every single startup.
            log.warning(
                "⚠️  require_confirm_token is FALSE - the preview -> token -> "
                "execute safety flow is DISABLED. Orders can execute without "
                "ever being previewed first. If this wasn't intentional, set "
                "MT5_REQUIRE_CONFIRM_TOKEN=true in .env and restart."
            )
        log.info(
            "bridge ready on %s:%s (symbols=%s, max_lot=%s, confirm_token=%s)",
            settings.bridge_host,
            settings.bridge_port,
            sorted(settings.allowed_symbols),
            settings.max_lot_size,
            settings.require_confirm_token,
        )
        yield
        resolved_terminal.shutdown()
        log.info("bridge shut down cleanly")

    app = FastAPI(
        title="MT5 Trading Bridge",
        version="2.0.0",
        summary="Guarded REST access to a MetaTrader 5 terminal.",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.terminal = resolved_terminal
    app.state.journal = resolved_journal
    app.state.trading = trading_service
    app.state.auth_limiter = resolved_auth_limiter

    register_error_handlers(app)
    for module in (health, account, market, trading_api, journal_api):
        app.include_router(module.router)

    @app.middleware("http")
    async def _security_headers(request, call_next):  # type: ignore[no-untyped-def]
        """Defense-in-depth response headers. This is a JSON-only API on a
        local network, so these are belt-and-suspenders rather than load-
        bearing - but they cost nothing and close off whole categories of
        "what if a browser somehow renders this" mistakes:
          - nosniff: never let a client MIME-sniff a JSON body into HTML/JS
          - DENY framing: this API is never meant to live in an <iframe>
          - no-store: order data and journal rows should not sit in any cache

        No CORSMiddleware is configured, and that absence is deliberate,
        not an oversight: every write endpoint requires a custom `x-api-key`
        header, and browsers refuse to send a custom header cross-origin
        without a CORS preflight succeeding first. With no CORS policy
        allowing any origin, that preflight fails, so a malicious page in
        someone's browser cannot make an authenticated cross-origin request
        to this bridge even if it somehow knew the key. Adding a permissive
        `allow_origins=["*"]` (or any origin list) here would silently
        remove that protection - if a real need for CORS ever comes up,
        restrict it to specific known origins, never a wildcard, and treat
        it as a deliberate security-relevant change, not routine config.
        """
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
