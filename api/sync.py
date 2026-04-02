"""
Plaid sync logic: pull balances + transactions for all connected items.
Called on startup, on-demand via API, and daily via APScheduler.
"""
import os
import json
import logging
from datetime import date

from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from api.database import get_db
from api.encryption import decrypt
from api import security_log
from api.plaid_client import get_plaid_client


def _ai_categorize_unmatched(conn, txn_ids: list[str]):
    """Call Claude Haiku to categorize transactions that had no rule-based match.

    Builds a single batched prompt for all unmatched transactions and inserts
    results as pending_review assignments with the AI-returned confidence score.
    All errors are caught and logged — this function must never crash sync.
    """
    try:
        import anthropic

        # Load transaction details
        placeholders = ",".join("?" * len(txn_ids))
        txns = conn.execute(
            f"SELECT transaction_id,"
            f" COALESCE(merchant_name, name, '') AS merchant,"
            f" ABS(amount) AS amount,"
            f" category"
            f" FROM transactions WHERE transaction_id IN ({placeholders})",
            txn_ids
        ).fetchall()
        if not txns:
            return

        # Load active budget items
        items = conn.execute(
            "SELECT bi.id, bi.name, bg.name AS group_name"
            " FROM budget_items bi"
            " JOIN budget_groups bg ON bg.id = bi.group_id"
            " WHERE bi.is_deleted = 0 AND bg.is_deleted = 0"
        ).fetchall()
        if not items:
            logger.info("AI categorization skipped — no active budget items")
            return

        valid_item_ids = {row["id"] for row in items}

        categories_block = "\n".join(
            f"{item['id']}|{item['group_name']}|{item['name']}" for item in items
        )
        txns_block = "\n".join(
            f"{i + 1}|{txn['merchant']}|${txn['amount']:.2f}|{txn['category'] or 'unknown'}"
            for i, txn in enumerate(txns)
        )

        prompt = (
            "You are a personal finance assistant helping categorize bank transactions into budget categories.\n\n"
            "Budget categories (id|group|name):\n"
            f"{categories_block}\n\n"
            "Transactions to categorize (index|merchant|amount|plaid_hint):\n"
            f"{txns_block}\n\n"
            "For each transaction, pick the single best budget category. Respond with ONLY a JSON array, no explanation:\n"
            '[{"index": 1, "item_id": 123, "confidence": 85}, ...]\n\n'
            "Confidence guide:\n"
            "- 90-99: very obvious match (merchant name clearly identifies the category)\n"
            "- 70-89: reasonable match based on merchant name or Plaid hint\n"
            "- 50-69: plausible guess\n"
            "- 30-49: low confidence guess\n\n"
            "If you are genuinely unsure (e.g., the transaction looks like an account transfer, "
            "payment, or credit that doesn't belong in a spending category), return item_id: null "
            "and confidence: 0. It is better to leave a transaction unassigned than to guess wrong."
        )

        logger.info(f"AI categorization: sending {len(txns)} transactions to Claude Haiku")

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        results = json.loads(raw_text)

        assigned = 0
        for result in results:
            idx = result.get("index")
            item_id = result.get("item_id")
            confidence = result.get("confidence", 50)
            if idx is None:
                continue
            if not (1 <= idx <= len(txns)):
                continue
            if not item_id or item_id not in valid_item_ids:
                continue  # leave unassigned — Sage wasn't confident enough
            txn_id = txns[idx - 1]["transaction_id"]
            conn.execute(
                "INSERT OR IGNORE INTO transaction_assignments"
                " (transaction_id, item_id, status, confidence)"
                " VALUES (?, ?, 'pending_review', ?)",
                (txn_id, item_id, confidence)
            )
            assigned += 1

        logger.info(f"AI categorization: assigned {assigned}/{len(txns)} transactions")

    except Exception as e:
        logger.error(f"AI categorization failed (non-fatal): {e}")


def _auto_categorize_new(conn, transaction_ids: list[str]):
    """Match newly synced transactions against budget_auto_rules and insert
    pending_review assignments. Transactions with no rule match are then sent
    to Claude Haiku for AI-powered categorization (second pass).

    Pass 1 — Rule-based: uses match_count to derive a confidence score:
      ≥ 10 matches → 95%  (very reliable, seen many times)
      5–9 matches  → 85%  (reliable)
      2–4 matches  → 70%  (seen a few times)
      1 match      → 55%  (seen once — low confidence)

    Pass 2 — AI fallback: transactions still unassigned after the rule pass
      are batched into a single Claude Haiku call which returns item_id +
      confidence for each. Results are inserted as pending_review so the user
      can approve or correct them in the Pending tab.

    Only transactions with no existing assignment are processed. Transactions
    skipped by both passes remain unassigned and appear in the New tab.
    """
    if not transaction_ids:
        return

    # Load all auto-rules ordered by match_count desc so the highest-confidence
    # rule for each merchant wins in the event of multiple rules per merchant.
    rules: dict[str, tuple[int, int]] = {}  # merchant → (item_id, match_count)
    for row in conn.execute(
        "SELECT merchant, item_id, match_count FROM budget_auto_rules"
        " WHERE item_id IS NOT NULL ORDER BY match_count DESC"
    ).fetchall():
        if row["merchant"] not in rules:
            rules[row["merchant"]] = (row["item_id"], row["match_count"])

    for txn_id in transaction_ids:
        # Skip if already assigned — check both assignment types:
        # 1. transaction_assignments: single-item direct assignment
        # 2. transaction_splits: multi-item split (e.g. $100 → $60 food + $40 household)
        # Without this dual check, split transactions reappear as pending after
        # a cursor-reset resync because they have no transaction_assignments row.
        existing = conn.execute(
            "SELECT 1 FROM transaction_assignments WHERE transaction_id = ? "
            "UNION ALL "
            "SELECT 1 FROM transaction_splits WHERE transaction_id = ?",
            (txn_id, txn_id)
        ).fetchone()
        if existing:
            continue

        txn = conn.execute(
            "SELECT COALESCE(merchant_name, name, '') AS merchant, budget_deleted"
            " FROM transactions WHERE transaction_id = ?",
            (txn_id,)
        ).fetchone()
        if not txn or txn["budget_deleted"]:
            continue

        merchant = txn["merchant"].strip()
        if merchant not in rules:
            continue

        item_id, match_count = rules[merchant]

        # Confidence score based on how many times we've seen this merchant→item pair
        if match_count >= 10:
            confidence = 95
        elif match_count >= 5:
            confidence = 85
        elif match_count >= 2:
            confidence = 70
        else:
            confidence = 55

        conn.execute(
            "INSERT OR IGNORE INTO transaction_assignments"
            " (transaction_id, item_id, status, confidence)"
            " VALUES (?, ?, 'pending_review', ?)",
            (txn_id, item_id, confidence)
        )

    # Pass 2: AI categorization for transactions still unassigned after the rule pass.
    # Re-check both tables since Pass 1 above may have created assignments.
    remaining_ids = []
    for txn_id in transaction_ids:
        assigned = conn.execute(
            "SELECT 1 FROM transaction_assignments WHERE transaction_id = ? "
            "UNION ALL "
            "SELECT 1 FROM transaction_splits WHERE transaction_id = ?",
            (txn_id, txn_id)
        ).fetchone()
        if not assigned:
            remaining_ids.append(txn_id)
    if remaining_ids:
        logger.info(f"Rule-based pass left {len(remaining_ids)} unmatched — sending to AI")
        _ai_categorize_unmatched(conn, remaining_ids)

logger = logging.getLogger("vaultic.sync")


def _get_plaid_client():
    """Wrapper for shared Plaid client factory."""
    return get_plaid_client()


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
                     check_number, logo_url, website, transaction_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    transaction_code = excluded.transaction_code
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
            ))

        for txn in removed:
            conn.execute(
                "DELETE FROM transactions WHERE transaction_id = ?", (txn.transaction_id,)
            )

        # Auto-categorize newly added transactions using learned merchant rules.
        # Matched transactions get status='pending_review' so the user can approve
        # or correct them in the Pending tab — they count toward spending totals
        # immediately but are visually distinct until confirmed.
        new_ids = [t.transaction_id for t in added if not int(getattr(t, "pending", 0))]
        if new_ids:
            before_count = conn.execute(
                "SELECT COUNT(*) FROM transaction_assignments WHERE status='pending_review'"
            ).fetchone()[0]

            _auto_categorize_new(conn, new_ids)

            after_count = conn.execute(
                "SELECT COUNT(*) FROM transaction_assignments WHERE status='pending_review'"
            ).fetchone()[0]

            # Send push notification if new pending_review assignments were created.
            # Imported here to avoid circular imports at module load time.
            newly_pending = after_count - before_count
            if newly_pending > 0:
                try:
                    from api.push import notify_pending_review
                    notify_pending_review(newly_pending)
                except Exception as push_err:
                    # Never let push failures interrupt the sync pipeline
                    logger.warning(f"Push notification failed (non-fatal): {push_err}")


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
                     institution_price_as_of, quantity, cost_basis, iso_currency_code, snapped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, security_id, snapped_at) DO UPDATE SET
                    institution_value       = excluded.institution_value,
                    institution_price       = excluded.institution_price,
                    institution_price_as_of = excluded.institution_price_as_of,
                    quantity                = excluded.quantity,
                    cost_basis              = excluded.cost_basis
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
                         quantity, amount, fees, type, subtype, cancel_transaction_id, iso_currency_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(investment_transaction_id) DO UPDATE SET
                        amount                = excluded.amount,
                        fees                  = excluded.fees,
                        cancel_transaction_id = excluded.cancel_transaction_id
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
                "SELECT current FROM account_balances WHERE account_id = ? AND snapped_at = ?",
                (acct["id"], today),
            ).fetchone()
            if not row or row["current"] is None:
                row = conn.execute(
                    "SELECT current FROM account_balances WHERE account_id = ? "
                    "AND current IS NOT NULL ORDER BY snapped_at DESC LIMIT 1",
                    (acct["id"],),
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
