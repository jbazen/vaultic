"""
Account endpoints: list, rename, notes, balance history, transactions,
Plaid investment holdings, and investment transaction history.

All endpoints require a valid JWT (via get_current_user dependency).
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from api.dependencies import get_current_user
from api.database import get_db

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountNotesBody(BaseModel):
    notes: str = ""


def _display(name: str, display_name: str | None, mask: str | None) -> str:
    """Build the display label: '{display_name or name} (...{mask})'"""
    label = display_name if display_name else name
    if mask:
        return f"{label} (...{mask})"
    return label


@router.get("")
async def list_accounts(_user: str = Depends(get_current_user)):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                a.id, a.name, a.display_name, a.mask, a.official_name,
                a.type, a.subtype, a.institution_name, a.is_manual,
                a.plaid_account_id AS coinbase_uuid,
                a.source, a.notes,
                b.current, b.available, b.limit_amount,
                b.native_balance, b.unit_price,
                b.snapped_at
            FROM accounts a
            LEFT JOIN account_balances b ON b.account_number = a.account_number
                AND b.snapped_at = (
                    SELECT MAX(snapped_at) FROM account_balances
                    WHERE account_number = a.account_number
                )
            WHERE a.is_active = 1
            ORDER BY a.institution_name, a.name
        """).fetchall()
    result = []
    for row in rows:
        r = dict(row)
        r["label"] = _display(r["name"], r["display_name"], r["mask"])
        result.append(r)
    return result


class RenameRequest(BaseModel):
    display_name: str


@router.patch("/{account_id}/rename")
async def rename_account(
    account_id: int,
    body: RenameRequest,
    _user: str = Depends(get_current_user),
):
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="display_name cannot be empty")
    with get_db() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        conn.execute(
            "UPDATE accounts SET display_name = ? WHERE id = ?", (name, account_id)
        )
    return {"status": "renamed"}


@router.patch("/{account_id}/notes")
async def update_account_notes(
    account_id: int,
    body: AccountNotesBody,
    _user: str = Depends(get_current_user),
):
    """
    Save a custom user-written description/note for any account.
    Notes are displayed inline on the Dashboard and Accounts pages.
    Empty string is stored as NULL (clears the note).
    Max 200 characters to keep display clean.
    """
    notes = body.notes.strip()[:200]
    with get_db() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        # Store empty string as NULL so the frontend can distinguish "no note" from ""
        conn.execute("UPDATE accounts SET notes = ? WHERE id = ?", (notes or None, account_id))
    return {"notes": notes or None}


@router.get("/transactions/recent")
async def recent_transactions(
    limit: int = Query(default=50, le=500),
    _user: str = Depends(get_current_user),
):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                t.transaction_id, t.amount, t.date, t.name,
                t.merchant_name, t.category, t.pending,
                a.name AS account_name, a.display_name, a.mask, a.institution_name
            FROM transactions t
            JOIN accounts a ON a.account_number = t.account_number
            WHERE a.is_active = 1
            ORDER BY t.date DESC
            LIMIT ?
        """, (limit,)).fetchall()
    result = []
    for row in rows:
        r = dict(row)
        r["account_label"] = _display(r["account_name"], r["display_name"], r["mask"])
        result.append(r)
    return result


@router.get("/portfolio/performance")
async def portfolio_performance(
    days: int = Query(default=365, le=1825),
    _user: str = Depends(get_current_user),
):
    """
    Daily total value of all investment/retirement accounts combined.
    Used for the Portfolio Performance chart on the Dashboard.
    Returns ASC (oldest first) with each row: snapped_at, total_value.

    Includes:
      - Plaid investment accounts (daily balances from account_balances)
      - PDF-imported manual entries (daily snapshots from manual_entry_snapshots
        when available; falls back to current manual_entries values as a constant
        baseline when historical snapshots don't exist yet)
    """
    with get_db() as conn:
        # Current total of manual invested entries (constant baseline until
        # enough PDF imports build up snapshot history)
        manual_total = conn.execute("""
            SELECT COALESCE(SUM(value), 0) AS total
            FROM manual_entries
            WHERE category = 'invested' AND exclude_from_net_worth = 0
        """).fetchone()["total"]

        # Check if real snapshot history exists for manual entries
        has_snapshots = conn.execute("""
            SELECT 1 FROM manual_entry_snapshots s
            JOIN manual_entries m ON (
                (m.account_number IS NOT NULL AND m.account_number != '' AND
                 SUBSTR(m.account_number, -4) = SUBSTR(s.account_number, -4))
                OR m.name = s.name
            ) AND m.category = s.category
            WHERE s.category = 'invested' AND m.exclude_from_net_worth = 0
            LIMIT 1
        """).fetchone()

        if has_snapshots:
            # Use real daily snapshots when available
            rows = conn.execute("""
                SELECT snapped_at, SUM(value) AS total_value
                FROM (
                    SELECT b.snapped_at, b.current AS value
                    FROM account_balances b
                    JOIN accounts a ON a.account_number = b.account_number
                    WHERE a.type = 'investment' AND a.is_active = 1
                      AND b.snapped_at >= date('now', '-' || ? || ' days')
                    UNION ALL
                    SELECT s.snapped_at, s.value
                    FROM manual_entry_snapshots s
                    JOIN manual_entries m ON (
                        (m.account_number IS NOT NULL AND m.account_number != '' AND
                         SUBSTR(m.account_number, -4) = SUBSTR(s.account_number, -4))
                        OR m.name = s.name
                    ) AND m.category = s.category
                    WHERE s.category = 'invested' AND m.exclude_from_net_worth = 0
                      AND s.snapped_at >= date('now', '-' || ? || ' days')
                )
                GROUP BY snapped_at ORDER BY snapped_at ASC
            """, (days, days)).fetchall()
        else:
            # No snapshot history yet — add manual entries as constant baseline
            rows = conn.execute("""
                SELECT b.snapped_at,
                       SUM(b.current) + ? AS total_value
                FROM account_balances b
                JOIN accounts a ON a.account_number = b.account_number
                WHERE a.type = 'investment' AND a.is_active = 1
                  AND b.snapped_at >= date('now', '-' || ? || ' days')
                GROUP BY b.snapped_at
                ORDER BY b.snapped_at ASC
            """, (manual_total, days)).fetchall()

    return [dict(row) for row in rows]


@router.get("/{account_id}/balances")
async def balance_history(
    account_id: int,
    days: int = Query(default=90, le=1825),
    _user: str = Depends(get_current_user),
):
    """Balance history for one account. Returns ASC (oldest first) for chart rendering."""
    with get_db() as conn:
        acct = conn.execute(
            "SELECT account_number FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not acct or not acct["account_number"]:
            return []
        rows = conn.execute("""
            SELECT snapped_at, current, available
            FROM account_balances
            WHERE account_number = ?
              AND snapped_at >= date('now', '-' || ? || ' days')
            ORDER BY snapped_at ASC
        """, (acct["account_number"], days)).fetchall()
    return [dict(row) for row in rows]


@router.get("/{account_id}/transactions")
async def transactions(
    account_id: int,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0),
    _user: str = Depends(get_current_user),
):
    with get_db() as conn:
        acct = conn.execute(
            "SELECT account_number FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not acct or not acct["account_number"]:
            return []
        rows = conn.execute("""
            SELECT transaction_id, amount, date, name, merchant_name, category, pending
            FROM transactions
            WHERE account_number = ?
            ORDER BY date DESC
            LIMIT ? OFFSET ?
        """, (acct["account_number"], limit, offset)).fetchall()
    return [dict(row) for row in rows]


@router.get("/{account_id}/holdings")
async def account_holdings(
    account_id: int,
    _user: str = Depends(get_current_user),
):
    """
    Current investment holdings for one account.
    Checks Plaid holdings first, then falls back to I360 holdings
    for accounts sourced from Investor360.
    """
    with get_db() as conn:
        acct = conn.execute(
            "SELECT id, source, account_number FROM accounts WHERE id = ? AND is_active = 1",
            (account_id,),
        ).fetchone()
        if not acct:
            raise HTTPException(status_code=404, detail="Account not found")

        acct_num = acct["account_number"]

        # Try I360 holdings for investor360 accounts
        if acct["source"] == "investor360":
            return _i360_holdings(conn, acct_num)

        rows = conn.execute("""
            SELECT
                h.security_id,
                s.name,
                s.ticker_symbol,
                s.type          AS security_type,
                s.cusip,
                s.isin,
                h.institution_value,
                h.institution_price,
                h.institution_price_as_of,
                h.quantity,
                h.cost_basis,
                h.iso_currency_code,
                h.snapped_at,
                CASE
                    WHEN h.cost_basis IS NOT NULL AND h.cost_basis > 0
                    THEN h.institution_value - h.cost_basis
                    ELSE NULL
                END AS gain_loss_dollars,
                CASE
                    WHEN h.cost_basis IS NOT NULL AND h.cost_basis > 0
                    THEN ((h.institution_value - h.cost_basis) / h.cost_basis) * 100
                    ELSE NULL
                END AS gain_loss_pct
            FROM plaid_holdings h
            LEFT JOIN plaid_securities s ON s.security_id = h.security_id
            WHERE h.account_number = ?
              AND h.snapped_at = (
                  SELECT MAX(snapped_at) FROM plaid_holdings WHERE account_number = ?
              )
            ORDER BY h.institution_value DESC NULLS LAST
        """, (acct_num, acct_num)).fetchall()

        total_row = conn.execute("""
            SELECT COALESCE(SUM(institution_value), 0) AS total
            FROM plaid_holdings
            WHERE account_number = ?
              AND snapped_at = (SELECT MAX(snapped_at) FROM plaid_holdings WHERE account_number = ?)
        """, (acct_num, acct_num)).fetchone()
        total_value = total_row["total"] if total_row else 0.0

    holdings = []
    for row in rows:
        h = dict(row)
        if total_value and h.get("institution_value"):
            h["pct_assets"] = (h["institution_value"] / total_value) * 100
        else:
            h["pct_assets"] = None
        holdings.append(h)

    return {"holdings": holdings, "total_value": total_value}


def _i360_holdings(conn, account_number: str) -> dict:
    """Return I360 holdings in the same shape as Plaid holdings."""
    rows = conn.execute("""
        SELECT symbol AS ticker_symbol, description AS name,
               value_dollars AS institution_value, price AS institution_price,
               quantity, cusip, asset_category AS security_type,
               est_tax_cost_dollars AS cost_basis, snapped_at,
               CASE
                   WHEN est_tax_cost_dollars IS NOT NULL AND est_tax_cost_dollars > 0
                   THEN value_dollars - est_tax_cost_dollars
                   ELSE NULL
               END AS gain_loss_dollars,
               CASE
                   WHEN est_tax_cost_dollars IS NOT NULL AND est_tax_cost_dollars > 0
                   THEN ((value_dollars - est_tax_cost_dollars) / est_tax_cost_dollars) * 100
                   ELSE NULL
               END AS gain_loss_pct
        FROM i360_holdings
        WHERE account_number = ?
          AND snapped_at = (SELECT MAX(snapped_at) FROM i360_holdings WHERE account_number = ?)
        ORDER BY value_dollars DESC NULLS LAST
    """, (account_number, account_number)).fetchall()

    total_value = sum(r["institution_value"] or 0 for r in rows)
    holdings = []
    for row in rows:
        h = dict(row)
        h["security_id"] = None
        h["isin"] = None
        h["institution_price_as_of"] = None
        h["iso_currency_code"] = "USD"
        if total_value and h.get("institution_value"):
            h["pct_assets"] = (h["institution_value"] / total_value) * 100
        else:
            h["pct_assets"] = None
        holdings.append(h)

    return {"holdings": holdings, "total_value": total_value}


@router.get("/{account_id}/holdings/history")
async def holdings_history(
    account_id: int,
    security_id: str = Query(...),
    days: int = Query(default=90, le=730),
    _user: str = Depends(get_current_user),
):
    """
    Daily price/value history for a single holding within an account.
    Useful for plotting the performance of an individual position over time.
    """
    with get_db() as conn:
        acct = conn.execute(
            "SELECT account_number FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not acct or not acct["account_number"]:
            return []
        rows = conn.execute("""
            SELECT snapped_at, institution_price, institution_value, quantity, cost_basis
            FROM plaid_holdings
            WHERE account_number = ? AND security_id = ?
              AND snapped_at >= date('now', '-' || ? || ' days')
            ORDER BY snapped_at ASC
        """, (acct["account_number"], security_id, days)).fetchall()
    return [dict(row) for row in rows]


@router.get("/{account_id}/investment-transactions")
async def investment_transactions(
    account_id: int,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0),
    _user: str = Depends(get_current_user),
):
    """
    Investment transaction history (buy/sell/dividend/contribution/transfer)
    for one account, joined with security name and ticker for display.
    """
    with get_db() as conn:
        acct = conn.execute(
            "SELECT account_number FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not acct or not acct["account_number"]:
            return []
        rows = conn.execute("""
            SELECT
                it.investment_transaction_id,
                it.date,
                it.name,
                it.type,
                it.subtype,
                it.quantity,
                it.amount,
                it.fees,
                it.iso_currency_code,
                it.cancel_transaction_id,
                s.name          AS security_name,
                s.ticker_symbol AS ticker
            FROM plaid_investment_transactions it
            LEFT JOIN plaid_securities s ON s.security_id = it.security_id
            WHERE it.account_number = ?
            ORDER BY it.date DESC
            LIMIT ? OFFSET ?
        """, (acct["account_number"], limit, offset)).fetchall()
    return [dict(row) for row in rows]
