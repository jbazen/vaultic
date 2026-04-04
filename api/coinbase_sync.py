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


def _coinbase_get(key_name: str, private_key: str, path: str) -> dict:
    token = _generate_jwt(key_name, private_key, "GET", path)
    resp = httpx.get(
        f"{COINBASE_API_BASE}{path}",
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


def sync_coinbase() -> dict:
    """
    Sync Coinbase portfolio to the database.

    Fetches all accounts, converts non-zero balances to USD, and upserts into
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
        data = _coinbase_get(key_name, private_key, "/api/v3/brokerage/accounts")
    except Exception as e:
        logger.error(f"Coinbase API error: {e}")
        raise

    accounts = data.get("accounts", [])
    today = date.today().isoformat()
    synced = 0
    total_usd = 0.0

    with get_db() as conn:
        for acct in accounts:
            currency = acct.get("currency", "")
            balance = float(acct.get("available_balance", {}).get("value", "0") or "0")

            if balance <= 0:
                continue

            price = _get_usd_price(currency)
            usd_value = round(balance * price, 2)

            if usd_value < 0.01:
                continue  # skip dust balances

            acct_uuid = acct["uuid"]
            display_name = f"Coinbase {currency}"

            # Upsert account — plaid_account_id used as generic external ID
            acct_number = f"coinbase{currency.upper()}"
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
                ON CONFLICT(account_id, snapped_at) DO UPDATE SET
                    current        = excluded.current,
                    available      = excluded.available,
                    native_balance = excluded.native_balance,
                    unit_price     = excluded.unit_price,
                    account_number = excluded.account_number
            """, (account_row["id"], usd_value, usd_value, balance, price, today,
                  acct_number))

            total_usd += usd_value
            synced += 1

    logger.info(f"Coinbase sync complete: {synced} holdings, ${total_usd:,.2f} total")
    return {"synced": synced, "total_usd": total_usd}
