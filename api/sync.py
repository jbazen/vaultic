"""
Plaid sync logic: pull balances + transactions for all connected items.
Called on startup, on-demand via API, and daily via APScheduler.
"""
import os
import logging
from datetime import date

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from api.database import get_db
from api.encryption import decrypt
from api import security_log

logger = logging.getLogger("vaultic.sync")


def _get_plaid_client():
    env_map = {
        "sandbox": plaid.Environment.Sandbox,
        "development": plaid.Environment.Sandbox,  # newer SDK dropped Development
        "production": plaid.Environment.Production,
    }
    host = env_map.get(os.environ.get("PLAID_ENV", "sandbox"), plaid.Environment.Sandbox)
    config = plaid.Configuration(
        host=host,
        api_key={
            "clientId": os.environ["PLAID_CLIENT_ID"],
            "secret": os.environ["PLAID_SECRET"],
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(config))


def sync_all():
    """Sync all Plaid items + Coinbase holdings, then snapshot net worth."""
    today = date.today().isoformat()
    security_log.log_sync_event(f"STARTED  date={today}")

    # --- Plaid ---
    with get_db() as conn:
        items = conn.execute("SELECT * FROM plaid_items").fetchall()

    ok, failed = 0, 0
    for item in items:
        try:
            access_token = decrypt(item["access_token_enc"])
            _sync_item(item["id"], item["item_id"], access_token, today)
            ok += 1
        except Exception as e:
            logger.error(f"Sync failed for item {item['item_id']}: {e}")
            security_log.log_sync_event(f"ITEM_FAILED  item={item['item_id']}  error={e}")
            failed += 1

    # --- Coinbase ---
    try:
        from api.coinbase_sync import sync_coinbase
        sync_coinbase()
    except Exception as e:
        logger.error(f"Coinbase sync failed: {e}")
        security_log.log_sync_event(f"COINBASE_FAILED  error={e}")

    # Net worth snapshot is taken inside _sync_item for each Plaid item, but we
    # call it once more here to capture Coinbase + any items that may have been
    # skipped, ensuring today's snapshot always reflects the full picture.
    _take_net_worth_snapshot(today)

    security_log.log_sync_event(f"COMPLETED  ok={ok}  failed={failed}")


def _sync_item(item_db_id: int, item_id: str, access_token: str, today: str):
    client = _get_plaid_client()

    # --- Accounts ---
    acct_resp = client.accounts_get(AccountsGetRequest(access_token=access_token))
    institution_name = None
    if acct_resp.item.institution_id:
        try:
            from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
            from plaid.model.country_code import CountryCode
            inst_resp = client.institutions_get_by_id(
                InstitutionsGetByIdRequest(
                    institution_id=acct_resp.item.institution_id,
                    country_codes=[CountryCode("US")],
                )
            )
            institution_name = inst_resp.institution.name
        except Exception:
            pass

    with get_db() as conn:
        for acct in acct_resp.accounts:
            conn.execute("""
                INSERT INTO accounts
                    (plaid_account_id, plaid_item_id, name, official_name, mask, type, subtype, institution_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plaid_account_id) DO UPDATE SET
                    name = excluded.name,
                    official_name = excluded.official_name,
                    mask = excluded.mask,
                    institution_name = excluded.institution_name
            """, (
                acct.account_id,
                item_db_id,
                acct.name,
                acct.official_name,
                acct.mask,
                acct.type.value,
                acct.subtype.value if acct.subtype else None,
                institution_name,
            ))

            # Balance snapshot for today
            account_row = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = ?", (acct.account_id,)
            ).fetchone()
            if account_row and acct.balances:
                conn.execute("""
                    INSERT INTO account_balances
                        (account_id, current, available, limit_amount, snapped_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, snapped_at) DO UPDATE SET
                        current = excluded.current,
                        available = excluded.available,
                        limit_amount = excluded.limit_amount
                """, (
                    account_row["id"],
                    acct.balances.current,
                    acct.balances.available,
                    acct.balances.limit,
                    today,
                ))

        conn.execute(
            "UPDATE plaid_items SET last_synced_at = CURRENT_TIMESTAMP WHERE id = ?",
            (item_db_id,),
        )

    # --- Transactions ---
    _sync_transactions(item_db_id, access_token, today)

    # --- Net worth snapshot ---
    _take_net_worth_snapshot(today)


def _sync_transactions(item_db_id: int, access_token: str, today: str):
    client = _get_plaid_client()

    with get_db() as conn:
        item = conn.execute(
            "SELECT cursor FROM plaid_items WHERE id = ?", (item_db_id,)
        ).fetchone()
        cursor = item["cursor"] or ""

    added, modified, removed = [], [], []
    has_more = True

    while has_more:
        req = TransactionsSyncRequest(access_token=access_token, cursor=cursor)
        resp = client.transactions_sync(req)
        added.extend(resp.added)
        modified.extend(resp.modified)
        removed.extend(resp.removed)
        has_more = resp.has_more
        cursor = resp.next_cursor

    with get_db() as conn:
        conn.execute(
            "UPDATE plaid_items SET cursor = ? WHERE id = ?", (cursor, item_db_id)
        )

        # Upsert added/modified
        for txn in added + modified:
            account_row = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = ?", (txn.account_id,)
            ).fetchone()
            if not account_row:
                continue
            category = None
            if txn.personal_finance_category:
                category = txn.personal_finance_category.primary
            conn.execute("""
                INSERT INTO transactions
                    (transaction_id, account_id, amount, date, name, merchant_name, category, pending)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    amount = excluded.amount,
                    date = excluded.date,
                    name = excluded.name,
                    merchant_name = excluded.merchant_name,
                    category = excluded.category,
                    pending = excluded.pending
            """, (
                txn.transaction_id,
                account_row["id"],
                txn.amount,
                txn.date.isoformat() if hasattr(txn.date, "isoformat") else str(txn.date),
                txn.name,
                txn.merchant_name,
                category,
                int(txn.pending),
            ))

        for txn in removed:
            conn.execute(
                "DELETE FROM transactions WHERE transaction_id = ?", (txn.transaction_id,)
            )


def _take_net_worth_snapshot(today: str):
    with get_db() as conn:
        accounts = conn.execute(
            "SELECT * FROM accounts WHERE is_active = 1 AND is_manual = 0"
        ).fetchall()

        liquid = invested = crypto = liabilities = 0.0

        for acct in accounts:
            row = conn.execute(
                "SELECT current FROM account_balances WHERE account_id = ? AND snapped_at = ?",
                (acct["id"], today),
            ).fetchone()
            if not row or row["current"] is None:
                continue
            bal = row["current"]
            t, s = acct["type"], (acct["subtype"] or "")

            if t == "crypto":
                crypto += bal
            elif t == "depository" and s in ("checking", "savings", "money market", "paypal", "prepaid"):
                liquid += bal
            elif t == "investment" or s in ("401k", "ira", "roth", "pension"):
                invested += bal
            elif t in ("credit", "loan"):
                liabilities += bal
            else:
                liquid += bal  # catch-all depository

        # Manual entries — latest value per category
        def _latest(category: str) -> float:
            r = conn.execute(
                "SELECT value FROM manual_entries WHERE category = ? ORDER BY entered_at DESC LIMIT 1",
                (category,),
            ).fetchone()
            return r["value"] if r else 0.0

        real_estate = _latest("home_value")
        vehicles = _latest("car_value")
        other_assets = _latest("other_asset")
        liabilities += _latest("other_liability")

        total = liquid + invested + crypto + real_estate + vehicles + other_assets - liabilities

        conn.execute("""
            INSERT INTO net_worth_snapshots
                (snapped_at, total, liquid, invested, crypto, real_estate, vehicles, liabilities, other_assets)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapped_at) DO UPDATE SET
                total        = excluded.total,
                liquid       = excluded.liquid,
                invested     = excluded.invested,
                crypto       = excluded.crypto,
                real_estate  = excluded.real_estate,
                vehicles     = excluded.vehicles,
                liabilities  = excluded.liabilities,
                other_assets = excluded.other_assets
        """, (today, total, liquid, invested, crypto, real_estate, vehicles, liabilities, other_assets))

    logger.info(f"Net worth snapshot {today}: ${total:,.0f}")
