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
    "revoked_tokens",
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


def test_transactions_has_no_budget_deleted_column(fresh_db):
    conn, _ = fresh_db
    cols = _columns(conn, "transactions")
    assert "budget_deleted" not in cols
    assert {"transaction_id", "account_id", "amount", "date", "name"} <= cols


def test_push_subscriptions_has_no_device_token_column(fresh_db):
    conn, _ = fresh_db
    cols = _columns(conn, "push_subscriptions")
    assert "device_token" not in cols
    assert {"endpoint", "p256dh", "auth", "username", "is_active"} <= cols


def test_init_db_does_not_promote_non_admin_users(fresh_db):
    """Issue #127: a restart must leave a non-admin user non-admin."""
    conn, _ = fresh_db
    conn.execute(
        "INSERT INTO users (username, password_hash, is_admin, is_active) VALUES (?, ?, ?, ?)",
        ("admin127", "x", 1, 1),
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, is_admin, is_active) VALUES (?, ?, ?, ?)",
        ("viewer127", "x", 0, 1),
    )
    conn.commit()

    db_module.init_db()

    flags = {r["username"]: r["is_admin"] for r in conn.execute("SELECT username, is_admin FROM users")}
    assert flags == {"admin127": 1, "viewer127": 0}


# ── Issue #129: a restart must not rewrite manual entries ────────────────────

def _manual_entries(conn) -> list[tuple]:
    return [
        (r["name"], r["category"], r["account_number"], r["value"])
        for r in conn.execute(
            "SELECT name, category, account_number, value FROM manual_entries ORDER BY id"
        )
    ]


def test_fresh_db_has_no_made_up_insperity_entry(fresh_db):
    """A fresh install must not invent a $1,391.66 Insperity balance."""
    conn, _ = fresh_db
    assert _manual_entries(conn) == []


def test_restart_does_not_bring_back_a_deleted_insperity_entry(fresh_db):
    """The old repair rebuilt a deleted entry from its last snapshot."""
    conn, _ = fresh_db
    conn.execute(
        "INSERT INTO manual_entries (name, category, value, account_number, entered_at) "
        "VALUES ('INSPERITY 401K PLAN', 'invested', 8643.25, '105001401K', '2026-11-03')"
    )
    conn.execute(
        "INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at) "
        "VALUES ('INSPERITY 401K PLAN', '105001401K', 'invested', 8643.25, '2026-11-03')"
    )
    conn.commit()
    db_module.init_db()
    conn.execute("DELETE FROM manual_entries WHERE account_number = '105001401K'")
    conn.commit()

    db_module.init_db()

    assert conn.execute(
        "SELECT COUNT(*) FROM manual_entries WHERE account_number = '105001401K'"
    ).fetchone()[0] == 0
    snaps = conn.execute(
        "SELECT account_number, value FROM manual_entry_snapshots"
    ).fetchall()
    assert [tuple(s) for s in snaps] == [("105001401K", 8643.25)]


def test_restart_does_not_duplicate_a_renamed_insperity_entry(fresh_db):
    """Renaming the 401k must not add a second row with the same account number."""
    conn, _ = fresh_db
    conn.execute(
        "INSERT INTO manual_entries (name, category, value, account_number, entered_at) "
        "VALUES ('Insperity 401k', 'invested', 8643.25, '105001401K', '2026-11-03')"
    )
    conn.commit()

    db_module.init_db()

    assert _manual_entries(conn) == [("Insperity 401k", "invested", "105001401K", 8643.25)]


def test_restart_keeps_two_accounts_that_share_a_name(fresh_db):
    """Same name + category but different account numbers are different accounts."""
    conn, _ = fresh_db
    for acct, value, day in (("1111", 100.0, "2026-10-01"), ("2222", 200.0, "2026-11-01")):
        conn.execute(
            "INSERT INTO manual_entries (name, category, value, account_number, entered_at) "
            "VALUES ('Joint Savings', 'liquid', ?, ?, ?)", (value, acct, day)
        )
        conn.execute(
            "INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at) "
            "VALUES ('Joint Savings', ?, 'liquid', ?, ?)", (acct, value, day)
        )
    conn.commit()

    db_module.init_db()

    assert _manual_entries(conn) == [
        ("Joint Savings", "liquid", "1111", 100.0),
        ("Joint Savings", "liquid", "2222", 200.0),
    ]
    snaps = conn.execute(
        "SELECT account_number, value FROM manual_entry_snapshots ORDER BY snapped_at"
    ).fetchall()
    assert [tuple(s) for s in snaps] == [("1111", 100.0), ("2222", 200.0)]


def test_restart_leaves_masked_snapshot_numbers_alone(fresh_db):
    """Snapshots are no longer re-keyed by last-4 match on every start."""
    conn, _ = fresh_db
    acct_id = conn.execute(
        "INSERT INTO accounts (plaid_account_id, name, type, subtype, source, is_active) "
        "VALUES ('i360_9999', 'Parker Joint', 'investment', 'brokerage', 'investor360', 1)"
    ).lastrowid
    conn.execute(
        "INSERT INTO i360_account_map "
        "(account_id, i360_account_id, account_number, household_id) "
        "VALUES (?, 9999, 'B37705429', 1)", (acct_id,)
    )
    conn.execute(
        "INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at) "
        "VALUES ('Parker Joint', 'XXXX5429', 'invested', 100.0, '2026-03-31')"
    )
    conn.execute(
        "INSERT INTO manual_holdings_snapshots "
        "(entry_name, account_number, snapped_at, holding_name, value) "
        "VALUES ('Parker Joint', 'XXXX5429', '2026-03-31', 'Index Fund', 100.0)"
    )
    conn.commit()

    db_module.init_db()

    assert conn.execute("SELECT account_number FROM manual_entry_snapshots").fetchone()[0] == "XXXX5429"
    assert conn.execute("SELECT account_number FROM manual_holdings_snapshots").fetchone()[0] == "XXXX5429"
