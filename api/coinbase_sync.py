"""
Coinbase Advanced Trade API integration.

Uses CDP (Coinbase Developer Platform) API keys with JWT/ES256 authentication.
Syncs non-zero crypto holdings into the standard accounts + account_balances
tables with source='coinbase', so they flow naturally into net worth snapshots
and show up alongside Plaid accounts in the UI.

Env vars required:
  COINBASE_API_KEY_NAME    — organizations/.../apiKeys/...
  COINBASE_API_KEY_PRIVATE — EC private key PEM (literal \\n in .env is fine)
"""
import os
import time
import secrets
import logging
from datetime import date

import jwt
import httpx

from api.database import get_db

logger = logging.getLogger("vaultic.coinbase")

COINBASE_API_BASE = "https://api.coinbase.com"


def _generate_jwt(key_name: str, private_key: str, method: str, path: str) -> str:
    """
    Generate a short-lived JWT for Coinbase CDP authentication.

    The `uri` claim encodes the exact method + path being accessed — Coinbase
    validates this so a token for /accounts can't be replayed against /orders.
    The `nonce` header provides additional replay protection. Tokens expire in
    120 seconds (Coinbase's enforced maximum).
    """
    payload = {
        "sub": key_name,
        "iss": "coinbase-cloud",
        "nbf": int(time.time()),
        "exp": int(time.time()) + 120,
        "uri": f"{method} api.coinbase.com{path}",
    }
    return jwt.encode(
        payload,
        private_key,
        algorithm="ES256",
        headers={"kid": key_name, "nonce": secrets.token_hex(16)},
    )


def _coinbase_get(key_name: str, private_key: str, path: str, params: dict | None = None) -> dict:
    # The JWT's uri claim is signed over the path only, never the query string.
    token = _generate_jwt(key_name, private_key, "GET", path)
    resp = httpx.get(
        f"{COINBASE_API_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _get_usd_price(currency: str) -> float:
    """
    Fetch current USD spot price from Coinbase's public price API (no auth required).
    Returns 1.0 for USD-pegged assets. Returns 0.0 on failure so a pricing
    error doesn't crash the whole sync — the account will just show $0 until
    the next sync succeeds.
    """
    if currency in ("USD", "USDC", "USDT", "DAI", "BUSD"):
        return 1.0
    try:
        resp = httpx.get(
            f"{COINBASE_API_BASE}/v2/prices/{currency}-USD/spot",
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["data"]["amount"])
    except Exception as e:
        logger.warning(f"Could not fetch USD price for {currency}: {e}")
        return 0.0


def _amount(field: dict | None) -> float:
    """Read a Coinbase Amount object ({"value": "1.23", ...}) as a float."""
    return float((field or {}).get("value") or 0)


def _list_accounts(key_name: str, private_key: str) -> list[dict]:
    """
    Every /api/v3/brokerage/accounts entry, following has_next/cursor pages.

    Raises if a cursor repeats or there are more than 50 pages, so a bad
    response can never hang the scheduled sync.
    """
    accounts: list[dict] = []
    params: dict = {"limit": 250}
    seen_cursors: set[str] = set()
    for _ in range(50):
        page = _coinbase_get(key_name, private_key, "/api/v3/brokerage/accounts", params)
        accounts.extend(page.get("accounts", []))
        cursor = page.get("cursor")
        if not (page.get("has_next") and cursor):
            return accounts
        if cursor in seen_cursors:
            raise RuntimeError("Coinbase accounts pagination repeated a cursor")
        seen_cursors.add(cursor)
        params = {"limit": 250, "cursor": cursor}
    raise RuntimeError("Coinbase accounts pagination exceeded 50 pages")


def _staked_balances(
    key_name: str, private_key: str, known_uuids: set[str]
) -> dict[str, tuple[str, float]]:
    """
    Staked coins per currency, as {currency: (staked_account_uuid, amount)}.

    Staked SOL/ETH is not in /api/v3/brokerage/accounts at all: the wallet
    account's available_balance and hold drop by the staked amount. The
    portfolio breakdown lists it as a separate spot position with
    account_type ACCOUNT_TYPE_STAKED_FUNDS and the amount in
    total_balance_crypto (confirmed against the live API for #184).

    Positions whose account is already in /accounts (known_uuids) are
    skipped so nothing is counted twice. Every non-deleted portfolio is
    walked; with the one Default portfolio this matches /accounts exactly.
    A positive non-wallet position of any other type is logged, so coins
    that move somewhere new (for example while unstaking) are noticed.
    """
    portfolios = _coinbase_get(key_name, private_key, "/api/v3/brokerage/portfolios")
    staked: dict[str, tuple[str, float]] = {}
    for portfolio in portfolios.get("portfolios", []):
        if portfolio.get("deleted"):
            continue
        breakdown = _coinbase_get(
            key_name, private_key, f"/api/v3/brokerage/portfolios/{portfolio['uuid']}"
        ).get("breakdown", {})
        for pos in breakdown.get("spot_positions", []):
            acct_uuid = pos.get("account_uuid") or ""
            if not acct_uuid or acct_uuid in known_uuids:
                continue
            currency = pos.get("asset", "")
            amount = float(pos.get("total_balance_crypto") or 0)
            if not currency or amount <= 0:
                continue
            account_type = pos.get("account_type")
            if account_type != "ACCOUNT_TYPE_STAKED_FUNDS":
                if account_type != "ACCOUNT_TYPE_WALLET":
                    logger.warning(
                        f"Coinbase {currency} position of type {account_type} not counted"
                    )
                continue
            prev_uuid, prev_amount = staked.get(currency, (acct_uuid, 0.0))
            staked[currency] = (prev_uuid, prev_amount + amount)
    return staked


def _combine_holdings(
    accounts: list[dict], staked: dict[str, tuple[str, float]]
) -> dict[str, tuple[str, float]]:
    """
    One holding per currency: {currency: (account_uuid, native_amount)}.

    The amount is the wallet's available_balance plus hold (coins held for
    pending transfers) plus any staked amount, so staking a coin no longer
    makes it vanish from net worth. The UUID is the wallet's, or the staked
    position's when there is no wallet; sync_coinbase uses it only for a
    currency that has no account row yet.
    """
    holdings: dict[str, tuple[str, float]] = {}
    for acct in accounts:
        currency = acct.get("currency", "")
        amount = _amount(acct.get("available_balance")) + _amount(acct.get("hold"))
        if currency in holdings:
            uuid, prev = holdings[currency]
            holdings[currency] = (uuid, prev + amount)
        else:
            holdings[currency] = (acct["uuid"], amount)
    for currency, (staked_uuid, amount) in staked.items():
        uuid, prev = holdings.get(currency, (staked_uuid, 0.0))
        holdings[currency] = (uuid, prev + amount)
    return holdings


def sync_coinbase() -> dict:
    """
    Sync Coinbase portfolio to the database.

    Fetches all accounts plus staked balances (one total per currency:
    available + hold + staked), converts non-zero totals to USD, and upserts into
    the standard accounts + account_balances tables. Uses the account UUID as
    the external ID (stored in plaid_account_id column) and source='coinbase'
    to distinguish from Plaid-sourced accounts.

    Returns a summary dict: {"synced": N, "total_usd": X} or {"skipped": True}.
    """
    key_name = os.environ.get("COINBASE_API_KEY_NAME", "")
    # .env stores the private key with literal \n — convert to real newlines
    private_key = os.environ.get("COINBASE_API_KEY_PRIVATE", "").replace("\\n", "\n")

    if not key_name or not private_key:
        logger.info("COINBASE_API_KEY_NAME / COINBASE_API_KEY_PRIVATE not set — skipping")
        return {"skipped": True}

    try:
        accounts = _list_accounts(key_name, private_key)
        staked = _staked_balances(key_name, private_key, {a["uuid"] for a in accounts})
    except Exception as e:
        logger.error(f"Coinbase API error: {e}")
        raise

    holdings = _combine_holdings(accounts, staked)
    today = date.today().isoformat()
    synced = 0
    total_usd = 0.0

    with get_db() as conn:
        for currency, (acct_uuid, balance) in holdings.items():
            if balance <= 0:
                continue

            price = _get_usd_price(currency)
            usd_value = round(balance * price, 2)

            if usd_value < 0.01:
                continue  # skip dust balances

            display_name = f"Coinbase {currency}"

            # Upsert account — plaid_account_id used as generic external ID.
            # A currency that already has a row keeps it, whichever UUID the
            # API led with this time, so there is only ever one row per
            # currency and net worth never counts a coin twice.
            acct_number = f"coinbase{currency.upper()}"
            existing = conn.execute(
                "SELECT plaid_account_id FROM accounts "
                "WHERE source = 'coinbase' AND account_number = ? ORDER BY id LIMIT 1",
                (acct_number,),
            ).fetchone()
            if existing:
                acct_uuid = existing["plaid_account_id"]
            conn.execute("""
                INSERT INTO accounts
                    (plaid_account_id, name, display_name, type, subtype,
                     institution_name, source, is_active, account_number)
                VALUES (?, ?, ?, 'crypto', ?, 'Coinbase', 'coinbase', 1, ?)
                ON CONFLICT(plaid_account_id) DO UPDATE SET
                    name             = excluded.name,
                    institution_name = 'Coinbase',
                    is_active        = 1,
                    account_number   = excluded.account_number
            """, (acct_uuid, display_name, display_name, currency.upper(), acct_number))

            account_row = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = ?", (acct_uuid,)
            ).fetchone()
            if not account_row:
                continue

            # Snapshot today's USD balance + native amount + unit price
            conn.execute("""
                INSERT INTO account_balances
                    (account_id, current, available, native_balance, unit_price,
                     snapped_at, account_number)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_number, snapped_at) DO UPDATE SET
                    current        = excluded.current,
                    available      = excluded.available,
                    native_balance = excluded.native_balance,
                    unit_price     = excluded.unit_price,
                    account_id     = excluded.account_id
            """, (account_row["id"], usd_value, usd_value, balance, price, today,
                  acct_number))

            total_usd += usd_value
            synced += 1

    logger.info(f"Coinbase sync complete: {synced} holdings, ${total_usd:,.2f} total")
    return {"synced": synced, "total_usd": total_usd}
