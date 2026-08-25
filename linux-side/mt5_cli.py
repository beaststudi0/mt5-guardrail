#!/usr/bin/env python3
"""CLI wrapper around mt5_client, built for an LLM agent's shell/exec tool.

Design constraints that matter for an agent caller, not a human:
- Every invocation prints exactly ONE line of JSON to stdout, success or
  failure. An agent parsing output does not want to distinguish "normal
  output" from "a traceback" - there is only ever one shape to parse.
  This includes argument errors: argparse normally prints usage to stderr
  and exits 2, which is exactly the unparseable shape we promised never to
  produce, so the parser is subclassed to emit JSON instead. (`--help` is
  the one deliberate exception: multi-line human/agent-readable text.)
- Exit code is 0 on success, 1 on any handled error (bad args, bridge
  unreachable, broker rejection). A crash the agent didn't handle is a bug
  in this script, not something the agent should have to work around.
- Error JSON carries everything the client knows (`error_type`, and
  `status_code`/`retcode` when present), so an agent can branch on
  "token expired" vs "broker rejected" without string-matching.
- No interactive prompts, no partial output, no logging noise on stdout.
  Anything diagnostic goes to stderr instead.

Subcommands mirror BridgeClient one-to-one, so this file needs no separate
design document - `mt5_cli.py --help` and the client's own docstrings are
the whole reference.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from typing import Any

from mt5_client import BridgeClient
from mt5_client.config import DEFAULT_PORT
from mt5_client.exceptions import BridgeClientError, BridgeHTTPError, OrderRejected


def _to_jsonable(value: Any) -> Any:
    """Dataclasses (all of mt5_client's models) aren't JSON-serialisable by
    default; unwrap them recursively so `json.dumps` just works."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, default=str))


def _ok(data: Any) -> None:
    _emit({"ok": True, "data": _to_jsonable(data)})


def _err(message: str, **extra: Any) -> None:
    _emit({"ok": False, "error": message, **extra})


def _parse_reasoning(raw: str) -> dict[str, Any]:
    """`type=` callback for --reasoning. Raising ArgumentTypeError here routes
    through _AgentParser.error() below, so a malformed JSON string still
    produces the one-line-JSON-output contract instead of an argparse
    traceback the agent can't parse."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"--reasoning must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--reasoning must be a JSON object, e.g. '{\"key\": \"value\"}'")
    return parsed


def cmd_health(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.health())
    return 0


def cmd_account(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.account())
    return 0


def cmd_positions(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.positions(symbol=args.symbol))
    return 0


def cmd_tick(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.tick(args.symbol))
    return 0


def cmd_spec(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.symbol_spec(args.symbol))
    return 0


def cmd_candles(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.candles(args.symbol, timeframe=args.timeframe, count=args.count))
    return 0


def cmd_journal(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.journal(symbol=args.symbol, limit=args.limit))
    return 0


def cmd_preview(client: BridgeClient, args: argparse.Namespace) -> int:
    """Read-only: quotes a price and mints a confirm_token. Sends nothing."""
    preview = client.preview_order(
        args.symbol, args.side, args.volume,
        stop_loss=args.sl, take_profit=args.tp, comment=args.comment,
        reasoning=args.reasoning,
    )
    _ok(preview)
    return 0


def cmd_execute(client: BridgeClient, args: argparse.Namespace) -> int:
    """The only subcommand that can move money. Requires a confirm_token
    minted by a matching `preview` call within the last ~60 seconds."""
    result = client.execute_order(
        args.symbol, args.side, args.volume, args.confirm_token,
        stop_loss=args.sl, take_profit=args.tp, comment=args.comment,
        reasoning=args.reasoning,
    )
    _ok(result)
    return 0


def cmd_close_preview(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.preview_close(args.ticket))
    return 0


def cmd_close(client: BridgeClient, args: argparse.Namespace) -> int:
    _ok(client.close_position(args.ticket, args.confirm_token))
    return 0


class _AgentParser(argparse.ArgumentParser):
    """argparse that keeps the one-line-JSON promise on bad arguments.

    Subparsers created via `add_subparsers` inherit this class automatically
    (argparse defaults `parser_class` to `type(self)`), so a bad subcommand
    argument gets the same JSON treatment as a bad top-level one.
    """

    def error(self, message: str) -> Any:  # noqa: ANN401 - argparse signature
        _err(f"invalid arguments: {message}", error_type="ArgumentError")
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = _AgentParser(
        prog="mt5_cli.py",
        description="Shell interface to the MT5 bridge, for agent exec tools.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Probe candidate WSL->Windows URLs instead of trusting "
             "MT5_BRIDGE_URL. Slower first call; use when the URL is unknown.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Bridge port for --discover (default {DEFAULT_PORT}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Bridge + terminal connectivity. No auth needed.")

    sub.add_parser("account", help="Balance, equity, currency, leverage.")

    p = sub.add_parser("positions", help="Open positions, optionally filtered.")
    p.add_argument("--symbol")

    p = sub.add_parser("tick", help="Live bid/ask/spread for a symbol.")
    p.add_argument("symbol")

    p = sub.add_parser("spec", help="Broker volume limits for a symbol.")
    p.add_argument("symbol")

    p = sub.add_parser("candles", help="Historical OHLC bars.")
    p.add_argument("symbol")
    p.add_argument("--timeframe", default="M15",
                    choices=["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    p.add_argument("--count", type=int, default=100)

    p = sub.add_parser("journal", help="Past trade events, including rejections.")
    p.add_argument("--symbol")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser(
        "preview",
        help="Validate + quote an order. Sends NOTHING to the broker. "
             "Returns a confirm_token required by 'execute'.",
    )
    p.add_argument("symbol")
    p.add_argument("side", choices=["buy", "sell"])
    p.add_argument("volume", type=float)
    p.add_argument("--sl", type=float, help="Stop loss price")
    p.add_argument("--tp", type=float, help="Take profit price")
    p.add_argument("--comment", default="mt5-guardrail")
    p.add_argument(
        "--reasoning", type=_parse_reasoning, default=None,
        help=(
            "JSON object explaining the decision, e.g. "
            '\'{"signal_source": "Agency 21:27", "sma50": 28945}\'. '
            "Stored in the journal for later review/training - anything "
            "left out here cannot be reconstructed after the fact."
        ),
    )

    p = sub.add_parser(
        "execute",
        help="Send a real order. Requires the confirm_token from a matching "
             "'preview' (positional, after volume).",
    )
    p.add_argument("symbol")
    p.add_argument("side", choices=["buy", "sell"])
    p.add_argument("volume", type=float)
    p.add_argument("confirm_token")
    p.add_argument("--sl", type=float)
    p.add_argument("--tp", type=float)
    p.add_argument("--comment", default="mt5-guardrail")
    p.add_argument(
        "--reasoning", type=_parse_reasoning, default=None,
        help="Same as preview's --reasoning. Pass it again here if this "
             "executed order should carry it too - preview's reasoning is "
             "not automatically reused.",
    )

    p = sub.add_parser("close-preview", help="Quote closing an open position.")
    p.add_argument("ticket", type=int)

    p = sub.add_parser(
        "close",
        help="Close an open position. Requires the confirm_token from a "
             "matching 'close-preview' (positional, after ticket).",
    )
    p.add_argument("ticket", type=int)
    p.add_argument("confirm_token")

    return parser


_DISPATCH = {
    "health": cmd_health,
    "account": cmd_account,
    "positions": cmd_positions,
    "tick": cmd_tick,
    "spec": cmd_spec,
    "candles": cmd_candles,
    "journal": cmd_journal,
    "preview": cmd_preview,
    "execute": cmd_execute,
    "close-preview": cmd_close_preview,
    "close": cmd_close,
}


def _build_client(args: argparse.Namespace) -> BridgeClient:
    if not args.discover:
        return BridgeClient.from_env()

    api_key = os.environ.get("MT5_BRIDGE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "MT5_BRIDGE_API_KEY is not set. --discover still needs the key to "
            "build the client it hands back."
        )
    return BridgeClient.autodiscover(api_key, port=args.port or DEFAULT_PORT)


def _error_extras(exc: BridgeClientError) -> dict[str, Any]:
    """Everything the client knows about the failure, so an agent can branch
    on fields instead of string-matching the message."""
    extras: dict[str, Any] = {"error_type": type(exc).__name__}
    if isinstance(exc, BridgeHTTPError):
        extras["status_code"] = exc.status_code
        if isinstance(exc, OrderRejected) and exc.retcode is not None:
            extras["retcode"] = exc.retcode
    return extras


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        # Bad args: _AgentParser.error already emitted the JSON line.
        # --help: argparse printed its (deliberately non-JSON) text, code 0.
        return int(exc.code or 0)

    try:
        client = _build_client(args)
    except Exception as exc:  # noqa: BLE001 - config errors must reach the agent as JSON
        _err(f"client configuration failed: {exc}", error_type=type(exc).__name__)
        return 1

    try:
        with client:
            return _DISPATCH[args.command](client, args)
    except BridgeClientError as exc:
        _err(str(exc), **_error_extras(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - never let a raw traceback reach the agent
        _err(f"unexpected error: {exc}", error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
