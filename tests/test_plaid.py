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


class TestPlaidItems:
    def test_list_items_requires_auth(self, client):
        res = client.get("/api/plaid/items")
        assert res.status_code == 401

    def test_list_items_returns_list(self, client, auth_headers):
        res = client.get("/api/plaid/items", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)
