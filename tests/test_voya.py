"""Tests for the Voya 401K local-sync integration (issue #51).

Covers:
  - parse_curl: cookie + session-token extraction, Windows ^ stripping, validation
  - _parse_accounts: defensive JSON parsing + SchemaUnknownError on unknown shape
  - POST /sync-local: auth guard, manual_entries upsert (861956/invested),
    snapshot history, voya_accounts + sync-log storage
  - status / accounts / sync-log query endpoints + auth guards
"""
import json
from pathlib import Path

import pytest
from unittest.mock import patch

from tests.conftest import _test_get_db
from api import voya_client
from api.voya_client import (
    parse_curl, _parse_accounts, SchemaUnknownError,
    parse_manage_investments, parse_transactions, parse_balance_summary,
    parse_allocations, fund_map_from_unitpricing,
)

_FIX = Path(__file__).parent / "fixtures" / "voya"


def _fix(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))

_COOKIE = ("MYVOYA_SSO_SESSION_ID=abc; MYVOYA_SESSION_ID=def; "
           "JSESSIONID=73091BB3; __cf_bm=xyz")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with _test_get_db() as conn:
        conn.execute("DELETE FROM manual_entries WHERE account_number = '861956'")
        conn.execute("DELETE FROM manual_entry_snapshots WHERE account_number = '861956'")
        conn.execute("DELETE FROM voya_accounts")
        conn.execute("DELETE FROM voya_sync_log")
        conn.execute("DELETE FROM voya_holdings")
        conn.execute("DELETE FROM voya_transactions")
        conn.execute("DELETE FROM voya_performance")
        conn.execute("DELETE FROM voya_allocations")
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
                "SELECT name, category, value FROM manual_entries WHERE account_number='861956'"
            ).fetchone()
            assert entry["category"] == "invested"
            assert entry["value"] == 22903.64
            snap = conn.execute(
                "SELECT value FROM manual_entry_snapshots WHERE account_number='861956'"
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
                "SELECT value FROM manual_entries WHERE account_number='861956'"
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


class TestVoyaRealAccountNumberMigration:
    """#53 — Voya moves onto its real account number (861956) and the Plaid-era
    balance history is stitched into the local-sync entry as one continuous
    series, queryable by account_number ordered by date. Must not touch net
    worth and must be idempotent."""

    def _seed(self, conn):
        # Two deactivated Voya Plaid accounts (the dead item from #52), each with
        # daily balances keyed by account_number — the migration sums them per day.
        for uuid, acct_no in (("voya-a", "PLAID-A"), ("voya-b", "PLAID-B")):
            conn.execute(
                "INSERT INTO accounts (plaid_account_id, account_number, name, type, "
                "subtype, institution_name, source, is_manual, is_active) VALUES "
                "(?, ?, 'Voya 401k', 'investment', '401k', "
                "'Voya Financial - Voya Services Company', 'plaid', 0, 0)",
                (uuid, acct_no),
            )
        a_id = conn.execute("SELECT id FROM accounts WHERE plaid_account_id='voya-a'").fetchone()["id"]
        b_id = conn.execute("SELECT id FROM accounts WHERE plaid_account_id='voya-b'").fetchone()["id"]
        bals = [
            (a_id, "PLAID-A", "2026-04-01", 10000.0), (a_id, "PLAID-A", "2026-04-17", 17898.62),
            (b_id, "PLAID-B", "2026-04-01", 5000.0),  (b_id, "PLAID-B", "2026-04-17", 5005.02),
        ]
        for acct_id, acct_no, day, val in bals:
            conn.execute(
                "INSERT INTO account_balances (account_id, account_number, current, snapped_at) "
                "VALUES (?, ?, ?, ?)", (acct_id, acct_no, val, day),
            )
        # The local-sync Voya entry + its current snapshot, still on the old key.
        conn.execute(
            "INSERT INTO manual_entries (name, category, value, entered_at, account_number) "
            "VALUES ('Voya 401(k)', 'invested', 22903.64, '2026-06-02', 'VOYA401K')"
        )
        conn.execute(
            "INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at) "
            "VALUES ('Voya 401(k)', 'VOYA401K', 'invested', 22903.64, '2026-06-02')"
        )
        # An Insperity entry sharing the '401K' suffix — must stay fully decoupled.
        conn.execute(
            "INSERT INTO manual_entries (name, category, value, entered_at, account_number) "
            "VALUES ('INSPERITY 401K PLAN', 'invested', 1391.66, '2026-04-01', '105001401K')"
        )
        conn.execute(
            "INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at) "
            "VALUES ('INSPERITY 401K PLAN', '105001401K', 'invested', 1391.66, '2026-04-01')"
        )
        conn.commit()

    def _cleanup(self, conn):
        conn.execute("DELETE FROM account_balances WHERE account_number IN ('PLAID-A','PLAID-B')")
        conn.execute("DELETE FROM accounts WHERE plaid_account_id IN ('voya-a','voya-b')")
        conn.execute("DELETE FROM manual_entries WHERE account_number IN ('861956','105001401K')")
        conn.execute("DELETE FROM manual_entry_snapshots WHERE account_number IN ('861956','105001401K')")
        conn.commit()

    def test_rekeys_backfills_and_is_net_worth_neutral(self):
        from api.database import _migrate_voya_real_account_number
        with _test_get_db() as conn:
            self._cleanup(conn)
            self._seed(conn)
            try:
                _migrate_voya_real_account_number(conn)

                # Entry moved to the real account number; value unchanged (NW-neutral).
                entry = conn.execute(
                    "SELECT account_number, value FROM manual_entries WHERE name='Voya 401(k)'"
                ).fetchone()
                assert entry["account_number"] == "861956"
                assert entry["value"] == 22903.64
                assert conn.execute(
                    "SELECT COUNT(*) FROM manual_entries WHERE account_number='VOYA401K'"
                ).fetchone()[0] == 0

                # History: Plaid-era points summed per day + preserved local point,
                # all under 861956, ordered by date.
                snaps = conn.execute(
                    "SELECT snapped_at, value FROM manual_entry_snapshots "
                    "WHERE account_number='861956' ORDER BY snapped_at"
                ).fetchall()
                assert [(s["snapped_at"], s["value"]) for s in snaps] == [
                    ("2026-04-01", 15000.0),
                    ("2026-04-17", 22903.64),
                    ("2026-06-02", 22903.64),   # local-sync point untouched
                ]

                # Insperity stays fully decoupled — not pulled in, not modified.
                insp = conn.execute(
                    "SELECT snapped_at, value FROM manual_entry_snapshots "
                    "WHERE account_number='105001401K'"
                ).fetchall()
                assert [(s["snapped_at"], s["value"]) for s in insp] == [("2026-04-01", 1391.66)]

                # Idempotent: a second run changes nothing.
                _migrate_voya_real_account_number(conn)
                assert conn.execute(
                    "SELECT COUNT(*) FROM manual_entry_snapshots WHERE account_number='861956'"
                ).fetchone()[0] == 3
            finally:
                self._cleanup(conn)


class TestFullDataParsers:
    """Parse the real captured Voya endpoint bodies (frozen fixtures, #54)."""

    def test_parse_manage_investments_holdings_and_perf(self):
        out = parse_manage_investments(_fix("manage_investments.json"))
        assert out["total_balance"] == 22950.32
        assert out["personal_ror_ytd"] == 11.55
        assert out["as_of"] == "2026-05-31"
        assert len(out["holdings"]) == 2
        h = out["holdings"][0]
        assert h["fund_name"] == "C975 Fidelity 500 Index Fund"
        assert h["balance"] == 5850.34
        assert h["units"] == 22.1217
        assert h["unit_price"] == 264.46
        assert round(h["ytd_pct"], 2) == 11.56
        assert h["pct_of_account"] == 25.49

    def test_parse_transactions_dedupes_and_maps_funds(self):
        fmap = fund_map_from_unitpricing(_fix("unitpricing.json"))
        txns = parse_transactions(_fix("transactions.json"), fmap)
        # fixture has 4 ungrouped rows incl an exact dup pair -> de-duped.
        keys = {(t["trade_date"], t["activity"], t["fund_id"], t["amount"]) for t in txns}
        assert len(keys) == len(txns)  # no duplicates survive
        for t in txns:
            assert t["trade_date"].startswith("2026-")
            if t["fund_id"] in fmap:
                assert t["fund_name"] == fmap[t["fund_id"]]

    def test_parse_transactions_handles_na_cash(self):
        # "Investment Earnings" rows can carry principalAmount N/A — must not crash.
        txns = parse_transactions(_fix("transactions.json"))
        assert all("amount" in t for t in txns)

    def test_parse_balance_summary(self):
        bal = parse_balance_summary(_fix("transactions.json"))
        assert bal["balance_start"] == 20665.48
        assert round(bal["balance_end"], 2) == 22950.32
        assert bal["growth"] is not None

    def test_parse_allocations(self):
        allocs = parse_allocations(_fix("allocations.json"))
        assert len(allocs) == 4
        assert allocs[0]["asset_class"] == "Large Cap Growth"
        assert allocs[0]["pct"] == 34
        assert allocs[0]["color"].startswith("#")

    def test_parsers_tolerate_empty(self):
        assert parse_manage_investments({})["holdings"] == []
        assert parse_transactions({}) == []
        assert parse_allocations({}) == []
        assert fund_map_from_unitpricing({}) == {}


class TestFullDataSyncAndEndpoints:
    """POST full Voya data and read it back through the tab endpoints (#54)."""

    def _payload(self):
        return {
            "total_balance": 22950.32,
            "accounts": [{"name": "JSTOOGOOD", "plan_id": "861956", "balance": 22950.32}],
            "holdings": [
                {"fund_name": "C975 Fidelity 500 Index Fund", "balance": 5850.34,
                 "units": 22.1217, "unit_price": 264.46, "ytd_pct": 11.56, "pct_of_account": 25.49},
                {"fund_name": "3494 JPMorgan Lrg Cp Growth", "balance": 7853.8,
                 "units": 84.71, "unit_price": 92.71, "ytd_pct": 7.25, "pct_of_account": 34.22},
            ],
            "transactions": [
                {"trade_date": "2026-05-28", "activity": "Contribution", "fund_name": "C975",
                 "fund_id": "38", "amount": 81.28, "units": 0.3088, "unit_price": 263.16},
                {"trade_date": "2026-05-29", "activity": "Investment Earnings", "fund_id": "38",
                 "amount": 0.12, "units": 0.0002, "unit_price": 263.74},
            ],
            "performance": {"personal_ror_ytd": 11.55, "total_balance": 22950.32,
                            "as_of": "2026-05-31", "balance_start": 20665.48,
                            "balance_end": 22950.32, "growth": 2284.84},
            "allocations": [
                {"asset_class": "Large Cap Growth", "pct": 34, "color": "#187C5B"},
                {"asset_class": "Global/International", "pct": 11, "color": "#C16E15"},
            ],
        }

    def test_sync_stores_all_sections(self, client, auth_headers):
        with patch("api.routers.voya._take_net_worth_snapshot"):
            res = client.post("/api/voya/sync-local", headers=auth_headers, json=self._payload())
        assert res.status_code == 200
        with _test_get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM voya_holdings").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM voya_transactions").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM voya_allocations").fetchone()[0] == 2
            perf = conn.execute("SELECT personal_ror_ytd, growth FROM voya_performance").fetchone()
            assert perf["personal_ror_ytd"] == 11.55

    def test_resync_is_idempotent_no_duplicate_transactions(self, client, auth_headers):
        with patch("api.routers.voya._take_net_worth_snapshot"):
            client.post("/api/voya/sync-local", headers=auth_headers, json=self._payload())
            client.post("/api/voya/sync-local", headers=auth_headers, json=self._payload())
        with _test_get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM voya_transactions").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM voya_holdings").fetchone()[0] == 2

    def test_read_endpoints(self, client, auth_headers):
        with patch("api.routers.voya._take_net_worth_snapshot"):
            client.post("/api/voya/sync-local", headers=auth_headers, json=self._payload())
        h = client.get("/api/voya/holdings", headers=auth_headers).json()
        assert h[0]["balance"] == 7853.8  # ordered by balance DESC
        t = client.get("/api/voya/transactions", headers=auth_headers).json()
        assert t[0]["trade_date"] == "2026-05-29"  # newest first
        p = client.get("/api/voya/performance", headers=auth_headers).json()
        assert p["personal_ror_ytd"] == 11.55
        a = client.get("/api/voya/allocations", headers=auth_headers).json()
        assert a[0]["pct"] == 34  # ordered by pct DESC

    def test_read_endpoints_require_auth(self, client):
        for path in ("holdings", "transactions", "performance", "allocations"):
            assert client.get(f"/api/voya/{path}").status_code == 401

    def test_transactions_limit_validation(self, client, auth_headers):
        assert client.get("/api/voya/transactions?limit=0", headers=auth_headers).status_code == 422
        assert client.get("/api/voya/transactions?limit=600", headers=auth_headers).status_code == 422


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
