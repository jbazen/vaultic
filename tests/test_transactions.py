"""Tests for transaction and balance history endpoints."""
from api.database import get_db
import datetime


TEST_ACCT_NUMBER = "test_checking_001"


def _insert_test_account():
    """Insert a test account and return its id."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (name, type, institution_name, account_number) "
            "VALUES (?, ?, ?, ?)",
            ("Test Checking", "depository", "Test Bank", TEST_ACCT_NUMBER)
        )
        # Ensure account_number is set even if a prior test inserted this row without one
        conn.execute(
            "UPDATE accounts SET account_number = ? WHERE name = 'Test Checking' "
            "AND (account_number IS NULL OR account_number = '')",
            (TEST_ACCT_NUMBER,)
        )
        row = conn.execute(
            "SELECT id FROM accounts WHERE name = 'Test Checking'"
        ).fetchone()
        return row["id"]


def _insert_test_balance(account_id, amount=1000.0):
    today = datetime.date.today().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO account_balances "
            "(account_id, current, available, snapped_at, account_number) "
            "VALUES (?, ?, ?, ?, ?)",
            (account_id, amount, amount, today, TEST_ACCT_NUMBER)
        )


def _insert_test_transaction(account_id):
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO transactions
               (transaction_id, account_id, amount, date, name, merchant_name, category,
                account_number)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("txn_test_001", account_id, 42.50, "2026-03-10", "Coffee Shop",
             "Blue Bottle Coffee", "Food and Drink", TEST_ACCT_NUMBER)
        )


class TestBalanceHistory:
    def setup_method(self):
        self.account_id = _insert_test_account()
        _insert_test_balance(self.account_id)

    def test_balance_history_requires_auth(self, client):
        res = client.get(f"/api/accounts/{self.account_id}/balances")
        assert res.status_code == 401

    def test_balance_history_returns_list(self, client, auth_headers):
        res = client.get(f"/api/accounts/{self.account_id}/balances", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_balance_history_contains_data(self, client, auth_headers):
        res = client.get(f"/api/accounts/{self.account_id}/balances", headers=auth_headers)
        data = res.json()
        assert len(data) >= 1
        assert data[0]["current"] == 1000.0

    def test_balance_history_days_param(self, client, auth_headers):
        res = client.get(f"/api/accounts/{self.account_id}/balances?days=7",
                         headers=auth_headers)
        assert res.status_code == 200


class TestTransactions:
    def setup_method(self):
        self.account_id = _insert_test_account()
        _insert_test_transaction(self.account_id)

    def test_transactions_requires_auth(self, client):
        res = client.get(f"/api/accounts/{self.account_id}/transactions")
        assert res.status_code == 401

    def test_transactions_returns_list(self, client, auth_headers):
        res = client.get(f"/api/accounts/{self.account_id}/transactions",
                         headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_transactions_contains_data(self, client, auth_headers):
        res = client.get(f"/api/accounts/{self.account_id}/transactions",
                         headers=auth_headers)
        data = res.json()
        assert any(t["name"] == "Coffee Shop" for t in data)

    def test_transactions_pagination(self, client, auth_headers):
        res = client.get(f"/api/accounts/{self.account_id}/transactions?limit=1&offset=0",
                         headers=auth_headers)
        assert res.status_code == 200
        assert len(res.json()) <= 1

    def test_recent_transactions_requires_auth(self, client):
        res = client.get("/api/accounts/transactions/recent")
        assert res.status_code == 401

    def test_recent_transactions_returns_list(self, client, auth_headers):
        res = client.get("/api/accounts/transactions/recent", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


class TestAccountRename:
    def setup_method(self):
        self.account_id = _insert_test_account()

    def test_rename_requires_auth(self, client):
        res = client.patch(f"/api/accounts/{self.account_id}/rename",
                           json={"display_name": "My Checking"})
        assert res.status_code == 401

    def test_rename_updates_display_name(self, client, auth_headers):
        res = client.patch(f"/api/accounts/{self.account_id}/rename",
                           headers=auth_headers,
                           json={"display_name": "My Main Checking"})
        assert res.status_code == 200

        accounts = client.get("/api/accounts", headers=auth_headers).json()
        match = next((a for a in accounts if a.get("id") == self.account_id or
                      a.get("name") == "Test Checking"), None)
        if match:
            assert match.get("display_name") == "My Main Checking"
