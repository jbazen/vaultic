"""Read-only MCP server for Vaultic data (net worth, accounts, holdings, 401k, crypto).

Speaks MCP over stdio (newline-delimited JSON-RPC 2.0), written with the standard
library only. It mints a short-lived access token in memory for the owner, sends
GET requests to the Vaultic API, and returns the JSON as tool results. Every path
goes through one allowlist (`check_request`), and the only HTTP verb used is GET.

Register it with Claude Code (user scope):

    claude mcp add --scope user vaultic -- C:\\Users\\jbaze\\IdeaProjects\\vaultic\\.venv\\Scripts\\python.exe C:\\Users\\jbaze\\IdeaProjects\\vaultic\\scripts\\vaultic_mcp.py

Environment:
    VAULTIC_URL       API base URL (default https://vaulticsage.com)
    VAULTIC_MCP_USER  user the token is minted for (default jbazen)

stdout carries only protocol JSON; logging goes to stderr. The token is never
written anywhere (not to disk, stdout, stderr or tool results).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2025-06-18"
TOKEN_HOURS = 1
TOKEN_REFRESH_MARGIN = 300.0  # seconds of life left that triggers a re-mint
HTTP_TIMEOUT = 20.0
MAX_RESULT_CHARS = 100_000
_ID = r"(\d{1,9})"


def log(msg: str) -> None:
    print(f"vaultic-mcp: {msg}", file=sys.stderr, flush=True)


# ── Allowlist ──────────────────────────────────────────────────────────────

class NotAllowed(Exception):
    """Raised before any request is sent when a path or query is not allowlisted."""


# (path regex, {param name: (min, max)}). Params are integers only.
_ALLOWED: list[tuple[re.Pattern[str], dict[str, tuple[int, int]]]] = [
    (re.compile(p), spec) for p, spec in [
        (r"/api/net-worth/latest", {}),
        (r"/api/net-worth/history", {"days": (1, 3650)}),
        (r"/api/accounts", {}),
        (r"/api/accounts/portfolio/performance", {"days": (1, 1825)}),
        (rf"/api/accounts/{_ID}/balances", {"days": (1, 1825)}),
        (rf"/api/accounts/{_ID}/holdings", {}),
        (rf"/api/accounts/{_ID}/transactions", {"limit": (1, 500), "offset": (0, 1_000_000)}),
        (rf"/api/accounts/{_ID}/investment-transactions",
         {"limit": (1, 500), "offset": (0, 1_000_000)}),
        (r"/api/crypto/holdings", {}),
        (r"/api/manual", {}),
        (rf"/api/manual/{_ID}/history", {"days": (1, 3650)}),
        (r"/api/plaid/items", {}),
        (r"/api/market/rates", {}),
        (r"/api/feed/quotes", {}),
        (r"/api/feed/news", {}),
        (r"/api/investor360/status", {}),
        (r"/api/investor360/holdings", {}),
        (rf"/api/investor360/holdings/{_ID}", {}),
        (r"/api/investor360/performance", {"account_id": (1, 999_999_999)}),
        (r"/api/investor360/asset-allocation", {"account_id": (1, 999_999_999)}),
        (r"/api/investor360/balance-history", {}),
        (r"/api/investor360/activity-summary", {"account_id": (1, 999_999_999)}),
        (r"/api/investor360/market-summary", {}),
        (r"/api/investor360/sync-log", {"limit": (1, 100)}),
        (r"/api/insperity/(status|holdings|performance|contributions|activity|"
         r"transactions|allocations|prices)", {}),
        (r"/api/insperity/sync-log", {"limit": (1, 100)}),
        (r"/api/voya/(status|accounts|holdings|performance|allocations|"
         r"fund-performance|contributions)", {}),
        (r"/api/voya/sync-log", {"limit": (1, 100)}),
        (r"/api/voya/transactions", {"limit": (1, 500)}),
    ]
]


def check_request(path: str, params: dict[str, Any] | None = None) -> dict[str, int]:
    """Validate a path and its query params against the allowlist.

    Returns the cleaned params. Raises NotAllowed for anything else; this runs
    before a request is built, so nothing outside the allowlist can be sent.
    """
    params = params or {}
    if not isinstance(path, str) or not path.isascii():
        raise NotAllowed("path not allowed")
    for pattern, spec in _ALLOWED:
        if not pattern.fullmatch(path):
            continue
        clean: dict[str, int] = {}
        for name, value in params.items():
            if name not in spec:
                raise NotAllowed(f"parameter not allowed: {name}")
            lo, hi = spec[name]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise NotAllowed(f"parameter out of range: {name} must be {lo}-{hi}")
            clean[name] = value
        return clean
    raise NotAllowed("path not allowed")


# ── Token + HTTP ───────────────────────────────────────────────────────────

def _now() -> float:
    return time.monotonic()


def _load_create_token() -> Callable[..., str]:
    """Import api.auth lazily, after load_dotenv, so the JWT secret is in the env."""
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from api.auth import create_token

    return create_token


class ToolError(Exception):
    """A failure reported to the model as an isError tool result (message is safe)."""


class VaulticClient:
    def __init__(self) -> None:
        self.base_url = os.environ.get("VAULTIC_URL", "https://vaulticsage.com").rstrip("/")
        self.username = os.environ.get("VAULTIC_MCP_USER", "jbazen")
        self._token: str | None = None
        self._expires_at = 0.0
        self._client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            host = urlparse(self.base_url)
            if host.scheme != "https" and host.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ToolError("VAULTIC_URL must be https")
            self._client = httpx.Client(base_url=self.base_url, timeout=HTTP_TIMEOUT)
        return self._client

    def _mint(self) -> str:
        try:
            token = _load_create_token()(self.username, hours=TOKEN_HOURS)
        except Exception:
            log("could not mint token")
            raise ToolError("authentication unavailable")
        self._token = token
        self._expires_at = _now() + TOKEN_HOURS * 3600
        return token

    def _get_token(self) -> str:
        if self._token is None or self._expires_at - _now() < TOKEN_REFRESH_MARGIN:
            return self._mint()
        return self._token

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET an allowlisted path and return the parsed JSON body."""
        try:
            clean = check_request(path, params)
        except NotAllowed as exc:
            raise ToolError(f"not allowed: {exc}")
        client = self._http()
        token = self._get_token()
        for attempt in (0, 1):
            try:
                resp = client.get(path, params=clean, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as exc:
                log(f"network error: {type(exc).__name__}")
                raise ToolError("network error talking to Vaultic")
            if resp.status_code == 401 and attempt == 0:
                token = self._mint()
                continue
            break
        if resp.status_code != 200:
            raise ToolError(f"Vaultic API returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise ToolError("Vaultic API returned a non-JSON response")


# ── Tools ──────────────────────────────────────────────────────────────────

def _int_arg(args: dict[str, Any], name: str, default: int | None, lo: int, hi: int) -> int | None:
    """Read an optional integer argument; clamp to [lo, hi]; reject non-integers."""
    value = args.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{name} must be an integer")
    return max(lo, min(hi, value))


def validate_args(args: dict[str, Any], schema: dict[str, Any]) -> None:
    """Reject unknown keys, wrong types, bad enum values and missing required arguments."""
    props = schema.get("properties", {})
    for key in args:
        if key not in props:
            raise ToolError(f"unknown argument: {key}")
    for key in schema.get("required", []):
        if args.get(key) is None:
            raise ToolError(f"{key} is required")
    for key, value in args.items():
        spec = props[key]
        if value is None:
            raise ToolError(f"{key} must not be null")
        if spec["type"] == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ToolError(f"{key} must be an integer")
        if spec["type"] == "string" and not isinstance(value, str):
            raise ToolError(f"{key} must be a string")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"{key} must be one of: " + ", ".join(spec["enum"]))


def _reject(args: dict[str, Any], names: tuple[str, ...], why: str) -> None:
    for name in names:
        if name in args:
            raise ToolError(f"{name} does not apply {why}")


def _id_arg(args: dict[str, Any], name: str, required: bool = True) -> int | None:
    value = args.get(name)
    if value is None:
        if required:
            raise ToolError(f"{name} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolError(f"{name} must be a positive integer")
    return value


Request = tuple[str, dict[str, Any]]


def _t_net_worth(args: dict[str, Any]) -> Request:
    return "/api/net-worth/latest", {}


def _t_history(args: dict[str, Any]) -> Request:
    return "/api/net-worth/history", {"days": _int_arg(args, "days", 365, 1, 3650)}


def _t_accounts(args: dict[str, Any]) -> Request:
    return "/api/accounts", {}


def _t_account_detail(args: dict[str, Any]) -> Request:
    acct = _id_arg(args, "account_id")
    view = args.get("view")
    base = f"/api/accounts/{acct}"
    if view != "balances":
        _reject(args, ("days",), f"to view={view}")
    if view not in ("transactions", "investment_transactions"):
        _reject(args, ("limit", "offset"), f"to view={view}")
    if view == "balances":
        return f"{base}/balances", {"days": _int_arg(args, "days", 90, 1, 1825)}
    if view == "holdings":
        return f"{base}/holdings", {}
    if view in ("transactions", "investment_transactions"):
        suffix = "transactions" if view == "transactions" else "investment-transactions"
        default_limit = 50 if view == "transactions" else 100
        return f"{base}/{suffix}", {
            "limit": _int_arg(args, "limit", default_limit, 1, 500),
            "offset": _int_arg(args, "offset", 0, 0, 1_000_000),
        }
    raise ToolError("view must be one of balances, holdings, transactions, investment_transactions")


def _t_performance(args: dict[str, Any]) -> Request:
    return "/api/accounts/portfolio/performance", {"days": _int_arg(args, "days", 365, 1, 1825)}


def _t_crypto(args: dict[str, Any]) -> Request:
    return "/api/crypto/holdings", {}


def _t_manual(args: dict[str, Any]) -> Request:
    entry = _id_arg(args, "entry_id", required=False)
    if entry is None:
        _reject(args, ("days",), "without entry_id")
        return "/api/manual", {}
    return f"/api/manual/{entry}/history", {"days": _int_arg(args, "days", 1825, 1, 3650)}


# provider -> resource -> (path, accepted optional params)
_PROVIDER_ROUTES: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "investor360": {
        "status": ("/api/investor360/status", ()),
        "holdings": ("/api/investor360/holdings", ("account_id",)),
        "performance": ("/api/investor360/performance", ("account_id",)),
        "asset_allocation": ("/api/investor360/asset-allocation", ("account_id",)),
        "balance_history": ("/api/investor360/balance-history", ()),
        "activity_summary": ("/api/investor360/activity-summary", ("account_id",)),
        "market_summary": ("/api/investor360/market-summary", ()),
        "sync_log": ("/api/investor360/sync-log", ("limit",)),
    },
    "insperity": {
        "status": ("/api/insperity/status", ()),
        "holdings": ("/api/insperity/holdings", ()),
        "performance": ("/api/insperity/performance", ()),
        "contributions": ("/api/insperity/contributions", ()),
        "activity": ("/api/insperity/activity", ()),
        "transactions": ("/api/insperity/transactions", ()),
        "allocations": ("/api/insperity/allocations", ()),
        "prices": ("/api/insperity/prices", ()),
        "sync_log": ("/api/insperity/sync-log", ("limit",)),
    },
    "voya": {
        "status": ("/api/voya/status", ()),
        "accounts": ("/api/voya/accounts", ()),
        "holdings": ("/api/voya/holdings", ()),
        "transactions": ("/api/voya/transactions", ("limit",)),
        "performance": ("/api/voya/performance", ()),
        "allocations": ("/api/voya/allocations", ()),
        "fund_performance": ("/api/voya/fund-performance", ()),
        "contributions": ("/api/voya/contributions", ()),
        "sync_log": ("/api/voya/sync-log", ("limit",)),
    },
}


def _t_provider(args: dict[str, Any]) -> Request:
    provider, resource = args.get("provider"), args.get("resource")
    routes = _PROVIDER_ROUTES.get(provider) if isinstance(provider, str) else None
    if routes is None:
        raise ToolError("provider must be one of: " + ", ".join(_PROVIDER_ROUTES))
    route = routes.get(resource) if isinstance(resource, str) else None
    if route is None:
        raise ToolError(f"resource for {provider} must be one of: " + ", ".join(routes))
    path, accepted = route
    params: dict[str, Any] = {}
    if "account_id" not in accepted:
        _reject(args, ("account_id",), f"to {provider} {resource}")
    if "limit" not in accepted:
        _reject(args, ("limit",), f"to {provider} {resource}")
    if "account_id" in accepted:
        acct = _id_arg(args, "account_id", required=False)
        if acct is not None:
            if resource == "holdings":
                path = f"{path}/{acct}"  # per-account route takes the id in the path
            else:
                params["account_id"] = acct
    if "limit" in accepted:
        is_log = resource == "sync_log"
        params["limit"] = _int_arg(args, "limit", 20 if is_log else 100, 1, 100 if is_log else 500)
    return path, params


_MARKET_ROUTES = {
    "rates": "/api/market/rates",
    "quotes": "/api/feed/quotes",
    "news": "/api/feed/news",
}


def _t_market(args: dict[str, Any]) -> Request:
    path = _MARKET_ROUTES.get(args.get("resource")) if isinstance(args.get("resource"), str) else None
    if path is None:
        raise ToolError("resource must be one of: " + ", ".join(_MARKET_ROUTES))
    return path, {}


def _t_plaid_items(args: dict[str, Any]) -> Request:
    return "/api/plaid/items", {}


_DAYS = {"type": "integer", "minimum": 1}
_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

TOOLS: dict[str, dict[str, Any]] = {
    "get_net_worth": {
        "description": (
            "Latest net worth snapshot (US dollars): total, liquid, invested, crypto, "
            "real_estate, vehicles, liabilities, other_assets, mortgage, and the derived "
            "'investable' figure (liquid + invested + crypto + other assets - credit card debt)."),
        "schema": _NO_ARGS, "handler": _t_net_worth,
    },
    "get_net_worth_history": {
        "description": (
            "Net worth over time, oldest first, same fields as get_net_worth plus snapped_at. "
            "Daily points for short ranges; one point per month when the range is long."),
        "schema": {"type": "object", "additionalProperties": False, "properties": {
            "days": {**_DAYS, "maximum": 3650, "default": 365,
                     "description": "Look-back window in days (max 3650)."}}},
        "handler": _t_history,
    },
    "list_accounts": {
        "description": (
            "Every active account (Plaid, Coinbase, Investor360/Parker, Insperity, Voya, manual) "
            "with its latest balance in US dollars: id, name, institution, type/subtype, "
            "current/available/limit, native_balance and unit_price for crypto, snapped_at, "
            "plus registration details for Investor360 accounts. Use the id with get_account_detail. "
            "Manual entries (home, car, credit score) have their own ids, usable only with "
            "get_manual_entries, not get_account_detail."),
        "schema": _NO_ARGS, "handler": _t_accounts,
    },
    "get_account_detail": {
        "description": (
            "Detail for one account id from list_accounts. view: 'balances' = daily balance "
            "history (days only, default 90); 'holdings' = current positions (no other arguments); "
            "'transactions' = bank/card transactions, newest first (limit, offset only); "
            "'investment_transactions' = buys, sells, dividends, contributions (limit, offset "
            "only). Arguments that do not apply to the chosen view are rejected."),
        "schema": {"type": "object", "additionalProperties": False,
                   "required": ["account_id", "view"], "properties": {
            "account_id": {"type": "integer", "minimum": 1},
            "view": {"type": "string",
                     "enum": ["balances", "holdings", "transactions", "investment_transactions"]},
            "days": {**_DAYS, "maximum": 1825, "description": "balances view only (default 90)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                      "description": "transaction views only (default 50 / 100)."},
            "offset": {"type": "integer", "minimum": 0, "description": "transaction views only."}}},
        "handler": _t_account_detail,
    },
    "get_portfolio_performance": {
        "description": (
            "Daily combined value (total_value, US dollars) of all investment and retirement "
            "accounts, oldest first."),
        "schema": {"type": "object", "additionalProperties": False, "properties": {
            "days": {**_DAYS, "maximum": 1825, "default": 365}}},
        "handler": _t_performance,
    },
    "get_crypto_holdings": {
        "description": (
            "Current Coinbase holdings: per currency usd_value, native_balance, unit_price, "
            "snapped_at, sorted by value."),
        "schema": _NO_ARGS, "handler": _t_crypto,
    },
    "get_manual_entries": {
        "description": (
            "Manually tracked assets and liabilities (home, car, credit score, PDF-imported "
            "accounts, etc.) with their holdings and activity summary. With entry_id, instead "
            "returns that entry's balance history (days, default 1825), oldest first."),
        "schema": {"type": "object", "additionalProperties": False, "properties": {
            "entry_id": {"type": "integer", "minimum": 1,
                         "description": "Return history for this manual entry id."},
            "days": {**_DAYS, "maximum": 3650, "description": "History window (with entry_id)."}}},
        "handler": _t_manual,
    },
    "get_provider_data": {
        "description": (
            "Detailed data synced from a provider. investor360 (Parker Financial IRAs/brokerage): "
            "status, holdings, performance, asset_allocation, balance_history, activity_summary, "
            "market_summary, sync_log; holdings/performance/asset_allocation/activity_summary take "
            "an optional Vaultic account_id for one account. insperity (401k): status, holdings, "
            "performance, contributions, activity, transactions, allocations, prices, sync_log. "
            "voya (401k): status, accounts, holdings, transactions, performance, allocations, "
            "fund_performance, contributions, sync_log. limit applies only to sync_log (max 100) "
            "and voya transactions (max 500); arguments that do not apply are rejected."),
        "schema": {"type": "object", "additionalProperties": False,
                   "required": ["provider", "resource"], "properties": {
            "provider": {"type": "string", "enum": list(_PROVIDER_ROUTES)},
            "resource": {"type": "string",
                         "enum": sorted({r for p in _PROVIDER_ROUTES.values() for r in p})},
            "account_id": {"type": "integer", "minimum": 1,
                           "description": "investor360 holdings, performance, asset_allocation "
                                          "and activity_summary only; rejected elsewhere."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                      "description": "sync_log (max 100, default 20) and voya transactions "
                                     "(max 500, default 100) only."}}},
        "handler": _t_provider,
    },
    "get_market_and_news": {
        "description": (
            "Market context: 'rates' = current interest rates (FRED), 'quotes' = cached quotes "
            "for held tickers, 'news' = cached news articles."),
        "schema": {"type": "object", "additionalProperties": False, "required": ["resource"],
                   "properties": {"resource": {"type": "string", "enum": list(_MARKET_ROUTES)}}},
        "handler": _t_market,
    },
    "list_plaid_connections": {
        "description": (
            "Linked Plaid institutions with last sync time and any error_code (use to spot "
            "broken connections). Contains no credentials."),
        "schema": _NO_ARGS, "handler": _t_plaid_items,
    },
}


def render_result(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS:
        return (text[:MAX_RESULT_CHARS]
                + f"\n[truncated: response was {len(text)} characters, showing {MAX_RESULT_CHARS}]")
    return text


# ── JSON-RPC / MCP ─────────────────────────────────────────────────────────

class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code, self.message = code, message


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class Server:
    def __init__(self, client: VaulticClient | None = None) -> None:
        self.client = client or VaulticClient()

    def _call_tool(self, params: Any) -> dict[str, Any]:
        name = params.get("name") if isinstance(params, dict) else None
        tool = TOOLS.get(name) if isinstance(name, str) else None
        if tool is None:
            raise _RpcError(-32602, "Unknown tool")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise _RpcError(-32602, "arguments must be an object")
        try:
            validate_args(args, tool["schema"])
            path, query = tool["handler"](args)
            data = self.client.get(path, {k: v for k, v in query.items() if v is not None})
            return {"content": [{"type": "text", "text": render_result(data)}]}
        except ToolError as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        except Exception:
            log(f"unexpected failure in tool {name}")
            return {"isError": True,
                    "content": [{"type": "text", "text": "Internal error in vaultic MCP server"}]}

    def _dispatch(self, method: str, params: Any) -> Any:
        if method == "initialize":
            wanted = params.get("protocolVersion") if isinstance(params, dict) else None
            return {
                "protocolVersion": wanted if wanted in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "vaultic", "version": "1.0.0"},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [
                {"name": n, "description": t["description"], "inputSchema": t["schema"]}
                for n, t in TOOLS.items()]}
        if method == "tools/call":
            return self._call_tool(params)
        raise _RpcError(-32601, "Method not found")

    def handle(self, msg: Any) -> dict[str, Any] | None:
        """Handle one decoded message; returns the response, or None for notifications."""
        if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
            return None  # a response to something we never sent; never answer a response
        if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
            if isinstance(msg, dict) and "id" not in msg:
                return None  # a stray response or notification-shaped object
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600,
                          "Invalid request")
        if "id" not in msg:
            return None  # notification: never reply
        try:
            return {"jsonrpc": "2.0", "id": msg["id"],
                    "result": self._dispatch(msg["method"], msg.get("params"))}
        except _RpcError as exc:
            return _error(msg["id"], exc.code, exc.message)
        except Exception:
            log(f"internal error in {msg['method']}")
            return _error(msg["id"], -32603, "Internal error")

    def handle_line(self, line: str) -> dict[str, Any] | None:
        try:
            msg = json.loads(line)
        except Exception:  # ValueError, or RecursionError on deeply nested input
            return _error(None, -32700, "Parse error")
        return self.handle(msg)


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            # Windows defaults to a legacy code page; stdout frames must end in LF, not CRLF
            reconfigure(encoding="utf-8", **({"newline": "\n"} if stream is sys.stdout else {}))
    server = Server()
    log("ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        reply = server.handle_line(line)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=True) + "\n")  # ASCII-only: survives lone surrogates
            sys.stdout.flush()


if __name__ == "__main__":
    main()
