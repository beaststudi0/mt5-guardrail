"""End-to-end: the real BridgeClient against the real FastAPI app.

Every other test file proves ONE side in isolation:
  - `test_terminal.py`/`test_trading.py`/`test_api.py` (this directory)
    exercise the windows-side app directly, via TestClient or the service
    objects underneath it.
  - `linux-side/tests/test_client.py` exercises `BridgeClient` against a
    hand-written `http.server` stub that some developer wrote to mimic
    expected responses.

Nothing anywhere runs the REAL client against the REAL server. If a field
gets renamed in `windows-side/mt5_bridge/models.py` without the matching
change in `linux-side/mt5_client/models.py`, both test suites above can
stay green while the actual integration silently breaks -- exactly the
"two systems tested independently, never together" shape this file exists
to close. Only the native MT5 layer is faked; everything else (HTTP,
JSON serialization, FastAPI's routing/validation, the client's retry and
error-translation logic) is the real code, talking over a real socket.

Requires `uvicorn` (already a windows-side dependency) to host the real
ASGI app on an actual port -- TestClient's in-process transport cannot be
reached by the client's real `requests`-based Transport, which needs a
genuine HTTP server to connect to.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
import uvicorn

from mt5_bridge.app import create_app
from mt5_bridge.config import Settings
from mt5_bridge.journal import NullJournal
from mt5_bridge.terminal import MT5Terminal
from tests.fakes import FakeMT5

# linux-side is a sibling directory with its own separate package, not
# normally on this side's import path -- reach across explicitly rather
# than restructuring either side's existing pyproject.toml testpaths.
_LINUX_SIDE = Path(__file__).resolve().parents[2] / "linux-side"
if str(_LINUX_SIDE) not in sys.path:
    sys.path.insert(0, str(_LINUX_SIDE))

from mt5_client import BridgeClient, ClientConfig  # noqa: E402
from mt5_client.exceptions import (  # noqa: E402
    BridgeUnauthorized,
    ConfirmTokenRejected,
    OrderRejected as ClientOrderRejected,
)

SYMBOL = "US100Cash"
API_KEY = "e2e-test-key-at-least-16-chars"


def _reasoning() -> dict[str, str]:
    return {
        "signal_source": "example breakout indicator",
        "agency_reference": "decision-log entry #1043",
    }


class _ServerThread(uvicorn.Server):
    """Runs uvicorn in a background thread instead of blocking the test
    process. `install_signal_handlers=False` is required outside the main
    thread -- uvicorn's default startup registers OS signal handlers,
    which only the main thread is allowed to do."""

    def install_signal_handlers(self) -> None:
        pass

    def run_in_thread(self) -> Iterator[str]:
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5.0
        while not self.started and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self.started:  # pragma: no cover - defensive
            raise RuntimeError("uvicorn did not start within 5s")
        port = self.servers[0].sockets[0].getsockname()[1]
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            self.should_exit = True
            thread.join(timeout=5.0)


@pytest.fixture
def fake_mt5() -> FakeMT5:
    return FakeMT5()


@pytest.fixture
def live_server_url(fake_mt5: FakeMT5) -> Iterator[str]:
    settings = Settings(
        _env_file=None,
        bridge_api_key=API_KEY,
        login=1,
        password="pw",
        server="Fake-Demo",
        allowed_symbols=frozenset({SYMBOL}),
        max_lot_size=1.0,
        max_daily_orders=3,
        require_confirm_token=True,
        confirm_token_ttl=60,
        journal_enabled=False,
    )
    terminal = MT5Terminal(
        fake_mt5, login=1, password="pw", server="Fake-Demo", sleep=lambda _: None
    )
    app = create_app(settings, terminal=terminal, journal=NullJournal())

    server = _ServerThread(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    yield from server.run_in_thread()


@pytest.fixture
def real_client(live_server_url: str) -> Iterator[BridgeClient]:
    config = ClientConfig(base_url=live_server_url, api_key=API_KEY, timeout=5.0)
    with BridgeClient(config) as c:
        yield c


# --------------------------------------------------------------------------
# The actual contract: does the real client correctly parse what the real
# server actually sends, over a real socket -- not a hand-written stub's
# guess at what the server sends.
# --------------------------------------------------------------------------
class TestRealClientAgainstRealServer:
    def test_health_round_trips_correctly(self, real_client: BridgeClient) -> None:
        health = real_client.health()
        assert health.is_ready is True

    def test_wrong_api_key_raises_the_clients_own_typed_exception(
        self, live_server_url: str
    ) -> None:
        bad_config = ClientConfig(base_url=live_server_url, api_key="wrong-key", timeout=5.0)
        with BridgeClient(bad_config) as c, pytest.raises(BridgeUnauthorized):
            c.health()

    def test_tick_field_names_match_between_client_and_server(
        self, real_client: BridgeClient, fake_mt5: FakeMT5
    ) -> None:
        """The specific failure mode this whole file exists to catch: if
        `models.py` (server) and `mt5_client/models.py` (client) drift on
        a field name, `Tick.from_dict()` would either raise a KeyError or
        silently produce a wrong/default value -- either way, only a real
        round trip through both sides at once would surface it.
        """
        tick = real_client.tick(SYMBOL)
        assert tick.bid == fake_mt5.tick_obj.bid
        assert tick.ask == fake_mt5.tick_obj.ask

    def test_full_preview_and_execute_flow_round_trips_through_real_http(
        self, real_client: BridgeClient, fake_mt5: FakeMT5
    ) -> None:
        preview = real_client.preview_order(SYMBOL, "buy", 0.01)
        assert preview.confirm_token

        result = real_client.execute_order(
            SYMBOL,
            "buy",
            0.01,
            preview.confirm_token,
            reasoning=_reasoning(),
        )
        assert result.retcode == fake_mt5.TRADE_RETCODE_DONE
        assert len(fake_mt5.sent_orders) == 1

    def test_confirm_token_rejection_maps_to_the_clients_specific_exception_type(
        self, real_client: BridgeClient
    ) -> None:
        """Proves `transport.py`'s `_ERROR_BY_NAME` mapping actually
        matches what `errors.py` really sends as `error` in the JSON body
        -- both sides were only ever independently self-consistent before
        this test existed."""
        with pytest.raises(ConfirmTokenRejected):
            real_client.execute_order(
                SYMBOL, "buy", 0.01, "not-a-real-token", reasoning=_reasoning()
            )

    def test_order_rejection_maps_to_the_clients_specific_exception_with_retcode(
        self, real_client: BridgeClient, fake_mt5: FakeMT5
    ) -> None:
        fake_mt5.reject_orders = True
        preview = real_client.preview_order(SYMBOL, "buy", 0.01)

        with pytest.raises(ClientOrderRejected) as exc_info:
            real_client.execute_order(
                SYMBOL, "buy", 0.01, preview.confirm_token, reasoning=_reasoning()
            )
        assert exc_info.value.retcode == fake_mt5.TRADE_RETCODE_REJECT
