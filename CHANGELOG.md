# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [v1.1.0-alpha.1] - 2026-08-28

### Added

- Full automated test suite for `windows-side` covering the trading
  service, guards, terminal connection handling, the HTTP API layer, and
  a real client-to-server end-to-end path — 126 tests total across both
  `windows-side` and `linux-side`, run against a fake MT5 terminal and a
  local stub HTTP server (no real broker or network access required).
  In particular, `trading.py` — the module that actually places, closes,
  and modifies orders — previously had no dedicated tests at all.
- `AuthAttemptLimiter`: repeated failed `x-api-key` attempts within a
  60-second window now trigger a temporary 30-second lockout (with a
  `Retry-After` header on the `429` response), independent of how strong
  the configured key is. See `guards.py` and `SECURITY.md`.
- `MT5_REQUIRE_DEMO_ACCOUNT` (default `true`): `execute` now refuses to
  open a new position unless the connected MT5 account reports
  `trade_mode == 0` (demo) — a freshly cloned copy of this project can no
  longer trade a live account by accident. Deliberately does not affect
  `close`: an already-open live position must always stay closeable
  through this bridge. See `trading.py`, `SECURITY.md`, and the README's
  "Four pillars of security" section.

### Changed

- `windows-side`: `is_connected` no longer round-trips to the MT5
  terminal on every single call. Every trading operation checked
  connection freshness by calling `connect()` first, which meant two
  native IPC round-trips per request instead of one. It's now a
  TTL-gated staleness check (`connection_check_interval`, default 5s) —
  connection health is still verified, just not re-verified on every
  request within the TTL window.
- `linux-side`: replaced `requests` with the standard library's
  `urllib.request`. `mt5_cli.py` runs as a fresh subprocess per
  invocation (by design — see its own docstring), so `requests`'
  connection-pooling advantage never applied here; only its import cost
  did. Measured import time dropped from ~170ms to ~88ms (~52%) per
  invocation.
- Default order comment tag changed to `mt5-guardrail` for consistency
  with the project name.

### Fixed

- Documented two real onboarding failures found while setting this
  project up on a fresh machine, with fixes, in the README's
  Troubleshooting section: Windows venvs breaking after the project
  folder is moved or renamed (venv launchers embed an absolute path at
  creation time and are not relocatable), and `pip install` failing
  with an SSL certificate mismatch under antivirus software that does
  HTTPS scanning (Kaspersky in particular).

---

## [v1.0.0-beta.1] - 2026-08-25

### Added

- **Beta Release:** Feature-complete public beta of the `mt5-guardrail` REST API bridge. Ready for community testing and feedback.
- Core cross-OS communication allowing Linux/WSL clients to securely execute commands on MetaTrader 5 (Windows).
- FastAPI server implementation for high-speed HTTP request processing.
- Two-step preview → confirm flow: no order can execute from a single call. `preview` quotes a price and returns a short-lived, single-use token; `execute` must present that exact token, bound to the same symbol/side/volume.
- Server-enforced daily order ceiling and per-order max lot size, independent of what the caller requests.
- Scale-in cooldown on live accounts, preventing repeated opens on the same symbol within a configurable window (demo accounts exempt by design).
- Mandatory, checkable reasoning (`signal_source`, `agency_reference`) required on every executed order.
- API Key Authentication system (`secrets.compare_digest`, constant-time) to secure endpoints from unauthorized access.
- Environment variable management setup (via `.env`, validated at startup with `pydantic-settings`) to prevent hardcoded secrets and fail fast on misconfiguration.
- Comprehensive `README.md` with cross-OS installation instructions and usage examples.
- Standardized open-source structure with Apache License 2.0.

### Security

- Security headers middleware on every response: `X-Content-Type-Options: nosniff` (MIME-sniffing), `X-Frame-Options: DENY` (clickjacking), `Cache-Control: no-store` (prevents order/journal data from being cached).
- No CORS policy configured, deliberately — the required `x-api-key` header forces a failed preflight on any cross-origin browser request. See the comment in `app.py`.
- Swept and sanitized the codebase before publishing: no personal logic, private account numbers, internal project references, or hardcoded API keys/credentials are present in the public source.

[Unreleased]: https://github.com/beaststudi0/mt5-guardrail/compare/v1.1.0...HEAD
[v1.1.0-alpha.1]: https://github.com/beaststudi0/mt5-guardrail/compare/v1.0.0-beta...v1.1.0
[v1.0.0-beta]: https://github.com/beaststudi0/mt5-guardrail/releases/tag/v1.0.0-beta
