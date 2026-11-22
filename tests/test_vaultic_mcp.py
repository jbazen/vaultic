"""Tests for scripts/vaultic_mcp.py (read-only MCP server). HTTP and token minting are mocked."""
import importlib.util
import io
import re
import json
import sys
from pathlib import Path

import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "vaultic_mcp", Path(__file__).resolve().parent.parent / "scripts" / "vaultic_mcp.py"
)
mcp = importlib.util.module_from_spec(_SPEC)
sys.modules["vaultic_mcp"] = mcp
_SPEC.loader.exec_module(mcp)

SECRET = "tok-SECRET-abc123"


class Env:
    """A server wired to a mock transport that records requests."""

    def __init__(self, monkeypatch, handler=None):
        self.requests: list[httpx.Request] = []
        self.minted: list[tuple] = []
        self.clock = 1000.0

        def default(request):
            return httpx.Response(200, json={"ok": True})

        self.handler = handler or default

        def transport(request):
            self.requests.append(request)
            return self.handler(request)

        def fake_create_token(username, hours=None):
            self.minted.append((username, hours))
            return SECRET if len(self.minted) == 1 else f"{SECRET}-{len(self.minted)}"

        monkeypatch.setattr(mcp, "_load_create_token", lambda: fake_create_token)
        monkeypatch.setattr(mcp, "_now", lambda: self.clock)
        monkeypatch.delenv("VAULTIC_URL", raising=False)
        monkeypatch.delenv("VAULTIC_MCP_USER", raising=False)
        self.client = mcp.VaulticClient()
        self.client._client = httpx.Client(
            base_url=self.client.base_url, transport=httpx.MockTransport(transport)
        )
        self.server = mcp.Server(self.client)

    def rpc(self, method, params=None, id_=1):
        msg = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params is not None:
            msg["params"] = params
        return self.server.handle(msg)

    def call(self, name, arguments=None):
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}})["result"]


@pytest.fixture
def env(monkeypatch):
    return Env(monkeypatch)


# ── protocol ───────────────────────────────────────────────────────────────

def test_initialize_echoes_supported_version(env):
    r = env.rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert r["protocolVersion"] == "2025-03-26"
    assert r["capabilities"] == {"tools": {}}
    assert r["serverInfo"]["name"] == "vaultic"


def test_initialize_unknown_version_falls_back(env):
    r = env.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert r["protocolVersion"] == "2025-06-18"


def test_ping(env):
    assert env.rpc("ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_tools_list_has_schemas_and_no_network_or_mint(env):
    tools = env.rpc("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"get_net_worth", "list_accounts", "get_account_detail", "get_provider_data"} <= names
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"
    assert env.requests == [] and env.minted == []


def test_unknown_method(env):
    assert env.rpc("nope")["error"]["code"] == -32601


def test_malformed_json(env):
    assert env.server.handle_line("{not json")["error"]["code"] == -32700


def test_notifications_get_no_reply(env):
    assert env.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert env.server.handle({"jsonrpc": "2.0", "method": "bogus/unknown"}) is None


def test_non_object_is_invalid_request(env):
    assert env.server.handle_line("[1]")["error"]["code"] == -32600


def test_unknown_tool_is_rpc_error(env):
    assert env.rpc("tools/call", {"name": "delete_everything"})["error"]["code"] == -32602


# ── tool -> route mapping ──────────────────────────────────────────────────

CASES = [
    ("get_net_worth", {}, "/api/net-worth/latest", {}),
    ("get_net_worth_history", {}, "/api/net-worth/history", {"days": "365"}),
    ("get_net_worth_history", {"days": 99999}, "/api/net-worth/history", {"days": "3650"}),
    ("list_accounts", {}, "/api/accounts", {}),
    ("get_account_detail", {"account_id": 7, "view": "balances"}, "/api/accounts/7/balances",
     {"days": "90"}),
    ("get_account_detail", {"account_id": 7, "view": "holdings"}, "/api/accounts/7/holdings", {}),
    ("get_account_detail", {"account_id": 7, "view": "transactions", "limit": 10, "offset": 5},
     "/api/accounts/7/transactions", {"limit": "10", "offset": "5"}),
    ("get_account_detail", {"account_id": 7, "view": "investment_transactions"},
     "/api/accounts/7/investment-transactions", {"limit": "100", "offset": "0"}),
    ("get_portfolio_performance", {"days": 30}, "/api/accounts/portfolio/performance",
     {"days": "30"}),
    ("get_crypto_holdings", {}, "/api/crypto/holdings", {}),
    ("get_manual_entries", {}, "/api/manual", {}),
    ("get_manual_entries", {"entry_id": 3}, "/api/manual/3/history", {"days": "1825"}),
    ("get_provider_data", {"provider": "investor360", "resource": "holdings"},
     "/api/investor360/holdings", {}),
    ("get_provider_data", {"provider": "investor360", "resource": "holdings", "account_id": 4},
     "/api/investor360/holdings/4", {}),
    ("get_provider_data", {"provider": "investor360", "resource": "performance", "account_id": 4},
     "/api/investor360/performance", {"account_id": "4"}),
    ("get_provider_data", {"provider": "insperity", "resource": "prices"},
     "/api/insperity/prices", {}),
    ("get_provider_data", {"provider": "voya", "resource": "fund_performance"},
     "/api/voya/fund-performance", {}),
    ("get_provider_data", {"provider": "voya", "resource": "transactions"},
     "/api/voya/transactions", {"limit": "100"}),
    ("get_provider_data", {"provider": "voya", "resource": "sync_log", "limit": 500},
     "/api/voya/sync-log", {"limit": "100"}),
    ("get_market_and_news", {"resource": "rates"}, "/api/market/rates", {}),
    ("get_market_and_news", {"resource": "quotes"}, "/api/feed/quotes", {}),
    ("get_market_and_news", {"resource": "news"}, "/api/feed/news", {}),
    ("list_plaid_connections", {}, "/api/plaid/items", {}),
]


@pytest.mark.parametrize("tool,args,path,query", CASES)
def test_tool_maps_to_get_route(env, tool, args, path, query):
    result = env.call(tool, args)
    assert "isError" not in result, result
    assert json.loads(result["content"][0]["text"]) == {"ok": True}
    (req,) = env.requests
    assert req.method == "GET"
    assert req.url.path == path
    assert dict(req.url.params) == query
    assert req.headers["Authorization"] == f"Bearer {SECRET}"


def test_provider_routes_are_allowlisted():
    for provider, routes in mcp._PROVIDER_ROUTES.items():
        for resource, (path, _accepted) in routes.items():
            assert mcp.check_request(path, {}) == {}, (provider, resource)
    assert mcp.check_request("/api/investor360/holdings/12", {}) == {}


def _app_routes():
    from api.main import app

    routes = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods:
            sample = re.sub(r"\{[^}]+\}", "1", route.path)
            routes.append((sample, set(methods)))
    return routes


def test_every_allowlisted_pattern_matches_a_real_get_route():
    routes = _app_routes()
    for pattern, _spec in mcp._ALLOWED:
        hits = [(p, m) for p, m in routes if pattern.fullmatch(p)]
        assert any("GET" in m for _p, m in hits), pattern.pattern


def test_allowlisted_paths_match_no_sensitive_route():
    for pattern, _spec in mcp._ALLOWED:
        for path, _methods in _app_routes():
            if pattern.fullmatch(path):
                assert not path.startswith(("/api/auth", "/api/push", "/api/pdf")), path
                assert not path.endswith(("/debug", "/refresh", "/sync", "/resync")), path


# ── allowlist ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/net-worth/debug",
    "/api/net-worth/refresh",
    "/api/auth/me",
    "/api/auth/login",
    "/api/push/subscribe",
    "/api/pdf/upload",
    "/api/plaid/resync",
    "/api/plaid/items/1",
    "/api/accounts/../auth/me",
    "/api/accounts/1/../2/balances",
    "/api/accounts/1a/balances",
    "/api/accounts/-1/balances",
    "/api/accounts/1/balances?days=1",
    "/api/accounts/1/balances/",
    "/api/accounts/institutions/order",
    "/api/accounts/1234567890/holdings",
    "/api/manual/abc/history",
    "/api/feed/summary",
    "/api/feed/quotes/refresh",
    "/api/investor360/sync",
    "/api/insperity/sync",
    "/api/voya/sync",
    "/api/voya/../auth/me",
    "https://evil.example/api/accounts",
    "api/accounts",
    "/api/accounts\n",
    "",
])
def test_allowlist_rejects_paths(path):
    with pytest.raises(mcp.NotAllowed):
        mcp.check_request(path)


@pytest.mark.parametrize("path,params", [
    ("/api/net-worth/latest", {"days": 1}),
    ("/api/net-worth/history", {"limit": 5}),
    ("/api/net-worth/history", {"days": 0}),
    ("/api/net-worth/history", {"days": 3651}),
    ("/api/net-worth/history", {"days": "30"}),
    ("/api/net-worth/history", {"days": True}),
    ("/api/net-worth/history", {"days": 1.5}),
    ("/api/accounts/1/transactions", {"limit": 501}),
    ("/api/accounts/1/transactions", {"offset": -1}),
    ("/api/accounts/1/holdings", {"limit": 1}),
    ("/api/voya/sync-log", {"limit": 101}),
    ("/api/investor360/performance", {"account_id": "1; drop"}),
])
def test_allowlist_rejects_params(path, params):
    with pytest.raises(mcp.NotAllowed):
        mcp.check_request(path, params)


def test_allowlist_accepts_valid():
    assert mcp.check_request("/api/net-worth/history", {"days": 3650}) == {"days": 3650}
    assert mcp.check_request("/api/accounts/12/balances", {"days": 1}) == {"days": 1}


def test_rejected_request_sends_nothing(env):
    with pytest.raises(mcp.ToolError):
        env.client.get("/api/net-worth/debug")
    assert env.requests == [] and env.minted == []


@pytest.mark.parametrize("tool,args", [
    ("get_account_detail", {"account_id": "../auth", "view": "balances"}),
    ("get_account_detail", {"account_id": 0, "view": "balances"}),
    ("get_account_detail", {"account_id": 1, "view": "debug"}),
    ("get_account_detail", {"view": "balances"}),
    ("get_account_detail", {"account_id": 1, "view": "balances", "days": "9"}),
    ("get_provider_data", {"provider": "auth", "resource": "status"}),
    ("get_provider_data", {"provider": "voya", "resource": "sync"}),
    ("get_market_and_news", {"resource": "summary"}),
    ("get_manual_entries", {"entry_id": "1/../../x"}),
])
def test_bad_tool_arguments_are_errors_without_network(env, tool, args):
    result = env.call(tool, args)
    assert result["isError"] is True
    assert env.requests == []


def test_days_out_of_range_is_clamped_by_tool(env):
    env.call("get_net_worth_history", {"days": -5})
    assert dict(env.requests[0].url.params) == {"days": "1"}


# ── token handling ─────────────────────────────────────────────────────────

def test_token_minted_once_and_cached(env):
    env.call("get_net_worth")
    env.call("list_accounts")
    assert env.minted == [("jbazen", 1)]


def test_username_override(env, monkeypatch):
    monkeypatch.setenv("VAULTIC_MCP_USER", "someone")
    env.client.username = mcp.os.environ["VAULTIC_MCP_USER"]
    env.call("get_net_worth")
    assert env.minted == [("someone", 1)]


def test_token_reminted_near_expiry(env):
    env.call("get_net_worth")
    env.clock += 3600 - 200  # under 5 minutes left
    env.call("get_net_worth")
    assert len(env.minted) == 2
    assert env.requests[1].headers["Authorization"] == f"Bearer {SECRET}-2"


def test_token_not_reminted_with_time_left(env):
    env.call("get_net_worth")
    env.clock += 3600 - 600
    env.call("get_net_worth")
    assert len(env.minted) == 1


def test_401_remints_once_and_retries_once(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(401 if len(seen) == 1 else 200, json={"ok": True})

    env = Env(monkeypatch, handler)
    result = env.call("get_net_worth")
    assert "isError" not in result
    assert seen == [f"Bearer {SECRET}", f"Bearer {SECRET}-2"]


def test_persistent_401_stops_after_one_retry(monkeypatch):
    env = Env(monkeypatch, lambda r: httpx.Response(401, json={"detail": "x"}))
    result = env.call("get_net_worth")
    assert result["isError"] is True and "401" in result["content"][0]["text"]
    assert len(env.requests) == 2 and len(env.minted) == 2


def test_token_never_in_outputs(monkeypatch, capsys):
    def handler(request):
        return httpx.Response(500, text=f"boom {request.headers['Authorization']}")

    env = Env(monkeypatch, handler)
    out = [env.call("get_net_worth"), env.rpc("tools/list"), env.call("list_accounts")]
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "get_net_worth", "arguments": {}}}) + "\n"
    )
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(mcp, "Server", lambda: env.server)
    mcp.main()
    captured = capsys.readouterr()
    blob = json.dumps(out) + captured.out + captured.err
    assert SECRET not in blob
    for line in captured.out.splitlines():
        json.loads(line)  # stdout is protocol JSON only


# ── GET only / errors ──────────────────────────────────────────────────────

def test_only_get_is_used(env):
    for tool, args, *_ in CASES:
        env.call(tool, args)
    assert {r.method for r in env.requests} == {"GET"}
    src = Path(mcp.__file__).read_text(encoding="utf-8")
    for verb in ("post", "put", "patch", "delete", "request", "send", "stream"):
        assert f"client.{verb}(" not in src and f"httpx.{verb}(" not in src


@pytest.mark.parametrize("status", [403, 404, 422, 500, 503])
def test_non_2xx_is_generic_error(monkeypatch, status):
    env = Env(monkeypatch, lambda r: httpx.Response(status, text="Traceback (most recent call)"))
    result = env.call("list_accounts")
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert str(status) in text and "Traceback" not in text


def test_redirect_is_error_not_followed(monkeypatch):
    env = Env(monkeypatch, lambda r: httpx.Response(302, headers={"Location": "https://x.example"}))
    assert env.call("list_accounts")["isError"] is True
    assert len(env.requests) == 1


def test_network_error_is_generic(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connect failed to host with details")

    env = Env(monkeypatch, handler)
    result = env.call("list_accounts")
    assert result["isError"] is True
    assert "details" not in result["content"][0]["text"]


def test_non_json_body_is_error(monkeypatch):
    env = Env(monkeypatch, lambda r: httpx.Response(200, text="<html>"))
    assert env.call("list_accounts")["isError"] is True


def test_mint_failure_is_generic_error(monkeypatch):
    env = Env(monkeypatch)

    def boom():
        raise RuntimeError("JWT_SECRET super-secret-detail")

    monkeypatch.setattr(mcp, "_load_create_token", boom)
    result = env.call("list_accounts")
    assert result["isError"] is True
    assert "super-secret" not in result["content"][0]["text"]
    assert env.requests == []


def test_large_body_truncated(monkeypatch):
    env = Env(monkeypatch, lambda r: httpx.Response(200, json=["x" * 1000] * 500))
    text = env.call("list_accounts")["content"][0]["text"]
    assert "truncated" in text and len(text) < mcp.MAX_RESULT_CHARS + 200


def test_http_base_url_refused_unless_local(monkeypatch):
    monkeypatch.setenv("VAULTIC_URL", "http://vaulticsage.com")
    client = mcp.VaulticClient()
    with pytest.raises(mcp.ToolError):
        client._http()
    monkeypatch.setenv("VAULTIC_URL", "http://localhost:8000")
    assert mcp.VaulticClient()._http() is not None


# ── hardening ──────────────────────────────────────────────────────────────

def test_lone_surrogate_does_not_crash_output(env, monkeypatch, capsys):
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_account_detail",
                                  "arguments": {"account_id": "\ud800", "view": "balances"}}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(line + "\n"))
    monkeypatch.setattr(mcp, "Server", lambda: env.server)
    mcp.main()
    out = capsys.readouterr().out
    assert out.isascii()
    json.loads(out)


def test_deeply_nested_json_is_parse_error(env):
    assert env.server.handle_line("[" * 100000)["error"]["code"] == -32700


def test_main_writes_lf_newlines(env, monkeypatch):
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="ascii", newline="\r\n", write_through=True)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'))
    monkeypatch.setattr(mcp, "Server", lambda: env.server)
    mcp.main()
    assert raw.getvalue().endswith(b"}\n") and b"\r" not in raw.getvalue()


def test_responses_are_never_answered(env):
    assert env.server.handle({"jsonrpc": "2.0", "id": 5, "result": {}}) is None
    assert env.server.handle({"jsonrpc": "2.0", "id": 5, "error": {"code": 1, "message": "x"}}) is None


@pytest.mark.parametrize("tool,args", [
    ("get_net_worth", {"days": 5}),
    ("list_accounts", {"extra": 1}),
    ("get_net_worth_history", {"days": 5, "limit": 1}),
    ("get_account_detail", {"account_id": 1, "view": "holdings", "days": 5}),
    ("get_account_detail", {"account_id": 1, "view": "holdings", "limit": 5}),
    ("get_account_detail", {"account_id": 1, "view": "balances", "limit": 5}),
    ("get_account_detail", {"account_id": 1, "view": "transactions", "days": 5}),
    ("get_provider_data", {"provider": "insperity", "resource": "holdings", "account_id": 1}),
    ("get_provider_data", {"provider": "voya", "resource": "holdings", "limit": 5}),
    ("get_provider_data", {"provider": "investor360", "resource": "status", "account_id": 1}),
    ("get_manual_entries", {"days": 30}),
    ("get_market_and_news", {"resource": "rates", "x": 1}),
    ("get_account_detail", {"account_id": 1, "view": None}),
])
def test_inapplicable_or_unknown_arguments_rejected(env, tool, args):
    result = env.call(tool, args)
    assert result["isError"] is True
    assert env.requests == []


def test_sync_log_limit_documented_max_is_100(env):
    env.call("get_provider_data", {"provider": "insperity", "resource": "sync_log", "limit": 500})
    assert dict(env.requests[0].url.params) == {"limit": "100"}
    assert "max 100" in mcp.TOOLS["get_provider_data"]["description"]


def test_list_accounts_description_mentions_manual_ids():
    assert "get_manual_entries" in mcp.TOOLS["list_accounts"]["description"]
