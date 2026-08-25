"""The journal must never break a trade, and must remember rejections."""

from __future__ import annotations

from pathlib import Path

from mt5_bridge.journal import JournalEntry, SqliteJournal


def entry(**overrides: object) -> JournalEntry:
    base = {"event": "executed", "symbol": "US100Cash", "side": "buy", "volume": 0.01}
    return JournalEntry(**{**base, **overrides})  # type: ignore[arg-type]


def test_round_trip(tmp_path: Path) -> None:
    journal = SqliteJournal(tmp_path / "j.sqlite3")
    journal.record(entry(price=19_900.5, ticket=1))

    rows = journal.recent()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "US100Cash"
    assert rows[0]["ticket"] == 1


def test_rejections_are_recorded_too(tmp_path: Path) -> None:
    """A bot that only remembers its fills cannot learn from its mistakes."""
    journal = SqliteJournal(tmp_path / "j.sqlite3")
    journal.record(entry(event="rejected", error="Unsupported filling mode", retcode=10006))

    row = journal.recent()[0]
    assert row["event"] == "rejected"
    assert row["retcode"] == 10006


def test_context_is_stored_as_json(tmp_path: Path) -> None:
    import json

    journal = SqliteJournal(tmp_path / "j.sqlite3")
    journal.record(entry(context={"spread": 0.5, "reason": "breakout"}))

    assert json.loads(journal.recent()[0]["context"])["spread"] == 0.5


def test_recent_filters_by_symbol_and_orders_newest_first(tmp_path: Path) -> None:
    journal = SqliteJournal(tmp_path / "j.sqlite3")
    journal.record(entry(symbol="US100Cash", ticket=1))
    journal.record(entry(symbol="EURUSD", ticket=2))
    journal.record(entry(symbol="US100Cash", ticket=3))

    tickets = [r["ticket"] for r in journal.recent(symbol="US100Cash")]
    assert tickets == [3, 1]


def test_a_broken_journal_never_raises_into_the_trading_path(tmp_path: Path) -> None:
    journal = SqliteJournal(tmp_path / "j.sqlite3")
    journal._path = "/nonexistent-dir/j.sqlite3"  # simulate disk failure
    journal.record(entry())  # must not raise
