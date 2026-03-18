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
    # Investable = gross financial assets only (liquid + invested + crypto + other_assets).
    # Intentionally excludes real estate and vehicles (illiquid, can't be redeployed easily).
    # Also excludes liabilities: a mortgage balance is offset by the home value already
    # captured in real_estate — subtracting it here would incorrectly reduce investable assets.
    d["investable"] = (
        (d.get("liquid") or 0) +
        (d.get("invested") or 0) +
        (d.get("crypto") or 0) +
        (d.get("other_assets") or 0)
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
    - Each row includes `investable` = liquid + invested + crypto + other_assets
      (same formula as /latest — excludes real estate, vehicles, and liabilities)
    - Max range: 3650 days (10 years)
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT snapped_at, total, liquid, invested, crypto, real_estate,
                   vehicles, liabilities, other_assets,
                   (COALESCE(liquid,0) + COALESCE(invested,0) + COALESCE(crypto,0) + COALESCE(other_assets,0)) AS investable
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
