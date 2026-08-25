#!/usr/bin/env python3
"""Ground-truth trade verifier. Does NOT trust the bot's narrative.

Run this whenever the Trader bot claims to have executed something. It reads
the same Journal the bot is supposed to read, but as plain code with no room
to embellish - a preview with no matching executed event is reported as
exactly that, nothing more.

Usage:
    cd ~/mt5-bridge-client
    python3 verify_trades.py                  # last 20 events
    python3 verify_trades.py --limit 50
    python3 verify_trades.py --symbol US100Cash
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone

from mt5_client import BridgeClient
from mt5_client.exceptions import BridgeClientError


def _extract_reasoning(context_raw: str | None) -> dict | None:
    """context is a JSON string as stored by the journal (bid/ask/spread,
    plus a nested "reasoning" key when the caller supplied one). Returns
    None whenever there is nothing usable for a later training pass -
    including when context itself is missing or unparseable, since either
    way the row carries no reconstructable decision context."""
    if not context_raw:
        return None
    try:
        context = json.loads(context_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return context.get("reasoning")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    try:
        client = BridgeClient.from_env()
    except Exception as exc:  # noqa: BLE001
        print(f"Cannot build client: {exc}", file=sys.stderr)
        return 1

    try:
        with client:
            rows = client.journal(symbol=args.symbol, limit=args.limit)
    except BridgeClientError as exc:
        print(f"Cannot reach bridge: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("No journal entries at all. The bridge has never been called")
        print("for a trade - not by hand, not by the bot.")
        return 0

    counts = Counter(row["event"] for row in rows)
    real_tickets = [row for row in rows if row.get("ticket") not in (None, "None")]

    print("=" * 60)
    print(f"GROUND TRUTH - last {len(rows)} journal entries")
    print(f"Checked at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    print()
    print("Event counts:", dict(counts))
    print()

    if not real_tickets:
        print("*** NO REAL ORDERS. Every entry has ticket=null. ***")
        print("Whatever the bot said in Discord about executing trades,")
        print("NONE of it reached MT5. This is confirmed fiction, not a")
        print("technical failure - a technical failure would still show")
        print("an 'executed' attempt with a rejection reason.")
    else:
        print(f"{len(real_tickets)} entr(y/ies) with a REAL ticket:")
        for row in real_tickets:
            print(
                f"  {row['created_at']}  {row['event']:<9}  "
                f"{row['symbol']}  vol={row['volume']}  ticket={row['ticket']}"
            )

    print()
    print("Full raw entries (most recent first):")
    for row in rows:
        flag = "REAL" if row.get("ticket") not in (None, "None") else "no-ticket"
        error = f"  error={row['error']!r}" if row.get("error") else ""
        print(f"  [{flag:>9}] {row['created_at']}  {row['event']:<9}  {row['symbol']}{error}")

        reasoning = _extract_reasoning(row.get("context"))
        if reasoning:
            print(f"             reasoning: {reasoning}")
        else:
            print(f"             reasoning: (none attached - not usable for training later)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
