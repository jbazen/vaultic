"""Status-only check of every Vaultic prod credential (issue #91, for #59).

Run on the server from the repo root:

    .venv/bin/python scripts/verify_secrets.py                 # every check
    .venv/bin/python scripts/verify_secrets.py plaid fred      # just these
    .venv/bin/python scripts/verify_secrets.py journal --since "2026-11-13 22:00"

Prints one line per check: a name plus an HTTP status, ok/fail, a count, a
length or a timestamp. It never prints a secret value:
  - response bodies and stderr are never printed;
  - an exception is reported by its class name only (httpx messages can carry
    a URL with an API key in its query string);
  - every line passes through a redactor that masks any .env value first.

Exit code 1 if any check fails.
"""
import argparse
import base64
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import httpx
from dotenv import dotenv_values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PLAID_HOSTS = {
    "production": "https://production.plaid.com",
    "development": "https://development.plaid.com",
    "sandbox": "https://sandbox.plaid.com",
}
COINBASE_ACCOUNTS_PATH = "/api/v3/brokerage/accounts"
# Lag allowed between the newest WAL in the local file replica and in R2.
LITESTREAM_MAX_LAG_SECONDS = 120
# Litestream logs replica errors as "<db>(s3): ... error: ..." under vaultic-api.
R2_ERROR_RE = re.compile(r"(\(s3\)|\bs3\b|r2\.cloudflarestorage).*(error|fail)", re.IGNORECASE)
REDACTED = "[REDACTED]"
MIN_SECRET_LEN = 8


def read_env(path: str) -> dict[str, str]:
    """Parse .env with python-dotenv, exactly as api/main.py's load_dotenv()
    does (inline `# comments` stripped, PEM `\\n` kept literal)."""
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def secret_fragments(env: dict[str, str]) -> list[str]:
    """Every string that must never reach stdout: each value, its PEM-decoded
    form and each PEM body line. Longest first so overlaps mask fully."""
    frags: set[str] = set()
    for value in env.values():
        if len(value) < MIN_SECRET_LEN:
            continue
        frags.add(value)
        if "\\n" in value:
            decoded = value.replace("\\n", "\n")
            frags.add(decoded)
            frags.update(p for p in decoded.split("\n") if len(p) >= MIN_SECRET_LEN)
    return sorted(frags, key=len, reverse=True)


def redact(text: str, fragments: list[str]) -> str:
    for frag in fragments:
        text = text.replace(frag, REDACTED)
    return text


@dataclass
class Ctx:
    env: dict[str, str]
    http: httpx.Client
    base_url: str = "http://127.0.0.1:8000"
    db_path: str = os.path.join(ROOT, "data", "vaultic.db")
    user: str = ""
    since: str = "24 hours ago"
    runner: Callable = subprocess.run
    sleep: Callable[[float], None] = time.sleep
    mint: Callable | None = None
    failures: list[str] = field(default_factory=list)
    fragments: list[str] = field(default_factory=list)

    def emit(self, name: str, ok: bool, detail: str) -> None:
        if not ok:
            self.failures.append(name)
        print(redact(f"{name}: {detail}", self.fragments))

    def need(self, name: str, *keys: str) -> bool:
        missing = [k for k in keys if not self.env.get(k)]
        if missing:
            self.emit(name, False, "not set (" + ", ".join(missing) + ")")
            return False
        return True


def _status(ctx: Ctx, name: str, resp: httpx.Response, extra: str = "") -> None:
    ctx.emit(name, resp.status_code == 200, f"HTTP {resp.status_code}{extra}")


def check_plaid(ctx: Ctx) -> None:
    name = "PLAID_SECRET"
    if not ctx.need(name, "PLAID_CLIENT_ID", "PLAID_SECRET"):
        return
    host = PLAID_HOSTS.get(ctx.env.get("PLAID_ENV", "sandbox"))
    if host is None:
        ctx.emit(name, False, "PLAID_ENV is not sandbox/development/production")
        return
    resp = ctx.http.post(f"{host}/institutions/get", json={
        "client_id": ctx.env["PLAID_CLIENT_ID"],
        "secret": ctx.env["PLAID_SECRET"],
        "count": 1,
        "offset": 0,
        "country_codes": ["US"],
    })
    extra = ""
    if resp.status_code != 200:
        # Plaid's error_code (e.g. INVALID_API_KEYS) is an enum, never a value
        code = str(resp.json().get("error_code", "")) if "json" in resp.headers.get("content-type", "") else ""
        extra = f" ({code})" if re.fullmatch(r"[A-Z_]{1,64}", code) else ""
    _status(ctx, name, resp, extra)


def check_fred(ctx: Ctx) -> None:
    name = "FRED_API_KEY"
    if not ctx.need(name, name):
        return
    resp = ctx.http.get("https://api.stlouisfed.org/fred/series/observations", params={
        "series_id": "DGS10",
        "api_key": ctx.env[name],
        "file_type": "json",
        "limit": "1",
    })
    _status(ctx, name, resp)


def check_coinbase(ctx: Ctx) -> None:
    from api.coinbase_sync import COINBASE_API_BASE, _generate_jwt

    name = "COINBASE_API_KEY"
    if not ctx.need(name, "COINBASE_API_KEY_NAME", "COINBASE_API_KEY_PRIVATE"):
        return
    key_name = ctx.env["COINBASE_API_KEY_NAME"]
    pem = ctx.env["COINBASE_API_KEY_PRIVATE"].replace("\\n", "\n")
    token = _generate_jwt(key_name, pem, "GET", COINBASE_ACCOUNTS_PATH)
    resp = ctx.http.get(f"{COINBASE_API_BASE}{COINBASE_ACCOUNTS_PATH}",
                        headers={"Authorization": f"Bearer {token}"})
    extra = ""
    if resp.status_code == 200:
        extra = f" (accounts={len(resp.json().get('accounts', []))})"
    _status(ctx, name, resp, extra)


def check_tavily(ctx: Ctx) -> None:
    name = "TAVILY_API_KEY"
    if not ctx.need(name, name):
        return
    resp = ctx.http.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {ctx.env[name]}"},
        json={"query": "mortgage rates", "max_results": 1, "search_depth": "basic"},
    )
    _status(ctx, name, resp)


def check_anthropic(ctx: Ctx) -> None:
    name = "ANTHROPIC_API_KEY"
    if not ctx.need(name, name):
        return
    resp = ctx.http.get("https://api.anthropic.com/v1/models", headers={
        "x-api-key": ctx.env[name],
        "anthropic-version": "2023-06-01",
    })
    _status(ctx, name, resp)


def check_health(ctx: Ctx) -> None:
    _status(ctx, "HEALTH", ctx.http.get(f"{ctx.base_url}/api/health"))


def check_jwt(ctx: Ctx) -> None:
    name = "JWT_SECRET"
    mint = ctx.mint
    if mint is None:
        from api.auth import create_token as mint
    user = ctx.user or ctx.env.get("AUTH_USERNAME") or "jbazen"
    token = mint(user, hours=1)
    ctx.sleep(3)  # a freshly minted token can 401 for ~1-2s on prod (clock skew)
    resp = ctx.http.get(f"{ctx.base_url}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    _status(ctx, name, resp, " (/api/auth/me)")


def _public_key_from_pem(pem: str) -> str:
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, load_pem_private_key,
    )
    key = load_pem_private_key(pem.replace("\\n", "\n").encode(), password=None)
    raw = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def check_vapid(ctx: Ctx) -> None:
    name = "VAPID"
    if not ctx.need(name, "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY_PEM"):
        return
    env_pub = ctx.env["VAPID_PUBLIC_KEY"]
    match = _public_key_from_pem(ctx.env["VAPID_PRIVATE_KEY_PEM"]) == env_pub
    ctx.emit(f"{name}_KEYPAIR", match, "match ok" if match else "MISMATCH")
    resp = ctx.http.get(f"{ctx.base_url}/api/push/vapid-public-key")
    served = resp.json().get("publicKey", "") if resp.status_code == 200 else ""
    ok = resp.status_code == 200 and len(served) == 87 and served == env_pub
    ctx.emit(f"{name}_ENDPOINT", ok,
             f"HTTP {resp.status_code} len={len(served)} matches_env={'yes' if served == env_pub else 'no'}")


def check_encryption(ctx: Ctx) -> None:
    from cryptography.fernet import Fernet, InvalidToken

    name = "ENCRYPTION_KEY"
    if not ctx.need(name, name):
        return
    cipher = Fernet(ctx.env[name].encode())
    conn = sqlite3.connect(f"file:{ctx.db_path}?mode=ro", uri=True)
    try:
        results = []
        for label, sql in (
            ("plaid_items", "SELECT access_token_enc FROM plaid_items"),
            ("totp_secret", "SELECT totp_secret FROM users WHERE totp_secret IS NOT NULL AND totp_secret != ''"),
        ):
            rows = [r[0] for r in conn.execute(sql)]
            good = 0
            for ciphertext in rows:
                try:
                    cipher.decrypt(ciphertext.encode())
                    good += 1
                except (InvalidToken, ValueError):
                    pass
            results.append((label, good, len(rows)))
    finally:
        conn.close()
    ok = all(good == total for _, good, total in results)
    ctx.emit(name, ok, ", ".join(f"{label} decrypt {good}/{total}" for label, good, total in results))


def _litestream_env(ctx: Ctx) -> dict[str, str]:
    env = dict(os.environ)
    env.update({k: v for k, v in ctx.env.items() if k.startswith("LITESTREAM_")})
    return env


def latest_by_replica(output: str) -> dict[str, datetime]:
    """Newest `created` timestamp per replica from `litestream wal`/`snapshots`."""
    latest: dict[str, datetime] = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            created = datetime.strptime(parts[-1], "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
        if parts[0] not in latest or created > latest[parts[0]]:
            latest[parts[0]] = created
    return latest


def check_litestream(ctx: Ctx) -> None:
    name = "LITESTREAM_R2"
    if not ctx.need(name, "LITESTREAM_ACCESS_KEY_ID", "LITESTREAM_SECRET_ACCESS_KEY"):
        return
    config = os.path.join(ROOT, "deploy", "litestream.yml")
    newest: dict[str, dict[str, datetime]] = {}
    for cmd in ("wal", "snapshots"):
        proc = ctx.runner(["litestream", cmd, "-config", config, ctx.db_path],
                          env=_litestream_env(ctx), capture_output=True, text=True)
        if proc.returncode != 0:
            ctx.emit(name, False, f"litestream {cmd} exit {proc.returncode}")
            return
        newest[cmd] = latest_by_replica(proc.stdout)
    s3_wal = newest["wal"].get("s3")
    if s3_wal is None:
        ctx.emit(name, False, "no s3 WAL segments listed")
        return
    local_wal = newest["wal"].get("file", s3_wal)
    lag = max(0, int((local_wal - s3_wal).total_seconds()))
    s3_snap = newest["snapshots"].get("s3")
    snap = s3_snap.strftime("%Y-%m-%dT%H:%M:%SZ") if s3_snap else "none"
    ctx.emit(name, lag <= LITESTREAM_MAX_LAG_SECONDS,
             f"latest WAL {s3_wal:%Y-%m-%dT%H:%M:%SZ}, lag vs local replica {lag}s, latest snapshot {snap}")


def check_journal(ctx: Ctx) -> None:
    name = "LITESTREAM_R2_ERRORS"
    proc = ctx.runner(["journalctl", "-u", "vaultic-api", "--since", ctx.since, "--no-pager", "-o", "cat"],
                      capture_output=True, text=True)
    if proc.returncode != 0:
        ctx.emit(name, False, f"journalctl exit {proc.returncode}")
        return
    count = sum(1 for line in proc.stdout.splitlines() if R2_ERROR_RE.search(line))
    ctx.emit(name, count == 0, f"count={count} since '{ctx.since}'")


CHECKS: dict[str, Callable[[Ctx], None]] = {
    "health": check_health,
    "plaid": check_plaid,
    "fred": check_fred,
    "coinbase": check_coinbase,
    "tavily": check_tavily,
    "anthropic": check_anthropic,
    "litestream": check_litestream,
    "journal": check_journal,
    "jwt": check_jwt,
    "vapid": check_vapid,
    "encryption": check_encryption,
}


def run(names: list[str], ctx: Ctx) -> int:
    ctx.fragments = secret_fragments(ctx.env)
    for check in names:
        try:
            CHECKS[check](ctx)
        except Exception as exc:  # report the class only: messages can carry secrets
            ctx.emit(check.upper(), False, f"error {type(exc).__name__}")
    return 1 if ctx.failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checks", nargs="*", metavar="CHECK",
                        help="any of: " + ", ".join(CHECKS) + " (default: all)")
    parser.add_argument("--env", default=os.path.join(ROOT, ".env"))
    parser.add_argument("--db", default=os.path.join(ROOT, "data", "vaultic.db"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default="", help="user for the JWT round-trip (default AUTH_USERNAME)")
    parser.add_argument("--since", default="24 hours ago", help="journalctl --since for the R2 error count")
    args = parser.parse_args(argv)
    unknown = [c for c in args.checks if c not in CHECKS]
    if unknown:
        parser.error("unknown check(s): " + ", ".join(unknown))

    with httpx.Client(timeout=15) as http:
        ctx = Ctx(env=read_env(args.env), http=http, base_url=args.base_url,
                  db_path=args.db, user=args.user, since=args.since)
        return run(args.checks or list(CHECKS), ctx)


if __name__ == "__main__":
    sys.exit(main())
