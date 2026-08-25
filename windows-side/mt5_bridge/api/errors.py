"""The single translation point from domain errors to HTTP status codes.

Because this mapping lives here, `TradingService` never imports FastAPI, and a
new transport (gRPC, CLI, message queue) needs no changes to the domain.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..exceptions import (
    BridgeError,
    ConfirmTokenInvalid,
    DailyLimitReached,
    InvalidStopLevels,
    OrderRejected,
    PositionNotFound,
    ScaleInCooldownActive,
    SymbolNotAllowed,
    SymbolUnavailable,
    TerminalConnectionError,
    TooManyAuthAttempts,
    VolumeOutOfRange,
)

log = logging.getLogger(__name__)

_STATUS_BY_ERROR: dict[type[BridgeError], HTTPStatus] = {
    SymbolNotAllowed: HTTPStatus.FORBIDDEN,
    SymbolUnavailable: HTTPStatus.NOT_FOUND,
    PositionNotFound: HTTPStatus.NOT_FOUND,
    VolumeOutOfRange: HTTPStatus.UNPROCESSABLE_ENTITY,
    InvalidStopLevels: HTTPStatus.UNPROCESSABLE_ENTITY,
    ConfirmTokenInvalid: HTTPStatus.BAD_REQUEST,
    DailyLimitReached: HTTPStatus.TOO_MANY_REQUESTS,
    ScaleInCooldownActive: HTTPStatus.TOO_MANY_REQUESTS,
    TooManyAuthAttempts: HTTPStatus.TOO_MANY_REQUESTS,
    OrderRejected: HTTPStatus.BAD_GATEWAY,
    TerminalConnectionError: HTTPStatus.SERVICE_UNAVAILABLE,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(BridgeError)
    async def _handle_bridge_error(request: Request, exc: BridgeError) -> JSONResponse:
        status = _STATUS_BY_ERROR.get(type(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            log.exception("unhandled bridge error on %s", request.url.path)
        else:
            log.warning("%s on %s: %s", type(exc).__name__, request.url.path, exc)

        body: dict[str, object] = {"error": type(exc).__name__, "detail": str(exc)}
        if isinstance(exc, OrderRejected) and exc.retcode is not None:
            body["retcode"] = exc.retcode
        headers = {}
        if isinstance(exc, TooManyAuthAttempts):
            headers["Retry-After"] = str(exc.retry_after_seconds)
        return JSONResponse(status_code=status, content=body, headers=headers)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Catches everything `_handle_bridge_error` does not: raw exceptions
        from the native MT5 API, bugs, or anything else nobody anticipated.

        Without this, FastAPI's default handler returns a bare 500 with no
        record of what happened - the exact symptom of "execute returns 500,
        preview works fine" that this handler exists to stop being a mystery.
        The full traceback always goes to the log file; the HTTP response
        never leaks internals to the caller.
        """
        log.exception(
            "UNHANDLED exception on %s %s - this is a bug, not a domain error. "
            "Full traceback follows.",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content={
                "error": type(exc).__name__,
                "detail": (
                    f"unexpected server error: {exc}. "
                    "Check mt5_bridge.log on the Windows side for the full traceback."
                ),
            },
        )
