"""Schema checks for a fresh database built by init_db() (issue #84).

The tables of the features removed in #82 must not be recreated on startup, and
no migration may be left pointing at them (an ALTER/UPDATE on a missing table
logs "Unexpected migration error" on every restart).
"""
import logging
import sqlite3
from contextlib import contextmanager

import pytest

import api.database as db_module

REMOVED_TABLES = {
    "budget_groups", "budget_items", "budget_amounts", "budget_history",
    "budget_auto_rules", "split_rules", "transaction_assignments",
    "transaction_splits", "transaction_metadata", "funds", "fund_transactions",
    "tax_returns", "tax_documents", "tax_docs", "paystubs", "w4s",
    "vault_documents", "financial_events",
    "crypto_trades", "crypto_lots", "crypto_gains",
}

REQUIRED_TABLES = {
    "users", "plaid_items", "accounts", "account_balances", "transactions",
    "net_worth_snapshots", "manual_entries", "manual_entry_snapshots",
    "push_subscriptions", "refresh_tokens", "ticker_quotes", "news_articles",
}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch, caplog):
    """Run the real init_db() against an empty file DB; return (conn, log records)."""
    path = tmp_path / "fresh.db"

    @contextmanager
    def _get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setattr(db_module, "get_db", _get_db)
    with caplog.at_level(logging.WARNING, logger="api.database"):
        db_module.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    yield conn, list(caplog.records)
    conn.close()


def _tables(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_removed_feature_tables_are_not_created(fresh_db):
    conn, _ = fresh_db
    assert _tables(conn) & REMOVED_TABLES == set()


def test_app_tables_still_created(fresh_db):
    conn, _ = fresh_db
    assert REQUIRED_TABLES - _tables(conn) == set()


def test_no_index_on_removed_tables(fresh_db):
    conn, _ = fresh_db
    indexed = {
        r["tbl_name"] for r in conn.execute("SELECT tbl_name FROM sqlite_master WHERE type = 'index'")
    }
    assert indexed & REMOVED_TABLES == set()


def _migration_errors(records) -> list[str]:
    return [r.getMessage() for r in records if "Unexpected migration error" in r.getMessage()]


def test_init_db_logs_no_migration_errors(fresh_db):
    _, records = fresh_db
    assert _migration_errors(records) == []


def test_init_db_is_repeatable(fresh_db, caplog):
    """A restart on an existing DB must also be warning-free."""
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="api.database"):
        db_module.init_db()
    assert _migration_errors(caplog.records) == []
