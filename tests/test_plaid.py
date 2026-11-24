"""Tests for Plaid link token creation and item management."""
import pytest
from unittest.mock import patch, MagicMock


def _make_mock_link_token_response(token="link-sandbox-test-token"):
    mock_resp = MagicMock()
    mock_resp.link_token = token
    return mock_resp


class TestPlaidLinkToken:
    def test_link_token_requires_auth(self, client):
        res = client.post("/api/plaid/link-token")
        assert res.status_code == 401

    def test_link_token_returns_token(self, client, auth_headers):
        with patch("api.routers.plaid._get_client") as mock_get_client:
            mock_api = MagicMock()
            mock_get_client.return_value = mock_api
            mock_api.link_token_create.return_value = _make_mock_link_token_response()

            res = client.post("/api/plaid/link-token", headers=auth_headers)

        assert res.status_code == 200
        assert "link_token" in res.json()

    def test_link_token_uses_optional_products_for_liabilities(self, client, auth_headers):
        """Liabilities must be in optional_products (not products) so institutions
        that don't support liabilities (e.g. Vanguard) don't cause a link failure."""
        captured_request = {}

        def capture_request(req):
            captured_request["req"] = req
            return _make_mock_link_token_response()

        with patch("api.routers.plaid._get_client") as mock_get_client:
            mock_api = MagicMock()
            mock_get_client.return_value = mock_api
            mock_api.link_token_create.side_effect = capture_request

            client.post("/api/plaid/link-token", headers=auth_headers)

        req = captured_request.get("req")
        assert req is not None, "link_token_create was not called"

        # Verify liabilities is in optional_products
        opt_products = [str(p) for p in (req.optional_products or [])]
        assert any("liabilities" in p for p in opt_products), (
            "liabilities should be in optional_products so institutions that don't "
            "support it don't block the Plaid Link flow"
        )

        # Verify liabilities is NOT in required products
        req_products = [str(p) for p in (req.products or [])]
        assert not any("liabilities" in p for p in req_products), (
            "liabilities must not be in products (required) — use optional_products"
        )

    def test_link_token_includes_transactions_and_investments(self, client, auth_headers):
        """transactions and investments should always be in required products."""
        captured_request = {}

        def capture_request(req):
            captured_request["req"] = req
            return _make_mock_link_token_response()

        with patch("api.routers.plaid._get_client") as mock_get_client:
            mock_api = MagicMock()
            mock_get_client.return_value = mock_api
            mock_api.link_token_create.side_effect = capture_request

            client.post("/api/plaid/link-token", headers=auth_headers)

        req = captured_request.get("req")
        req_products = [str(p) for p in (req.products or [])]
        assert any("transactions" in p for p in req_products)
        assert any("investments" in p for p in req_products)

    def test_link_token_plaid_error_returns_502(self, client, auth_headers):
        """Plaid API errors should surface as 502, not 500."""
        import plaid
        with patch("api.routers.plaid._get_client") as mock_get_client:
            mock_api = MagicMock()
            mock_get_client.return_value = mock_api
            mock_api.link_token_create.side_effect = plaid.ApiException(status=400, reason="Bad Request")

            res = client.post("/api/plaid/link-token", headers=auth_headers)

        assert res.status_code == 502


class TestPlaidUpdateLinkToken:
    """Update-mode (Reconnect) link token — repairs an item whose login expired."""

    def _insert_item(self, item_id="item-reconnect-test"):
        from tests.conftest import _test_get_db
        from api.encryption import encrypt
        with _test_get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO plaid_items (item_id, institution_name, access_token_enc) "
                "VALUES (?, ?, ?)",
                (item_id, "Voya Financial", encrypt("access-sandbox-xyz")),
            )
            conn.commit()
        return item_id

    def _cleanup_item(self, item_id):
        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            conn.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))
            conn.commit()

    def test_update_link_token_requires_auth(self, client):
        res = client.post("/api/plaid/link-token/update", json={"item_id": "x"})
        assert res.status_code == 401

    def test_update_link_token_404_for_unknown_item(self, client, auth_headers):
        res = client.post("/api/plaid/link-token/update",
                          headers=auth_headers, json={"item_id": "does-not-exist"})
        assert res.status_code == 404

    def test_update_link_token_returns_token(self, client, auth_headers):
        item_id = self._insert_item()
        try:
            with patch("api.routers.plaid._get_client") as mock_get_client:
                mock_api = MagicMock()
                mock_get_client.return_value = mock_api
                mock_api.link_token_create.return_value = _make_mock_link_token_response()
                res = client.post("/api/plaid/link-token/update",
                                  headers=auth_headers, json={"item_id": item_id})
            assert res.status_code == 200
            assert "link_token" in res.json()
        finally:
            self._cleanup_item(item_id)

    def test_update_link_token_passes_access_token_and_no_products(self, client, auth_headers):
        """Update mode must send the existing access_token and omit `products`."""
        item_id = self._insert_item("item-reconnect-test-2")
        captured = {}

        def capture_request(req):
            captured["req"] = req
            return _make_mock_link_token_response()

        try:
            with patch("api.routers.plaid._get_client") as mock_get_client:
                mock_api = MagicMock()
                mock_get_client.return_value = mock_api
                mock_api.link_token_create.side_effect = capture_request
                client.post("/api/plaid/link-token/update",
                            headers=auth_headers, json={"item_id": item_id})
            req = captured.get("req")
            assert req is not None
            as_dict = req.to_dict()
            assert as_dict.get("access_token") == "access-sandbox-xyz"
            # `products` must be unset in update mode (Plaid rejects it otherwise)
            assert "products" not in as_dict or not as_dict["products"]
        finally:
            self._cleanup_item(item_id)

    def test_update_link_token_plaid_error_returns_502(self, client, auth_headers):
        item_id = self._insert_item("item-reconnect-test-3")
        try:
            import plaid
            with patch("api.routers.plaid._get_client") as mock_get_client:
                mock_api = MagicMock()
                mock_get_client.return_value = mock_api
                mock_api.link_token_create.side_effect = plaid.ApiException(status=400, reason="Bad Request")
                res = client.post("/api/plaid/link-token/update",
                                  headers=auth_headers, json={"item_id": item_id})
            assert res.status_code == 502
        finally:
            self._cleanup_item(item_id)


class TestPlaidExchange:
    """Linking an institution that's already connected must not create a
    duplicate item and a second copy of every account (issue #76)."""

    EXISTING = "item-exchange-existing"
    NEW = "item-exchange-new"

    def _mock_client(self, new_item_id):
        mock_api = MagicMock()
        mock_api.item_public_token_exchange.return_value = MagicMock(
            access_token="access-new", item_id=new_item_id)
        # Both the existing and the new item report the same account: the same
        # login linked twice (#76). Different logins are covered in
        # TestPlaidExchangeSecondLogin (#179).
        acct = MagicMock()
        acct.mask, acct.type, acct.subtype, acct.name = "0001", "depository", "checking", "TOTAL CHECKING"
        mock_api.accounts_get.return_value = MagicMock(accounts=[acct])
        return mock_api

    def _insert_existing(self):
        from tests.conftest import _test_get_db
        from api.encryption import encrypt
        with _test_get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO plaid_items (item_id, institution_id, institution_name, access_token_enc) "
                "VALUES (?, 'ins_exchange_test', 'Chase', ?)", (self.EXISTING, encrypt("access-old")))

    def _cleanup(self):
        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            conn.execute("DELETE FROM plaid_items WHERE item_id IN (?, ?)", (self.EXISTING, self.NEW))

    def _exchange(self, client, auth_headers, mock_api, institution_id="ins_exchange_test"):
        with patch("api.routers.plaid._get_client", return_value=mock_api), patch("api.sync.sync_all"):
            return client.post("/api/plaid/exchange", headers=auth_headers, json={
                "public_token": "public-x", "institution_id": institution_id, "institution_name": "Chase"})

    def _item_ids(self):
        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            return {r[0] for r in conn.execute(
                "SELECT item_id FROM plaid_items WHERE item_id IN (?, ?)", (self.EXISTING, self.NEW))}

    def test_exchange_requires_auth(self, client):
        res = client.post("/api/plaid/exchange", json={"public_token": "x"})
        assert res.status_code == 401

    def test_new_institution_is_stored(self, client, auth_headers):
        try:
            res = self._exchange(client, auth_headers, self._mock_client(self.NEW))
            assert res.status_code == 200
            assert self._item_ids() == {self.NEW}
        finally:
            self._cleanup()

    def test_already_connected_institution_returns_409(self, client, auth_headers):
        self._insert_existing()
        try:
            mock_api = self._mock_client(self.NEW)
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 409
            assert "Reconnect" in res.json()["detail"]
            assert self._item_ids() == {self.EXISTING}
            # The new item is removed at Plaid so it never bills
            req = mock_api.item_remove.call_args[0][0]
            assert req.access_token == "access-new"
        finally:
            self._cleanup()

    def test_409_even_if_plaid_item_remove_fails(self, client, auth_headers):
        import plaid
        self._insert_existing()
        try:
            mock_api = self._mock_client(self.NEW)
            mock_api.item_remove.side_effect = plaid.ApiException(status=400, reason="Bad Request")
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 409
            assert self._item_ids() == {self.EXISTING}
        finally:
            self._cleanup()

    def test_different_institution_is_allowed(self, client, auth_headers):
        self._insert_existing()
        try:
            res = self._exchange(client, auth_headers, self._mock_client(self.NEW), institution_id="ins_other")
            assert res.status_code == 200
            assert self._item_ids() == {self.EXISTING, self.NEW}
        finally:
            self._cleanup()


class TestPlaidExchangeSecondLogin:
    """#179: a second login at the same bank (LPL, one per spouse) is a
    different set of accounts and must be stored. Linking the same login again,
    or a login that shares an account, is still refused (#76)."""

    EXISTING = "item-login-existing"
    NEW = "item-login-new"
    INST = "ins_login_test"

    @staticmethod
    def _acct(mask, subtype="ira", name="SAM - Retirement", acct_type="investment"):
        a = MagicMock()
        a.mask, a.type, a.subtype, a.name = mask, acct_type, subtype, name
        return a

    def _mock_client(self, new_accounts, existing_accounts=None):
        mock_api = MagicMock()
        mock_api.item_public_token_exchange.return_value = MagicMock(
            access_token="access-new", item_id=self.NEW)
        by_token = {"access-new": new_accounts, "access-old": existing_accounts or []}
        mock_api.accounts_get.side_effect = lambda req: MagicMock(accounts=by_token[req.access_token])
        return mock_api

    def _insert_existing(self, stored_masks=()):
        from tests.conftest import _test_get_db
        from api.encryption import encrypt
        with _test_get_db() as conn:
            cur = conn.execute(
                "INSERT INTO plaid_items (item_id, institution_id, institution_name, access_token_enc) "
                "VALUES (?, ?, 'LPL Financial', ?)", (self.EXISTING, self.INST, encrypt("access-old")))
            for mask in stored_masks:
                conn.execute(
                    "INSERT INTO accounts (plaid_account_id, plaid_item_id, name, mask, type, subtype, "
                    "account_number) VALUES (?, ?, 'SAM - Retirement', ?, 'investment', 'ira', ?)",
                    (f"pa-login-{mask}", cur.lastrowid, mask, f"pa-login-{mask}"))

    def _cleanup(self):
        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            conn.execute("DELETE FROM accounts WHERE plaid_account_id LIKE 'pa-login-%'")
            conn.execute("DELETE FROM plaid_items WHERE item_id IN (?, ?)", (self.EXISTING, self.NEW))

    def _exchange(self, client, auth_headers, mock_api):
        with patch("api.routers.plaid._get_client", return_value=mock_api), patch("api.sync.sync_all"):
            return client.post("/api/plaid/exchange", headers=auth_headers, json={
                "public_token": "public-x", "institution_id": self.INST,
                "institution_name": "LPL Financial"})

    def _item_ids(self):
        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            return {r[0] for r in conn.execute(
                "SELECT item_id FROM plaid_items WHERE item_id IN (?, ?)", (self.EXISTING, self.NEW))}

    def test_second_login_with_different_accounts_is_stored(self, client, auth_headers):
        self._insert_existing(stored_masks=("4616", "1440"))
        try:
            mock_api = self._mock_client([self._acct("7001"), self._acct("7002", "brokerage")])
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 200
            assert self._item_ids() == {self.EXISTING, self.NEW}
            mock_api.item_remove.assert_not_called()
        finally:
            self._cleanup()

    def test_same_login_again_is_refused(self, client, auth_headers):
        self._insert_existing(stored_masks=("4616", "1440"))
        try:
            mock_api = self._mock_client([self._acct("4616"), self._acct("1440")])
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 409
            assert "Reconnect" in res.json()["detail"]
            assert self._item_ids() == {self.EXISTING}
            assert mock_api.item_remove.call_args[0][0].access_token == "access-new"
        finally:
            self._cleanup()

    def test_login_sharing_one_account_is_refused(self, client, auth_headers):
        """A shared (joint) account in both logins would be counted twice."""
        self._insert_existing(stored_masks=("4616",))
        try:
            mock_api = self._mock_client([self._acct("7001"), self._acct("4616")])
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 409
            assert self._item_ids() == {self.EXISTING}
        finally:
            self._cleanup()

    def test_same_mask_different_account_type_is_allowed(self, client, auth_headers):
        self._insert_existing(stored_masks=("4616",))
        try:
            mock_api = self._mock_client([self._acct("4616", subtype="checking", acct_type="depository")])
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 200
        finally:
            self._cleanup()

    def test_existing_item_without_stored_accounts_is_asked_from_plaid(self, client, auth_headers):
        """If the first login's accounts were never stored (its first sync failed),
        its accounts come from Plaid, so a fresh duplicate is still caught."""
        self._insert_existing()
        try:
            mock_api = self._mock_client([self._acct("4616")], existing_accounts=[self._acct("4616")])
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 409
            assert self._item_ids() == {self.EXISTING}
        finally:
            self._cleanup()

    def test_plaid_accounts_error_removes_new_item_and_stores_nothing(self, client, auth_headers):
        import plaid
        self._insert_existing(stored_masks=("4616",))
        try:
            mock_api = self._mock_client([])
            mock_api.accounts_get.side_effect = plaid.ApiException(status=500, reason="Server Error")
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 502
            assert "was not added" in res.json()["detail"]
            assert self._item_ids() == {self.EXISTING}
            assert mock_api.item_remove.call_args[0][0].access_token == "access-new"
        finally:
            self._cleanup()

    def test_reclassified_subtype_still_counts_as_same_account(self, client, auth_headers):
        """Stored subtype is never refreshed by the sync, so a subtype Plaid has
        since changed must not let the same login through."""
        self._insert_existing(stored_masks=("4616",))
        try:
            mock_api = self._mock_client([self._acct("4616", subtype="retirement")])
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 409
        finally:
            self._cleanup()

    def test_non_plaid_error_in_check_still_removes_new_item(self, client, auth_headers):
        """A dropped connection or SDK parse error is not a plaid.ApiException;
        the new item must still be removed at Plaid and the answer is a 502."""
        self._insert_existing(stored_masks=("4616",))
        try:
            mock_api = self._mock_client([])
            mock_api.accounts_get.side_effect = ConnectionResetError("connection reset")
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 502
            assert "was not added" in res.json()["detail"]
            assert "connection reset" not in res.json()["detail"]
            assert self._item_ids() == {self.EXISTING}
            assert mock_api.item_remove.call_args[0][0].access_token == "access-new"
        finally:
            self._cleanup()

    def test_failed_removal_still_answers_409(self, client, auth_headers):
        self._insert_existing(stored_masks=("4616",))
        try:
            mock_api = self._mock_client([self._acct("4616")])
            mock_api.item_remove.side_effect = TimeoutError("timed out")
            res = self._exchange(client, auth_headers, mock_api)
            assert res.status_code == 409
            assert self._item_ids() == {self.EXISTING}
        finally:
            self._cleanup()

    def test_message_without_institution_name_reads_cleanly(self, client, auth_headers):
        self._insert_existing(stored_masks=("4616",))
        try:
            mock_api = self._mock_client([self._acct("4616")])
            with patch("api.routers.plaid._get_client", return_value=mock_api), patch("api.sync.sync_all"):
                res = client.post("/api/plaid/exchange", headers=auth_headers, json={
                    "public_token": "public-x", "institution_id": self.INST})
            assert res.status_code == 409
            assert res.json()["detail"].startswith(
                "This login at this bank (or one of its accounts) is already connected.")
        finally:
            self._cleanup()

    def test_accounts_list_carries_plaid_item_id(self, client, auth_headers):
        """The Dashboard labels each login's Reconnect/Disconnect with this field."""
        self._insert_existing(stored_masks=("4616",))
        try:
            rows = client.get("/api/accounts", headers=auth_headers).json()
            row = next(a for a in rows if a["coinbase_uuid"] == "pa-login-4616")
            assert row["plaid_item_id"] == self.EXISTING
        finally:
            self._cleanup()


class TestPlaidRemoveItem:
    """Disconnect removes the item at Plaid first (so it stops billing), then
    deactivates its accounts and deletes the item without an FK error (issue #77)."""

    ITEM = "item-remove-test"

    def _seed(self):
        from tests.conftest import _test_get_db
        from api.encryption import encrypt
        with _test_get_db() as conn:
            cur = conn.execute(
                "INSERT INTO plaid_items (item_id, institution_id, institution_name, access_token_enc) "
                "VALUES (?, 'ins_remove_test', 'Voya', ?)", (self.ITEM, encrypt("access-remove")))
            item_db_id = cur.lastrowid
            conn.execute(
                "INSERT INTO accounts (plaid_account_id, plaid_item_id, name, type, account_number) "
                "VALUES ('pa-remove-test', ?, '401K', 'investment', 'pa-remove-test')", (item_db_id,))
            acct_id = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = 'pa-remove-test'").fetchone()[0]
            conn.execute(
                "INSERT INTO account_balances (account_id, current, snapped_at, account_number) "
                "VALUES (?, 100.0, '2099-02-01', 'pa-remove-test')", (acct_id,))
        return acct_id

    def _cleanup(self):
        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            conn.execute("DELETE FROM account_balances WHERE account_number = 'pa-remove-test'")
            conn.execute("DELETE FROM accounts WHERE plaid_account_id = 'pa-remove-test'")
            conn.execute("DELETE FROM plaid_items WHERE item_id = ?", (self.ITEM,))

    def _state(self):
        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            item = conn.execute("SELECT 1 FROM plaid_items WHERE item_id = ?", (self.ITEM,)).fetchone()
            acct = conn.execute(
                "SELECT is_active, plaid_item_id FROM accounts WHERE plaid_account_id = 'pa-remove-test'").fetchone()
            bals = conn.execute(
                "SELECT COUNT(*) FROM account_balances WHERE account_number = 'pa-remove-test'").fetchone()[0]
        return item is not None, dict(acct) if acct else None, bals

    def _delete(self, client, auth_headers, mock_api):
        with patch("api.routers.plaid._get_client", return_value=mock_api):
            return client.delete(f"/api/plaid/items/{self.ITEM}", headers=auth_headers)

    def test_remove_requires_auth(self, client):
        res = client.delete(f"/api/plaid/items/{self.ITEM}")
        assert res.status_code == 401

    def test_remove_unknown_item_404(self, client, auth_headers):
        res = client.delete("/api/plaid/items/does-not-exist", headers=auth_headers)
        assert res.status_code == 404

    def test_remove_calls_plaid_and_keeps_history(self, client, auth_headers):
        self._seed()
        try:
            mock_api = MagicMock()
            res = self._delete(client, auth_headers, mock_api)
            assert res.status_code == 200
            assert mock_api.item_remove.call_args[0][0].access_token == "access-remove"
            item_exists, acct, bals = self._state()
            # Regression: item with accounts deletes without a FOREIGN KEY failure
            assert item_exists is False
            assert acct == {"is_active": 0, "plaid_item_id": None}
            assert bals == 1
        finally:
            self._cleanup()

    def test_plaid_error_changes_nothing(self, client, auth_headers):
        import plaid
        self._seed()
        try:
            mock_api = MagicMock()
            mock_api.item_remove.side_effect = plaid.ApiException(status=500, reason="Server Error")
            res = self._delete(client, auth_headers, mock_api)
            assert res.status_code == 502
            item_exists, acct, _ = self._state()
            assert item_exists is True
            assert acct["is_active"] == 1
            assert acct["plaid_item_id"] is not None
        finally:
            self._cleanup()

    def test_item_not_found_at_plaid_counts_as_removed(self, client, auth_headers):
        import plaid
        self._seed()
        try:
            mock_api = MagicMock()
            err = plaid.ApiException(status=400, reason="Bad Request")
            err.body = '{"error_code": "ITEM_NOT_FOUND"}'
            mock_api.item_remove.side_effect = err
            res = self._delete(client, auth_headers, mock_api)
            assert res.status_code == 200
            item_exists, acct, _ = self._state()
            assert item_exists is False
            assert acct["is_active"] == 0
        finally:
            self._cleanup()


class TestPlaidSync:
    """Manual /sync runs the normal sync only — no transactions/refresh (issue #79)."""

    @pytest.fixture(autouse=True)
    def _reset_rate_limit(self):
        from api import rate_limit
        rate_limit._sync_calls.clear()
        yield
        rate_limit._sync_calls.clear()

    def test_sync_requires_auth(self, client):
        res = client.post("/api/plaid/sync")
        assert res.status_code == 401

    def test_sync_calls_sync_all_without_args(self, client, auth_headers):
        with patch("api.sync.sync_all") as mock_sync:
            res = client.post("/api/plaid/sync", headers=auth_headers)
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
        mock_sync.assert_called_once_with()

    def test_sync_ignores_legacy_refresh_param(self, client, auth_headers):
        """An old cached frontend may still send ?refresh=true — harmless."""
        with patch("api.sync.sync_all") as mock_sync:
            res = client.post("/api/plaid/sync?refresh=true", headers=auth_headers)
        assert res.status_code == 200
        mock_sync.assert_called_once_with()

    def test_sync_rate_limited_after_five_calls(self, client, auth_headers):
        with patch("api.sync.sync_all"):
            for _ in range(5):
                assert client.post("/api/plaid/sync", headers=auth_headers).status_code == 200
            res = client.post("/api/plaid/sync", headers=auth_headers)
        assert res.status_code == 429


class TestPlaidItems:
    def test_list_items_requires_auth(self, client):
        res = client.get("/api/plaid/items")
        assert res.status_code == 401

    def test_list_items_returns_list(self, client, auth_headers):
        res = client.get("/api/plaid/items", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_items_exposes_error_code(self, client, auth_headers):
        """The items feed must surface error_code so the Accounts banner can key
        off a real disconnect (issue #73) rather than sync staleness."""
        from tests.conftest import _test_get_db
        from api.encryption import encrypt
        item_id = "item-error-code-test"
        with _test_get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO plaid_items (item_id, institution_name, access_token_enc, error_code) "
                "VALUES (?, ?, ?, ?)",
                (item_id, "Chase", encrypt("access-sandbox-x"), "ITEM_LOGIN_REQUIRED"),
            )
            conn.commit()
        try:
            res = client.get("/api/plaid/items", headers=auth_headers)
            assert res.status_code == 200
            row = next((i for i in res.json() if i["item_id"] == item_id), None)
            assert row is not None
            assert row["error_code"] == "ITEM_LOGIN_REQUIRED"
            assert "last_error_at" in row
        finally:
            with _test_get_db() as conn:
                conn.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))
                conn.commit()
