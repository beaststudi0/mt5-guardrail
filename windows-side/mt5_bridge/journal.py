"""Trade journal - the substrate for "learn from past mistakes".

Every order attempt is recorded, *including rejections*, because a bot that
only remembers its fills learns nothing. Each row carries the market context
at decision time, so a later reflection pass can ask questions like
"what did the spread look like on the trades I lost?".

Storage is behind a Protocol. SQLite (stdlib, zero dependencies) is the default;
swap in Postgres later without touching the trading service.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    event         TEXT    NOT NULL,   -- preview | executed | rejected | closed
    symbol        TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    volume        REAL    NOT NULL,
    price         REAL,
    stop_loss     REAL,
    take_profit   REAL,
    ticket        INTEGER,
    retcode       INTEGER,
    profit        REAL,
    error         TEXT,
    context       TEXT                -- JSON: spread, equity, reasoning, ...
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, created_at);
CREATE INDEX IF NOT EXISTS idx_trades_event       ON trades(event);
"""


@dataclass(slots=True)
class JournalEntry:
    event: str
    symbol: str
    side: str
    volume: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    ticket: int | None = None
    retcode: int | None = None
    profit: float | None = None
    error: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Journal(Protocol):
    def record(self, entry: JournalEntry) -> None: ...
    def recent(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...


class NullJournal:
    """Used when journalling is disabled. Keeps callers branch-free."""

    def record(self, entry: JournalEntry) -> None:  # noqa: D102
        return None

    def recent(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return []


class SqliteJournal:
    """Thread-safe writer. Never raises into the trading path.

    A failed audit write must not cancel a successful trade, nor mask the real
    error on a failed one - so writes are best-effort and logged loudly.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent reads during writes
        return conn

    # NB: `sqlite3.Connection`'s own context manager only manages the
    # *transaction* - it never closes the handle. `closing(...)` does, which
    # is what stops every call from leaking a file descriptor and an open
    # WAL side-file.
    def _init_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def record(self, entry: JournalEntry) -> None:
        payload = asdict(entry)
        payload["created_at"] = entry.created_at.isoformat()
        payload["context"] = json.dumps(entry.context, default=str)
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{k}" for k in payload)
        try:
            with self._lock, closing(self._connect()) as conn, conn:
                conn.execute(f"INSERT INTO trades ({columns}) VALUES ({placeholders})", payload)
        except sqlite3.Error:
            log.exception("journal write failed (trade itself is unaffected)")

    def recent(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM trades"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
