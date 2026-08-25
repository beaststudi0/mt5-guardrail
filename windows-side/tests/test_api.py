"""HTTP-level tests: the actual wire contract, not just the service objects
behind it.

Every test file before this one exercised `TradingService`/`MT5Terminal`
directly. Nothing verified that the real FastAPI routes are wired
correctly, that auth is actually enforced at the HTTP layer, or -- most
importantly -- that each domain exception in `trading.py`/`terminal.py`
really does turn into the specific status code `errors.py` claims it does.
That mapping is the actual contract the linux-side `BridgeClient` depends
on (see `transport.py`'s `_ERROR_BY_NAME`); nothing before this proved it
end-to-end through a real request.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from mt5_bridge.terminal import MT5Terminal
from tests.fakes import FakeMT5

SYMBOL = "US100Cash"


def _reasoning() -> dict[str, str]:
    return {
        "signal_source": "example breakout indicator",
        "agency_reference": "decision-log entry #1042",
    }


# --------------------------------------------------------------------------
# Auth: every route requires x-api-key, checked before anything else
# --------------------------------------------------------------------------
class TestAuth:
    def test_missing_api_key_is_rejected(self, client: TestClient) -> None:
        del client.headers["x-api-key"]
        response = client.get("/health")
        assert response.status_code == 401

    def test_wrong_api_key_is_rejected(self, client: TestClient) -> None:
        client.headers["x-api-key"] = "wrong-key-entirely"
        response = client.get("/health")
        assert response.status_code == 401

    def test_auth_is_enforced_on_every_router_not_just_health(
        self, client: TestClient
    ) -> None:
        """Regression guard: it would be easy for a *new* route to forget
        `dependencies=[RequireApiKey]` on its router. This does not prove
        a route added after this test is protected, but it does prove the
        pattern holds across every router that exists today."""
        del client.headers["x-api-key"]
        protected_routes: list[tuple[str, str, dict | None]] = [
            ("GET", "/health", None),
            ("GET", "/account", None),
            ("GET", "/positions", None),
            ("GET", f"/market/{SYMBOL}/tick", None),
            ("GET", "/journal/recent", None),
            # A fully valid body, not {} -- isolates this to test only the
            # auth dimension. An empty body risks FastAPI's own request
            # validation firing before the auth dependency ever runs,
            # which would make this assert 401 when it might actually get
            # 422 for a completely unrelated reason.
            ("POST", "/order/preview", {"symbol": SYMBOL, "side": "buy", "volume": 0.01}),
        ]
        for method, path, body in protected_routes:
            response = client.request(method, path, json=body)
            assert response.status_code == 401, f"{method} {path} was not auth-protected"

    def test_correct_api_key_is_accepted(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200

    def test_repeated_failed_auth_attempts_trigger_a_lockout(
        self, client: TestClient
    ) -> None:
        """Verifies the AuthAttemptLimiter wiring through the real
        dependency chain (verify_api_key -> app.state.auth_limiter), not
        just the isolated guard logic already covered in test_guards.py.
        Default threshold is 10 failures; the 11th attempt must be a 429
        with a Retry-After header, regardless of whether that 11th attempt
        even uses a bad key."""
        client.headers["x-api-key"] = "wrong-key"
        for _ in range(10):
            client.get("/health")

        response = client.get("/health")
        assert response.status_code == 429
        assert "Retry-After" in response.headers


# --------------------------------------------------------------------------
# Read-only routes: health, account, positions, market data
# --------------------------------------------------------------------------
class TestReadRoutes:
    def test_health_reports_the_real_connection_state(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["mt5_connected"] is True  # terminal fixture connects eagerly via lifespan

    def test_account_returns_the_terminal_snapshot(
        self, client: TestClient, fake_mt5: FakeMT5
    ) -> None:
        response = client.get("/account")
        assert response.status_code == 200
        assert response.json()["currency"] == "USD"

    def test_positions_empty_by_default(self, client: TestClient) -> None:
        response = client.get("/positions")
        assert response.status_code == 200
        assert response.json() == []

    def test_positions_filters_by_symbol_query_param(
        self, client: TestClient, fake_mt5: FakeMT5
    ) -> None:
        from tests.fakes import _Position

        fake_mt5.positions.append(_Position(ticket=1, symbol="US100Cash"))
        fake_mt5.positions.append(_Position(ticket=2, symbol="EURUSD"))

        response = client.get("/positions", params={"symbol": "EURUSD"})
        assert response.status_code == 200
        tickets = [p["ticket"] for p in response.json()]
        assert tickets == [2]

    def test_tick_returns_the_live_quote(self, client: TestClient, fake_mt5: FakeMT5) -> None:
        response = client.get(f"/market/{SYMBOL}/tick")
        assert response.status_code == 200
        body = response.json()
        assert body["bid"] == fake_mt5.tick_obj.bid
        assert body["ask"] == fake_mt5.tick_obj.ask

    def test_tick_for_an_unknown_symbol_is_a_404(self, client: TestClient) -> None:
        response = client.get("/market/NOPE/tick")
        assert response.status_code == 404

    def test_symbol_spec_returns_broker_limits(self, client: TestClient) -> None:
        response = client.get(f"/market/{SYMBOL}/spec")
        assert response.status_code == 200
        assert response.json()["volume_min"] == 0.01

    def test_candles_default_timeframe_and_count(self, client: TestClient) -> None:
        response = client.get(f"/market/{SYMBOL}/candles")
        assert response.status_code == 200
        assert len(response.json()) == 100  # documented default count

    def test_candles_count_above_the_ceiling_is_a_422(self, client: TestClient) -> None:
        """`Query(default=100, ge=1, le=5000)` -- proves FastAPI's own
        parameter validation actually fires, not just that a huge count
        happens to work."""
        response = client.get(f"/market/{SYMBOL}/candles", params={"count": 5001})
        assert response.status_code == 422

    def test_journal_recent_is_reachable(self, client: TestClient) -> None:
        response = client.get("/journal/recent")
        assert response.status_code == 200
        assert response.json() == []  # journal_enabled=False in the settings fixture


# --------------------------------------------------------------------------
# The actual wire contract: every domain exception -> the status code
# errors.py claims, proven through a real request, not a direct call.
# --------------------------------------------------------------------------
class TestErrorStatusCodeMapping:
    def test_symbol_not_allowed_is_403(self, client: TestClient) -> None:
        response = client.post(
            "/order/preview",
            json={"symbol": "NOTALLOWED", "side": "buy", "volume": 0.01},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "SymbolNotAllowed"

    def test_volume_out_of_range_is_422(self, client: TestClient) -> None:
        response = client.post(
            "/order/preview",
            json={"symbol": SYMBOL, "side": "buy", "volume": 999.0},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "VolumeOutOfRange"

    def test_invalid_stop_levels_is_422(
        self, client: TestClient, fake_mt5: FakeMT5
    ) -> None:
        bad_sl = fake_mt5.tick_obj.ask + 1.0  # above entry: wrong side for BUY
        response = client.post(
            "/order/preview",
            json={"symbol": SYMBOL, "side": "buy", "volume": 0.01, "stop_loss": bad_sl},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "InvalidStopLevels"

    def test_confirm_token_invalid_is_400(self, client: TestClient) -> None:
        response = client.post(
            "/order/execute",
            json={
                "symbol": SYMBOL,
                "side": "buy",
                "volume": 0.01,
                "confirm_token": "not-a-real-token",
                "reasoning": _reasoning(),
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "ConfirmTokenInvalid"

    def test_position_not_found_is_404(self, client: TestClient) -> None:
        response = client.post("/position/close/preview", json={"ticket": 999999})
        assert response.status_code == 404
        assert response.json()["error"] == "PositionNotFound"

    def test_daily_limit_reached_is_429(self, client: TestClient) -> None:
        # settings fixture: max_daily_orders=3
        for _ in range(3):
            preview = client.post(
                "/order/preview",
                json={"symbol": SYMBOL, "side": "buy", "volume": 0.01},
            ).json()
            client.post(
                "/order/execute",
                json={
                    "symbol": SYMBOL,
                    "side": "buy",
                    "volume": 0.01,
                    "confirm_token": preview["confirm_token"],
                    "reasoning": _reasoning(),
                },
            )

        preview = client.post(
            "/order/preview", json={"symbol": SYMBOL, "side": "buy", "volume": 0.01}
        ).json()
        response = client.post(
            "/order/execute",
            json={
                "symbol": SYMBOL,
                "side": "buy",
                "volume": 0.01,
                "confirm_token": preview["confirm_token"],
                "reasoning": _reasoning(),
            },
        )
        assert response.status_code == 429
        assert response.json()["error"] == "DailyLimitReached"

    def test_order_rejected_is_502_and_carries_the_retcode(
        self, client: TestClient, fake_mt5: FakeMT5
    ) -> None:
        fake_mt5.reject_orders = True
        preview = client.post(
            "/order/preview", json={"symbol": SYMBOL, "side": "buy", "volume": 0.01}
        ).json()

        response = client.post(
            "/order/execute",
            json={
                "symbol": SYMBOL,
                "side": "buy",
                "volume": 0.01,
                "confirm_token": preview["confirm_token"],
                "reasoning": _reasoning(),
            },
        )
        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "OrderRejected"
        assert body["retcode"] == fake_mt5.TRADE_RETCODE_REJECT

    def test_missing_reasoning_on_execute_is_a_422_from_pydantic_itself(
        self, client: TestClient
    ) -> None:
        """Unlike the other cases above (domain exceptions caught by
        errors.py's handler), this validation lives directly on
        ExecuteOrderRequest and fires before TradingService is ever
        reached -- FastAPI's own request-validation 422, not the custom
        handler's. Worth its own test: the two paths produce
        differently-shaped error bodies, and a client parsing one must
        not break on the other.
        """
        response = client.post(
            "/order/execute",
            json={
                "symbol": SYMBOL,
                "side": "buy",
                "volume": 0.01,
                "confirm_token": "irrelevant",
                "reasoning": {},
            },
        )
        assert response.status_code == 422


# --------------------------------------------------------------------------
# The full preview -> execute happy path, through real HTTP end to end
# --------------------------------------------------------------------------
class TestFullOrderFlow:
    def test_preview_then_execute_succeeds(self, client: TestClient, fake_mt5: FakeMT5) -> None:
        preview_response = client.post(
            "/order/preview", json={"symbol": SYMBOL, "side": "buy", "volume": 0.01}
        )
        assert preview_response.status_code == 200
        token = preview_response.json()["confirm_token"]

        execute_response = client.post(
            "/order/execute",
            json={
                "symbol": SYMBOL,
                "side": "buy",
                "volume": 0.01,
                "confirm_token": token,
                "reasoning": _reasoning(),
            },
        )
        assert execute_response.status_code == 200
        assert execute_response.json()["retcode"] == fake_mt5.TRADE_RETCODE_DONE

    def test_a_preview_token_cannot_be_reused_across_two_execute_calls(
        self, client: TestClient
    ) -> None:
        preview = client.post(
            "/order/preview", json={"symbol": SYMBOL, "side": "buy", "volume": 0.01}
        ).json()
        body = {
            "symbol": SYMBOL,
            "side": "buy",
            "volume": 0.01,
            "confirm_token": preview["confirm_token"],
            "reasoning": _reasoning(),
        }
        first = client.post("/order/execute", json=body)
        assert first.status_code == 200

        second = client.post("/order/execute", json=body)
        assert second.status_code == 400
        assert second.json()["error"] == "ConfirmTokenInvalid"
