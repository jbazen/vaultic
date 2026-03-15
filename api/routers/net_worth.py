from fastapi import APIRouter, Depends, Query
from api.dependencies import get_current_user
from api.database import get_db

router = APIRouter(prefix="/api/net-worth", tags=["net-worth"])


@router.get("/latest")
async def latest(_user: str = Depends(get_current_user)):
    """Most recent net worth snapshot."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"message": "No data yet — connect accounts and sync to build your first snapshot."}
    return dict(row)


@router.get("/history")
async def history(
    days: int = Query(default=365, le=1825),
    _user: str = Depends(get_current_user),
):
    """Net worth history for chart (default: 1 year)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT snapped_at, total, liquid, invested, crypto, real_estate,
                   vehicles, liabilities, other_assets
            FROM net_worth_snapshots
            ORDER BY snapped_at ASC
            LIMIT ?
        """, (days,)).fetchall()
    return [dict(row) for row in rows]
