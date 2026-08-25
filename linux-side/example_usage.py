"""Runnable example of the safe two-step flow. Nothing executes without a token.

    export MT5_BRIDGE_API_KEY="<same key as the Windows .env>"
    python3 example_usage.py
"""

from __future__ import annotations

import os
import sys

from mt5_client import BridgeClient
from mt5_client.exceptions import BridgeClientError, BridgeUnavailable

SYMBOL = "US100Cash"


def main() -> int:
    api_key = os.environ.get("MT5_BRIDGE_API_KEY")
    if not api_key:
        print("Set MT5_BRIDGE_API_KEY first.", file=sys.stderr)
        return 1

    try:
        # Probes localhost and the WSL host IP, so no manual URL hunting.
        client = BridgeClient.autodiscover(api_key)
    except BridgeUnavailable as exc:
        print(exc, file=sys.stderr)
        return 1

    with client:
        print("health :", client.health())
        account = client.account()
        print(f"account: {account.login} equity={account.equity} {account.currency}")
        print("tick   :", client.tick(SYMBOL))

        # Ask the broker what volume is actually allowed for this symbol,
        # instead of guessing 0.01 - minimum lot size varies a lot between
        # brokers and even between accounts on the same broker.
        spec = client.symbol_spec(SYMBOL)
        print(f"spec   : min={spec.volume_min} max={spec.volume_max} step={spec.volume_step}")
        order_volume = spec.volume_min

        # Step 1 - read-only. Validates size/symbol and quotes a live price.
        preview = client.preview_order(SYMBOL, "buy", order_volume, stop_loss=19_800.0)
        print("\nProposed trade:", preview.summary())
        print(f"confirm_token expires in {preview.expires_in_seconds}s")

        # Step 2 - would send the order. Left commented on purpose: a human
        # (or an explicit Discord confirmation) belongs between these lines.
        #
        # result = client.execute_order(
        #     SYMBOL, "buy", order_volume, preview.confirm_token, stop_loss=19_800.0
        # )
        # print("executed:", result)

        print("\nNothing was sent to the broker. Uncomment execute_order to trade.")

        for row in client.journal(symbol=SYMBOL, limit=5):
            print(f"  {row['created_at']} {row['event']:<9} {row['symbol']} {row['volume']}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BridgeClientError as exc:
        print(f"bridge error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
