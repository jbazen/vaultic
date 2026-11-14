"""Tests for the Plaid sync pipeline — no on-demand transactions/refresh
(issue #79) and real disconnect detection via error_code (issue #73)."""
import json
from unittest.mock import patch, MagicMock

import plaid

from api import sync
from api.encryption import encrypt
from tests.conftest import _test_get_db


def _login_required_exc():
    exc = plaid.ApiException(status=400, reason="Bad Request")
    exc.body = json.dumps({"error_type": "ITEM_ERROR", "error_code": "ITEM_LOGIN_REQUIRED"})
    return exc


def _insert_item(item_id, institution="Test Bank", error_code=None):
    with _test_get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO plaid_items (item_id, institution_name, access_token_enc, error_code) "
            "VALUES (?, ?, ?, ?)",
            (item_id, institution, encrypt("access-sandbox-abc"), error_code),
        )
        row = conn.execute("SELECT id FROM plaid_items WHERE item_id = ?", (item_id,)).fetchone()
        conn.commit()
    return row["id"]


def _cleanup_item(item_id):
    with _test_get_db() as conn:
        conn.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))
        conn.commit()


class TestSyncNeverRefreshes:
    """Manual sync must not call Plaid transactions/refresh (issue #79)."""

    def test_refresh_helper_is_gone(self):
        assert not hasattr(sync, "_refresh_transactions")

    def test_sync_all_never_calls_transactions_refresh(self):
        item_db_id = _insert_item("no-refresh-item")
        try:
            with patch("api.sync._get_plaid_client") as m_client, \
                 patch("api.sync._sync_transactions"), \
                 patch("api.sync._sync_investments"), \
                 patch("api.sync._take_net_worth_snapshot") as m_snap, \
                 patch("api.coinbase_sync.sync_coinbase"), \
                 patch("api.push.notify_stale_i360"):
                api = MagicMock()
                resp = MagicMock()
                resp.accounts = []
                resp.item.institution_id = None
                api.accounts_get.return_value = resp
                m_client.return_value = api
                sync.sync_all()
            api.transactions_refresh.assert_not_called()
            assert api.accounts_get.called
            assert m_snap.called
        finally:
            _cleanup_item(item_db_id)


class TestExtractErrorCode:
    def test_extracts_plaid_error_code(self):
        assert sync._extract_plaid_error_code(_login_required_exc()) == "ITEM_LOGIN_REQUIRED"

    def test_returns_none_for_non_plaid(self):
        assert sync._extract_plaid_error_code(ValueError("boom")) is None

    def test_returns_none_for_unparseable_body(self):
        exc = plaid.ApiException(status=500)
        exc.body = "not json"
        assert sync._extract_plaid_error_code(exc) is None


class TestRecordItemError:
    """_record_item_error persists the real Plaid code and pushes once (issue #73)."""

    def test_records_login_required(self):
        item_db_id = _insert_item("rec-err-1")
        try:
            with patch("api.push.notify_item_disconnected"):
                sync._record_item_error(item_db_id, _login_required_exc())
            with _test_get_db() as conn:
                row = conn.execute(
                    "SELECT error_code, last_error_at FROM plaid_items WHERE id = ?", (item_db_id,)
                ).fetchone()
            assert row["error_code"] == "ITEM_LOGIN_REQUIRED"
            assert row["last_error_at"] is not None
        finally:
            _cleanup_item(item_db_id)

    def test_non_plaid_error_records_generic_code(self):
        item_db_id = _insert_item("rec-err-2")
        try:
            with patch("api.push.notify_item_disconnected") as m_push:
                sync._record_item_error(item_db_id, ValueError("boom"))
            with _test_get_db() as conn:
                row = conn.execute(
                    "SELECT error_code FROM plaid_items WHERE id = ?", (item_db_id,)
                ).fetchone()
            assert row["error_code"] == "SYNC_ERROR"
            assert m_push.call_count == 0  # only login_required triggers a push
        finally:
            _cleanup_item(item_db_id)

    def test_push_fires_only_on_first_transition(self):
        item_db_id = _insert_item("rec-err-3")
        try:
            with patch("api.push.notify_item_disconnected") as m_push:
                sync._record_item_error(item_db_id, _login_required_exc())  # healthy → error
                sync._record_item_error(item_db_id, _login_required_exc())  # already errored
            assert m_push.call_count == 1
        finally:
            _cleanup_item(item_db_id)


class TestSyncAllErrorTracking:
    """sync_all records the error on failure and clears it on success (issue #73)."""

    def _run(self, side_effect):
        with patch("api.sync._sync_item", side_effect=side_effect), \
             patch("api.sync._take_net_worth_snapshot"), \
             patch("api.coinbase_sync.sync_coinbase"), \
             patch("api.push.notify_stale_i360"), \
             patch("api.push.notify_item_disconnected"):
            sync.sync_all()

    def test_failure_sets_error_code(self):
        item_db_id = _insert_item("all-err-1")
        try:
            self._run(_login_required_exc())
            with _test_get_db() as conn:
                row = conn.execute(
                    "SELECT error_code FROM plaid_items WHERE id = ?", (item_db_id,)
                ).fetchone()
            assert row["error_code"] == "ITEM_LOGIN_REQUIRED"
        finally:
            _cleanup_item(item_db_id)

    def test_success_clears_error_code(self):
        item_db_id = _insert_item("all-err-2", error_code="ITEM_LOGIN_REQUIRED")
        try:
            self._run(None)  # MagicMock side_effect=None → no-op success
            with _test_get_db() as conn:
                row = conn.execute(
                    "SELECT error_code, last_error_at FROM plaid_items WHERE id = ?", (item_db_id,)
                ).fetchone()
            assert row["error_code"] is None
            assert row["last_error_at"] is None
        finally:
            _cleanup_item(item_db_id)


class TestNetWorthSnapshotMortgage:
    """_take_net_worth_snapshot stores the manual liability it adds to
    liabilities in the mortgage column (#9)."""

    def _snapshot(self, liabilities_rows):
        from tests.conftest import make_isolated_db
        conn, get_db = make_isolated_db()
        for name, value in liabilities_rows:
            conn.execute(
                "INSERT INTO manual_entries (name, category, value, entered_at) "
                "VALUES (?, 'other_liability', ?, '2026-01-01')",
                (name, value),
            )
        with patch("api.sync.get_db", get_db):
            sync._take_net_worth_snapshot("2026-05-01")
        row = conn.execute(
            "SELECT liabilities, mortgage FROM net_worth_snapshots WHERE snapped_at = '2026-05-01'"
        ).fetchone()
        conn.close()
        return row

    def test_writes_manual_liability_total(self):
        row = self._snapshot([("Mortgage", 180000.0)])
        assert row["mortgage"] == 180000.0
        assert row["liabilities"] == 180000.0

    def test_zero_when_no_liability(self):
        row = self._snapshot([])
        assert row["mortgage"] == 0.0

    def test_negative_stored_liability_written_positive(self):
        row = self._snapshot([("Mortgage", -187121.85)])
        assert row["mortgage"] == 187121.85
        assert row["liabilities"] == 187121.85


class TestSnapshotManualEntries:
    """snapshot_manual_entries records a manual entry's value only when it
    changed (#9). Isolated DB per test."""

    def _setup(self, value=40000.0, account_number="vehicle_kia"):
        from tests.conftest import make_isolated_db
        conn, get_db = make_isolated_db()
        conn.execute(
            "INSERT INTO manual_entries (name, category, value, entered_at, account_number) "
            "VALUES ('Kia Carnival', 'car_value', ?, '2026-03-18', ?)",
            (value, account_number),
        )
        return conn, get_db

    def _run(self, get_db, today):
        with patch("api.sync.get_db", get_db):
            sync.snapshot_manual_entries(today)

    def _snaps(self, conn):
        return [
            (r["name"], r["account_number"], r["value"], str(r["snapped_at"]))
            for r in conn.execute("SELECT * FROM manual_entry_snapshots ORDER BY snapped_at, id")
        ]

    def _add_snap(self, conn, name, value, snapped_at, acct="vehicle_kia"):
        conn.execute(
            "INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at) "
            "VALUES (?, ?, 'car_value', ?, ?)",
            (name, acct, value, snapped_at),
        )

    def test_writes_row_when_entry_has_none(self):
        conn, get_db = self._setup()
        self._run(get_db, "2026-11-13")
        assert self._snaps(conn) == [("Kia Carnival", "vehicle_kia", 40000.0, "2026-11-13")]

    def test_writes_row_when_value_changed(self):
        conn, get_db = self._setup(value=38500.0)
        self._add_snap(conn, "Kia Carnival", 40000.0, "2026-03-18")
        self._run(get_db, "2026-11-13")
        assert self._snaps(conn)[-1] == ("Kia Carnival", "vehicle_kia", 38500.0, "2026-11-13")
        assert len(self._snaps(conn)) == 2

    def test_writes_nothing_when_unchanged(self):
        conn, get_db = self._setup(value=40000.004)  # within half a cent
        self._add_snap(conn, "Kia Carnival", 40000.0, "2026-03-18")
        self._run(get_db, "2026-11-13")
        assert len(self._snaps(conn)) == 1

    def test_second_run_same_day_is_noop(self):
        conn, get_db = self._setup(value=38500.0)
        self._add_snap(conn, "Kia Carnival", 40000.0, "2026-03-18")
        self._run(get_db, "2026-11-13")
        first = self._snaps(conn)
        self._run(get_db, "2026-11-13")
        assert self._snaps(conn) == first

    def test_skips_entries_without_account_number(self):
        conn, get_db = self._setup(account_number=None)
        self._run(get_db, "2026-11-13")
        assert self._snaps(conn) == []

    def test_matches_by_account_number_not_name(self):
        conn, get_db = self._setup()
        self._add_snap(conn, "Old Kia Name", 40000.0, "2026-03-18")
        self._run(get_db, "2026-11-13")
        assert self._snaps(conn) == [("Old Kia Name", "vehicle_kia", 40000.0, "2026-03-18")]

    def test_sync_all_runs_it_before_net_worth_snapshot(self):
        calls = []
        with patch("api.sync.get_db", self._setup()[1]), \
             patch("api.sync.snapshot_manual_entries", side_effect=lambda d: calls.append(("manual", d))), \
             patch("api.sync._take_net_worth_snapshot", side_effect=lambda d: calls.append(("nw", d))), \
             patch("api.coinbase_sync.sync_coinbase"), \
             patch("api.push.notify_stale_i360"):
            sync.sync_all()
        assert [c[0] for c in calls] == ["manual", "nw"]
        assert calls[0][1] == calls[1][1]


class TestSyncInvestmentTransactions:
    """Investment transactions are saved with plain-text type/subtype (#119).

    Plaid's SDK returns type/subtype as enum model objects, which SQLite cannot
    bind; before the fix every row was rejected and the table stayed empty.
    These tests use real SDK model objects, not mocks, for the transactions.
    """

    def _txn(self, txn_id, txn_type="buy", subtype="buy", amount=100.0):
        from datetime import date
        from plaid.model.investment_transaction import InvestmentTransaction
        from plaid.model.investment_transaction_subtype import InvestmentTransactionSubtype
        from plaid.model.investment_transaction_type import InvestmentTransactionType
        return InvestmentTransaction(
            investment_transaction_id=txn_id,
            account_id="plaid-inv-acct",
            security_id="sec-vtsax",
            date=date(2026, 11, 3),
            name="BUY VTSAX",
            quantity=1.5,
            amount=amount,
            price=66.67,
            fees=0.0,
            type=InvestmentTransactionType(txn_type),
            subtype=InvestmentTransactionSubtype(subtype),
            iso_currency_code="USD",
            unofficial_currency_code=None,
            cancel_transaction_id=None,
        )

    def _setup(self):
        from tests.conftest import make_isolated_db
        conn, get_db = make_isolated_db()
        conn.execute(
            "INSERT INTO accounts (plaid_account_id, name, type, account_number) "
            "VALUES ('plaid-inv-acct', 'Brokerage', 'investment', 'plaid-inv-acct')"
        )
        return conn, get_db

    def _run(self, get_db, txns):
        client = MagicMock()
        holdings = MagicMock()
        holdings.holdings = []
        holdings.securities = []
        client.investments_holdings_get.return_value = holdings
        page = MagicMock()
        page.investment_transactions = txns
        page.total_investment_transactions = len(txns)
        client.investments_transactions_get.return_value = page
        with patch("api.sync.get_db", get_db), \
             patch("api.sync._get_plaid_client", return_value=client), \
             patch.object(sync.logger, "warning") as m_warn:
            sync._sync_investments(1, "access-sandbox-abc", "2026-11-14")
        return m_warn

    def _rows(self, conn):
        return [
            tuple(r) for r in conn.execute(
                "SELECT investment_transaction_id, type, subtype, typeof(type), typeof(subtype), "
                "amount, account_number FROM plaid_investment_transactions ORDER BY investment_transaction_id"
            )
        ]

    def test_saves_enum_type_and_subtype_as_text(self):
        conn, get_db = self._setup()
        m_warn = self._run(get_db, [
            self._txn("inv-1"),
            self._txn("inv-2", "cash", "dividend", amount=-12.34),
        ])
        m_warn.assert_not_called()
        assert self._rows(conn) == [
            ("inv-1", "buy", "buy", "text", "text", 100.0, "plaid-inv-acct"),
            ("inv-2", "cash", "dividend", "text", "text", -12.34, "plaid-inv-acct"),
        ]

    def test_resync_updates_without_duplicates(self):
        conn, get_db = self._setup()
        self._run(get_db, [self._txn("inv-1", amount=100.0)])
        self._run(get_db, [self._txn("inv-1", amount=101.0)])
        rows = self._rows(conn)
        assert len(rows) == 1
        assert rows[0][5] == 101.0

    def test_skips_transactions_for_unknown_accounts(self):
        conn, get_db = self._setup()
        conn.execute("DELETE FROM accounts")
        m_warn = self._run(get_db, [self._txn("inv-1")])
        m_warn.assert_not_called()
        assert self._rows(conn) == []
