from fastapi import APIRouter, Depends, Query
from api.dependencies import get_current_user
from api.database import get_db

router = APIRouter(prefix="/api/net-worth", tags=["net-worth"])


def _get_mortgage(conn) -> float:
    """Return the most recent other_liability manual entry value (the mortgage balance).
    This is stored in the liabilities column of net_worth_snapshots alongside Plaid
    credit/loan balances, so we need it separately to compute investable correctly."""
    row = conn.execute(
        "SELECT COALESCE(value, 0) FROM manual_entries "
        "WHERE category = 'other_liability' "
        "AND (exclude_from_net_worth IS NULL OR exclude_from_net_worth = 0) "
        "ORDER BY entered_at DESC LIMIT 1"
    ).fetchone()
    return float(row[0]) if row else 0.0


def _investable(d: dict, mortgage: float) -> float:
    """Investable Net Worth = financial assets net of credit card debt, excluding home and car.

    Formula: liquid + invested + crypto + other_assets - credit_card_liabilities
    Where:   credit_card_liabilities = total_liabilities - mortgage

    We start with gross financial assets (no real_estate/vehicles) then subtract only
    revolving/credit liabilities. The mortgage is excluded because the home is already
    removed — we don't want to penalise investable for a loan that's backed by an asset
    we've already stripped out.
    """
    credit_liabilities = max(0.0, (d.get("liabilities") or 0) - mortgage)
    return (
        (d.get("liquid") or 0) +
        (d.get("invested") or 0) +
        (d.get("crypto") or 0) +
        (d.get("other_assets") or 0) -
        credit_liabilities
    )


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
        mortgage = _get_mortgage(conn)

    d["investable"] = _investable(d, mortgage)
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
    - Each row includes `investable` = liquid + invested + crypto + other_assets - credit_liabilities
      (gross financial assets minus credit card debt; mortgage excluded since home is already stripped)
    - Max range: 3650 days (10 years)
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT snapped_at, total, liquid, invested, crypto, real_estate,
                   vehicles, liabilities, other_assets
            FROM net_worth_snapshots
            WHERE snapped_at >= date('now', '-' || ? || ' days')
            ORDER BY snapped_at ASC
        """, (days,)).fetchall()
        mortgage = _get_mortgage(conn)

    data = [dict(row) for row in rows]
    for row in data:
        row["investable"] = _investable(row, mortgage)

    # Monthly aggregation when range > 90 days (keep last snapshot per month)
    if days > 90 and len(data) > 90:
        monthly = {}
        for row in data:
            month_key = row["snapped_at"][:7]  # "YYYY-MM"
            monthly[month_key] = row           # last day of month wins
        data = list(monthly.values())

    return data
