"""Sinking funds tracker router.

A sinking fund is a named savings bucket with an optional target balance.
Money is added or removed via fund_transactions rows (positive = deposit,
negative = withdrawal). The current balance is always derived by summing
the transaction history — there is no stored 'balance' column.

Tables used (created by migration — not here):
  funds             — named savings buckets with optional target_amount
  fund_transactions — individual deposits and withdrawals per fund
"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from api.dependencies import get_current_user
from api.database import get_db

router = APIRouter(prefix="/api/funds", tags=["funds"])


# ---------------------------------------------------------------------------
# Pydantic request bodies
# ---------------------------------------------------------------------------

class FundCreate(BaseModel):
    name: str
    description: str | None = None
    target_amount: float | None = None


class FundTxnCreate(BaseModel):
    date: str | None = None      # defaults to today if omitted
    amount: float
    description: str | None = None


class FundUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    target_amount: float | None = None


# ---------------------------------------------------------------------------
# Helper: fetch a single fund row with its computed balance
# ---------------------------------------------------------------------------

def _get_fund_with_balance(conn, fund_id: int) -> dict:
    """Return the fund row merged with its live balance, or raise 404."""
    row = conn.execute(
        "SELECT id, name, description, target_amount, display_order FROM funds WHERE id = ? AND is_active = 1",
        (fund_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Fund not found")

    bal_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS balance FROM fund_transactions WHERE fund_id = ?",
        (fund_id,)
    ).fetchone()

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "target_amount": row["target_amount"],
        "balance": float(bal_row["balance"]),
        "display_order": row["display_order"],
    }


# ---------------------------------------------------------------------------
# GET / — list all active funds with live balances
# ---------------------------------------------------------------------------

@router.get("/")
async def list_funds(_user: str = Depends(get_current_user)):
    """List all active sinking funds with their current balance (sum of transactions)."""
    with get_db() as conn:
        funds = conn.execute(
            "SELECT id FROM funds WHERE is_active = 1 ORDER BY display_order, name"
        ).fetchall()

        return [_get_fund_with_balance(conn, f["id"]) for f in funds]


# ---------------------------------------------------------------------------
# POST / — create a new fund
# ---------------------------------------------------------------------------

@router.post("/")
async def create_fund(body: FundCreate, _user: str = Depends(get_current_user)):
    """Create a new sinking fund. target_amount is optional."""
    name = body.name.strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    description = body.description.strip()[:255] if body.description else None

    with get_db() as conn:
        # Place at the end of the display order among all funds (including inactive,
        # so display_order values remain stable if a fund is soft-deleted later)
        max_row = conn.execute("SELECT COALESCE(MAX(display_order), 0) AS m FROM funds").fetchone()
        order = max_row["m"] + 1

        cur = conn.execute(
            "INSERT INTO funds (name, description, target_amount, display_order, is_active) VALUES (?, ?, ?, ?, 1)",
            (name, description, body.target_amount, order)
        )
        fid = cur.lastrowid

    return {
        "id": fid,
        "name": name,
        "description": description,
        "target_amount": body.target_amount,
        "balance": 0.0,
        "display_order": order,
    }


# ---------------------------------------------------------------------------
# PATCH /{fund_id} — update fund metadata
# ---------------------------------------------------------------------------

@router.patch("/{fund_id}")
async def update_fund(fund_id: int, body: FundUpdate, _user: str = Depends(get_current_user)):
    """Update one or more fields on a fund (name, description, target_amount).

    Only keys present in the request body are updated; omitted keys keep
    their current values.
    """
    provided = body.model_fields_set
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name, description, target_amount FROM funds WHERE id = ? AND is_active = 1",
            (fund_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Fund not found")

        name = body.name.strip()[:100] if "name" in provided and body.name else row["name"]
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")

        description = (
            str(body.description).strip()[:255] if body.description else None
        ) if "description" in provided else row["description"]

        # target_amount can be explicitly set to None to clear it
        if "target_amount" in provided:
            target = body.target_amount
        else:
            target = row["target_amount"]

        conn.execute(
            "UPDATE funds SET name = ?, description = ?, target_amount = ? WHERE id = ?",
            (name, description, target, fund_id)
        )

        return _get_fund_with_balance(conn, fund_id)


# ---------------------------------------------------------------------------
# DELETE /{fund_id} — soft-delete a fund
# ---------------------------------------------------------------------------

@router.delete("/{fund_id}")
async def delete_fund(fund_id: int, _user: str = Depends(get_current_user)):
    """Mark a fund as inactive (soft delete). Transaction history is preserved.

    Hard DELETE is intentionally avoided so that historical fund transaction
    data is not lost. The fund simply stops appearing in GET /.
    """
    with get_db() as conn:
        row = conn.execute("SELECT id FROM funds WHERE id = ? AND is_active = 1", (fund_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Fund not found")
        conn.execute("UPDATE funds SET is_active = 0 WHERE id = ?", (fund_id,))
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /{fund_id}/transactions — transaction history for a fund
# ---------------------------------------------------------------------------

@router.get("/{fund_id}/transactions")
async def list_transactions(fund_id: int, _user: str = Depends(get_current_user)):
    """Return all transactions for a fund, newest first."""
    with get_db() as conn:
        # Confirm the fund exists before querying its transactions
        row = conn.execute("SELECT id FROM funds WHERE id = ? AND is_active = 1", (fund_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Fund not found")

        rows = conn.execute("""
            SELECT id, date, amount, description, created_at
            FROM fund_transactions
            WHERE fund_id = ?
            ORDER BY date DESC, created_at DESC
        """, (fund_id,)).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# POST /{fund_id}/transactions — add a deposit or withdrawal
# ---------------------------------------------------------------------------

@router.post("/{fund_id}/transactions")
async def add_transaction(fund_id: int, body: FundTxnCreate, _user: str = Depends(get_current_user)):
    """Add a transaction to a fund.

    Positive amount  = deposit (adding money to the fund).
    Negative amount  = withdrawal (spending from the fund).

    If date is omitted, today's date is used.
    """
    with get_db() as conn:
        row = conn.execute("SELECT id FROM funds WHERE id = ? AND is_active = 1", (fund_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Fund not found")

        txn_date = body.date if body.date else date.today().isoformat()
        description = body.description.strip()[:255] if body.description else None

        cur = conn.execute(
            "INSERT INTO fund_transactions (fund_id, date, amount, description) VALUES (?, ?, ?, ?)",
            (fund_id, txn_date, body.amount, description)
        )
        txn_id = cur.lastrowid

        # Fetch the full row so created_at is included in the response
        txn_row = conn.execute(
            "SELECT id, date, amount, description, created_at FROM fund_transactions WHERE id = ?",
            (txn_id,)
        ).fetchone()

    return dict(txn_row)


# ---------------------------------------------------------------------------
# DELETE /transactions/{txn_id} — remove a single transaction
# ---------------------------------------------------------------------------

@router.delete("/transactions/{txn_id}")
async def delete_transaction(txn_id: int, _user: str = Depends(get_current_user)):
    """Delete a fund transaction (corrects mistakes or reversals).

    The fund balance updates automatically since it is always computed from
    the remaining transaction rows.
    """
    with get_db() as conn:
        row = conn.execute("SELECT id FROM fund_transactions WHERE id = ?", (txn_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Transaction not found")
        conn.execute("DELETE FROM fund_transactions WHERE id = ?", (txn_id,))
    return {"ok": True}
