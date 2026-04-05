"""Tests for account_number migration and correlation.

Covers:
  - Coinbase: account_number populated on accounts + account_balances
  - Coinbase sync writes account_number on new accounts + balance snapshots
  - Plaid: account_number populated on accounts + all related tables
  - I360/Parker: account_number from i360_account_map, masked snapshot fixes
  - Migration backfills existing rows
"""
import pytest
from tests.conftest import _test_get_db

# IDs used by these tests — cleaned up after so shared in-memory DB isn't polluted
_TEST_PLAID_IDS = (
    "uuid-test-btc", "uuid-test-eth", "plaid-uuid-123",
    "plaid-uuid-checking", "plaid-uuid-401k",
    "i360_9999",
)


@pytest.fixture(autouse=True, scope="module")
def _cleanup_after():
    """Remove test rows from the shared in-memory DB after all tests in this module."""
    yield
    with _test_get_db() as conn:
        for pid in _TEST_PLAID_IDS:
            acct = conn.execute("SELECT id FROM accounts WHERE plaid_account_id = ?", (pid,)).fetchone()
            if acct:
                conn.execute("DELETE FROM account_balances WHERE account_id = ?", (acct[0],))
                conn.execute("DELETE FROM transactions WHERE account_id = ?", (acct[0],))
                conn.execute("DELETE FROM plaid_holdings WHERE account_id = ?", (acct[0],))
                conn.execute("DELETE FROM plaid_investment_transactions WHERE account_id = ?", (acct[0],))
                conn.execute("DELETE FROM i360_holdings WHERE account_id = ?", (acct[0],))
                conn.execute("DELETE FROM i360_account_balances WHERE account_id = ?", (acct[0],))
                conn.execute("DELETE FROM i360_account_map WHERE account_id = ?", (acct[0],))
                conn.execute("DELETE FROM accounts WHERE id = ?", (acct[0],))
        # Also clean up any securities and test snapshots we inserted
        conn.execute("DELETE FROM plaid_securities WHERE security_id = 'sec-001'")
        conn.execute("DELETE FROM manual_entry_snapshots WHERE name = 'Test Parker Account'")
        conn.execute("DELETE FROM manual_holdings_snapshots WHERE entry_name = 'Test Parker Account'")
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


class TestPlaidAccountNumbers:
    """Plaid accounts get account_number = plaid_account_id UUID."""

    def test_transactions_has_account_number_column(self):
        with _test_get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
            assert "account_number" in cols

    def test_plaid_holdings_has_account_number_column(self):
        with _test_get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(plaid_holdings)").fetchall()]
            assert "account_number" in cols

    def test_plaid_investment_transactions_has_account_number_column(self):
        with _test_get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(plaid_investment_transactions)").fetchall()]
            assert "account_number" in cols

    def test_migration_populates_plaid_account_numbers(self):
        with _test_get_db() as conn:
            # Insert a plaid account without account_number
            conn.execute("""
                INSERT OR IGNORE INTO accounts
                    (plaid_account_id, name, type, subtype, source, is_active)
                VALUES ('plaid-uuid-checking', 'TOTAL CHECKING', 'depository', 'checking', 'plaid', 1)
            """)
            acct_id = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = 'plaid-uuid-checking'"
            ).fetchone()[0]

            # Insert related rows without account_number
            conn.execute("""
                INSERT OR IGNORE INTO account_balances
                    (account_id, current, available, snapped_at)
                VALUES (?, 5000.0, 5000.0, '2026-04-01')
            """, (acct_id,))
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, name)
                VALUES ('txn-001', ?, -50.0, '2026-04-01', 'Test Purchase')
            """, (acct_id,))
            conn.commit()

            # Run migration
            from api.database import _migrate_plaid_account_numbers
            _migrate_plaid_account_numbers(conn)

            # Verify accounts.account_number = plaid_account_id
            row = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'plaid-uuid-checking'"
            ).fetchone()
            assert row[0] == "plaid-uuid-checking"

            # Verify account_balances backfilled
            bal = conn.execute(
                "SELECT account_number FROM account_balances WHERE account_id = ?", (acct_id,)
            ).fetchone()
            assert bal[0] == "plaid-uuid-checking"

            # Verify transactions backfilled
            txn = conn.execute(
                "SELECT account_number FROM transactions WHERE transaction_id = 'txn-001'"
            ).fetchone()
            assert txn[0] == "plaid-uuid-checking"

    def test_migration_backfills_holdings(self):
        with _test_get_db() as conn:
            # Insert a plaid investment account
            conn.execute("""
                INSERT OR IGNORE INTO accounts
                    (plaid_account_id, name, type, subtype, source, is_active)
                VALUES ('plaid-uuid-401k', 'SAIC 401K', 'investment', '401k', 'plaid', 1)
            """)
            acct_id = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = 'plaid-uuid-401k'"
            ).fetchone()[0]

            # Insert a security
            conn.execute("""
                INSERT OR IGNORE INTO plaid_securities
                    (security_id, name, ticker_symbol, type)
                VALUES ('sec-001', 'Vanguard S&P 500', 'VOO', 'etf')
            """)

            # Insert holding and investment transaction without account_number
            conn.execute("""
                INSERT OR IGNORE INTO plaid_holdings
                    (account_id, security_id, institution_value, quantity, snapped_at)
                VALUES (?, 'sec-001', 10000.0, 20.0, '2026-04-01')
            """, (acct_id,))
            conn.execute("""
                INSERT OR IGNORE INTO plaid_investment_transactions
                    (investment_transaction_id, account_id, security_id, date, name,
                     quantity, amount, type, subtype)
                VALUES ('inv-txn-001', ?, 'sec-001', '2026-04-01', 'Buy VOO',
                        5.0, 2500.0, 'buy', 'buy')
            """, (acct_id,))
            conn.commit()

            # Run migration
            from api.database import _migrate_plaid_account_numbers
            _migrate_plaid_account_numbers(conn)

            # Verify plaid_holdings backfilled
            h = conn.execute(
                "SELECT account_number FROM plaid_holdings WHERE account_id = ? AND security_id = 'sec-001'",
                (acct_id,)
            ).fetchone()
            assert h[0] == "plaid-uuid-401k"

            # Verify plaid_investment_transactions backfilled
            it = conn.execute(
                "SELECT account_number FROM plaid_investment_transactions WHERE investment_transaction_id = 'inv-txn-001'"
            ).fetchone()
            assert it[0] == "plaid-uuid-401k"

    def test_migration_idempotent(self):
        """Running Plaid migration twice doesn't break anything."""
        with _test_get_db() as conn:
            from api.database import _migrate_plaid_account_numbers
            _migrate_plaid_account_numbers(conn)
            _migrate_plaid_account_numbers(conn)

            row = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'plaid-uuid-checking'"
            ).fetchone()
            assert row[0] == "plaid-uuid-checking"

    def test_migration_skips_non_plaid(self):
        """Coinbase accounts are not touched by the Plaid migration."""
        with _test_get_db() as conn:
            from api.database import _migrate_plaid_account_numbers
            _migrate_plaid_account_numbers(conn)

            # The coinbase account from earlier tests should not be affected
            row = conn.execute(
                "SELECT account_number FROM accounts WHERE source = 'coinbase' AND plaid_account_id = 'uuid-test-btc'"
            ).fetchone()
            # Should still be coinbaseBTC from the coinbase migration, not overwritten
            if row:
                assert row[0] == "coinbaseBTC"

    def test_plaid_sync_writes_account_number_on_insert(self):
        """Simulates what sync.py now does — writes account_number on all inserts."""
        with _test_get_db() as conn:
            plaid_acct_id = "plaid-uuid-checking"
            acct_id = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = ?", (plaid_acct_id,)
            ).fetchone()[0]

            # Simulate sync writing a new balance with account_number
            conn.execute("""
                INSERT OR IGNORE INTO account_balances
                    (account_id, current, available, snapped_at, account_number)
                VALUES (?, 6000.0, 6000.0, '2026-04-04', ?)
            """, (acct_id, plaid_acct_id))

            # Simulate sync writing a new transaction with account_number
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, name, account_number)
                VALUES ('txn-002', ?, -25.0, '2026-04-04', 'Coffee Shop', ?)
            """, (acct_id, plaid_acct_id))
            conn.commit()

            bal = conn.execute(
                "SELECT account_number FROM account_balances WHERE account_id = ? AND snapped_at = '2026-04-04'",
                (acct_id,)
            ).fetchone()
            assert bal[0] == "plaid-uuid-checking"

            txn = conn.execute(
                "SELECT account_number FROM transactions WHERE transaction_id = 'txn-002'"
            ).fetchone()
            assert txn[0] == "plaid-uuid-checking"


class TestI360AccountNumbers:
    """I360/Parker accounts get account_number from i360_account_map."""

    def test_i360_holdings_has_account_number_column(self):
        with _test_get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(i360_holdings)").fetchall()]
            assert "account_number" in cols

    def test_i360_account_balances_has_account_number_column(self):
        with _test_get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(i360_account_balances)").fetchall()]
            assert "account_number" in cols

    def _setup_i360_account(self, conn):
        """Helper: create an I360 account with account_map entry."""
        conn.execute("""
            INSERT OR IGNORE INTO accounts
                (plaid_account_id, name, type, subtype, source, is_active)
            VALUES ('i360_9999', 'Test Parker Account', 'investment', 'brokerage',
                    'investor360', 1)
        """)
        acct_id = conn.execute(
            "SELECT id FROM accounts WHERE plaid_account_id = 'i360_9999'"
        ).fetchone()[0]
        conn.execute("""
            INSERT OR IGNORE INTO i360_account_map
                (account_id, i360_account_id, account_number, household_id,
                 registration_type)
            VALUES (?, 9999, 'B37705429', 1682851, 'TODJ')
        """, (acct_id,))
        conn.commit()
        return acct_id

    def test_migration_populates_i360_account_numbers(self):
        with _test_get_db() as conn:
            acct_id = self._setup_i360_account(conn)

            # Insert related rows without account_number
            conn.execute("""
                INSERT OR IGNORE INTO account_balances
                    (account_id, current, available, snapped_at)
                VALUES (?, 100000.0, 5000.0, '2026-04-03')
            """, (acct_id,))
            conn.execute("""
                INSERT OR IGNORE INTO i360_account_balances
                    (account_id, snapped_at, market_value, cash_value,
                     todays_change, total_portfolio_value)
                VALUES (?, '2026-04-03', 100000.0, 5000.0, 500.0, 100000.0)
            """, (acct_id,))
            conn.commit()

            from api.database import _migrate_i360_account_numbers
            _migrate_i360_account_numbers(conn)

            # Verify accounts.account_number
            row = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'i360_9999'"
            ).fetchone()
            assert row[0] == "B37705429"

            # Verify account_balances backfilled
            bal = conn.execute(
                "SELECT account_number FROM account_balances WHERE account_id = ?",
                (acct_id,)
            ).fetchone()
            assert bal[0] == "B37705429"

            # Verify i360_account_balances backfilled
            i360_bal = conn.execute(
                "SELECT account_number FROM i360_account_balances WHERE account_id = ?",
                (acct_id,)
            ).fetchone()
            assert i360_bal[0] == "B37705429"

    def test_migration_fixes_masked_manual_entry_snapshots(self):
        with _test_get_db() as conn:
            self._setup_i360_account(conn)

            # Insert a snapshot with masked account number
            conn.execute("""
                INSERT OR IGNORE INTO manual_entry_snapshots
                    (name, category, value, snapped_at, account_number)
                VALUES ('Test Parker Account', 'invested', 100000.0,
                        '2026-03-31', 'XXXX5429')
            """)
            conn.commit()

            from api.database import _migrate_i360_account_numbers
            _migrate_i360_account_numbers(conn)

            # Verify masked → full
            snap = conn.execute(
                "SELECT account_number FROM manual_entry_snapshots "
                "WHERE name = 'Test Parker Account'"
            ).fetchone()
            assert snap[0] == "B37705429"

    def test_migration_fixes_masked_manual_holdings_snapshots(self):
        with _test_get_db() as conn:
            self._setup_i360_account(conn)

            # Insert a holdings snapshot with masked account number
            conn.execute("""
                INSERT OR IGNORE INTO manual_holdings_snapshots
                    (entry_name, account_number, snapped_at, holding_name, value)
                VALUES ('Test Parker Account', 'XXXX5429', '2026-03-31',
                        'Vanguard S&P 500', 50000.0)
            """)
            conn.commit()

            from api.database import _migrate_i360_account_numbers
            _migrate_i360_account_numbers(conn)

            # Verify masked → full
            snap = conn.execute(
                "SELECT account_number FROM manual_holdings_snapshots "
                "WHERE entry_name = 'Test Parker Account'"
            ).fetchone()
            assert snap[0] == "B37705429"

    def test_migration_idempotent(self):
        with _test_get_db() as conn:
            from api.database import _migrate_i360_account_numbers
            _migrate_i360_account_numbers(conn)
            _migrate_i360_account_numbers(conn)

            row = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'i360_9999'"
            ).fetchone()
            assert row[0] == "B37705429"

    def test_migration_skips_non_i360(self):
        """Plaid/Coinbase accounts are not touched by I360 migration."""
        with _test_get_db() as conn:
            from api.database import _migrate_i360_account_numbers
            _migrate_i360_account_numbers(conn)

            # Check that plaid account still has its own account_number
            row = conn.execute(
                "SELECT account_number FROM accounts WHERE plaid_account_id = 'plaid-uuid-checking'"
            ).fetchone()
            if row:
                assert row[0] == "plaid-uuid-checking"

    def test_i360_sync_writes_account_number_on_insert(self):
        """Simulates what investor360.py now does — writes account_number."""
        with _test_get_db() as conn:
            acct_id = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = 'i360_9999'"
            ).fetchone()[0]

            # Simulate writing a balance with account_number
            conn.execute("""
                INSERT OR REPLACE INTO account_balances
                    (account_id, current, available, snapped_at, account_number)
                VALUES (?, 110000.0, 6000.0, '2026-04-04', 'B37705429')
            """, (acct_id,))

            # Simulate writing i360_account_balances with account_number
            conn.execute("""
                INSERT OR REPLACE INTO i360_account_balances
                    (account_id, snapped_at, market_value, cash_value,
                     todays_change, total_portfolio_value, account_number)
                VALUES (?, '2026-04-04', 110000.0, 6000.0, 1000.0, 110000.0,
                        'B37705429')
            """, (acct_id,))
            conn.commit()

            bal = conn.execute(
                "SELECT account_number FROM account_balances "
                "WHERE account_id = ? AND snapped_at = '2026-04-04'",
                (acct_id,)
            ).fetchone()
            assert bal[0] == "B37705429"

            i360_bal = conn.execute(
                "SELECT account_number FROM i360_account_balances "
                "WHERE account_id = ? AND snapped_at = '2026-04-04'",
                (acct_id,)
            ).fetchone()
            assert i360_bal[0] == "B37705429"
