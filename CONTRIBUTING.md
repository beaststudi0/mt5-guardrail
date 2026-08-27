# Contributing to mt5-guardrail

Thanks for considering a contribution. This project exists to make one
specific thing safer — an LLM agent placing real trades through MetaTrader 5
— and the guardrails only stay trustworthy if changes to them are reviewed
and tested as carefully as the originals were. This document explains how
to get a dev environment running on both sides of the bridge, what a good
pull request looks like here, and where the lines are that we don't cross.

---

## Before you write any code

**Open an issue first for anything beyond a small fix.** New guardrails,
changes to existing guardrail behavior, or anything touching authentication
or order execution should be discussed before a PR lands, not after. Typo
fixes, doc corrections, and small non-behavioral cleanups can skip this.

**Found a security issue?** Do not open a public issue or PR that
demonstrates it. See [SECURITY.md](SECURITY.md) for how to report it
privately.

---

## Development setup

This is a two-sided project — `windows-side/` only runs on Windows (the
`MetaTrader5` package is Windows-only), `linux-side/` runs anywhere Python
does. You don't need both running to contribute to one of them, but if
you're touching the wire protocol between them, test both.

### windows-side (the bridge server)

```powershell
cd windows-side
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

ruff check .
ruff format --check .
mypy .
pytest
```

You do **not** need a real MT5 terminal or trading account to develop or
test this side. The entire suite runs against `tests/fakes.py`'s `FakeMT5`
— a fake that implements the same surface as the real `MetaTrader5` module.
If you're adding a guardrail, add its test here using the same fixtures
(`conftest.py`) the existing tests use, not a real terminal.

### linux-side (the client + CLI)

```bash
cd linux-side
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt

ruff check .
mypy .
pytest
```

`linux-side`'s tests run the real `mt5_client` against a local stub HTTP
server (see `tests/test_client.py`) — no live bridge or network access is
needed either.

---

## Code style and quality bar

- **`ruff check` and `mypy --strict` both have to pass.** `pyproject.toml`
  defines the exact rule sets; run them locally before opening a PR, CI
  will re-run them regardless.
- **Match the module docstring style already in the codebase.** Most
  modules here explain *why* a design decision was made, not just what the
  code does — see `guards.py` or `app.py`'s CORS section for the pattern.
  If you're adding non-obvious behavior, explain the reasoning next to it,
  the same way.
- **A monotonic clock is injected wherever time matters** (see `Clock` in
  `guards.py`). This keeps time-based tests instant instead of sleeping.
  Follow the same pattern for any new time-based logic instead of calling
  `time.time()`/`time.monotonic()` directly inside the class.

---

## Tests are not optional

Every guardrail in this codebase exists because of a specific failure mode
it closes. A guardrail change without a test that would have caught the
old behavior isn't reviewable — the whole point of the test suite is that
the next refactor can't quietly reopen a closed gap.

At minimum, a PR that touches behavior should include:

- A test for the new/changed behavior itself.
- A test for the boundary case (what happens exactly at the limit, not
  just comfortably inside or outside it — see `test_guards.py` for the
  existing style: `allows_up_to_the_limit_then_blocks`, not just "blocks
  when over").
- If it's a security-relevant change (auth, rate limiting, token handling):
  a test for the failure path, not just the happy path.

If you're touching `windows-side/mt5_bridge/trading.py` specifically —
its own docstring calls it out as "the only method that can move real
money" — the bar is higher. Explain in the PR description what property
you verified and how, the way `test_trading.py`'s docstrings do for the
existing safety properties (quota isn't burned on a bad token, demo
accounts skip the scale-in guard, close bypasses the daily limiter).

---

## Opening a pull request

1. Fork, branch off `main`, keep the PR scoped to one change.
2. Make sure `ruff`, `mypy`, and `pytest` are clean on both sides you
   touched.
3. Describe *why*, not just *what* — what failure mode does this close or
   what does it enable, and what did you verify (a test run, not just a
   read-through — see the project's own working discipline: nothing here
   gets called "fixed" without an actual test proving it).
4. If your change affects `.env.example`, `README.md`'s guardrail table,
   or `SECURITY.md`'s "already hardened" table, update those in the same
   PR — they're meant to describe the actual current behavior, not a
   snapshot from whenever they were last written.

Small, focused PRs get reviewed faster than large ones that bundle
unrelated cleanups with a behavioral change.

---

## What we're not looking for

- Convenience features that trade away a guardrail (e.g., a flag that
  skips the confirm-token step by default, broader CORS, disabling the
  auth rate limiter) without a very concrete justification and its own
  tests for the new failure modes it opens up.
- Dependencies added to `linux-side/requirements.txt` without checking
  whether they're needed on *every* CLI invocation first — see the comment
  at the top of that file for why import cost is treated as a real budget
  there, not a nitpick.

---

## Questions

Open an issue for anything not covered here. If you're not sure whether
something counts as a security-relevant change, treat it as one and ask
first — see the [Security](SECURITY.md) doc for the report path.
