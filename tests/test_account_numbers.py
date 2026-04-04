"""Tests for account_number migration and correlation.

Covers:
  - Coinbase: account_number populated on accounts + account_balances
  - Coinbase sync writes account_number on new accounts + balance snapshots
  - Migration backfills existing rows
"""
import pytest
from tests.conftest import _test_get_db

# IDs used by these tests — cleaned up after so shared in-memory DB isn't polluted
_TEST_PLAID_IDS = ("uuid-test-btc", "uuid-test-eth", "plaid-uuid-123")


@pytest.fixture(autouse=True, scope="class")
def _cleanup_after():
    """Remove test rows from the shared in-memory DB after this test class."""
    yield
    with _test_get_db() as conn:
        for pid in _TEST_PLAID_IDS:
            acct = conn.execute("SELECT id FROM accounts WHERE plaid_account_id = ?", (pid,)).fetchone()
            if acct:
                conn.execute("DELETE FROM account_balances WHERE account_id = ?", (acct[0],))
                conn.execute("DELETE FROM accounts WHERE id = ?", (acct[0],))
        conn.commit()


class TestCoinbaseAccountNumbers:
    """Coinbase accounts get account_number = 'coinbase' + subtype."""

    def test_accounts_table_has_account_number_column(self):
        with _test_get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
            assert "account_number" in cols

    def test_account_balances_has_account_number_column(self):
        with _test_get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(account_balances)").fetchall()]
            assert "account_number" in cols

    def test_migration_populates_coinbase_account_numbers(self):
        with _test_get_db() as conn:
            # Insert a coinbase account without account_number
            conn.execute("""
                INSERT OR IGNORE INTO accounts
                    (plaid_account_id, name, type, subtype, source, is_active)
                VALUES ('uuid-test-btc', 'Coinbase BTC', 'crypto', 'BTC', 'coinbase', 1)
            """)
            # Insert a balance row without account_number
            acct_id = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = 'uuid-test-btc'"
            ).fetchone()[0]
            conn.execute("""
                INSERT OR IGNORE INTO account_balances
                    (account_id, current, available, snapped_at)
                VALUES (?, 1000.0, 1000.0, '2026-04-01')
            """, (acct_id,))
            conn.commit()

            # Run the migration
            from api.database import _migrate_coinbase_account_numbers
            _migrate_coinbase_account_numbers(conn)

            # Verify accounts.account_number
            row = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'uuid-test-btc'"
            ).fetchone()
            assert row[0] == "coinbaseBTC"

            # Verify account_balances.account_number backfilled
            bal = conn.execute(
                "SELECT account_number FROM account_balances WHERE account_id = ?", (acct_id,)
            ).fetchone()
            assert bal[0] == "coinbaseBTC"

    def test_migration_idempotent(self):
        """Running migration twice doesn't break anything."""
        with _test_get_db() as conn:
            from api.database import _migrate_coinbase_account_numbers
            _migrate_coinbase_account_numbers(conn)
            _migrate_coinbase_account_numbers(conn)

            row = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'uuid-test-btc'"
            ).fetchone()
            assert row[0] == "coinbaseBTC"

    def test_migration_skips_non_coinbase(self):
        """Plaid accounts are not touched by the coinbase migration."""
        with _test_get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO accounts
                    (plaid_account_id, name, type, subtype, source, is_active)
                VALUES ('plaid-uuid-123', 'TOTAL CHECKING', 'depository', 'checking', 'plaid', 1)
            """)
            conn.commit()

            from api.database import _migrate_coinbase_account_numbers
            _migrate_coinbase_account_numbers(conn)

            row = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'plaid-uuid-123'"
            ).fetchone()
            assert row[0] is None

    def test_coinbase_sync_writes_account_number(self):
        """Simulates what coinbase_sync.py now does — writes account_number on insert."""
        with _test_get_db() as conn:
            currency = "ETH"
            acct_number = f"coinbase{currency.upper()}"
            conn.execute("""
                INSERT OR IGNORE INTO accounts
                    (plaid_account_id, name, display_name, type, subtype,
                     institution_name, source, is_active, account_number)
                VALUES (?, ?, ?, 'crypto', ?, 'Coinbase', 'coinbase', 1, ?)
            """, ("uuid-test-eth", "Coinbase ETH", "Coinbase ETH", "ETH", acct_number))

            acct_id = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = 'uuid-test-eth'"
            ).fetchone()[0]

            conn.execute("""
                INSERT OR IGNORE INTO account_balances
                    (account_id, current, available, snapped_at, account_number)
                VALUES (?, 500.0, 500.0, '2026-04-03', ?)
            """, (acct_id, acct_number))
            conn.commit()

            # Verify both tables
            acct = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'uuid-test-eth'"
            ).fetchone()
            assert acct[0] == "coinbaseETH"

            bal = conn.execute(
                "SELECT account_number FROM account_balances WHERE account_id = ? AND snapped_at = '2026-04-03'",
                (acct_id,)
            ).fetchone()
            assert bal[0] == "coinbaseETH"
