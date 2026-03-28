"""Tests for the Funds (sinking funds) API endpoints.

Covers CRUD for funds, transactions (deposits/withdrawals), balance computation,
and soft-delete behavior.
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_fund(client, auth_headers, name="Test Fund", target=None):
    """Create a fund and return the parsed JSON response."""
    body = {"name": name}
    if target is not None:
        body["target_amount"] = target
    res = client.post("/api/funds/", json=body, headers=auth_headers)
    assert res.status_code == 200, res.text
    return res.json()


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestFundsAuth:
    def test_list_funds_requires_auth(self, client):
        assert client.get("/api/funds/").status_code == 401

    def test_create_fund_requires_auth(self, client):
        assert client.post("/api/funds/", json={"name": "X"}).status_code == 401


# ── Fund CRUD ─────────────────────────────────────────────────────────────────

class TestFundCRUD:
    def test_create_fund(self, client, auth_headers):
        data = _create_fund(client, auth_headers, "Vacation", target=3000.0)
        assert data["id"] > 0
        assert data["name"] == "Vacation"
        assert data["target_amount"] == 3000.0
        assert data["balance"] == 0.0

    def test_create_fund_no_target(self, client, auth_headers):
        data = _create_fund(client, auth_headers, "Gifts")
        assert data["target_amount"] is None

    def test_create_fund_empty_name(self, client, auth_headers):
        res = client.post("/api/funds/", json={"name": "  "}, headers=auth_headers)
        assert res.status_code == 400

    def test_list_funds(self, client, auth_headers):
        _create_fund(client, auth_headers, "Visible Fund")
        res = client.get("/api/funds/", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)
        names = [f["name"] for f in res.json()]
        assert "Visible Fund" in names

    def test_update_fund_name(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "Old Fund")
        res = client.patch(f"/api/funds/{fund['id']}", json={"name": "New Fund"},
                           headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["name"] == "New Fund"

    def test_update_fund_target(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "Target Fund", target=1000.0)
        res = client.patch(f"/api/funds/{fund['id']}", json={"target_amount": 2000.0},
                           headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["target_amount"] == 2000.0

    def test_delete_fund(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "To Delete Fund")
        res = client.delete(f"/api/funds/{fund['id']}", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "deleted"

    def test_deleted_fund_not_in_list(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "Ghost Fund")
        client.delete(f"/api/funds/{fund['id']}", headers=auth_headers)
        res = client.get("/api/funds/", headers=auth_headers)
        ids = [f["id"] for f in res.json()]
        assert fund["id"] not in ids

    def test_delete_nonexistent_fund(self, client, auth_headers):
        res = client.delete("/api/funds/999999", headers=auth_headers)
        assert res.status_code == 404


# ── Fund transactions ─────────────────────────────────────────────────────────

class TestFundTransactions:
    def test_add_deposit(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "Deposit Fund")
        res = client.post(f"/api/funds/{fund['id']}/transactions",
                          json={"amount": 500.0, "description": "Paycheck"},
                          headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["amount"] == 500.0

    def test_add_withdrawal(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "Withdraw Fund")
        client.post(f"/api/funds/{fund['id']}/transactions",
                    json={"amount": 1000.0}, headers=auth_headers)
        res = client.post(f"/api/funds/{fund['id']}/transactions",
                          json={"amount": -300.0, "description": "Spent"},
                          headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["amount"] == -300.0

    def test_balance_reflects_transactions(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "Balance Fund")
        client.post(f"/api/funds/{fund['id']}/transactions",
                    json={"amount": 200.0}, headers=auth_headers)
        client.post(f"/api/funds/{fund['id']}/transactions",
                    json={"amount": -50.0}, headers=auth_headers)
        # Check balance via list endpoint
        res = client.get("/api/funds/", headers=auth_headers)
        match = [f for f in res.json() if f["id"] == fund["id"]]
        assert match[0]["balance"] == 150.0

    def test_list_transactions(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "Txn List Fund")
        client.post(f"/api/funds/{fund['id']}/transactions",
                    json={"amount": 100.0}, headers=auth_headers)
        client.post(f"/api/funds/{fund['id']}/transactions",
                    json={"amount": -25.0}, headers=auth_headers)
        res = client.get(f"/api/funds/{fund['id']}/transactions", headers=auth_headers)
        assert res.status_code == 200
        assert len(res.json()) == 2

    def test_transaction_custom_date(self, client, auth_headers):
        fund = _create_fund(client, auth_headers, "Date Fund")
        res = client.post(f"/api/funds/{fund['id']}/transactions",
                          json={"amount": 75.0, "date": "2025-12-25"},
                          headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["date"] == "2025-12-25"
