"""CLI contract tests: one line of JSON on stdout, always, and exit 0/1.

Reuses the stub bridge from test_client so the wire behaviour matches what
the real client sees.
"""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer
from typing import Any, Iterator

import pytest

from mt5_cli import main
from tests.test_client import API_KEY, STATE, _Handler


@pytest.fixture
def stub_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    STATE.clear()
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("MT5_BRIDGE_API_KEY", API_KEY)
    monkeypatch.setenv("MT5_BRIDGE_URL", url)
    monkeypatch.setenv("MT5_BRIDGE_TIMEOUT", "2")
    yield url
    server.shutdown()


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    """Run the CLI and enforce the contract: exactly one JSON line on stdout."""
    code = main(list(argv))
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line]
    assert len(lines) == 1, f"expected exactly one line of JSON, got: {out!r}"
    return code, json.loads(lines[0])


class TestJsonContract:
    def test_success_is_one_json_line(self, stub_server: str, capsys) -> None:
        code, body = run_cli(capsys, "health")
        assert code == 0
        assert body["ok"] is True
        assert body["data"]["mt5_connected"] is True

    def test_bad_args_emit_json_not_usage_text(self, capsys) -> None:
        """The regression this suite exists for: argparse's default behaviour
        is usage text on stderr and exit code 2 - unparseable by an agent."""
        code, body = run_cli(capsys, "tick")  # missing required symbol
        assert code == 1
        assert body["ok"] is False
        assert body["error_type"] == "ArgumentError"

    def test_bad_subcommand_choice_emits_json(self, capsys) -> None:
        code, body = run_cli(capsys, "preview", "US100Cash", "hold", "0.01")
        assert code == 1
        assert body["error_type"] == "ArgumentError"

    def test_missing_api_key_is_a_config_error(
        self, capsys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MT5_BRIDGE_API_KEY", raising=False)
        code, body = run_cli(capsys, "health")
        assert code == 1
        assert "configuration failed" in body["error"]


class TestErrorDetail:
    def test_broker_rejection_carries_status_and_retcode(
        self, stub_server: str, capsys
    ) -> None:
        """An agent must be able to branch on fields, not parse the message."""
        STATE["reject_order"] = True
        code, body = run_cli(
            capsys, "execute", "US100Cash", "buy", "0.01", "tok-1"
        )
        assert code == 1
        assert body["error_type"] == "OrderRejected"
        assert body["status_code"] == 502
        assert body["retcode"] == 10006

    def test_expired_token_is_branchable(self, stub_server: str, capsys) -> None:
        STATE["bad_token"] = True
        code, body = run_cli(
            capsys, "execute", "US100Cash", "buy", "0.01", "stale"
        )
        assert code == 1
        assert body["error_type"] == "ConfirmTokenRejected"
        assert body["status_code"] == 400

    def test_unreachable_bridge_is_json_not_traceback(
        self, capsys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT5_BRIDGE_API_KEY", API_KEY)
        monkeypatch.setenv("MT5_BRIDGE_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("MT5_BRIDGE_TIMEOUT", "1")
        code, body = run_cli(capsys, "health")
        assert code == 1
        assert body["error_type"] == "BridgeUnavailable"


class TestOrderFlow:
    def test_preview_then_execute_round_trip(self, stub_server: str, capsys) -> None:
        code, preview = run_cli(capsys, "preview", "US100Cash", "buy", "0.01")
        assert code == 0
        token = preview["data"]["confirm_token"]

        code, executed = run_cli(
            capsys, "execute", "US100Cash", "buy", "0.01", token
        )
        assert code == 0
        assert executed["data"]["retcode"] == 10009

    def test_discover_finds_the_stub_and_answers(
        self, stub_server: str, capsys
    ) -> None:
        port = stub_server.rsplit(":", 1)[1]
        code, body = run_cli(capsys, "--discover", "--port", port, "health")
        assert code == 0
        assert body["ok"] is True
