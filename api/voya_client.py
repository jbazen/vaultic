"""
Voya 401K retirement portal client.

Reads the my.voya.com internal JSON API using session cookies + the per-session
token obtained from a browser "Copy as cURL" command. Like Insperity (and unlike
Investor360), Voya sits behind Cloudflare bot-management + an IBM ISAM session,
both IP-bound — so this runs LOCALLY on the user's machine via sync_voya.py, not
on the server.

The /dashboard/accounts response shape has not been frozen with a fixture yet, so
the parser is defensive: it searches well-known field names and, if it can't find
a balance, raises SchemaUnknownError carrying the raw JSON so sync_voya.py can dump
it for inspection and we can harden the parser.
"""
import logging
import re

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://my.voya.com"
REQUEST_TIMEOUT = 20.0
ACCOUNTS_PATH = "/myvoyage/ws/ers/dashboard/accounts"

# Session cookies that prove the paste came from an authenticated my.voya.com tab.
REQUIRED_COOKIES = ["MYVOYA_SSO_SESSION_ID", "MYVOYA_SESSION_ID", "JSESSIONID"]

# Field-name candidates for defensive JSON parsing (Voya/ERS naming varies).
_NAME_KEYS = ["planName", "name", "accountName", "planDesc", "planDescription",
              "description", "displayName", "investmentName"]
_BALANCE_KEYS = ["totalBalance", "balance", "vestedBalance", "marketValue",
                 "currentBalance", "totalValue", "accountBalance", "value",
                 "totalMarketValue", "endingBalance"]
_PLAN_KEYS = ["planId", "planID", "planNumber", "accountNumber", "accountId", "id"]
_LIST_KEYS = ["accounts", "accountList", "data", "vstAccounts", "items",
              "results", "accountSummaries", "planAccounts"]


class SessionExpiredError(Exception):
    """Voya session cookies are stale (401/403) — re-grab the cURL."""


class EndpointChangedError(Exception):
    """Voya endpoint returned an unexpected status (404/5xx)."""


class SchemaUnknownError(Exception):
    """Couldn't locate balances in the JSON. Carries raw payload for dumping."""

    def __init__(self, message: str, payload):
        super().__init__(message)
        self.payload = payload


def parse_curl(curl_command: str) -> tuple[dict, str | None]:
    """Extract (cookies, session_token) from a browser 'Copy as cURL' string.

    Strips Windows cmd ^ escaping. The session token is the `s` / `sessionId` /
    `sessionID` query param on any my.voya.com dashboard request. Returns the
    token or None (some deployments authenticate on cookies alone).
    """
    cleaned = curl_command.replace("^", "")

    cookie_match = re.search(r'(?:-b|--cookie)\s+["\']([^"\']+)["\']', cleaned)
    if cookie_match:
        raw = cookie_match.group(1)
    elif "=" in cleaned and "curl" not in cleaned.lower():
        raw = cleaned
    else:
        raise ValueError(
            "Could not find cookies in the cURL command. On my.voya.com open "
            "DevTools → Network → Fetch/XHR, right-click any row → Copy all as cURL."
        )

    cookies = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        cookies[name.strip()] = value.strip()

    missing = [c for c in REQUIRED_COOKIES if c not in cookies]
    if missing:
        raise ValueError(
            f"Missing required Voya cookies: {', '.join(missing)}. "
            f"Make sure you copied the cURL from a logged-in my.voya.com tab."
        )

    # Session token from a dashboard URL: ?s=... / ?sessionId=... / &sessionID=...
    tok = re.search(r"[?&](?:s|sessionId|sessionID)=([0-9A-Za-z]+)", cleaned)
    return cookies, (tok.group(1) if tok else None)


class VoyaClient:
    def __init__(self, cookies: dict, session_token: str | None = None):
        self.cookies = cookies
        self.session_token = session_token

    def _headers(self) -> dict:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Referer": f"{BASE_URL}/myvoyageui/",
            "X-Requested-By": "myvoyagewebui",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/148.0.0.0 Safari/537.36"),
        }

    def _get(self, path: str, params: dict | None = None):
        url = f"{BASE_URL}{path}"
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(url, params=params or {},
                              cookies=self.cookies, headers=self._headers())
        if resp.status_code in (401, 403):
            raise SessionExpiredError(
                f"Voya session expired or IP-locked ({resp.status_code}). "
                f"Re-grab the cURL from a fresh my.voya.com tab."
            )
        if resp.status_code == 404:
            raise EndpointChangedError(f"Endpoint not found (404): {path}")
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            raise SchemaUnknownError(
                "Voya did not return JSON (likely a Cloudflare/login HTML page).",
                resp.text[:2000],
            )

    def get_accounts(self) -> dict:
        """Fetch + parse account balances.

        Returns {"total_balance": float, "accounts": [{name, plan_id, balance}]}.
        Raises SchemaUnknownError (with the raw payload) if no balance is found.
        """
        params = {"s": self.session_token} if self.session_token else {}
        data = self._get(ACCOUNTS_PATH, params)
        return _parse_accounts(data)


def _find_list(data):
    """Locate the list of account objects within an arbitrary JSON envelope."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in _LIST_KEYS:
            if isinstance(data.get(k), list) and data[k]:
                return data[k]
        # Fall back to the first list-of-dicts value anywhere in the dict.
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
            if isinstance(v, dict):
                inner = _find_list(v)
                if inner:
                    return inner
    return []


def _first(d: dict, keys: list[str]):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _to_float(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace("$", "").replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _parse_accounts(data) -> dict:
    rows = _find_list(data)
    accounts = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        bal = _to_float(_first(item, _BALANCE_KEYS))
        if bal is None:
            continue
        accounts.append({
            "name": str(_first(item, _NAME_KEYS) or "Voya Account")[:100],
            "plan_id": (str(_first(item, _PLAN_KEYS)) if _first(item, _PLAN_KEYS) else None),
            "balance": bal,
        })

    if not accounts:
        raise SchemaUnknownError(
            "Could not locate any account balances in Voya's JSON response. "
            "The API shape differs from what the parser expects.",
            data,
        )

    total = round(sum(a["balance"] for a in accounts), 2)
    return {"total_balance": total, "accounts": accounts}


# ── Full-data parsers (holdings / transactions / performance / allocations) ─────
# These take the raw JSON bodies captured from the my.voya.com endpoints (via the
# browser grabber — Cloudflare blocks non-browser fetches) and normalize them into
# the structures /api/voya/sync-local stores. Defensive: tolerate missing keys and
# string-formatted numbers ("N/A" -> None via _to_float).

def _norm_date(s):
    """Normalize a Voya date (MM/DD/YYYY or YYYY-MM-DD...) to ISO YYYY-MM-DD."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        mo, da, yr = m.groups()
        return f"{yr}-{int(mo):02d}-{int(da):02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else None


def parse_manage_investments(data: dict) -> dict:
    """Parse /epweb/ws/ers/manageInvestments/<plan> into holdings + performance.

    Returns {total_balance, personal_ror_ytd, as_of, holdings}, where holdings is
    [{fund_name, balance, units, unit_price, ytd_pct, pct_of_account}].
    """
    ov = (data or {}).get("overviewSection") or {}
    funds = ((ov.get("investmentElection") or {}).get("myFundsList")) or []
    holdings = []
    for fnd in funds:
        if not isinstance(fnd, dict) or not fnd.get("fundName"):
            continue
        holdings.append({
            "fund_name": str(fnd["fundName"])[:120],
            "balance": _to_float(fnd.get("fundBalance")),
            "units": _to_float(fnd.get("fundNoOfUnits")),
            "unit_price": _to_float(fnd.get("fundUnitPrice")),
            "ytd_pct": _to_float(fnd.get("fundYTD")),
            "pct_of_account": _to_float(fnd.get("fundPercentage")),
        })
    return {
        "total_balance": _to_float(ov.get("totalBalance")),
        "personal_ror_ytd": _to_float(ov.get("rateOfReturn")),
        "as_of": _norm_date(ov.get("asOfLastClosingDate")),
        "holdings": holdings,
    }


def fund_map_from_unitpricing(data: dict) -> dict:
    """Build {fundId: fundName} from /unitpricing for labeling transactions."""
    fb = ((data or {}).get("data") or {}).get("fundBals") or []
    return {str(f.get("fundId")): f.get("fundName")
            for f in fb if f.get("fundId") and f.get("fundName")}


def parse_transactions(data: dict, fund_map: dict | None = None) -> list[dict]:
    """Parse /ppthistory/transactions/completed/... into a flat, de-duped list.

    Voya returns each row twice (grouped + ungrouped overlap), so we de-dup on the
    full value tuple. fund_map maps ivId -> fund_name when available.
    """
    fund_map = fund_map or {}
    out, seen = [], set()
    for t in (data or {}).get("ungroupedTransactions") or []:
        if not isinstance(t, dict):
            continue
        d = _norm_date(t.get("tradeDate"))
        if not d:
            continue
        iv = t.get("ivId")
        rec = {
            "trade_date": d,
            "activity": str(t.get("activityDescription") or "")[:80],
            "fund_id": str(iv) if iv not in (None, "") else None,
            "fund_name": fund_map.get(str(iv)),
            "amount": _to_float(t.get("cash")),
            "units": _to_float(t.get("unit_or_unshrs")),
            "unit_price": _to_float(t.get("br161_shr_price")),
        }
        key = (rec["trade_date"], rec["activity"], rec["fund_id"],
               rec["amount"], rec["units"], rec["unit_price"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def parse_balance_summary(tx_data: dict) -> dict:
    """Pull period start/end balance + growth from the transactions payload."""
    bal = (tx_data or {}).get("balance") or {}
    return {
        "balance_start": _to_float(bal.get("totalStart")),
        "balance_end": _to_float(bal.get("totalEnd")),
        "growth": _to_float(bal.get("totalGrowthDifference")),
    }


def parse_allocations(data: dict) -> list[dict]:
    """Parse the asset-class allocation breakdown into [{asset_class, pct, color}]."""
    out = []
    for a in (data or {}).get("data") or []:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        out.append({
            "asset_class": str(a["name"])[:80],
            "pct": _to_float(a.get("pct")),
            "color": a.get("color"),
        })
    return out


def fund_code(fund_name) -> str | None:
    """Leading fund code from a holding name: 'C975 Fidelity 500...' -> 'C975'."""
    if not isinstance(fund_name, str):
        return None
    parts = fund_name.split()
    return parts[0] if parts else None


_FUND_RETURN_COLS = [
    ("one_month", "oneMonth"), ("three_month", "threeMonth"),
    ("ytd", "yearToDate"), ("one_year", "oneYear"),
    ("three_year", "threeYear"), ("five_year", "fiveYear"),
    ("ten_year", "tenYear"), ("inception", "inception"),
]


def parse_fund_performance(data: dict, owned_codes=None) -> list[dict]:
    """Per-fund multi-timeframe returns from the fund-performance payload.

    Returns [{fund_code, fund_name, benchmark, one_month, ..., inception}].
    Filters to `owned_codes` (the fund codes the user holds) when provided. The
    feed lists each fund twice, so we de-dup on fund_code.
    """
    owned = set(owned_codes) if owned_codes else None
    out, seen = [], set()
    for f in (data or {}).get("monthEndFundData") or []:
        if not isinstance(f, dict):
            continue
        code = f.get("fundNumber")
        pr = f.get("performanceReturnsElement") or {}
        if not code or code in seen or not pr:
            continue
        if owned is not None and code not in owned:
            continue
        seen.add(code)
        # Clean Voya's fund name: strip non-ASCII (the ® arrives mangled) and drop
        # the trailing " - <code>" so "Fidelity® 500 Index Fund - C975" -> "Fidelity 500 Index Fund".
        name = str(f.get("fundName") or "").encode("ascii", "ignore").decode()
        name = re.sub(r"\s*-\s*" + re.escape(str(code)) + r"\s*$", "", name).strip()
        rec = {
            "fund_code": str(code),
            "fund_name": name[:120],
            "benchmark": f.get("fundBenchmarkDesc"),
        }
        for col, key in _FUND_RETURN_COLS:
            rec[col] = _to_float(pr.get(key))
        out.append(rec)
    return out


def parse_contributions(reg_data: dict, summary_data: dict | None = None) -> dict:
    """Contribution rate + sources from contributionsedge/regular (+ summary).

    Returns {contrib_type, total_pct, catchup, sources:[{source_id, name,
    current, actual}]}.
    """
    contrib = (reg_data or {}).get("contribution") or {}
    sources = []
    for s in contrib.get("sources") or []:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        sources.append({
            "source_id": (str(s["id"]) if s.get("id") else None),
            "name": str(s["name"])[:60],
            "current": _to_float(s.get("currentContribution")),
            "actual": _to_float(s.get("actualContribution")),
        })
    summ = (summary_data or {}).get("contributions") or {}
    return {
        "contrib_type": summ.get("contribType"),
        "total_pct": _to_float(summ.get("totalContrib")),
        "catchup": _to_float(summ.get("totalCatchup")),
        "sources": sources,
    }
