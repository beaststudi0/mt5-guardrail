# mt5-guardrail

**A safety-guardrailed REST bridge between an LLM agent and a MetaTrader 5 terminal.**

Most MT5-to-REST bridges answer one question: *how do I send an HTTP
request that places a trade?* `mt5-guardrail` starts from a different
question: **an LLM agent is going to be the one deciding when to call
that endpoint — what needs to be true before that's actually safe?**

That question is the whole point of this project. Everything in it
exists to answer it: a two-step preview → confirm flow so nothing
executes on a single unchecked call, a hard ceiling on order size and
daily order count, a cooldown against an agent repeatedly scaling into
the same position, and a requirement that every executed trade carries a
specific, checkable justification — not just an assertion that "the
agent decided to."

---

## Why this exists

Handing an LLM agent the ability to place real trades is a meaningfully
different problem from handing a human a REST API. A human pauses. An
agent, given a tool call and no friction in the way, does not. The
guardrails in this project are the friction, made explicit and testable
instead of left to a prompt instruction that can be missed.

| Without a guardrail layer | With `mt5-guardrail` |
|---|---|
| One call places a trade | `preview` (read-only, quotes a price) → `execute` (requires the token from that exact preview) |
| Nothing stops a runaway loop | Hard daily order ceiling, enforced server-side |
| An agent can pile into the same symbol repeatedly | Cooldown between opening a second position on the same symbol (live accounts) |
| "The agent decided to trade" is unfalsifiable after the fact | Every execute must cite a specific, checkable decision record |
| A leaked or guessed API key has unlimited attempts | Failed-auth attempts are rate-limited and lock out |

None of this replaces a human's own judgment about whether to let an
agent trade at all. It exists for the moment after that decision has
already been made, to keep the *mechanics* of execution from being the
weak point.

---

## Architecture

```
┌─────────────────────┐        HTTP + x-api-key        ┌──────────────────────┐
│  Linux / WSL         │ ──────────────────────────────▶│  Windows              │
│  mt5_client          │                                 │  mt5_bridge (FastAPI) │
│  (your agent/script) │ ◀────────────────────────────── │  ↓                    │
└─────────────────────┘         JSON responses           │  MetaTrader 5 terminal│
                                                           └──────────────────────┘
```

The split exists because the official `MetaTrader5` Python package only
runs on Windows, while most agent tooling (and WSL) runs on Linux. The
bridge is the only thing that touches MT5 directly; the client is a
thin, portable HTTP wrapper around it.

- **`windows-side/`** — the FastAPI server. Runs on native Windows Python,
  next to a running MT5 terminal. This is where every guardrail actually
  lives and is enforced.
- **`linux-side/`** — the client library (`mt5_client`) plus a CLI wrapper
  (`mt5_cli.py`) built specifically for an LLM agent's shell/exec tool: one
  JSON line out per call, a single predictable shape for both success and
  failure, no interactive prompts.

See each directory's own module docstrings for the internal layering —
`windows-side/mt5_bridge/__init__.py` in particular lays out the
dependency direction between `api/ → trading → terminal → guards/journal`.

---

## Quick start

### 1. Windows side (where MT5 runs)

```powershell
cd windows-side
python -m venv venv
venv\Scripts\pip install -r requirements.txt

copy .env.example .env
# edit .env: set MT5_BRIDGE_API_KEY, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER

.\start_bridge.ps1
# or: python main.py
```

The bridge validates its configuration at startup and fails fast and
loudly if anything required is missing — check the error message against
`.env.example` if it won't start.

### 2. Linux / WSL side (your agent's environment)

```bash
cd linux-side
pip install -r requirements.txt --break-system-packages

export MT5_BRIDGE_API_KEY="same key as the Windows .env"
python3 example_usage.py
```

`example_usage.py` runs the full safe flow end to end — health check,
account snapshot, a live quote, a `preview_order` call — with the actual
`execute_order` call left commented out on purpose. Uncomment it once
you've confirmed everything else works.

For an LLM agent, the entry point is `mt5_cli.py` instead of importing
the library directly:

```bash
python3 mt5_cli.py health
python3 mt5_cli.py tick US100Cash
python3 mt5_cli.py preview US100Cash buy 0.01 --reasoning '{"signal_source": "...", "agency_reference": "..."}'
python3 mt5_cli.py execute US100Cash buy 0.01 <confirm_token> --reasoning '{...}'
```

Every subcommand prints exactly one line of JSON, success or failure —
see `mt5_cli.py`'s own docstring for the full design rationale (it's
written specifically for a shell/exec-tool caller, not a human at a
terminal).

---

## The guardrails, specifically

| Guardrail | What it prevents |
|---|---|
| **Preview → confirm token** | An order can never execute from a single call. `preview` validates and quotes a price; the short-lived, single-use token it returns is required by `execute`, and must match the same symbol/side/volume — a token minted for 0.01 lots cannot execute 0.10. |
| **Daily order ceiling** | A runaway loop (agent or bug) is capped, not unlimited. Resets at UTC midnight. |
| **Max lot size** | A single order can never exceed a configured ceiling, regardless of what the agent requests. |
| **Scale-in cooldown** | On live accounts, opening a second position on the same symbol too soon after the first is blocked for a configurable window. Demo accounts are exempt by design. |
| **Mandatory reasoning** | `execute` requires `reasoning.signal_source` and `reasoning.agency_reference` — a specific, checkable decision record, not a free-text assertion that reasoning happened. |
| **Constant-time auth + lockout** | The API key is compared with `secrets.compare_digest` (not `==`, which leaks timing information one byte at a time), and repeated failed attempts trigger a temporary lockout. |
| **No CORS, on purpose** | The required custom `x-api-key` header forces a browser to preflight any cross-origin request. With no CORS policy allowing any origin, that preflight fails — a malicious webpage cannot make an authenticated request to your bridge even if it somehow knew the key. See the comment in `app.py` before "fixing" this by adding permissive CORS. |
| **Allowlisted symbols** | The bridge only trades symbols you explicitly configure — everything else is rejected before it reaches the broker. |

Full behavioral detail and the reasoning behind each one is in the
module docstrings next to the code — `guards.py`, `trading.py`, and
`models.py` in particular.

---

## Configuration

Every setting is documented in `windows-side/.env.example` and
`linux-side/.env.example`. Windows-side configuration is validated with
`pydantic-settings` at process start — a missing or malformed value fails
immediately with a clear message, not on the first request.

---

## Testing

```bash
cd windows-side
pip install -r requirements-dev.txt --break-system-packages
pytest

cd ../linux-side
pip install -r requirements-dev.txt --break-system-packages
pytest
```

The windows-side suite runs entirely against a fake MT5 terminal — no
real MetaTrader 5 installation or account is needed to run it. The
linux-side suite runs the real client against a local stub HTTP server.
Neither suite touches a real broker.

---

## Troubleshooting

**`pip`/`pytest` fails with `Fatal error in launcher: Unable to create
process using "...\venv\Scripts\python.exe"`, pointing at a path that
doesn't match where the project actually is.**

Windows venvs are not relocatable: `venv\Scripts\pip.exe` (and every
other launcher in that folder) has the absolute path to `python.exe`
baked in at the moment the venv was created. If you create the venv in
one folder and then move or rename that folder afterward, every
launcher still points at the old, now-nonexistent path. The fix is to
recreate the venv from its current location — there's no way to
"repoint" an existing one:

```powershell
cd <wherever the project actually is now>
deactivate           # if a venv is currently active
Remove-Item -Recurse -Force venv
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

---

**`pip install` fails with `There was a problem confirming the ssl
certificate: ("The certificate's CN name does not match the passed
value.",)`**

This is almost always antivirus software doing HTTPS scanning —
injecting its own certificate into every HTTPS connection your machine
makes, including pip's connection to PyPI. Python correctly rejects the
substituted certificate (this is the same protection that would catch a
real man-in-the-middle attack; it just doesn't know your antivirus is a
benign one). Kaspersky is a common trigger for this specific symptom,
but any antivirus with "HTTPS scanning" or "web protection" can do it.

Unblock immediately (scoped to just these two hosts, not a global
change):

```powershell
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements-dev.txt
```

Fix it properly, once, so you never need the flag above again on this
machine:

```powershell
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org pip-system-certs
pip install -r requirements-dev.txt   # no --trusted-host needed from here on
```

`pip-system-certs` patches pip to trust the Windows certificate store
instead of only its own bundled list — which already trusts your
antivirus's injected certificate, since the antivirus installed it
there itself.

---

## Security

See [SECURITY.md](SECURITY.md) for the threat model, what's already
hardened, and how to report a vulnerability. Short version: this bridge
can move real money on a real trading account. Treat your `.env` file
and your `MT5_BRIDGE_API_KEY` accordingly, and read SECURITY.md's
network-exposure section before running this anywhere other than
`127.0.0.1`.

---

## Contributing

Issues and pull requests are welcome. If you're adding a new guardrail
or changing an existing one's behavior, please include tests — every
guardrail in this codebase exists because of a specific failure mode it
closes, and tests are what keep it closed after the next refactor.

---

## Disclaimer

This software connects to a real trading account and can place, modify,
and close real orders. It is provided as infrastructure tooling, not
financial advice, and not a trading strategy. **You are solely
responsible for any trades it executes on your account, and for testing
thoroughly on a demo account before ever pointing it at a live one.**
Automated and agent-driven trading carries real financial risk,
including the risk of losses beyond what you intend. The guardrails in
this project reduce specific, known failure modes — they do not
eliminate trading risk itself, and they do not audit or validate
whatever strategy or agent is deciding *when* to call this bridge.

---

## License

Apache License 2.0 - see [LICENSE](LICENSE).
