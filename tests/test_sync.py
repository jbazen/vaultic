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
