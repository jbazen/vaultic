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
                a.source,
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


@router.get("/{account_id}/balances")
async def balance_history(
    account_id: int,
    days: int = Query(default=90, le=365),
    _user: str = Depends(get_current_user),
):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT snapped_at, current, available
            FROM account_balances
            WHERE account_id = ?
            ORDER BY snapped_at DESC
            LIMIT ?
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
