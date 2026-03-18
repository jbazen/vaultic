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
            LEFT JOIN account_balances b ON b.account_id = a.id
                AND b.snapped_at = (
                    SELECT MAX(snapped_at) FROM account_balances
                    WHERE account_id = a.id
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
    body: dict,
    _user: str = Depends(get_current_user),
):
    """
    Save a custom user-written description/note for any account.
    Notes are displayed inline on the Dashboard and Accounts pages.
    Empty string is stored as NULL (clears the note).
    Max 200 characters to keep display clean.
    """
    notes = str(body.get("notes", "")).strip()[:200]
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
            JOIN accounts a ON a.id = t.account_id
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
    Daily total value of all Plaid investment/retirement accounts combined.
    Used for the Portfolio Performance chart on the Dashboard.
    Returns ASC (oldest first) with each row: snapped_at, total_value.
    Only includes active accounts with type='investment'.
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT b.snapped_at, SUM(b.current) AS total_value
            FROM account_balances b
            JOIN accounts a ON a.id = b.account_id
            WHERE a.type = 'investment'
              AND a.is_active = 1
              AND b.snapped_at >= date('now', '-' || ? || ' days')
            GROUP BY b.snapped_at
            ORDER BY b.snapped_at ASC
        """, (days,)).fetchall()
    return [dict(row) for row in rows]


@router.get("/{account_id}/balances")
async def balance_history(
    account_id: int,
    days: int = Query(default=90, le=1825),
    _user: str = Depends(get_current_user),
):
    """Balance history for one account. Returns ASC (oldest first) for chart rendering."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT snapped_at, current, available
            FROM account_balances
            WHERE account_id = ?
              AND snapped_at >= date('now', '-' || ? || ' days')
            ORDER BY snapped_at ASC
        """, (account_id, days)).fetchall()
    return [dict(row) for row in rows]


@router.get("/{account_id}/transactions")
async def transactions(
    account_id: int,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0),
    _user: str = Depends(get_current_user),
):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT transaction_id, amount, date, name, merchant_name, category, pending
            FROM transactions
            WHERE account_id = ?
            ORDER BY date DESC
            LIMIT ? OFFSET ?
        """, (account_id, limit, offset)).fetchall()
    return [dict(row) for row in rows]


@router.get("/{account_id}/holdings")
async def account_holdings(
    account_id: int,
    _user: str = Depends(get_current_user),
):
    """
    Current Plaid investment holdings for one account, joined with security
    metadata. Includes computed gain/loss and pct_assets relative to account total.
    Returns the most recent snapshot date's holdings.
    """
    with get_db() as conn:
        acct = conn.execute(
            "SELECT id FROM accounts WHERE id = ? AND is_active = 1", (account_id,)
        ).fetchone()
        if not acct:
            raise HTTPException(status_code=404, detail="Account not found")

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
            WHERE h.account_id = ?
              AND h.snapped_at = (
                  SELECT MAX(snapped_at) FROM plaid_holdings WHERE account_id = ?
              )
            ORDER BY h.institution_value DESC NULLS LAST
        """, (account_id, account_id)).fetchall()

        total_row = conn.execute("""
            SELECT COALESCE(SUM(institution_value), 0) AS total
            FROM plaid_holdings
            WHERE account_id = ?
              AND snapped_at = (SELECT MAX(snapped_at) FROM plaid_holdings WHERE account_id = ?)
        """, (account_id, account_id)).fetchone()
        total_value = total_row["total"] if total_row else 0.0

    holdings = []
    for row in rows:
        h = dict(row)
        # Compute percentage of account total
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
        rows = conn.execute("""
            SELECT snapped_at, institution_price, institution_value, quantity, cost_basis
            FROM plaid_holdings
            WHERE account_id = ? AND security_id = ?
              AND snapped_at >= date('now', '-' || ? || ' days')
            ORDER BY snapped_at ASC
        """, (account_id, security_id, days)).fetchall()
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
            WHERE it.account_id = ?
            ORDER BY it.date DESC
            LIMIT ? OFFSET ?
        """, (account_id, limit, offset)).fetchall()
    return [dict(row) for row in rows]
