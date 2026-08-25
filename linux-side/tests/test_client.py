"""Client tests. A local stub server proves the wire contract without a broker."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Iterator
from unittest import mock

import pytest

from mt5_client import BridgeClient, ClientConfig
from mt5_client.config import Path, windows_host_ip
from mt5_client.exceptions import (
    BridgeUnauthorized,
    BridgeUnavailable,
    ConfirmTokenRejected,
    OrderRejected,
)

API_KEY = "stub-key"
STATE: dict[str, Any] = {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence the test output
        pass

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorised(self) -> bool:
        return self.headers.get("x-api-key") == API_KEY

    def do_GET(self) -> None:  # noqa: N802
        STATE.setdefault("get_calls", []).append(self.path)
        if self.path == "/health":
            return self._reply(200, {"status": "ok", "mt5_connected": True, "time": "now"})
        if not self._authorised():
            return self._reply(401, {"error": "unauthorized", "detail": "bad key"})
        if self.path.startswith("/market/") and self.path.endswith("/tick"):
            return self._reply(
                200,
                {"symbol": "US100Cash", "bid": 100.0, "ask": 100.5, "spread": 0.5, "time": "now"},
            )
        if self.path.startswith("/market/") and self.path.endswith("/spec"):
            return self._reply(
                200,
                {
                    "symbol": "US100Cash",
                    "digits": 2,
                    "volume_min": 0.01,
                    "volume_max": 50.0,
                    "volume_step": 0.01,
                },
            )
        if self.path.startswith("/flaky"):
            STATE["flaky_hits"] = STATE.get("flaky_hits", 0) + 1
            return self._reply(503, {"error": "Unavailable", "detail": "try again"})
        return self._reply(404, {"error": "NotFound", "detail": self.path})

    def do_POST(self) -> None:  # noqa: N802
        STATE.setdefault("post_calls", []).append(self.path)
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)

        if not self._authorised():
            return self._reply(401, {"error": "unauthorized", "detail": "bad key"})
        if self.path == "/order/preview":
            return self._reply(
                200,
                {
                    "symbol": "US100Cash",
                    "side": "buy",
                    "volume": 0.01,
                    "estimated_price": 100.5,
                    "stop_loss": None,
                    "take_profit": None,
                    "confirm_token": "tok-1",
                    "expires_in_seconds": 60,
                    "unknown_future_field": "ignored by from_dict",
                },
            )
        if self.path == "/order/execute":
            if STATE.get("reject_order"):
                return self._reply(
                    502, {"error": "OrderRejected", "detail": "no liquidity", "retcode": 10006}
                )
            if STATE.get("bad_token"):
                return self._reply(
                    400, {"error": "ConfirmTokenInvalid", "detail": "expired token"}
                )
            return self._reply(
                200,
                {
                    "retcode": 10009,
                    "order": 1,
                    "deal": 2,
                    "price": 100.5,
                    "volume": 0.01,
                    "symbol": "US100Cash",
                    "side": "buy",
                },
            )
        if self.path == "/flaky-post":
            STATE["flaky_post_hits"] = STATE.get("flaky_post_hits", 0) + 1
            return self._reply(503, {"error": "Unavailable", "detail": "try again"})
        return self._reply(404, {"error": "NotFound", "detail": self.path})


@pytest.fixture
def stub_server() -> Iterator[str]:
    STATE.clear()
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def client(stub_server: str) -> Iterator[BridgeClient]:
    config = ClientConfig(base_url=stub_server, api_key=API_KEY, timeout=2.0, backoff_factor=0.0)
    with BridgeClient(config) as c:
        yield c


class TestWindowsHostDiscovery:
    """Regression coverage for a real bug: WSL images with systemd-resolved
    put `nameserver 127.0.0.53` (a local stub resolver) in /etc/resolv.conf.
    That is not the Windows host and must never end up as a candidate URL."""

    def test_systemd_resolved_stub_is_not_mistaken_for_the_windows_host(self) -> None:
        fake_resolv_conf = "nameserver 127.0.0.53\noptions edns0 trust-ad\n"
        with (
            mock.patch.object(Path, "read_text", return_value=fake_resolv_conf),
            mock.patch("mt5_client.config._default_gateway", return_value=None),
        ):
            assert windows_host_ip() is None

    def test_default_gateway_is_preferred_when_available(self) -> None:
        with mock.patch("mt5_client.config._default_gateway", return_value="172.20.0.1"):
            assert windows_host_ip() == "172.20.0.1"

    def test_legacy_resolv_conf_nameserver_still_works_without_systemd(self) -> None:
        """Older WSL images without systemd-resolved legitimately put the
        Windows host's IP directly in resolv.conf - that path must still work."""
        fake_resolv_conf = "nameserver 172.19.32.1\n"
        with (
            mock.patch.object(Path, "read_text", return_value=fake_resolv_conf),
            mock.patch("mt5_client.config._default_gateway", return_value=None),
        ):
            assert windows_host_ip() == "172.19.32.1"


class TestReads:
    def test_health(self, client: BridgeClient) -> None:
        assert client.health().is_ready is True

    def test_tick_exposes_a_computed_mid(self, client: BridgeClient) -> None:
        assert client.tick("US100Cash").mid == 100.25

    def test_symbol_spec_exposes_broker_volume_limits(self, client: BridgeClient) -> None:
        spec = client.symbol_spec("US100Cash")
        assert spec.volume_min == 0.01
        assert spec.symbol == "US100Cash"

    def test_unknown_response_fields_do_not_break_an_older_client(
        self, client: BridgeClient
    ) -> None:
        """The stub returns `unknown_future_field`; from_dict must ignore it."""
        assert client.preview_order("US100Cash", "buy", 0.01).confirm_token == "tok-1"


class TestErrorMapping:
    def test_bad_key_raises_unauthorized(self, stub_server: str) -> None:
        with BridgeClient(ClientConfig(base_url=stub_server, api_key="wrong")) as c:
            with pytest.raises(BridgeUnauthorized):
                c.account()

    def test_expired_token_raises_a_catchable_type(self, client: BridgeClient) -> None:
        STATE["bad_token"] = True
        with pytest.raises(ConfirmTokenRejected):
            client.execute_order("US100Cash", "buy", 0.01, "stale")

    def test_broker_rejection_carries_the_retcode(self, client: BridgeClient) -> None:
        STATE["reject_order"] = True
        with pytest.raises(OrderRejected) as exc:
            client.execute_order("US100Cash", "buy", 0.01, "tok-1")
        assert exc.value.retcode == 10006

    def test_unreachable_bridge_says_so_clearly(self) -> None:
        config = ClientConfig(base_url="http://127.0.0.1:1", api_key=API_KEY, max_retries=0)
        with BridgeClient(config) as c, pytest.raises(BridgeUnavailable, match="cannot reach"):
            c.health()


class TestRetryPolicy:
    def test_get_is_retried_on_503(self, client: BridgeClient) -> None:
        with pytest.raises(Exception):
            client._transport.get("/flaky")
        assert STATE["flaky_hits"] > 1  # retried

    def test_post_is_never_retried(self, client: BridgeClient) -> None:
        """A duplicated market order is far worse than a failed one."""
        with pytest.raises(Exception):
            client._transport.post("/flaky-post", {})
        assert STATE["flaky_post_hits"] == 1  # sent exactly once


class TestOrderFlow:
    def test_preview_then_execute_sends_two_distinct_requests(
        self, client: BridgeClient
    ) -> None:
        preview = client.preview_order("US100Cash", "buy", 0.01)
        client.execute_order("US100Cash", "buy", 0.01, preview.confirm_token)
        assert STATE["post_calls"] == ["/order/preview", "/order/execute"]

    def test_preview_summary_is_human_approvable(self, client: BridgeClient) -> None:
        summary = client.preview_order("US100Cash", "buy", 0.01).summary()
        assert summary == "BUY 0.01 US100Cash @ ~100.5"
