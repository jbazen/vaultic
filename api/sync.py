"""
Plaid sync logic: pull balances + transactions for all connected items.
Called on startup, on-demand via API, and daily via APScheduler.
"""
import json
import logging
from datetime import date

import plaid
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_refresh_request import TransactionsRefreshRequest

from api.database import get_db
from api.encryption import decrypt
from api import security_log
from api.plaid_client import get_plaid_client

logger = logging.getLogger("vaultic.sync")


def _get_plaid_client():
    """Wrapper for shared Plaid client factory."""
    return get_plaid_client()


def _extract_plaid_error_code(exc: Exception) -> str | None:
    """Pull Plaid's `error_code` (e.g. ITEM_LOGIN_REQUIRED) out of an exception.

    plaid.ApiException carries the error as a JSON string in `.body`. Returns
    None for non-Plaid exceptions or when the body can't be parsed.
    """
    if isinstance(exc, plaid.ApiException) and getattr(exc, "body", None):
        try:
            return json.loads(exc.body).get("error_code")
        except (ValueError, TypeError):
            return None
    return None


def _record_item_error(item_db_id: int, exc: Exception):
    """Persist the real Plaid error on the item (issue #73) so the Accounts
    banner can show an accurate 'reconnect needed' state instead of guessing
    from sync staleness. Falls back to a generic SYNC_ERROR code for non-Plaid
    failures. Fires a one-time push on the first transition into an error state.
    """
    error_code = _extract_plaid_error_code(exc) or "SYNC_ERROR"
    try:
        with get_db() as conn:
            prior = conn.execute(
                "SELECT error_code, institution_name FROM plaid_items WHERE id = ?",
                (item_db_id,),
            ).fetchone()
            was_healthy = not prior or not prior["error_code"]
            institution = prior["institution_name"] if prior else None
            conn.execute(
                "UPDATE plaid_items SET error_code = ?, last_error_at = CURRENT_TIMESTAMP WHERE id = ?",
                (error_code, item_db_id),
            )
    except Exception as db_err:
        logger.warning(f"Failed to record item error for {item_db_id}: {db_err}")
        return

    # Notify only on the first transition into a login-required state — a push on
    # every 4×/day cron run for the same dead item would be noise.
    if was_healthy and error_code == "ITEM_LOGIN_REQUIRED":
        try:
            from api.push import notify_item_disconnected
            notify_item_disconnected(institution)
        except Exception as push_err:
            logger.warning(f"Disconnect push failed (non-fatal): {push_err}")


def _clear_item_error(item_db_id: int):
    """Clear any stored error on an item after a successful sync (issue #73)."""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE plaid_items SET error_code = NULL, last_error_at = NULL WHERE id = ?",
                (item_db_id,),
            )
    except Exception as db_err:
        logger.warning(f"Failed to clear item error for {item_db_id}: {db_err}")


def _refresh_transactions(access_token: str):
    """Ask Plaid to pull fresh transactions from the bank right now (issue #72).

    transactions/refresh is an on-demand, ASYNCHRONOUS product: the call returns
    immediately and Plaid fetches new data in the background (seconds to a
    minute), signalling completion via a SYNC_UPDATES_AVAILABLE webhook. The
    fresh transactions therefore land on a SUBSEQUENT transactions/sync, not in
    this same request. Called only on the manual Sync button — never on the
    scheduled cron — because each call is billed as an on-demand request.
    """
    client = _get_plaid_client()
    client.transactions_refresh(TransactionsRefreshRequest(access_token=access_token))


def sync_all(refresh: bool = False):
    """Sync all Plaid items + Coinbase holdings, then snapshot net worth.

    refresh=True forces a Plaid transactions/refresh per item before syncing
    (issue #72). Only the manual Sync button passes refresh=True; the scheduled
    cron calls sync_all() with the default so it never incurs on-demand cost.
    """
    today = date.today().isoformat()
    security_log.log_sync_event(f"STARTED  date={today}  refresh={refresh}")

    # --- Plaid ---
    with get_db() as conn:
        items = conn.execute("SELECT * FROM plaid_items").fetchall()

    ok, failed = 0, 0
    for item in items:
        try:
            access_token = decrypt(item["access_token_enc"])
            _sync_item(item["id"], item["item_id"], access_token, today, refresh=refresh)
            _clear_item_error(item["id"])
            ok += 1
        except Exception as e:
            logger.error(f"Sync failed for item {item['item_id']}: {e}")
            security_log.log_sync_event(f"ITEM_FAILED  item={item['item_id']}  error={e}")
            _record_item_error(item["id"], e)
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

    # Check if Parker/I360 data is stale and send a push reminder (once per day)
    try:
        from api.push import notify_stale_i360
        notify_stale_i360()
    except Exception as push_err:
        logger.warning(f"I360 stale push check failed (non-fatal): {push_err}")

    security_log.log_sync_event(f"COMPLETED  ok={ok}  failed={failed}")


def _sync_item(item_db_id: int, item_id: str, access_token: str, today: str, refresh: bool = False):
    client = _get_plaid_client()

    # --- Force-refresh from the bank (manual sync only) ---
    # Fired first so Plaid's async background pull has the most lead time; the
    # freshly-pulled transactions land on a later sync, not this one. Best-effort:
    # a refresh failure (e.g. a dead login) is swallowed here — the accounts_get
    # below will raise the same error and drive the reconnect state (issue #73).
    if refresh:
        try:
            _refresh_transactions(access_token)
        except Exception as e:
            logger.info(f"transactions/refresh skipped for item {item_db_id}: {e}")

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
                    (plaid_account_id, plaid_item_id, name, official_name, mask,
                     type, subtype, institution_name, account_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plaid_account_id) DO UPDATE SET
                    name = excluded.name,
                    official_name = excluded.official_name,
                    mask = excluded.mask,
                    institution_name = excluded.institution_name,
                    account_number = excluded.account_number
            """, (
                acct.account_id,
                item_db_id,
                acct.name,
                acct.official_name,
                acct.mask,
                acct.type.value,
                acct.subtype.value if acct.subtype else None,
                institution_name,
                acct.account_id,  # account_number = plaid_account_id
            ))

            # Balance snapshot for today
            account_row = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = ?", (acct.account_id,)
            ).fetchone()
            if account_row and acct.balances:
                conn.execute("""
                    INSERT INTO account_balances
                        (account_id, current, available, limit_amount, snapped_at,
                         account_number)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_number, snapped_at) DO UPDATE SET
                        current = excluded.current,
                        available = excluded.available,
                        limit_amount = excluded.limit_amount,
                        account_id = excluded.account_id
                """, (
                    account_row["id"],
                    acct.balances.current,
                    acct.balances.available,
                    acct.balances.limit,
                    today,
                    acct.account_id,  # account_number = plaid_account_id
                ))

        conn.execute(
            "UPDATE plaid_items SET last_synced_at = CURRENT_TIMESTAMP WHERE id = ?",
            (item_db_id,),
        )

    # --- Transactions ---
    _sync_transactions(item_db_id, access_token, today)

    # --- Investment holdings + transactions (401k, IRA, brokerage) ---
    _sync_investments(item_db_id, access_token, today)

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
            category_detail = None
            if txn.personal_finance_category:
                category = txn.personal_finance_category.primary
                category_detail = getattr(txn.personal_finance_category, "detailed", None)

            # Fix Plaid sign inconsistency for refunds: Plaid sometimes sends
            # refunds with a positive amount (money out) even though the bank
            # statement shows a credit. The detailed category reliably identifies
            # refunds regardless of the sign. If the detailed category says it's
            # a refund/return but the amount is positive, negate it.
            amount = txn.amount
            if amount > 0 and category_detail and any(
                tag in category_detail.upper()
                for tag in ("REFUND", "RETURN", "CREDIT_CARD_PAYMENT")
            ):
                amount = -amount

            # Extract optional fields — use getattr to handle older Plaid SDK versions
            authorized_date = getattr(txn, "authorized_date", None)
            if authorized_date and hasattr(authorized_date, "isoformat"):
                authorized_date = authorized_date.isoformat()

            transaction_code = getattr(txn, "transaction_code", None)
            if transaction_code and hasattr(transaction_code, "value"):
                transaction_code = transaction_code.value

            conn.execute("""
                INSERT INTO transactions
                    (transaction_id, account_id, amount, date, name, merchant_name,
                     category, category_detail, pending, payment_channel,
                     authorized_date, original_description, merchant_entity_id,
                     check_number, logo_url, website, transaction_code,
                     account_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    amount = excluded.amount,
                    date = excluded.date,
                    name = excluded.name,
                    merchant_name = excluded.merchant_name,
                    category = excluded.category,
                    category_detail = excluded.category_detail,
                    pending = excluded.pending,
                    payment_channel = excluded.payment_channel,
                    authorized_date = excluded.authorized_date,
                    original_description = excluded.original_description,
                    merchant_entity_id = excluded.merchant_entity_id,
                    check_number = excluded.check_number,
                    logo_url = excluded.logo_url,
                    website = excluded.website,
                    transaction_code = excluded.transaction_code,
                    account_number = excluded.account_number
            """, (
                txn.transaction_id,
                account_row["id"],
                amount,
                txn.date.isoformat() if hasattr(txn.date, "isoformat") else str(txn.date),
                txn.name,
                txn.merchant_name,
                category,
                category_detail,
                int(txn.pending),
                getattr(txn, "payment_channel", None),
                authorized_date,
                getattr(txn, "original_description", None),
                getattr(txn, "merchant_entity_id", None),
                getattr(txn, "check_number", None),
                getattr(txn, "logo_url", None),
                getattr(txn, "website", None),
                transaction_code,
                txn.account_id,  # account_number = plaid_account_id
            ))

        for txn in removed:
            conn.execute(
                "DELETE FROM transactions WHERE transaction_id = ?", (txn.transaction_id,)
            )


def _sync_investments(item_db_id: int, access_token: str, today: str):
    """
    Fetch investment holdings and transactions from Plaid for one item.
    Snapshots holdings daily (like account_balances) so portfolio value can be
    tracked over time. Investment transactions (buy/sell/dividend) are upserted
    so the full history accumulates without duplicates.
    Silently skips items that don't have the investments product enabled.
    """
    from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
    from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
    from plaid.model.investments_transactions_get_request_options import InvestmentsTransactionsGetRequestOptions
    from datetime import date, timedelta

    client = _get_plaid_client()

    # ── Holdings ──────────────────────────────────────────────────────────────
    try:
        resp = client.investments_holdings_get(
            InvestmentsHoldingsGetRequest(access_token=access_token)
        )
    except Exception as e:
        # PRODUCT_NOT_READY or not enabled for this institution — not an error
        logger.info(f"investments_holdings_get skipped for item {item_db_id}: {e}")
        return

    with get_db() as conn:
        # Upsert security metadata (name, ticker, type, etc.)
        for sec in resp.securities:
            conn.execute("""
                INSERT INTO plaid_securities
                    (security_id, name, ticker_symbol, type, close_price,
                     close_price_as_of, iso_currency_code, cusip, isin, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(security_id) DO UPDATE SET
                    name               = excluded.name,
                    ticker_symbol      = excluded.ticker_symbol,
                    type               = excluded.type,
                    close_price        = excluded.close_price,
                    close_price_as_of  = excluded.close_price_as_of,
                    iso_currency_code  = excluded.iso_currency_code,
                    updated_at         = CURRENT_TIMESTAMP
            """, (
                sec.security_id,
                sec.name,
                sec.ticker_symbol,
                sec.type,
                sec.close_price,
                str(sec.close_price_as_of) if sec.close_price_as_of else None,
                sec.iso_currency_code or "USD",
                sec.cusip,
                sec.isin,
            ))

        # Snapshot each holding for today
        for h in resp.holdings:
            acct = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = ?", (h.account_id,)
            ).fetchone()
            if not acct:
                continue
            conn.execute("""
                INSERT INTO plaid_holdings
                    (account_id, security_id, institution_value, institution_price,
                     institution_price_as_of, quantity, cost_basis, iso_currency_code,
                     snapped_at, account_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_number, security_id, snapped_at) DO UPDATE SET
                    institution_value       = excluded.institution_value,
                    institution_price       = excluded.institution_price,
                    institution_price_as_of = excluded.institution_price_as_of,
                    quantity                = excluded.quantity,
                    cost_basis              = excluded.cost_basis,
                    account_id              = excluded.account_id
            """, (
                acct["id"],
                h.security_id,
                h.institution_value,
                h.institution_price,
                str(h.institution_price_as_of) if h.institution_price_as_of else None,
                h.quantity,
                h.cost_basis,
                h.iso_currency_code or "USD",
                today,
                h.account_id,  # account_number = plaid_account_id
            ))

    logger.info(f"Holdings snapshot: item {item_db_id}  {len(resp.holdings)} holdings  {len(resp.securities)} securities")

    # ── Investment transactions ────────────────────────────────────────────────
    try:
        end_dt   = date.today()
        start_dt = end_dt - timedelta(days=730)  # 2 years of history
        options  = InvestmentsTransactionsGetRequestOptions(count=500, offset=0)
        resp2 = client.investments_transactions_get(
            InvestmentsTransactionsGetRequest(
                access_token=access_token,
                start_date=start_dt,
                end_date=end_dt,
                options=options,
            )
        )
        all_txns = list(resp2.investment_transactions)

        # Paginate through remaining pages
        while len(all_txns) < resp2.total_investment_transactions:
            options = InvestmentsTransactionsGetRequestOptions(
                count=500, offset=len(all_txns)
            )
            page = client.investments_transactions_get(
                InvestmentsTransactionsGetRequest(
                    access_token=access_token,
                    start_date=start_dt,
                    end_date=end_dt,
                    options=options,
                )
            )
            all_txns.extend(page.investment_transactions)

        with get_db() as conn:
            for txn in all_txns:
                acct = conn.execute(
                    "SELECT id FROM accounts WHERE plaid_account_id = ?", (txn.account_id,)
                ).fetchone()
                if not acct:
                    continue
                date_str = txn.date.isoformat() if hasattr(txn.date, "isoformat") else str(txn.date)
                conn.execute("""
                    INSERT INTO plaid_investment_transactions
                        (investment_transaction_id, account_id, security_id, date, name,
                         quantity, amount, fees, type, subtype, cancel_transaction_id,
                         iso_currency_code, account_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(investment_transaction_id) DO UPDATE SET
                        amount                = excluded.amount,
                        fees                  = excluded.fees,
                        cancel_transaction_id = excluded.cancel_transaction_id,
                        account_number        = excluded.account_number
                """, (
                    txn.investment_transaction_id,
                    acct["id"],
                    txn.security_id,
                    date_str,
                    txn.name,
                    txn.quantity,
                    txn.amount,
                    txn.fees,
                    txn.type,
                    txn.subtype,
                    txn.cancel_transaction_id,
                    txn.iso_currency_code or "USD",
                    txn.account_id,  # account_number = plaid_account_id
                ))

        logger.info(f"Investment transactions: item {item_db_id}  {len(all_txns)} txns")

    except Exception as e:
        logger.warning(f"investment_transactions_get failed for item {item_db_id}: {e}")


def _take_net_worth_snapshot(today: str):
    with get_db() as conn:
        accounts = conn.execute(
            "SELECT * FROM accounts WHERE is_active = 1 AND is_manual = 0"
        ).fetchall()

        liquid = invested = crypto = liabilities = 0.0

        for acct in accounts:
            # Try today's balance first; fall back to most recent balance so
            # accounts aren't silently dropped when sync hasn't run yet today.
            row = conn.execute(
                "SELECT current FROM account_balances WHERE account_number = ? AND snapped_at = ?",
                (acct["account_number"], today),
            ).fetchone()
            if not row or row["current"] is None:
                row = conn.execute(
                    "SELECT current FROM account_balances WHERE account_number = ? "
                    "AND current IS NOT NULL ORDER BY snapped_at DESC LIMIT 1",
                    (acct["account_number"],),
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

        # Manual entries — latest value per category (excluding display-only entries).
        # home_value / car_value use _latest (one value per category,
        # most-recent entry wins) because users typically update a single running estimate.
        def _latest(category: str) -> float:
            r = conn.execute(
                "SELECT value FROM manual_entries WHERE category = ? AND (exclude_from_net_worth IS NULL OR exclude_from_net_worth = 0) ORDER BY entered_at DESC LIMIT 1",
                (category,),
            ).fetchone()
            return r["value"] if r else 0.0

        # _sum_manual sums ALL non-excluded entries in a category. Used for categories
        # where a user can have multiple PDF-imported accounts (e.g. IRA + 529).
        # Entries with exclude_from_net_worth=1 are skipped — typically consolidated
        # "Overall Portfolio" summaries that would double-count individual accounts.
        def _sum_manual(category: str) -> float:
            r = conn.execute(
                "SELECT COALESCE(SUM(value), 0) FROM manual_entries WHERE category = ? AND (exclude_from_net_worth IS NULL OR exclude_from_net_worth = 0)",
                (category,),
            ).fetchone()
            return float(r[0]) if r else 0.0

        real_estate  = _latest("home_value")
        vehicles     = _latest("car_value")
        # Liabilities from manual entries (mortgage, etc.) may be stored as
        # negative values from PDF import. Use abs() so they always ADD to
        # the liabilities total (which is subtracted from net worth later).
        liabilities += abs(_sum_manual("other_liability"))

        other_assets  = _sum_manual("other_asset")
        invested     += _sum_manual("invested")
        liquid       += _sum_manual("liquid")
        crypto       += _sum_manual("crypto")
        real_estate  += _sum_manual("real_estate")
        vehicles     += _sum_manual("vehicles")

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
