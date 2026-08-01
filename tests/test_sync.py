"""Tests for the Plaid sync pipeline — force-refresh (issue #72)."""
from unittest.mock import patch, MagicMock

from api import sync
from api.encryption import encrypt
from tests.conftest import _test_get_db


def _insert_item(item_id, institution="Test Bank"):
    with _test_get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO plaid_items (item_id, institution_name, access_token_enc) "
            "VALUES (?, ?, ?)",
            (item_id, institution, encrypt("access-sandbox-abc")),
        )
        row = conn.execute("SELECT id FROM plaid_items WHERE item_id = ?", (item_id,)).fetchone()
        conn.commit()
    return row["id"]


def _cleanup_item(item_id):
    with _test_get_db() as conn:
        conn.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))
        conn.commit()


class TestRefreshTransactions:
    def test_calls_plaid_transactions_refresh(self):
        with patch("api.sync._get_plaid_client") as m_client:
            api = MagicMock()
            m_client.return_value = api
            sync._refresh_transactions("access-token-xyz")
        assert api.transactions_refresh.called
        req = api.transactions_refresh.call_args.args[0]
        assert req.access_token == "access-token-xyz"


class TestSyncItemRefreshBranch:
    """_sync_item fires transactions/refresh only when refresh=True."""

    def _run(self, refresh):
        with patch("api.sync._refresh_transactions") as m_refresh, \
             patch("api.sync._get_plaid_client") as m_client, \
             patch("api.sync._sync_transactions"), \
             patch("api.sync._sync_investments"), \
             patch("api.sync._take_net_worth_snapshot"):
            api = MagicMock()
            resp = MagicMock()
            resp.accounts = []
            resp.item.institution_id = None
            api.accounts_get.return_value = resp
            m_client.return_value = api
            sync._sync_item(1, "item-x", "tok", "2026-07-31", refresh=refresh)
        return m_refresh

    def test_refresh_true_calls_refresh(self):
        assert self._run(True).called

    def test_refresh_false_skips_refresh(self):
        assert not self._run(False).called

    def test_refresh_failure_is_non_fatal(self):
        """A dead login makes refresh raise — sync must continue, not crash."""
        with patch("api.sync._refresh_transactions", side_effect=Exception("ITEM_LOGIN_REQUIRED")), \
             patch("api.sync._get_plaid_client") as m_client, \
             patch("api.sync._sync_transactions"), \
             patch("api.sync._sync_investments"), \
             patch("api.sync._take_net_worth_snapshot"):
            api = MagicMock()
            resp = MagicMock()
            resp.accounts = []
            resp.item.institution_id = None
            api.accounts_get.return_value = resp
            m_client.return_value = api
            # Should not raise
            sync._sync_item(1, "item-x", "tok", "2026-07-31", refresh=True)


class TestSyncAllRefreshThreading:
    """sync_all threads the refresh flag to every item; cron default = no refresh."""

    def _collect_refresh_flags(self, *args):
        item_db_id = _insert_item("refresh-thread-item")
        try:
            with patch("api.sync._sync_item") as m_item, \
                 patch("api.sync._take_net_worth_snapshot"), \
                 patch("api.coinbase_sync.sync_coinbase"), \
                 patch("api.push.notify_stale_i360"):
                sync.sync_all(*args)
            return [c.kwargs.get("refresh") for c in m_item.call_args_list]
        finally:
            _cleanup_item(item_db_id)

    def test_manual_refresh_true(self):
        flags = self._collect_refresh_flags(True)
        assert flags and all(f is True for f in flags)

    def test_cron_default_no_refresh(self):
        flags = self._collect_refresh_flags()
        assert flags and all(f is False for f in flags)
