"""Manual entries: home value, car value, credit score, custom assets/liabilities, PDF-imported investments."""
import json
import re
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from api.dependencies import get_current_user
from api.database import get_db
from api import sync

router = APIRouter(prefix="/api/manual", tags=["manual"])


class RenameEntryBody(BaseModel):
    name: str
    notes: str | None = None

VALID_CATEGORIES = {
    "home_value", "car_value", "credit_score",
    "other_asset", "other_liability",
    "invested", "liquid", "real_estate", "vehicles", "crypto",
}


class ManualEntryRequest(BaseModel):
    name: str
    category: str
    value: float
    notes: str | None = None
    entered_at: str | None = None
    account_number: str | None = None


@router.get("")
async def list_entries(_user: str = Depends(get_current_user)):
    """All manual entries with their holdings."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM manual_entries ORDER BY category, entered_at DESC"
        ).fetchall()
        entries = [dict(row) for row in rows]
        for entry in entries:
            holding_rows = conn.execute(
                "SELECT * FROM manual_holdings WHERE manual_entry_id = ? ORDER BY value DESC",
                (entry["id"],)
            ).fetchall()
            entry["holdings"] = [dict(h) for h in holding_rows]
            # Parse summary_json string back to object for the frontend
            if entry.get("summary_json"):
                try:
                    entry["activity_summary"] = json.loads(entry["summary_json"])
                except Exception:
                    entry["activity_summary"] = None
            else:
                entry["activity_summary"] = None
    return entries


@router.post("")
async def add_entry(body: ManualEntryRequest, _user: str = Depends(get_current_user)):
    """
    Create a new manual entry. Triggers a net worth snapshot immediately so the
    Dashboard reflects the new value without waiting for the nightly cron job.
    """
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category must be one of {sorted(VALID_CATEGORIES)}")
    entered_at = body.entered_at or date.today().isoformat()
    acct_num = re.sub(r"[^A-Z0-9]", "", body.account_number.upper()) if body.account_number else None

    # Categories that are inherently single-value (one entry per category, no account_number)
    SINGLETON_CATEGORIES = {"home_value", "car_value", "credit_score"}

    with get_db() as conn:
        existing = None
        if acct_num:
            existing = conn.execute(
                "SELECT id FROM manual_entries WHERE account_number = ?", (acct_num,)
            ).fetchone()
        if not existing and body.category in SINGLETON_CATEGORIES:
            existing = conn.execute(
                "SELECT id FROM manual_entries WHERE category = ? ORDER BY entered_at DESC LIMIT 1",
                (body.category,)
            ).fetchone()
        if existing:
            conn.execute("""
                UPDATE manual_entries
                SET name = ?, category = ?, value = ?, notes = ?, entered_at = ?
                WHERE id = ?
            """, (body.name, body.category, body.value, body.notes, entered_at, existing["id"]))
        else:
            conn.execute("""
                INSERT INTO manual_entries (name, category, value, notes, entered_at, account_number)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (body.name, body.category, body.value, body.notes, entered_at, acct_num or None))

        # Write to manual_entry_snapshots so balance history builds up over
        # time, same as PDF imports do. Keyed by account_number when available.
        if acct_num:
            conn.execute(
                "DELETE FROM manual_entry_snapshots WHERE account_number=? AND snapped_at=?",
                (acct_num, entered_at)
            )
        conn.execute("""
            INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(name, snapped_at) DO UPDATE SET
                value=excluded.value, account_number=excluded.account_number
        """, (body.name, acct_num, body.category, body.value, entered_at))
    try:
        sync._take_net_worth_snapshot(date.today().isoformat())
    except Exception:
        pass
    return {"status": "saved"}


@router.patch("/{entry_id}/exclude")
async def toggle_exclude(entry_id: int, _user: str = Depends(get_current_user)):
    """Toggle exclude_from_net_worth for an entry.
    Use case: a PDF import may produce both an "Overall Portfolio" summary entry AND
    separate per-account entries (e.g. IRA, college fund). The summary should display
    for reference but must be excluded from the net worth total to avoid double-counting
    the individual accounts that are already included.
    """
    with get_db() as conn:
        row = conn.execute("SELECT exclude_from_net_worth FROM manual_entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")
        new_val = 0 if row["exclude_from_net_worth"] else 1
        conn.execute("UPDATE manual_entries SET exclude_from_net_worth = ? WHERE id = ?", (new_val, entry_id))
    try:
        sync._take_net_worth_snapshot(date.today().isoformat())
    except Exception:
        pass
    return {"exclude_from_net_worth": new_val}


@router.patch("/{entry_id}/rename")
async def rename_entry(entry_id: int, body: RenameEntryBody, _user: str = Depends(get_current_user)):
    """Update name and/or notes on a manual entry."""
    name = body.name.strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    notes = body.notes
    notes_val = str(notes).strip()[:200] if notes is not None else None
    with get_db() as conn:
        row = conn.execute("SELECT id, notes FROM manual_entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")
        # Only update notes if caller passed it; otherwise preserve existing
        if notes is not None:
            conn.execute("UPDATE manual_entries SET name = ?, notes = ? WHERE id = ?", (name, notes_val or None, entry_id))
        else:
            conn.execute("UPDATE manual_entries SET name = ? WHERE id = ?", (name, entry_id))
    return {"name": name, "notes": notes_val}


@router.get("/{entry_id}/history")
async def get_entry_history(entry_id: int, days: int = 1825, _user: str = Depends(get_current_user)):
    """
    Return balance history for a PDF-imported manual entry from manual_entry_snapshots.
    The snapshots table accumulates one row per (name, date) on each PDF import —
    re-importing monthly builds a time-series we can plot as a performance chart.

    Returns [{snapped_at, current}] in ascending date order, same shape as
    /api/accounts/{id}/balances so the frontend BalanceChart component can render
    both Plaid-connected and PDF-imported accounts identically.
    """
    if days < 1 or days > 3650:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="days must be 1–3650")
    with get_db() as conn:
        row = conn.execute(
            "SELECT name, account_number FROM manual_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Entry not found")
        # Match snapshots by account_number when available — survives renames and
        # PDF name variations. Fall back to name for entries without account numbers.
        if row["account_number"]:
            # Match by exact account_number, or by last-4 digits as fallback
            # (account number format varies between NFS statements and Investor360 reports)
            last4 = row["account_number"][-4:] if len(row["account_number"]) >= 4 else row["account_number"]
            rows = conn.execute("""
                SELECT snapped_at, value AS current
                FROM manual_entry_snapshots
                WHERE (account_number = ? OR account_number LIKE ?)
                  AND snapped_at >= date('now', '-' || ? || ' days')
                ORDER BY snapped_at ASC
            """, (row["account_number"], f"%{last4}", days)).fetchall()
        else:
            rows = conn.execute("""
                SELECT snapped_at, value AS current
                FROM manual_entry_snapshots
                WHERE name = ? AND snapped_at >= date('now', '-' || ? || ' days')
                ORDER BY snapped_at ASC
            """, (row["name"], days)).fetchall()
    return [dict(r) for r in rows]


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, _user: str = Depends(get_current_user)):
    """
    Delete a manual entry. Associated holdings are removed automatically via
    the ON DELETE CASCADE constraint on manual_holdings.manual_entry_id.
    """
    with get_db() as conn:
        conn.execute("DELETE FROM manual_entries WHERE id = ?", (entry_id,))
    return {"ok": True}
