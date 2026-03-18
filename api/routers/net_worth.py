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
    d = dict(row)
    # Investable Net Worth = total net worth minus illiquid assets (home + car).
    # Because total already has liabilities subtracted, credit card debt and mortgage
    # are properly reflected here — giving a true "deployable wealth" number.
    d["investable"] = (
        (d.get("total") or 0) -
        (d.get("real_estate") or 0) -
        (d.get("vehicles") or 0)
    )
    return d


@router.get("/history")
async def history(
    days: int = Query(default=365, le=3650),
    _user: str = Depends(get_current_user),
):
    """
    Net worth history for chart.
    - Returns daily points for <= 90 days of data
    - Collapses to one point per month (last snapshot of each month) for longer ranges
    - Each row includes `investable` = total - real_estate - vehicles
      (same formula as /latest — net worth minus illiquid assets; liabilities already in total)
    - Max range: 3650 days (10 years)
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT snapped_at, total, liquid, invested, crypto, real_estate,
                   vehicles, liabilities, other_assets,
                   (COALESCE(total,0) - COALESCE(real_estate,0) - COALESCE(vehicles,0)) AS investable
            FROM net_worth_snapshots
            WHERE snapped_at >= date('now', '-' || ? || ' days')
            ORDER BY snapped_at ASC
        """, (days,)).fetchall()

    data = [dict(row) for row in rows]

    # Monthly aggregation when range > 90 days (keep last snapshot per month)
    if days > 90 and len(data) > 90:
        monthly = {}
        for row in data:
            month_key = row["snapped_at"][:7]  # "YYYY-MM"
            monthly[month_key] = row           # last day of month wins
        data = list(monthly.values())

    return data
