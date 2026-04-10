"""Tests for Insperity 401K integration — client parsing, cURL extraction, router endpoints."""
from unittest.mock import AsyncMock, patch

import pytest

from api.insperity_client import (
    parse_curl_cookies,
    parse_holdings,
    parse_performance,
    parse_contributions,
    parse_allocations,
    parse_activity,
    parse_prices,
    parse_summary,
    _parse_dollar,
    _parse_pct,
    InsperityClient,
    SessionExpiredError,
    PageChangedError,
)


# ── Dollar / percent parsing ─────────────────────────────────────────────────

class TestParsers:
    def test_parse_dollar_normal(self):
        assert _parse_dollar("$1,234.56") == 1234.56

    def test_parse_dollar_no_sign(self):
        assert _parse_dollar("1,401.93") == 1401.93

    def test_parse_dollar_negative_angle_brackets(self):
        assert _parse_dollar("<1.87>") == -1.87

    def test_parse_dollar_zero(self):
        assert _parse_dollar("$.00") is None

    def test_parse_dollar_empty(self):
        assert _parse_dollar("") is None

    def test_parse_pct(self):
        assert _parse_pct("79.83%") == 79.83

    def test_parse_pct_no_leading_zero(self):
        assert _parse_pct(".48%") == 0.48

    def test_parse_pct_negative(self):
        assert _parse_pct("-2.62%") == -2.62


# ── cURL cookie extraction ───────────────────────────────────────────────────

class TestCurlParsing:
    def test_parse_full_curl(self):
        curl = (
            'curl "https://retirement.insperity.com/index.aspx" '
            '-b "ASP.NET_SessionId=abc123; ENCRYPT=xyz; cf_clearance=test; '
            'ESC5ReSync=True; ak_bmsc=botcookie; _ga=junk"'
        )
        cookies = parse_curl_cookies(curl)
        assert cookies["ASP.NET_SessionId"] == "abc123"
        assert cookies["ENCRYPT"] == "xyz"
        assert cookies["cf_clearance"] == "test"
        assert cookies["ESC5ReSync"] == "True"
        assert cookies["ak_bmsc"] == "botcookie"
        assert cookies["_ga"] == "junk"  # extra cookies preserved

    def test_parse_curl_with_windows_escaping(self):
        curl = (
            'curl ^"https://retirement.insperity.com/index.aspx^" '
            '-b ^"ASP.NET_SessionId=abc; ENCRYPT=xyz; cf_clearance=c; '
            'ESC5ReSync=True; ak_bmsc=b^"'
        )
        cookies = parse_curl_cookies(curl)
        assert cookies["ASP.NET_SessionId"] == "abc"

    def test_parse_missing_required_cookie(self):
        curl = 'curl "https://example.com" -b "ASP.NET_SessionId=abc; ENCRYPT=xyz"'
        with pytest.raises(ValueError, match="Missing required cookies"):
            parse_curl_cookies(curl)

    def test_parse_no_cookies_at_all(self):
        curl = 'curl "https://example.com" -H "accept: text/html"'
        with pytest.raises(ValueError, match="Could not find cookies"):
            parse_curl_cookies(curl)

    def test_parse_bare_cookie_string(self):
        raw = (
            "ASP.NET_SessionId=abc; ENCRYPT=xyz; cf_clearance=c; "
            "ESC5ReSync=True; ak_bmsc=b"
        )
        cookies = parse_curl_cookies(raw)
        assert cookies["ASP.NET_SessionId"] == "abc"


# ── HTML page parsers ────────────────────────────────────────────────────────

# Minimal HTML fixtures that replicate the real page structure

HOLDINGS_HTML = """
<table><tr><th>Investment</th><th>Balance</th><th>Shares</th><th>Price</th><th>Ratio</th></tr>
<tr><td>Ssga S&P 500 Index Fund</td><td>$1,116.64</td><td>96.7706</td><td>$11.539</td><td>79.83%</td></tr>
<tr><td>Ssga Russell Sm Md Cap</td><td>$282.05</td><td>9.4042</td><td>$29.992</td><td>20.17%</td></tr>
<tr><td>Total</td><td>$1,398.69</td><td>100%</td></tr></table>
"""

PERFORMANCE_HTML = """
<table><tr><td>My Rate of Return</td><td>Available through 4/30/2026</td></tr></table>
<table>
<tr><th colspan="3">Cumulative</th></tr>
<tr><th>1 Month</th><th>3 Month</th><th>Year To Date</th></tr>
<tr><td>1.43%</td><td>1.43%</td><td>1.43%</td></tr>
</table>
<table>
<tr><th colspan="3">Annualized</th></tr>
<tr><th>1 Month</th><th>3 Month</th><th>Year To Date</th></tr>
<tr><td>1.43%</td><td>.48%</td><td>.29%</td></tr>
</table>
"""

CONTRIBUTIONS_HTML = """
<table>
<tr><td>Pretax</td><td>0.00%</td></tr>
<tr><td>Roth</td><td>6.00%</td></tr>
</table>
<table>
<tr><td>Employee</td><td>$934.62</td><td>$311.54</td><td>03/30/2026</td></tr>
<tr><td>Employer</td><td>$467.31</td><td>$155.77</td><td>03/30/2026</td></tr>
</table>
"""

ALLOCATIONS_HTML = """
<table>
<tr><td>Large Cap</td><td>80.0%</td></tr>
<tr><td>Ssga S&P 500 Index Fund Cl M</td><td>80%</td></tr>
<tr><td>Mid Cap</td><td>20.0%</td></tr>
<tr><td>Ssga Russell Sm Md Cap Index</td><td>20%</td></tr>
<tr><td>Total</td><td>100%</td></tr>
</table>
"""

ACTIVITY_HTML = """
<table>
<tr><td>Beginning Balance</td><td>$.00</td><td>.0000</td></tr>
<tr><td>Contributions</td><td>1,401.93</td><td>106.4388</td></tr>
<tr><td>03/02/2026</td><td>Ssg Sp500 M/ Ee Roth</td><td>09U</td><td>249.23</td><td>20.7952</td><td>11.985</td></tr>
<tr><td>Ending Balance</td><td>$1,398.69</td><td>106.1748</td></tr>
</table>
"""

PRICES_HTML = """
<table>
<tr><td>Ssga S&P 500 Index Fund Cl M</td><td>Large Cap</td><td>04/07/2026</td><td>$11.53</td><td>03/10/2026</td><td>$11.81</td><td>-2.34%</td></tr>
<tr><td>Ssga Russell Sm Md Cap Index</td><td>Mid Cap</td><td>04/07/2026</td><td>$29.99</td><td>03/10/2026</td><td>$30.09</td><td>-0.33%</td></tr>
</table>
"""

SUMMARY_HTML = """
<table><tr><td>Account Balance</td></tr></table>
<table><tr><td>$1,398.69</td></tr></table>
<table><tr><td>$1,398.69</td></tr></table>
"""


class TestHoldingsParser:
    def test_parse_holdings(self):
        result = parse_holdings(HOLDINGS_HTML)
        assert len(result["holdings"]) == 2
        assert result["total_balance"] == 1398.69
        sp500 = result["holdings"][0]
        assert sp500["fund"] == "Ssga S&P 500 Index Fund"
        assert sp500["balance"] == 1116.64
        assert sp500["shares"] == 96.7706
        assert sp500["price"] == 11.539
        assert sp500["ratio_pct"] == 79.83

    def test_parse_holdings_empty_table_raises(self):
        with pytest.raises(ValueError, match="no fund rows found"):
            parse_holdings("<table><tr><td>Nothing here</td></tr></table>")

    def test_parse_holdings_no_total_raises(self):
        html = """<table>
        <tr><td>Fund A</td><td>$100.00</td><td>10.0</td><td>$10.00</td><td>100%</td></tr>
        </table>"""
        with pytest.raises(ValueError, match="total balance row not found"):
            parse_holdings(html)


class TestPerformanceParser:
    def test_parse_performance_full_horizontal_layout(self):
        """Old layout: 3 horizontal periods per section."""
        result = parse_performance(PERFORMANCE_HTML)
        assert result["as_of"] == "4/30/2026"
        assert result["cumulative"]["1_month"] == 1.43
        assert result["cumulative"]["ytd"] == 1.43
        assert result["annualized"]["3_month"] == 0.48
        assert result["annualized"]["ytd"] == 0.29

    def test_parse_performance_limited_history_layout(self):
        """Limited-history layout from real Insperity HTML fixture: 1 cell wide,
        single period per section, with Cumulative=1 Month and Annualized=1 Year."""
        from pathlib import Path
        fixture = Path(__file__).parent / "fixtures" / "insperity_investment_return_limited.html"
        html = fixture.read_text(encoding="utf-8")
        result = parse_performance(html)
        assert result["as_of"] == "3/31/2026"
        assert result["cumulative"] == {"1_month": -2.87}
        assert result["annualized"] == {"1_year": 0.0}

    def test_parse_performance_no_data_raises(self):
        with pytest.raises(ValueError, match="no Cumulative or Annualized"):
            parse_performance("<table><tr><td>No data</td></tr></table>")

    def test_parse_performance_only_cumulative_present(self):
        """Annualized may be entirely absent if there's not even 1 month of data."""
        html = """
        <table>
        <tr><th>Cumulative</th></tr>
        <tr><td>1 Month</td></tr>
        <tr><td>2.50%</td></tr>
        </table>
        """
        result = parse_performance(html)
        assert result["cumulative"] == {"1_month": 2.5}
        assert result["annualized"] == {}


class TestContributionsParser:
    def test_parse_contributions(self):
        result = parse_contributions(CONTRIBUTIONS_HTML)
        assert result["rates"]["pretax_pct"] == 0.0
        assert result["rates"]["roth_pct"] == 6.0
        assert result["ytd"]["employee"] == 934.62
        assert result["ytd"]["employer"] == 467.31
        assert result["last"]["employee_amount"] == 311.54
        assert result["last"]["employee_date"] == "03/30/2026"

    def test_parse_contributions_no_data_raises(self):
        with pytest.raises(ValueError, match="no Employee/Employer"):
            parse_contributions("<table><tr><td>Empty</td></tr></table>")


class TestAllocationsParser:
    def test_parse_allocations(self):
        result = parse_allocations(ALLOCATIONS_HTML)
        assert len(result["allocations"]) == 2
        assert result["allocations"][0]["fund"] == "Ssga S&P 500 Index Fund Cl M"
        assert result["allocations"][0]["target_pct"] == 80.0
        assert result["allocations"][0]["asset_class"] == "Large Cap"

    def test_parse_allocations_no_data_raises(self):
        with pytest.raises(ValueError, match="no fund allocation"):
            parse_allocations("<table><tr><td>Empty</td></tr></table>")


class TestActivityParser:
    def test_parse_activity(self):
        result = parse_activity(ACTIVITY_HTML)
        assert result["beginning_balance"] == 0
        assert result["ending_balance"] == 1398.69
        assert result["contributions"] == 1401.93
        assert len(result["transactions"]) == 1
        assert result["transactions"][0]["date"] == "03/02/2026"
        assert result["transactions"][0]["amount"] == 249.23

    def test_parse_activity_no_data_raises(self):
        with pytest.raises(ValueError, match="no Beginning/Ending Balance"):
            parse_activity("<table><tr><td>Empty</td></tr></table>")


class TestPricesParser:
    def test_parse_prices(self):
        result = parse_prices(PRICES_HTML)
        assert len(result) == 2
        assert result[0]["fund"] == "Ssga S&P 500 Index Fund Cl M"
        assert result[0]["price"] == 11.53
        assert result[0]["change_pct"] == -2.34

    def test_parse_prices_no_data_raises(self):
        with pytest.raises(ValueError, match="no fund price rows"):
            parse_prices("<table><tr><td>Empty</td></tr></table>")


class TestSummaryParser:
    def test_parse_summary(self):
        result = parse_summary(SUMMARY_HTML)
        assert result["account_balance"] == 1398.69
        assert result["vested_balance"] == 1398.69

    def test_parse_summary_no_data_raises(self):
        with pytest.raises(ValueError, match="account balance not found"):
            parse_summary("<table><tr><td>No money</td></tr></table>")


# ── Router endpoint tests ────────────────────────────────────────────────────
# Uses shared client + auth_headers fixtures from conftest.py


class TestInsperityRouter:
    def test_status_no_sync(self, client, auth_headers):
        resp = client.get("/api/insperity/status", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["synced"] is False

    def test_sync_bad_curl(self, client, auth_headers):
        resp = client.post("/api/insperity/sync", headers=auth_headers,
                           json={"curl_command": "not a curl command"})
        assert resp.status_code == 400
        assert "Could not find cookies" in resp.json()["detail"]

    def test_sync_missing_cookies(self, client, auth_headers):
        resp = client.post("/api/insperity/sync", headers=auth_headers,
                           json={"curl_command": 'curl "https://x.com" -b "ASP.NET_SessionId=a"'})
        assert resp.status_code == 400
        assert "Missing required cookies" in resp.json()["detail"]

    def test_sync_requires_auth(self, client):
        resp = client.post("/api/insperity/sync",
                           json={"curl_command": "test"})
        assert resp.status_code == 401

    def test_holdings_requires_auth(self, client):
        resp = client.get("/api/insperity/holdings")
        assert resp.status_code == 401

    def test_holdings_empty(self, client, auth_headers):
        resp = client.get("/api/insperity/holdings", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_performance_empty(self, client, auth_headers):
        resp = client.get("/api/insperity/performance", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_contributions_empty(self, client, auth_headers):
        resp = client.get("/api/insperity/contributions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_activity_empty(self, client, auth_headers):
        resp = client.get("/api/insperity/activity", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_prices_empty(self, client, auth_headers):
        resp = client.get("/api/insperity/prices", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_transactions_empty(self, client, auth_headers):
        resp = client.get("/api/insperity/transactions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_allocations_empty(self, client, auth_headers):
        resp = client.get("/api/insperity/allocations", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_sync_log_empty(self, client, auth_headers):
        resp = client.get("/api/insperity/sync-log", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []


class TestInsperityStorage:
    """Test that sync stores and retrieves data correctly."""

    def test_store_and_retrieve_holdings(self, client, auth_headers):
        """Directly insert holdings and verify retrieval."""
        from api.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO insperity_holdings
                   (snapped_at, fund, balance, shares, price, ratio_pct)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("2026-04-08", "Ssga S&P 500 Index Fund", 1116.64, 96.77, 11.54, 79.83),
            )
        resp = client.get("/api/insperity/holdings", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["fund"] == "Ssga S&P 500 Index Fund"
        assert data[0]["balance"] == 1116.64

    def test_store_and_retrieve_performance(self, client, auth_headers):
        from api.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO insperity_performance
                   (snapped_at, period, cumulative, annualized)
                   VALUES (?, ?, ?, ?)""",
                ("2026-04-08", "ytd", 1.43, 0.29),
            )
        resp = client.get("/api/insperity/performance", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        ytd = [r for r in data if r["period"] == "ytd"][0]
        assert ytd["cumulative"] == 1.43

    def test_store_and_retrieve_contributions(self, client, auth_headers):
        from api.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO insperity_contributions
                   (snapped_at, pretax_pct, roth_pct, ytd_employee, ytd_employer,
                    last_ee_amount, last_er_amount, last_ee_date, last_er_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2026-04-08", 0.0, 6.0, 934.62, 467.31, 311.54, 155.77,
                 "03/30/2026", "03/30/2026"),
            )
        resp = client.get("/api/insperity/contributions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["roth_pct"] == 6.0
        assert data["ytd_employee"] == 934.62

    def test_store_and_retrieve_transactions(self, client, auth_headers):
        from api.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO insperity_transactions
                   (snapped_at, txn_date, description, code, amount, shares, price)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("2026-04-08", "03/02/2026", "Ssg Sp500 M/ Ee Roth", "09U",
                 249.23, 20.7952, 11.985),
            )
        resp = client.get("/api/insperity/transactions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["description"] == "Ssg Sp500 M/ Ee Roth"
        assert data[0]["amount"] == 249.23

    def test_store_and_retrieve_allocations(self, client, auth_headers):
        from api.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO insperity_allocations
                   (snapped_at, fund, target_pct, asset_class)
                   VALUES (?, ?, ?, ?)""",
                ("2026-04-08", "Ssga S&P 500 Index Fund", 80.0, "Large Cap"),
            )
        resp = client.get("/api/insperity/allocations", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["target_pct"] == 80.0

    def test_sync_log_after_insert(self, client, auth_headers):
        from api.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO insperity_sync_log
                   (status, holdings_count, total_balance, duration_ms)
                   VALUES (?, ?, ?, ?)""",
                ("success", 2, 1398.69, 1500),
            )
        resp = client.get("/api/insperity/sync-log", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["status"] == "success"
        assert data[0]["total_balance"] == 1398.69


class TestSyncLocal:
    """Test the sync-local endpoint that accepts pre-parsed data."""

    def test_sync_local_full_data(self, client, auth_headers):
        payload = {
            "summary": {"account_balance": 1398.69, "vested_balance": 1398.69},
            "holdings": {
                "holdings": [
                    {"fund": "Test Fund", "balance": 1398.69,
                     "shares": 100.0, "price": 13.99, "ratio_pct": 100.0}
                ],
                "total_balance": 1398.69,
            },
            "performance": {
                "cumulative": {"1_month": 1.43, "3_month": 1.43, "ytd": 1.43},
                "annualized": {"1_month": 1.43, "3_month": 0.48, "ytd": 0.29},
            },
            "contributions": {
                "rates": {"pretax_pct": 0.0, "roth_pct": 6.0},
                "ytd": {"employee": 934.62, "employer": 467.31},
                "last": {"employee_amount": 311.54, "employee_date": "03/30/2026",
                         "employer_amount": 155.77, "employer_date": "03/30/2026"},
            },
            "allocations": {
                "allocations": [
                    {"fund": "Test Fund", "target_pct": 100.0, "asset_class": "Large Cap"}
                ]
            },
            "activity": {
                "beginning_balance": 0, "ending_balance": 1398.69,
                "contributions": 1401.93,
                "transactions": [
                    {"date": "03/02/2026", "description": "Test Contrib",
                     "code": "09U", "amount": 249.23, "shares": 20.79, "price": 11.99}
                ],
            },
            "prices": [
                {"fund": "Test Fund", "asset_class": "Large Cap",
                 "date": "04/08/2026", "price": 13.99,
                 "prior_date": "03/10/2026", "prior_price": 14.20,
                 "change_pct": -1.48}
            ],
        }
        resp = client.post("/api/insperity/sync-local",
                           headers=auth_headers, json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "success"
        assert data["holdings"] == 1
        assert data["total_balance"] == 1398.69

    def test_sync_local_requires_auth(self, client):
        resp = client.post("/api/insperity/sync-local", json={})
        assert resp.status_code == 401

    def test_sync_local_partial(self, client, auth_headers):
        """Sync with only holdings — should still succeed."""
        payload = {
            "holdings": {
                "holdings": [
                    {"fund": "Partial Fund", "balance": 500.0,
                     "shares": 50.0, "price": 10.0, "ratio_pct": 100.0}
                ],
                "total_balance": 500.0,
            },
        }
        resp = client.post("/api/insperity/sync-local",
                           headers=auth_headers, json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"


class TestSyncUpdatesManualEntry:
    """Sync-local should update manual_entries and trigger net worth snapshot."""

    PAYLOAD = {
        "summary": {"account_balance": 1500.00, "vested_balance": 1500.00},
        "holdings": {
            "holdings": [
                {"fund": "Test Fund", "balance": 1500.00,
                 "shares": 100.0, "price": 15.00, "ratio_pct": 100.0}
            ],
            "total_balance": 1500.00,
        },
    }

    def _seed_manual_entry(self):
        from api.database import get_db
        with get_db() as conn:
            conn.execute(
                "DELETE FROM manual_entries "
                "WHERE account_number = '105001401K'"
            )
            conn.execute(
                "INSERT INTO manual_entries "
                "(name, category, value, account_number, entered_at, "
                "exclude_from_net_worth) "
                "VALUES ('INSPERITY 401K PLAN', 'invested', 1000.00, "
                "'105001401K', '2026-04-01', 0)"
            )

    def test_sync_local_updates_manual_entry_value(self, client, auth_headers):
        self._seed_manual_entry()
        resp = client.post("/api/insperity/sync-local",
                           headers=auth_headers, json=self.PAYLOAD)
        assert resp.status_code == 200

        from api.database import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM manual_entries "
                "WHERE account_number = '105001401K'"
            ).fetchone()
        assert row["value"] == 1500.00

    def test_sync_local_triggers_net_worth_snapshot(self, client, auth_headers):
        self._seed_manual_entry()
        resp = client.post("/api/insperity/sync-local",
                           headers=auth_headers, json=self.PAYLOAD)
        assert resp.status_code == 200

        from api.database import get_db
        from datetime import date
        today = date.today().isoformat()
        with get_db() as conn:
            snap = conn.execute(
                "SELECT invested FROM net_worth_snapshots "
                "WHERE snapped_at = ?", (today,)
            ).fetchone()
        assert snap is not None, "net worth snapshot should exist after sync"
        assert snap["invested"] >= 1500.00

    def test_sync_local_no_balance_skips_update(self, client, auth_headers):
        """If sync has no total_balance, manual entry should not change."""
        self._seed_manual_entry()
        payload = {"performance": {
            "cumulative": {"1_month": 1.0, "3_month": 2.0, "ytd": 3.0},
            "annualized": {"1_month": 1.0, "3_month": 0.5, "ytd": 0.3},
        }}
        client.post("/api/insperity/sync-local",
                     headers=auth_headers, json=payload)

        from api.database import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM manual_entries "
                "WHERE account_number = '105001401K'"
            ).fetchone()
        assert row["value"] == 1000.00
