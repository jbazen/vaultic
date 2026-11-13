"""One-time: drop the tables and columns of the features removed in #82 (issue #84).

Dry run by default: prints every table and column it would drop, with row
counts, and changes nothing. Pass --apply to:

  1. lock the database for writing (BEGIN IMMEDIATE, 30 s busy timeout),
  2. copy it with SQLite's backup API to data/vaultic-pre-84-<timestamp>.db,
  3. check the copy (integrity_check, and every table's row count equals the
     live database) and stop without changing anything if it does not match,
  4. drop the tables children-first and the columns, all in that one
     transaction, check foreign keys and roll everything back on any error,
  5. run integrity_check and print the tables that remain.

Idempotent: tables and columns that are already gone are skipped, so it can be
re-run (for example to drop push_subscriptions.device_token once #85 has
shipped). The app and Litestream can keep running; no restart is needed.

    .venv/bin/python scripts/drop_removed_tables.py
    .venv/bin/python scripts/drop_removed_tables.py --apply
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "vaultic.db"
PUSH_ROUTER = REPO_ROOT / "api" / "routers" / "push.py"

# Children before parents, so no DROP fires an ON DELETE action on another
# table: budget_amounts / budget_auto_rules / budget_history /
# transaction_assignments / transaction_splits -> budget_items -> budget_groups,
# fund_transactions -> funds, crypto_gains -> crypto_lots -> crypto_trades.
# No table that stays references any of these.
TABLES = (
    "budget_amounts",
    "budget_auto_rules",
    "budget_history",
    "transaction_assignments",
    "transaction_splits",
    "budget_items",
    "budget_groups",
    "fund_transactions",
    "funds",
    "split_rules",
    "transaction_metadata",
    "tax_returns",
    "tax_documents",
    "tax_docs",
    "paystubs",
    "w4s",
    "vault_documents",
    "financial_events",
    "crypto_gains",
    "crypto_lots",
    "crypto_trades",
)

COLUMNS = (
    ("transactions", "budget_deleted"),
    ("push_subscriptions", "device_token"),
)

BUSY_TIMEOUT_MS = 30_000


def device_token_in_use(push_router: Path = PUSH_ROUTER) -> bool:
    """True while the deployed push router still reads/writes device_token (#85 not shipped)."""
    return push_router.exists() and "device_token" in push_router.read_text()


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    # isolation_level=None: this script issues BEGIN/COMMIT/ROLLBACK itself.
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _existing_tables(conn) -> list[str]:
    return [
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]


def _has_column(conn, table: str, column: str) -> bool:
    return any(r["name"] == column for r in conn.execute(f'PRAGMA table_info("{table}")'))


def _count(conn, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def build_plan(conn, skip_columns: tuple = ()) -> dict:
    """What would be dropped, with row counts. Reads only."""
    existing = set(_existing_tables(conn))
    tables = [(t, _count(conn, t)) for t in TABLES if t in existing]
    columns, skipped = [], []
    for table, column in COLUMNS:
        if table not in existing or not _has_column(conn, table, column):
            continue
        if (table, column) in skip_columns:
            skipped.append((table, column))
            continue
        filled = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL'
        ).fetchone()[0]
        columns.append((table, column, filled))
    return {"tables": tables, "columns": columns, "skipped": skipped}


def print_plan(plan: dict) -> None:
    if not plan["tables"] and not plan["columns"]:
        print("Nothing to drop: every target table and column is already gone.")
    for table, rows in plan["tables"]:
        print(f"  DROP TABLE   {table:<26} {rows:>7} rows")
    for table, column, filled in plan["columns"]:
        print(f"  DROP COLUMN  {table}.{column:<{25 - len(table)}} {filled:>7} non-null values")
    for table, column in plan["skipped"]:
        print(f"  SKIP COLUMN  {table}.{column} (still used by api/routers/push.py; re-run after #85 ships)")


def backup_and_verify(conn, db_path: Path, backup_path: Path) -> None:
    """Copy the database while the caller holds the write lock, and check the copy.

    The copy is read through a second connection: SQLite's backup cannot read
    through a connection that has a write transaction open. Because ``conn``
    holds that lock, nothing can change between the copy and the drop.
    """
    reader = sqlite3.connect(db_path)
    dest = sqlite3.connect(backup_path)
    try:
        reader.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        reader.backup(dest)
        result = dest.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup integrity_check failed: {result}")
        live_tables = _existing_tables(conn)
        dest.row_factory = sqlite3.Row
        if _existing_tables(dest) != live_tables:
            raise RuntimeError("Backup table list differs from the live database")
        for table in live_tables:
            live, copy = _count(conn, table), _count(dest, table)
            if live != copy:
                raise RuntimeError(f"Backup row count differs for {table}: live {live}, backup {copy}")
    finally:
        dest.close()
        reader.close()


def apply_plan(conn, db_path: Path, plan: dict, backup_path: Path) -> None:
    """Back up, then drop everything in the plan in one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        backup_and_verify(conn, db_path, backup_path)
        print(f"Backup written and verified: {backup_path}")
        for table, _ in plan["tables"]:
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        for table, column, _ in plan["columns"]:
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign_key_check found {len(violations)} violation(s)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="back up, then drop (default: dry run)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="database path (default: data/vaultic.db)")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    try:
        skip = (("push_subscriptions", "device_token"),) if device_token_in_use() else ()
        plan = build_plan(conn, skip_columns=skip)
        print(f"Database: {args.db}")
        print_plan(plan)
        if not args.apply:
            print("\nDry run: nothing changed. Re-run with --apply to back up and drop.")
            return 0
        if not plan["tables"] and not plan["columns"]:
            return 0
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = args.db.parent / f"vaultic-pre-84-{stamp}.db"
        apply_plan(conn, args.db, plan, backup_path)
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"Dropped {len(plan['tables'])} table(s) and {len(plan['columns'])} column(s).")
        print(f"integrity_check: {result}")
        print("Remaining tables: " + ", ".join(_existing_tables(conn)))
        return 0 if result == "ok" else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
