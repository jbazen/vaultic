"""
Investor360 API client with schema validation and breakage detection.

Calls internal Investor360 JSON endpoints using a session cookie obtained
from manual login. All responses are validated against expected schemas to
detect API changes before they cause silent data corruption.
"""
import json
import logging
import re
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://my.investor360.com"
REQUEST_TIMEOUT = 15.0  # seconds per request

# ── Expected response schemas ──────────────────────────────────────────────
# Missing a "required" field = hard failure for that endpoint.
# Missing an "optional" field = warning logged but sync continues.
SCHEMAS = {
    "holdings": {
        "top_required": ["page", "data"],
        "item_required": [
            "symbol", "description", "valueDollars", "quantity", "price",
            "accountId", "assetCategory",
        ],
        "item_optional": [
            "cusip", "productType", "accruedInterest", "assetsPercentage",
            "assetType", "assetSubType", "primaryAssetClass", "positionType",
            "estTaxCostDollars", "estTaxCostGainLossDollars", "estTaxCostGainLossPercent",
            "estUnitTaxCost", "principalDollars", "principalGainLossDollars",
            "principalGainLossPercent", "unitPrincipalCost",
            "previousDayValue", "oneDayPriceChangePercent",
            "oneDayValueChangeDollars", "oneDayValueChangePercent",
            "estimatedAnnualIncomeDollars", "currentYieldDistributionRatePercent",
            "dividendInstructions", "capGainInstructions", "initialPurchaseDate",
            "isCore", "intraday", "holdingId", "productId",
        ],
    },
    "account_balances": {
        "top_required": ["accountBalances"],
        "item_required": [
            "accountNumber", "accountMarketValue", "cfnAccountId",
        ],
        "item_optional": [
            "accountCashValue", "todaysChange", "totalMarketValue", "accountType",
        ],
    },
    "performance": {
        "item_required": ["timePeriod", "portfolio", "benchmarks"],
        "item_optional": ["displayName", "shortDisplayName"],
    },
    "balance_history": {
        "top_required": ["portfolioGrowths"],
        "item_required": ["marketValue", "netInvestment", "balanceDate"],
    },
    "asset_allocation": {
        "top_required": ["assetBalances"],
        "item_required": ["assetName", "marketValue"],
    },
    "activity_summary": {
        "item_required": ["beginningBalance", "endingBalance"],
        "item_optional": [
            "netContributionsWithdrawals", "netChange", "managementFee",
            "managementFeesPaid", "interest", "capGains",
            "positionsChangeInValue", "totalGainLossAfterFee", "credits12B1",
        ],
    },
    "market_summary": {
        "top_required": ["data"],
        "item_required": ["symbol", "name", "lastTradeAmount"],
        "item_optional": ["netChange", "percentChange"],
    },
}


def validate_response(endpoint_name: str, data) -> list[str]:
    """Validate response against expected schema. Returns list of warnings.

    Raises ValueError for missing required fields.
    """
    warnings = []
    schema = SCHEMAS.get(endpoint_name)
    if not schema:
        return warnings

    # Check top-level required keys (dict responses)
    if isinstance(data, dict):
        for key in schema.get("top_required", []):
            if key not in data:
                raise ValueError(
                    f"{endpoint_name}: missing required top-level key '{key}'"
                )

    # Get the list of items to validate
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Find the data array
        for key in schema.get("top_required", []):
            val = data.get(key)
            if isinstance(val, list) and val:
                items = val
                break
        # For holdings, items are nested deeper: data[].securities[].holdings[]
        if endpoint_name == "holdings" and "data" in data:
            items = []
            for acct in data["data"]:
                for sec in acct.get("securities", []):
                    items.extend(sec.get("holdings", []))

    # Validate item fields
    if items:
        sample = items[0] if items else {}
        for field in schema.get("item_required", []):
            if field not in sample:
                raise ValueError(
                    f"{endpoint_name}: missing required item field '{field}'"
                )
        for field in schema.get("item_optional", []):
            if field not in sample:
                warnings.append(
                    f"{endpoint_name}: optional field '{field}' missing"
                )

    return warnings


def detect_api_versions(urls: list[str]) -> dict:
    """Extract version numbers from URL paths for drift detection."""
    versions = {}
    for url in urls:
        # Match /v1/ or /v2/ anywhere in the path
        match = re.search(r"/(v\d+)/([^/?]+)", url)
        if match:
            version = match.group(1)
            endpoint = match.group(2).split("?")[0]
            versions[endpoint] = version
    return versions


class Investor360Client:
    """HTTP client for Investor360 internal API."""

    def __init__(self, session_cookie: str):
        self.session_cookie = session_cookie
        self.warnings: list[str] = []
        self.urls_called: list[str] = []

    def _cookies(self) -> dict:
        return {"CFNSession": self.session_cookie}

    def _headers(self) -> dict:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/nce/Dashboard",
        }

    async def _get(self, path: str) -> dict | list:
        url = f"{BASE_URL}{path}"
        self.urls_called.append(url)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                url, cookies=self._cookies(), headers=self._headers()
            )
        if resp.status_code == 401:
            raise SessionExpiredError("Investor360 session expired (401)")
        if resp.status_code == 403:
            raise SessionExpiredError("Investor360 session forbidden (403)")
        if resp.status_code == 404:
            raise EndpointChangedError(f"Endpoint not found (404): {path}")
        resp.raise_for_status()
        data = resp.json()
        return data if data is not None else {}

    async def _post(self, path: str, body: dict) -> dict | list:
        url = f"{BASE_URL}{path}"
        self.urls_called.append(url)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                url,
                json=body,
                cookies=self._cookies(),
                headers=self._headers(),
            )
        if resp.status_code == 401:
            raise SessionExpiredError("Investor360 session expired (401)")
        if resp.status_code == 403:
            raise SessionExpiredError("Investor360 session forbidden (403)")
        if resp.status_code == 404:
            raise EndpointChangedError(f"Endpoint not found (404): {path}")
        resp.raise_for_status()
        data = resp.json()
        return data if data is not None else {}

    # ── Session ────────────────────────────────────────────────────────────

    async def check_session(self) -> int:
        """Verify session is valid, return remaining seconds."""
        data = await self._get(
            "/Applications/WebServices/SecurityService/api/Session/v2/remainingTime"
        )
        remaining = data.get("data", [{}])[0].get("remainingTime", 0)
        return remaining

    # ── Data endpoints ─────────────────────────────────────────────────────

    async def get_account_list(self) -> dict:
        """Account hierarchy: household -> groups -> individual accounts."""
        data = await self._get(
            "/Applications/WebServices/A360.Web.Header.Services/api/v1/GetAccountList"
        )
        return data

    async def get_holdings(self) -> dict:
        """Full holdings across all accounts (43 fields per position)."""
        data = await self._get(
            "/api/trading/accounts/v2/holdings?grouping=Account&cacheHoldings=true"
        )
        self.warnings.extend(validate_response("holdings", data))
        return data

    async def get_account_balances(self, household_id: int, as_of: date) -> list:
        """Per-account market values, cash, today's change."""
        body = {
            "accountSelectionGroupType": 0,
            "accountSelectionValue": household_id,
            "groupName": "All Accounts",
            "asOfDate": f"{as_of.isoformat()}T00:00:00",
            "startDate": f"{as_of.year}-01-01T00:00:00",
            "endDate": f"{as_of.isoformat()}T00:00:00",
            "householdId": 0,
            "householdGroupId": 0,
            "balanceWidgetDisplay": 0,
            "showOpenAccountsWithZeroValue": True,
            "showAdditionalAccounts": True,
            "showInactiveAdditionalAccounts": False,
        }
        data = await self._post(
            "/Applications/Reports/AccountBalances/GetAccountBalances", body
        )
        self.warnings.extend(validate_response("account_balances", data))
        return data

    async def get_performance(self, household_id: int, as_of: date) -> list:
        """TWR returns: MTD, QTD, YTD, 1/3/5yr, inception vs benchmarks."""
        body = {
            "accountSelectionGroupType": 0,
            "accountSelectionValue": household_id,
            "groupName": "All Accounts",
            "asOfDate": f"{as_of.isoformat()}T00:00:00",
            "startDate": f"{as_of.year}-01-01T00:00:00",
            "endDate": f"{as_of.isoformat()}T00:00:00",
            "householdId": 0,
            "benchmarksToDisplay": 6,
        }
        data = await self._post(
            "/Applications/Reports/Performance/GetTWRPerformance", body
        )
        self.warnings.extend(validate_response("performance", data))
        return data

    async def get_asset_allocation(self, household_id: int, as_of: date) -> dict:
        """Asset class breakdown with market values.

        Uses yesterday's date because today's allocation isn't available
        until after market close.
        """
        from datetime import timedelta
        # Try yesterday first (today's data not available during market hours)
        for offset in [1, 0, 2]:
            target = as_of - timedelta(days=offset)
            body = {
                "accountSelectionGroupType": 0,
                "accountSelectionValue": household_id,
                "groupName": "All Accounts",
                "asOfDate": f"{target.isoformat()}T00:00:00",
                "householdId": 0,
                "assetType": 1,
                "inforceInsuranceEnabled": False,
            }
            data = await self._post(
                "/Applications/Reports/AssetAllocation/GetAssetAllocation", body
            )
            if data and isinstance(data, dict) and data.get("assetBalances"):
                self.warnings.extend(validate_response("asset_allocation", data))
                return data
        # Return empty if all attempts fail
        return {"assetBalances": []}

    async def get_balance_history(
        self, household_id: int, start_date: str = "2019-08-30"
    ) -> dict:
        """Monthly portfolio values from start_date to today."""
        today = date.today()
        now = datetime.now()
        body = {
            "accountSelectionGroupType": 0,
            "accountSelectionValue": household_id,
            "groupName": "All Accounts",
            "startDate": f"{start_date}T00:00:00.000Z",
            "endDate": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "householdId": 0,
            "householdGroupId": 0,
            "dateType": 2,
            "startDateString": start_date.replace("-0", "-").lstrip("0"),
            "endDateString": f"{today.year}-{today.month}-{today.day}",
        }
        data = await self._post(
            "/Applications/Reports/BalanceHistory/GetBalanceHistory", body
        )
        self.warnings.extend(validate_response("balance_history", data))
        return data

    async def get_activity_summary(
        self, household_id: int, as_of: date, start_date: str | None = None
    ) -> list:
        """Period contributions, withdrawals, fees, net change."""
        start = start_date or f"{as_of.year}-01-01"
        body = {
            "accountSelectionGroupType": 0,
            "accountSelectionValue": household_id,
            "groupName": "All Accounts",
            "asOfDate": f"{as_of.isoformat()}T00:00:00",
            "startDate": f"{start}T00:00:00",
            "householdId": 0,
            "activityLevel": "LOW",
            "isIntradayRequest": True,
        }
        data = await self._post(
            "/Applications/Reports/ActivitySummary/RetrieveActivitySummary", body
        )
        self.warnings.extend(validate_response("activity_summary", data))
        return data

    async def get_market_summary(self) -> dict:
        """DJI, NASDAQ, S&P500, 10yr/30yr Treasury."""
        data = await self._get(
            "/api/trading/products/v1/marketSummaries?realtime=false"
        )
        self.warnings.extend(validate_response("market_summary", data))
        return data


class SessionExpiredError(Exception):
    """Raised when the Investor360 session cookie is invalid or expired."""


class EndpointChangedError(Exception):
    """Raised when an Investor360 endpoint returns 404 (likely URL change)."""
