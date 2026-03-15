"""Manual entries: home value, car value, credit score, custom assets/liabilities."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from api.dependencies import get_current_user
from api.database import get_db
from api import sync

router = APIRouter(prefix="/api/manual", tags=["manual"])

VALID_CATEGORIES = {"home_value", "car_value", "credit_score", "other_asset", "other_liability"}


class ManualEntryRequest(BaseModel):
    name: str
    category: str
    value: float
    notes: str | None = None
    entered_at: str | None = None  # ISO date string; defaults to today


@router.get("")
async def list_entries(_user: str = Depends(get_current_user)):
    """Latest entry per category plus full history."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM manual_entries ORDER BY category, entered_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


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
    # Refresh net worth snapshot for today
    try:
        sync._take_net_worth_snapshot(date.today().isoformat())
    except Exception:
        pass
    return {"status": "saved"}


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, _user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM manual_entries WHERE id = ?", (entry_id,))
    return {"status": "deleted"}
