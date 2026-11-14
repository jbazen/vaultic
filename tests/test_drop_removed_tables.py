"""Tests for the one-time drop of the removed features' tables (issue #84)."""
import sqlite3

import pytest

import api.database as db_module
from scripts import drop_removed_tables as drop

# Old schema of the removed features, trimmed to the columns and foreign keys
# that matter for the drop (parent/child chains with CASCADE and SET NULL).
OLD_SCHEMA = """
CREATE TABLE budget_groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE budget_items (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES budget_groups(id) ON DELETE CASCADE,
    name TEXT NOT NULL);
CREATE TABLE budget_amounts (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES budget_items(id) ON DELETE CASCADE,
    month TEXT NOT NULL, planned REAL);
CREATE TABLE budget_history (
    id INTEGER PRIMARY KEY,
    item_id INTEGER REFERENCES budget_items(id) ON DELETE SET NULL,
    amount REAL NOT NULL);
CREATE TABLE budget_auto_rules (
    id INTEGER PRIMARY KEY, merchant TEXT NOT NULL,
    item_id INTEGER REFERENCES budget_items(id) ON DELETE CASCADE);
CREATE TABLE transaction_assignments (
    id INTEGER PRIMARY KEY, transaction_id TEXT NOT NULL UNIQUE,
    item_id INTEGER REFERENCES budget_items(id) ON DELETE SET NULL);
CREATE TABLE transaction_splits (
    id INTEGER PRIMARY KEY, transaction_id TEXT NOT NULL,
    item_id INTEGER REFERENCES budget_items(id) ON DELETE SET NULL, amount REAL NOT NULL);
CREATE TABLE funds (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE fund_transactions (
    id INTEGER PRIMARY KEY,
    fund_id INTEGER NOT NULL REFERENCES funds(id) ON DELETE CASCADE, amount REAL NOT NULL);
CREATE TABLE split_rules (id INTEGER PRIMARY KEY, merchant TEXT NOT NULL UNIQUE, splits TEXT NOT NULL);
CREATE TABLE transaction_metadata (transaction_id TEXT PRIMARY KEY, notes TEXT);
CREATE TABLE tax_returns (id INTEGER PRIMARY KEY, tax_year INTEGER NOT NULL UNIQUE);
CREATE TABLE tax_documents (id INTEGER PRIMARY KEY, doc_type TEXT NOT NULL);
CREATE TABLE tax_docs (id INTEGER PRIMARY KEY, tax_year INTEGER NOT NULL);
CREATE TABLE paystubs (id INTEGER PRIMARY KEY, employer TEXT, pay_date TEXT);
CREATE TABLE w4s (id INTEGER PRIMARY KEY, employer TEXT);
CREATE TABLE vault_documents (id INTEGER PRIMARY KEY, file_path TEXT NOT NULL);
CREATE TABLE financial_events (id INTEGER PRIMARY KEY, username TEXT NOT NULL, start_dt TEXT NOT NULL);
CREATE INDEX idx_financial_events_user_date ON financial_events(username, start_dt);
CREATE TABLE crypto_trades (id INTEGER PRIMARY KEY);
CREATE TABLE crypto_lots (id INTEGER PRIMARY KEY);
CREATE TABLE crypto_gains (id INTEGER PRIMARY KEY);
"""

DEVICE_TOKEN = ("push_subscriptions", "device_token")


@pytest.fixture
def old_db(tmp_path):
    """A file DB with today's schema plus the old tables and columns, all holding rows."""
    path = tmp_path / "vaultic.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(db_module.SCHEMA)
    for migration in db_module.MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass
    conn.executescript(OLD_SCHEMA)
    if "device_token" not in {r[1] for r in conn.execute("PRAGMA table_info(push_subscriptions)")}:
        conn.execute("ALTER TABLE push_subscriptions ADD COLUMN device_token TEXT")
    conn.execute("ALTER TABLE transactions ADD COLUMN budget_deleted INTEGER DEFAULT 0")

    conn.execute("INSERT INTO budget_groups (id, name) VALUES (1, 'Food')")
    conn.execute("INSERT INTO budget_items (id, group_id, name) VALUES (10, 1, 'Groceries')")
    conn.execute("INSERT INTO budget_amounts (item_id, month, planned) VALUES (10, '2026-10', 600)")
    conn.execute("INSERT INTO budget_history (item_id, amount) VALUES (10, 42.5)")
    conn.execute("INSERT INTO budget_auto_rules (merchant, item_id) VALUES ('Costco', 10)")
    conn.execute("INSERT INTO transaction_assignments (transaction_id, item_id) VALUES ('t1', 10)")
    conn.execute("INSERT INTO transaction_splits (transaction_id, item_id, amount) VALUES ('t2', 10, 5)")
    conn.execute("INSERT INTO funds (id, name) VALUES (1, 'Vacation')")
    conn.execute("INSERT INTO fund_transactions (fund_id, amount) VALUES (1, 100)")
    conn.execute("INSERT INTO paystubs (employer, pay_date) VALUES ('Acme', '2026-10-15')")
    conn.execute("INSERT INTO tax_returns (tax_year) VALUES (2025)")

    conn.execute(
        "INSERT INTO accounts (id, plaid_account_id, name, type) VALUES (500, 'pa-500', 'Checking', 'depository')"
    )
    for tid, flag in (("t1", 0), ("t2", 1), ("t3", None)):
        conn.execute(
            "INSERT INTO transactions (transaction_id, account_id, amount, date, name, budget_deleted)"
            " VALUES (?, 500, 12.5, '2026-10-01', 'Store', ?)",
            (tid, flag),
        )
    conn.execute(
        "INSERT INTO push_subscriptions (endpoint, p256dh, auth, device_token, username, is_active)"
        " VALUES ('https://push.example/1', 'p', 'a', 'tok', 'testuser', 0)"
    )
    conn.commit()
    conn.close()
    return path


def _tables(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def _columns(path, table) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _rows(path, sql) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def _backups(path) -> list:
    return sorted(path.parent.glob("vaultic-pre-84-*.db"))


@pytest.fixture
def push_router_unused(monkeypatch):
    monkeypatch.setattr(drop, "device_token_in_use", lambda: False)


def test_plan_lists_every_target_with_row_counts(old_db):
    conn = drop.connect(old_db)
    plan = drop.build_plan(conn)
    conn.close()
    assert [t for t, _ in plan["tables"]] == list(drop.TABLES)
    counts = dict(plan["tables"])
    assert counts["budget_items"] == 1 and counts["paystubs"] == 1 and counts["w4s"] == 0
    assert plan["columns"] == [("transactions", "budget_deleted", 2), ("push_subscriptions", "device_token", 1)]


def test_dry_run_changes_nothing(old_db, push_router_unused, capsys):
    before = _rows(old_db, "SELECT type, name, sql FROM sqlite_master ORDER BY name")
    assert drop.main(["--db", str(old_db)]) == 0
    assert _rows(old_db, "SELECT type, name, sql FROM sqlite_master ORDER BY name") == before
    assert _backups(old_db) == []
    out = capsys.readouterr().out
    assert "DROP TABLE   budget_history" in out
    assert "Dry run: nothing changed" in out


def test_apply_backs_up_then_drops_everything(old_db, push_router_unused):
    assert drop.main(["--db", str(old_db), "--apply"]) == 0

    tables = _tables(old_db)
    assert tables & set(drop.TABLES) == set()
    assert {"transactions", "push_subscriptions", "accounts", "net_worth_snapshots"} <= tables
    assert "budget_deleted" not in _columns(old_db, "transactions")
    assert "device_token" not in _columns(old_db, "push_subscriptions")
    assert _rows(old_db, "SELECT transaction_id, amount, name FROM transactions ORDER BY transaction_id") == [
        ("t1", 12.5, "Store"), ("t2", 12.5, "Store"), ("t3", 12.5, "Store"),
    ]
    assert _rows(old_db, "SELECT endpoint, username, is_active FROM push_subscriptions") == [
        ("https://push.example/1", "testuser", 0),
    ]
    assert _rows(old_db, "PRAGMA foreign_key_check") == []
    assert _rows(old_db, "PRAGMA integrity_check") == [("ok",)]

    [backup] = _backups(old_db)
    assert set(drop.TABLES) <= _tables(backup)
    assert _rows(backup, "SELECT name FROM budget_items") == [("Groceries",)]
    assert _rows(backup, "SELECT transaction_id, budget_deleted FROM transactions ORDER BY transaction_id") == [
        ("t1", 0), ("t2", 1), ("t3", None),
    ]
    assert _rows(backup, "SELECT device_token FROM push_subscriptions") == [("tok",)]


def test_second_run_is_a_no_op(old_db, push_router_unused, capsys):
    assert drop.main(["--db", str(old_db), "--apply"]) == 0
    after_first = _rows(old_db, "SELECT type, name, sql FROM sqlite_master ORDER BY name")
    capsys.readouterr()

    assert drop.main(["--db", str(old_db), "--apply"]) == 0
    assert "Nothing to drop" in capsys.readouterr().out
    assert _rows(old_db, "SELECT type, name, sql FROM sqlite_master ORDER BY name") == after_first
    assert len(_backups(old_db)) == 1


def test_failure_part_way_rolls_back_everything(old_db, push_router_unused):
    # An index on budget_deleted makes its DROP COLUMN fail after every table
    # has already been dropped inside the transaction.
    conn = sqlite3.connect(old_db)
    conn.execute("CREATE INDEX idx_budget_deleted ON transactions(budget_deleted)")
    conn.commit()
    conn.close()
    before = _rows(old_db, "SELECT type, name, sql FROM sqlite_master ORDER BY name")

    with pytest.raises(sqlite3.OperationalError):
        drop.main(["--db", str(old_db), "--apply"])

    assert _rows(old_db, "SELECT type, name, sql FROM sqlite_master ORDER BY name") == before
    assert _rows(old_db, "SELECT COUNT(*) FROM budget_history") == [(1,)]


def test_device_token_kept_while_push_router_still_uses_it(old_db, monkeypatch, capsys):
    monkeypatch.setattr(drop, "device_token_in_use", lambda: True)
    assert drop.main(["--db", str(old_db), "--apply"]) == 0
    assert "SKIP COLUMN  push_subscriptions.device_token" in capsys.readouterr().out
    assert "device_token" in _columns(old_db, "push_subscriptions")
    assert "budget_deleted" not in _columns(old_db, "transactions")
    assert _tables(old_db) & set(drop.TABLES) == set()

    # Re-run once #85 has shipped: only the column is left to drop.
    monkeypatch.setattr(drop, "device_token_in_use", lambda: False)
    assert drop.main(["--db", str(old_db), "--apply"]) == 0
    assert "device_token" not in _columns(old_db, "push_subscriptions")
    assert _rows(old_db, "SELECT endpoint FROM push_subscriptions") == [("https://push.example/1",)]


def test_device_token_in_use_reads_the_push_router(tmp_path):
    router = tmp_path / "push.py"
    router.write_text("def subscribe():\n    device_token = make()\n")
    assert drop.device_token_in_use(router) is True
    router.write_text("def subscribe():\n    pass\n")
    assert drop.device_token_in_use(router) is False
    assert drop.device_token_in_use(tmp_path / "missing.py") is False


def test_missing_database_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        drop.main(["--db", str(tmp_path / "nope.db")])
