"""Manual entries: home value, car value, credit score, custom assets/liabilities, PDF-imported investments."""
import json
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from api.dependencies import get_current_user
from api.database import get_db
from api import sync

router = APIRouter(prefix="/api/manual", tags=["manual"])

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
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category must be one of {sorted(VALID_CATEGORIES)}")
    entered_at = body.entered_at or date.today().isoformat()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO manual_entries (name, category, value, notes, entered_at)
            VALUES (?, ?, ?, ?, ?)
        """, (body.name, body.category, body.value, body.notes, entered_at))
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


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, _user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM manual_entries WHERE id = ?", (entry_id,))
    return {"status": "deleted"}
