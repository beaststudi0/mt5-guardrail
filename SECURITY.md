# Security Policy

This bridge can place, modify, and close orders on a real MetaTrader 5
trading account. Take its security posture at least as seriously as you
would any other software with direct access to money.

---

## Threat model

**In scope — what this bridge defends against:**

- A caller without the correct `x-api-key` reaching any endpoint.
- Brute-forcing or scanning for a valid API key.
- An order executing without having gone through a matching `preview`
  first (when `MT5_REQUIRE_CONFIRM_TOKEN=true`, the default).
- A confirm token being replayed, reused for a different order, or used
  after expiry.
- A single call, or a bug/runaway loop, placing an unbounded number of
  orders or an oversized position.
- An executed trade with no recorded justification.
- A misconfigured or untested setup opening a new position on a live
  account by accident (demo-only is the default; see below).
- A browser-based cross-origin request reaching a write endpoint (see
  the CORS section below).

**Out of scope — this bridge does not, and cannot, defend against:**

- **The strategy or agent deciding *when* to call this bridge.** This
  project guards the mechanics of execution, not the judgment behind it.
  A well-formed, fully-authorized, reasoning-attached order for a bad
  trade is still a bad trade.
- **A compromised Windows machine.** If an attacker has code execution
  on the box running the bridge, they can read `.env`, the MT5 terminal's
  own stored credentials, and everything else on that machine. This
  bridge assumes the host it runs on is itself trustworthy.
- **Broker-side risk** — slippage, requotes, and execution differing
  from what was quoted in `preview`. The bridge reports what the broker
  actually did; it cannot control it.

---

## Network exposure

**Default configuration binds to `127.0.0.1` only** (`MT5_BRIDGE_HOST`
in `.env.example`) — the bridge is not reachable from any other machine
unless you deliberately change this.

If you do change `MT5_BRIDGE_HOST` to `0.0.0.0` or a LAN-visible address
(for example, to reach it from WSL2 in NAT mode without port
mirroring), be aware that:

- The API key becomes the *only* thing standing between anyone on that
  network and your trading account.
- The auth rate-limiter (`AuthAttemptLimiter` in `guards.py`) helps
  against brute-forcing but is not a substitute for keeping the bridge
  off a network you don't trust.
- Consider a firewall rule scoping access to the specific host that
  needs it, rather than the whole subnet.

**Never expose this bridge directly to the public internet.** It has no
TLS of its own — run it behind a reverse proxy with TLS termination and
its own access controls if you need remote access, and treat that
proxy's configuration as part of this bridge's security surface.

---

## Already hardened (verified, not just claimed)

| Area | What's actually done |
|---|---|
| API key comparison | `secrets.compare_digest`, not `==` — a naive equality check leaks the correct key one byte at a time via response-time differences. |
| Confirm tokens | `secrets.token_urlsafe(16)` — 128 bits of cryptographically secure randomness, single-use, short-lived, and bound to the exact symbol/side/volume they were minted for. |
| Failed-auth lockout | Repeated bad `x-api-key` attempts within a short window trigger a temporary lockout (`AuthAttemptLimiter`), independent of how strong the underlying key is. |
| Demo-only by default | `execute` refuses to open a new position unless the connected account reports MT5's own `trade_mode == 0` (demo) — `MT5_REQUIRE_DEMO_ACCOUNT=true` by default. A misconfigured `.env` pointing at a live account fails closed instead of silently trading it. `close` is deliberately exempt (see `trading.py`), so an already-open live position can never be stranded by this setting. |
| CORS | Deliberately absent. The required `x-api-key` custom header forces a CORS preflight for any cross-origin browser request; with no origin allowlisted, that preflight fails closed. Adding a permissive CORS policy here would silently remove this protection — see the comment in `app.py`. |
| Response headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Cache-Control: no-store` on every response. |
| Error responses | The catch-all exception handler logs full tracebacks server-side but never returns internal details (stack traces, file paths, native error codes beyond what's explicitly whitelisted) to the caller. |
| Secrets in config | `bridge_api_key`, `password`, and `webhook_url` are `pydantic.SecretStr` — they don't appear in `repr()`, accidental `print()`, or structured logging by default. |

---

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security
vulnerability.**

Instead, use GitHub's private vulnerability reporting for this
repository (Security tab → "Report a vulnerability"), or open an issue
asking for a private contact channel if that isn't available yet.

When reporting, please include:

- The affected file/endpoint and, if possible, a minimal reproduction.
- What you'd expect to happen vs. what actually happens.
- Whether you believe it's exploitable against a bridge running with
  default configuration, or only under a specific non-default setup.

We'll acknowledge reports as quickly as we can and aim to have a fix or
a documented mitigation before any public disclosure.

---

## A note on scope for reviewers and contributors

If you're auditing this project (including for a funding or grant
review): the guardrails documented in the README and this file are the
actual security surface this project claims to cover. If you find a way
around any of them — a code path that executes without a valid token, a
way to exceed the daily limit, a timing side-channel in the auth check,
anything in that category — that is exactly the kind of finding this
policy exists for, please report it as above rather than opening a
public issue or PR that demonstrates it.
