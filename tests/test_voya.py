"""Tests for the Voya 401K local-sync integration (issue #51).

Covers:
  - parse_curl: cookie + session-token extraction, Windows ^ stripping, validation
  - _parse_accounts: defensive JSON parsing + SchemaUnknownError on unknown shape
  - POST /sync-local: auth guard, manual_entries upsert (VOYA401K/invested),
    snapshot history, voya_accounts + sync-log storage
  - status / accounts / sync-log query endpoints + auth guards
"""
import pytest
from unittest.mock import patch

from tests.conftest import _test_get_db
from api import voya_client
from api.voya_client import parse_curl, _parse_accounts, SchemaUnknownError

_COOKIE = ("MYVOYA_SSO_SESSION_ID=abc; MYVOYA_SESSION_ID=def; "
           "JSESSIONID=73091BB3; __cf_bm=xyz")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with _test_get_db() as conn:
        conn.execute("DELETE FROM manual_entries WHERE account_number = 'VOYA401K'")
        conn.execute("DELETE FROM manual_entry_snapshots WHERE account_number = 'VOYA401K'")
        conn.execute("DELETE FROM voya_accounts")
        conn.execute("DELETE FROM voya_sync_log")
        conn.commit()


class TestParseCurl:
    def test_extracts_cookies_and_session_token(self):
        curl = (f'curl "https://my.voya.com/myvoyage/ws/ers/dashboard/accounts?s=05525458F908" '
                f'-b "{_COOKIE}"')
        cookies, token = parse_curl(curl)
        assert cookies["JSESSIONID"] == "73091BB3"
        assert token == "05525458F908"

    def test_strips_windows_caret_escaping(self):
        curl = f'curl ^"https://my.voya.com^" -b ^"{_COOKIE}^"'
        cookies, _ = parse_curl(curl)
        assert cookies["MYVOYA_SSO_SESSION_ID"] == "abc"

    def test_missing_required_cookie_raises(self):
        with pytest.raises(ValueError, match="Missing required Voya cookies"):
            parse_curl('curl "https://my.voya.com" -b "JSESSIONID=x"')

    def test_no_session_token_returns_none(self):
        cookies, token = parse_curl(f'curl "https://my.voya.com/x" -b "{_COOKIE}"')
        assert token is None


class TestParseAccounts:
    def test_parses_named_balances(self):
        data = {"accounts": [
            {"planName": "JSTOOGOOD, LLC 401(K) P/S PLAN", "totalBalance": 22903.64, "planId": "P1"},
            {"planName": "DISYS TECHNOLOGIES", "totalBalance": 0.0},
        ]}
        out = _parse_accounts(data)
        assert out["total_balance"] == 22903.64
        assert len(out["accounts"]) == 2
        assert out["accounts"][0]["name"].startswith("JSTOOGOOD")
        assert out["accounts"][0]["plan_id"] == "P1"

    def test_parses_bare_list_and_string_dollars(self):
        out = _parse_accounts([{"name": "Voya", "balance": "$1,234.56"}])
        assert out["total_balance"] == 1234.56

    def test_finds_nested_list(self):
        out = _parse_accounts({"payload": {"accountList": [{"name": "A", "marketValue": 10}]}})
        assert out["total_balance"] == 10.0

    def test_unknown_shape_raises_with_payload(self):
        payload = {"greeting": "hello", "nope": 1}
        with pytest.raises(SchemaUnknownError) as exc:
            _parse_accounts(payload)
        assert exc.value.payload == payload


class TestSyncLocal:
    def test_requires_auth(self, client):
        res = client.post("/api/voya/sync-local", json={"total_balance": 1.0})
        assert res.status_code == 401

    def test_stores_balance_and_snapshot(self, client, auth_headers):
        with patch("api.routers.voya._take_net_worth_snapshot"):
            res = client.post("/api/voya/sync-local", headers=auth_headers, json={
                "total_balance": 22903.64,
                "accounts": [
                    {"name": "JSTOOGOOD 401(K)", "plan_id": "P1", "balance": 22903.64},
                    {"name": "DISYS", "balance": 0.0},
                ],
            })
        assert res.status_code == 200
        assert res.json()["accounts"] == 2
        with _test_get_db() as conn:
            entry = conn.execute(
                "SELECT name, category, value FROM manual_entries WHERE account_number='VOYA401K'"
            ).fetchone()
            assert entry["category"] == "invested"
            assert entry["value"] == 22903.64
            snap = conn.execute(
                "SELECT value FROM manual_entry_snapshots WHERE account_number='VOYA401K'"
            ).fetchone()
            assert snap["value"] == 22903.64
            n_accts = conn.execute("SELECT COUNT(*) FROM voya_accounts").fetchone()[0]
            assert n_accts == 2
            log = conn.execute(
                "SELECT status, total_balance FROM voya_sync_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert log["status"] == "success"
            assert log["total_balance"] == 22903.64

    def test_second_sync_updates_single_row(self, client, auth_headers):
        with patch("api.routers.voya._take_net_worth_snapshot"):
            client.post("/api/voya/sync-local", headers=auth_headers,
                        json={"total_balance": 100.0, "accounts": []})
            client.post("/api/voya/sync-local", headers=auth_headers,
                        json={"total_balance": 250.0, "accounts": []})
        with _test_get_db() as conn:
            rows = conn.execute(
                "SELECT value FROM manual_entries WHERE account_number='VOYA401K'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == 250.0


class TestDeactivateVoyaPlaidMigration:
    """The dead Voya Plaid accounts must be deactivated so the local sync is the
    sole source and net worth doesn't double-count (#51)."""

    def _seed(self, conn):
        conn.execute(
            "INSERT INTO accounts (plaid_account_id, name, type, subtype, "
            "institution_name, is_manual, is_active) VALUES "
            "('voya-jstoo', 'JSTOOGOOD, LLC 401(K) P/S PLAN', 'investment', '401k', "
            "'Voya Financial - Voya Services Company', 0, 1)"
        )
        conn.execute(
            "INSERT INTO accounts (plaid_account_id, name, type, subtype, "
            "institution_name, is_manual, is_active) VALUES "
            "('chase-1', 'Chase Checking', 'depository', 'checking', 'Chase', 0, 1)"
        )
        conn.commit()

    def _cleanup(self, conn):
        conn.execute("DELETE FROM accounts WHERE plaid_account_id IN ('voya-jstoo', 'chase-1')")
        conn.commit()

    def test_deactivates_only_voya_plaid_and_is_idempotent(self):
        from api.database import _migrate_deactivate_voya_plaid
        with _test_get_db() as conn:
            self._seed(conn)
            try:
                _migrate_deactivate_voya_plaid(conn)
                voya = conn.execute(
                    "SELECT is_active FROM accounts WHERE plaid_account_id = 'voya-jstoo'"
                ).fetchone()
                chase = conn.execute(
                    "SELECT is_active FROM accounts WHERE plaid_account_id = 'chase-1'"
                ).fetchone()
                assert voya["is_active"] == 0          # Voya deactivated
                assert chase["is_active"] == 1          # non-Voya untouched
                # Idempotent: second run changes nothing.
                _migrate_deactivate_voya_plaid(conn)
                voya2 = conn.execute(
                    "SELECT is_active FROM accounts WHERE plaid_account_id = 'voya-jstoo'"
                ).fetchone()
                assert voya2["is_active"] == 0
            finally:
                self._cleanup(conn)


class TestQueryEndpoints:
    def test_status_requires_auth(self, client):
        assert client.get("/api/voya/status").status_code == 401

    def test_status_after_sync(self, client, auth_headers):
        with patch("api.routers.voya._take_net_worth_snapshot"):
            client.post("/api/voya/sync-local", headers=auth_headers,
                        json={"total_balance": 500.0, "accounts": []})
        res = client.get("/api/voya/status", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["synced"] is True
        assert res.json()["total_balance"] == 500.0

    def test_sync_log_limit_validation(self, client, auth_headers):
        assert client.get("/api/voya/sync-log?limit=0", headers=auth_headers).status_code == 422
        assert client.get("/api/voya/sync-log?limit=5", headers=auth_headers).status_code == 200
