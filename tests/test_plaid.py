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


class TestPlaidSyncRefresh:
    """Manual /sync forces a Plaid transactions/refresh (issue #72); the
    follow-up poll passes refresh=false to avoid a second on-demand charge."""

    def test_sync_requires_auth(self, client):
        res = client.post("/api/plaid/sync")
        assert res.status_code == 401

    def test_sync_defaults_to_refresh_true(self, client, auth_headers):
        with patch("api.sync.sync_all") as mock_sync:
            res = client.post("/api/plaid/sync", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["refreshed"] is True
        mock_sync.assert_called_once_with(True)

    def test_sync_refresh_false_skips_refresh(self, client, auth_headers):
        with patch("api.sync.sync_all") as mock_sync:
            res = client.post("/api/plaid/sync?refresh=false", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["refreshed"] is False
        mock_sync.assert_called_once_with(False)


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
