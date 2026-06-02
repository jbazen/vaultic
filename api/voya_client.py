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
