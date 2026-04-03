"""Tests for Investor360 integration — router, client schema validation, sync logic."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from api.investor360_client import validate_response, detect_api_versions


# ── Schema validation tests ────────────────────────────────────────────────

class TestSchemaValidation:
    def test_holdings_valid(self):
        data = {
            "page": 1,
            "data": [{
                "accountId": 123,
                "securities": [{
                    "holdings": [{
                        "symbol": "FXAIX", "description": "Fidelity 500",
                        "valueDollars": 100.0, "quantity": 10, "price": 10.0,
                        "accountId": 123, "assetCategory": "Large-Cap",
                    }]
                }]
            }],
        }
        warnings = validate_response("holdings", data)
        # Should have optional field warnings but no error
        assert isinstance(warnings, list)

    def test_holdings_missing_required_top_key(self):
        data = {"page": 1}  # missing "data"
        with pytest.raises(ValueError, match="missing required top-level key 'data'"):
            validate_response("holdings", data)

    def test_holdings_missing_required_item_field(self):
        data = {
            "page": 1,
            "data": [{
                "accountId": 123,
                "securities": [{
                    "holdings": [{
                        "symbol": "FXAIX", "description": "Fidelity 500",
                        # missing valueDollars, quantity, price, accountId, assetCategory
                    }]
                }]
            }],
        }
        with pytest.raises(ValueError, match="missing required item field"):
            validate_response("holdings", data)

    def test_account_balances_valid(self):
        data = {
            "accountBalances": [{
                "accountNumber": "B123", "accountMarketValue": 50000,
                "cfnAccountId": 1,
            }]
        }
        warnings = validate_response("account_balances", data)
        assert isinstance(warnings, list)

    def test_performance_valid(self):
        data = [
            {"timePeriod": "MTDReturn", "portfolio": "0.74",
             "benchmarks": [{"benchmarkName": "S&P 500", "benchmarkValue": "0.72"}]}
        ]
        warnings = validate_response("performance", data)
        assert isinstance(warnings, list)

    def test_balance_history_valid(self):
        data = {
            "portfolioGrowths": [
                {"marketValue": 100000, "netInvestment": 90000,
                 "balanceDate": "2019-08-30T00:00:00"}
            ]
        }
        warnings = validate_response("balance_history", data)
        assert isinstance(warnings, list)

    def test_unknown_endpoint_returns_empty(self):
        warnings = validate_response("nonexistent", {"foo": "bar"})
        assert warnings == []


class TestVersionDetection:
    def test_detects_versions(self):
        urls = [
            "https://my.investor360.com/api/trading/accounts/v2/holdings?grouping=Account",
            "https://my.investor360.com/api/trading/accounts/v1/balances",
            "https://my.investor360.com/api/trading/products/v1/marketSummaries",
        ]
        versions = detect_api_versions(urls)
        assert "holdings" in versions
        assert versions["holdings"] == "v2"
        assert "balances" in versions
        assert versions["balances"] == "v1"


# ── Auth guard tests ───────────────────────────────────────────────────────

class TestAuthGuards:
    def test_status_requires_auth(self, client):
        resp = client.get("/api/investor360/status")
        assert resp.status_code == 401

    def test_holdings_requires_auth(self, client):
        resp = client.get("/api/investor360/holdings")
        assert resp.status_code == 401

    def test_sync_requires_auth(self, client):
        resp = client.post("/api/investor360/sync",
                           json={"session_cookie": "test"})
        assert resp.status_code == 401

    def test_performance_requires_auth(self, client):
        resp = client.get("/api/investor360/performance")
        assert resp.status_code == 401

    def test_asset_allocation_requires_auth(self, client):
        resp = client.get("/api/investor360/asset-allocation")
        assert resp.status_code == 401

    def test_market_summary_requires_auth(self, client):
        resp = client.get("/api/investor360/market-summary")
        assert resp.status_code == 401

    def test_sync_log_requires_auth(self, client):
        resp = client.get("/api/investor360/sync-log")
        assert resp.status_code == 401


# ── Data endpoint tests (empty DB) ────────────────────────────────────────

class TestEmptyDataEndpoints:
    def test_status_unconfigured(self, client, auth_headers):
        resp = client.get("/api/investor360/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False

    def test_holdings_empty(self, client, auth_headers):
        resp = client.get("/api/investor360/holdings", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["accounts"] == []

    def test_performance_empty(self, client, auth_headers):
        resp = client.get("/api/investor360/performance", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_asset_allocation_empty(self, client, auth_headers):
        resp = client.get("/api/investor360/asset-allocation", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_balance_history_empty(self, client, auth_headers):
        resp = client.get("/api/investor360/balance-history", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_activity_summary_empty(self, client, auth_headers):
        resp = client.get("/api/investor360/activity-summary", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_market_summary_empty(self, client, auth_headers):
        resp = client.get("/api/investor360/market-summary", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_sync_log_empty(self, client, auth_headers):
        resp = client.get("/api/investor360/sync-log", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []


# ── Bookmarklet endpoint ──────────────────────────────────────────────────

class TestBookmarklet:
    def test_returns_bookmarklet(self, client, auth_headers):
        resp = client.get("/api/investor360/bookmarklet.js", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "bookmarklet" in data
        assert "CFNSession" in data["bookmarklet"]
        assert "instructions" in data
        assert len(data["instructions"]) > 0


# ── Sync endpoint tests ───────────────────────────────────────────────────

class TestSync:
    def test_sync_invalid_session(self, client, auth_headers):
        """Sync with a bad session cookie should return 401."""
        with patch("api.routers.investor360.Investor360Client") as MockClient:
            instance = MockClient.return_value
            instance.check_session = AsyncMock(side_effect=Exception("Connection refused"))
            resp = client.post(
                "/api/investor360/sync",
                json={"session_cookie": "bad-cookie"},
                headers=auth_headers,
            )
            assert resp.status_code == 401

    def test_sync_expiring_session(self, client, auth_headers):
        """Sync with < 60s remaining should abort."""
        with patch("api.routers.investor360.Investor360Client") as MockClient:
            instance = MockClient.return_value
            instance.check_session = AsyncMock(return_value=30)
            resp = client.post(
                "/api/investor360/sync",
                json={"session_cookie": "expiring-cookie"},
                headers=auth_headers,
            )
            assert resp.status_code == 401
            assert "expiring" in resp.json()["detail"].lower()


# ── Store helpers test ─────────────────────────────────────────────────────

class TestStoreHelpers:
    def test_store_and_retrieve_performance(self, client, auth_headers):
        """Store performance data directly and verify retrieval."""
        from api.database import get_db
        from api.routers.investor360 import _store_performance

        perf_data = [
            {
                "timePeriod": "YTDReturn",
                "displayName": "Year to Date",
                "portfolio": "5.25",
                "benchmarks": [
                    {"benchmarkName": "S&P 500", "benchmarkValue": "4.10"},
                    {"benchmarkName": "Bloomberg US Aggregate Bond", "benchmarkValue": "1.20"},
                    {"benchmarkName": "Bloomberg 1-3 Month T-Bills", "benchmarkValue": "0.90"},
                ],
                "hideMe": False,
            }
        ]
        from datetime import date
        today = date.today()
        with get_db() as conn:
            _store_performance(conn, perf_data, today)

        resp = client.get("/api/investor360/performance", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        ytd = [d for d in data if d["time_period"] == "YTDReturn"]
        assert len(ytd) == 1
        assert ytd[0]["portfolio_return"] == 5.25
        assert ytd[0]["benchmark_sp500"] == 4.10

    def test_store_and_retrieve_asset_allocation(self, client, auth_headers):
        from api.database import get_db
        from api.routers.investor360 import _store_asset_allocation
        from datetime import date

        today = date.today()
        alloc = {
            "assetBalances": [
                {"assetName": "Large-Cap Growth", "marketValue": 145858.33},
                {"assetName": "Cash and Equivalents", "marketValue": 5986.95},
            ]
        }
        with get_db() as conn:
            _store_asset_allocation(conn, alloc, today)

        resp = client.get("/api/investor360/asset-allocation", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        names = [d["asset_name"] for d in data]
        assert "Large-Cap Growth" in names

    def test_store_and_retrieve_market_summary(self, client, auth_headers):
        from api.database import get_db
        from api.routers.investor360 import _store_market_summary

        market = {
            "data": [
                {"symbol": "US:SP500", "name": "S&P500",
                 "lastTradeAmount": 6582.69, "netChange": 7.37,
                 "percentChange": 0.11},
                {"symbol": "US:I:DJI", "name": "DJI",
                 "lastTradeAmount": 46504.67, "netChange": -61.07,
                 "percentChange": -0.13},
            ]
        }
        with get_db() as conn:
            _store_market_summary(conn, market)

        resp = client.get("/api/investor360/market-summary", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        sp500 = [d for d in data if d["name"] == "S&P500"]
        assert len(sp500) == 1
        assert sp500[0]["last_trade_amount"] == 6582.69

    def test_store_and_retrieve_balance_history(self, client, auth_headers):
        from api.database import get_db
        from api.routers.investor360 import _store_balance_history

        history = {
            "portfolioGrowths": [
                {"marketValue": 92215.16, "netInvestment": 92205.5,
                 "balanceDate": "2019-08-30T00:00:00"},
                {"marketValue": 586679.35, "netInvestment": 456108.97,
                 "balanceDate": "2026-04-02T00:00:00"},
            ]
        }
        with get_db() as conn:
            _store_balance_history(conn, history)

        resp = client.get("/api/investor360/balance-history", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        assert data[0]["balance_date"] == "2019-08-30"
        assert data[-1]["market_value"] == 586679.35

    def test_store_and_retrieve_activity_summary(self, client, auth_headers):
        from api.database import get_db
        from api.routers.investor360 import _store_activity_summary
        from datetime import date

        today = date.today()
        activity = [{
            "beginningBalance": 598640.01,
            "endingBalance": 586679.35,
            "netContributionsWithdrawals": 4200.0,
            "netChange": -16160.66,
            "managementFee": 0,
            "managementFeesPaid": 0,
            "interest": 0, "capGains": 0,
            "positionsChangeInValue": 0,
            "totalGainLossAfterFee": 0,
            "credits12B1": 0,
        }]
        with get_db() as conn:
            _store_activity_summary(conn, activity, today, f"{today.year}-01-01")

        resp = client.get("/api/investor360/activity-summary", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["beginning_balance"] == 598640.01
        assert data["ending_balance"] == 586679.35
        assert data["net_contributions_withdrawals"] == 4200.0


# ── Sync log test ──────────────────────────────────────────────────────────

class TestSyncLog:
    def test_sync_log_records(self, client, auth_headers):
        """After storing data (which implicitly doesn't create sync log entries
        via the helpers), verify the sync log endpoint structure."""
        from api.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO i360_sync_log
                   (status, accounts_synced, holdings_count,
                    total_portfolio_value, duration_ms)
                   VALUES ('success', 5, 43, 586679.35, 2500)"""
            )
        resp = client.get("/api/investor360/sync-log", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["status"] == "success"
        assert data[0]["accounts_synced"] == 5
        assert data[0]["total_portfolio_value"] == 586679.35

    def test_status_after_sync(self, client, auth_headers):
        resp = client.get("/api/investor360/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["accounts"] == 5
        assert data["healthy"] is True
