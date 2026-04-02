from datetime import date
from fastapi import APIRouter, Depends, Query
from api.dependencies import get_current_user, admin_required
from api.database import get_db

router = APIRouter(prefix="/api/net-worth", tags=["net-worth"])


def _get_mortgage(conn) -> float:
    """Return the total of all non-excluded other_liability manual entries.
    Uses abs() + SUM to match sync.py's _sum_manual("other_liability") semantics —
    PDF-imported mortgages may be stored as negative values."""
    row = conn.execute(
        "SELECT COALESCE(SUM(ABS(value)), 0) FROM manual_entries "
        "WHERE category = 'other_liability' "
        "AND (exclude_from_net_worth IS NULL OR exclude_from_net_worth = 0)"
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


@router.post("/refresh")
async def refresh_snapshot(_user: str = Depends(get_current_user)):
    """Force a fresh net worth snapshot using current Plaid balances + manual entries."""
    from api import sync
    today = date.today().isoformat()
    sync._take_net_worth_snapshot(today)
    # Return the newly computed snapshot
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT 1"
        ).fetchone()
        d = dict(row) if row else {}
        mortgage = _get_mortgage(conn)
    d["investable"] = _investable(d, mortgage)
    return d


@router.get("/debug")
async def debug_breakdown(_user: str = Depends(get_current_user)):
    """Full breakdown of investable net worth — every Plaid account and manual entry
    that contributes to the current snapshot, plus a live recalculation."""
    today = date.today().isoformat()
    with get_db() as conn:
        # Latest stored snapshot
        snap = conn.execute(
            "SELECT * FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT 1"
        ).fetchone()
        snap_dict = dict(snap) if snap else {}

        # Plaid accounts — live balances for today
        plaid_accounts = conn.execute("""
            SELECT a.id, a.name, a.display_name, a.type, a.subtype,
                   a.institution_name, b.current, b.snapped_at
            FROM accounts a
            LEFT JOIN account_balances b ON b.account_id = a.id AND b.snapped_at = ?
            WHERE a.is_active = 1 AND a.is_manual = 0
        """, (today,)).fetchall()

        plaid_detail = []
        plaid_liquid = plaid_invested = plaid_crypto = plaid_liabilities = 0.0
        for acct in plaid_accounts:
            a = dict(acct)
            bal = a.get("current") or 0
            t, s = a["type"], (a["subtype"] or "")
            bucket = "skipped"
            if bal == 0 and a.get("current") is None:
                bucket = "no_balance_today"
            elif t == "crypto":
                plaid_crypto += bal
                bucket = "crypto"
            elif t == "depository" and s in ("checking", "savings", "money market", "paypal", "prepaid"):
                plaid_liquid += bal
                bucket = "liquid"
            elif t == "investment" or s in ("401k", "ira", "roth", "pension"):
                plaid_invested += bal
                bucket = "invested"
            elif t in ("credit", "loan"):
                plaid_liabilities += bal
                bucket = "liabilities"
            else:
                plaid_liquid += bal
                bucket = "liquid (catch-all)"
            a["bucket"] = bucket
            a["contributed"] = bal
            plaid_detail.append(a)

        # Manual entries
        manual_rows = conn.execute(
            "SELECT id, name, category, value, exclude_from_net_worth, account_number "
            "FROM manual_entries ORDER BY category, name"
        ).fetchall()
        manual_detail = [dict(r) for r in manual_rows]

        manual_invested = manual_liquid = manual_crypto = manual_other_assets = 0.0
        manual_liabilities = manual_real_estate = manual_vehicles = 0.0
        for m in manual_detail:
            if m.get("exclude_from_net_worth"):
                m["bucket"] = "excluded"
                continue
            cat = m["category"]
            val = m.get("value") or 0
            if cat == "invested":
                manual_invested += val
                m["bucket"] = "invested"
            elif cat == "liquid":
                manual_liquid += val
                m["bucket"] = "liquid"
            elif cat == "crypto":
                manual_crypto += val
                m["bucket"] = "crypto"
            elif cat == "other_asset":
                manual_other_assets += val
                m["bucket"] = "other_asset"
            elif cat == "other_liability":
                manual_liabilities += abs(val)
                m["bucket"] = "liabilities"
            elif cat == "home_value":
                manual_real_estate += val
                m["bucket"] = "real_estate"
            elif cat == "car_value":
                manual_vehicles += val
                m["bucket"] = "vehicles"
            else:
                m["bucket"] = cat

        mortgage = _get_mortgage(conn)

    # Live recalculation (matches sync.py _take_net_worth_snapshot logic)
    live_liquid = plaid_liquid + manual_liquid
    live_invested = plaid_invested + manual_invested
    live_crypto = plaid_crypto + manual_crypto
    live_other_assets = manual_other_assets
    live_real_estate = manual_real_estate
    live_vehicles = manual_vehicles
    live_liabilities = plaid_liabilities + manual_liabilities
    live_total = live_liquid + live_invested + live_crypto + live_real_estate + live_vehicles + live_other_assets - live_liabilities
    live_credit_liabilities = max(0.0, live_liabilities - mortgage)
    live_investable = live_liquid + live_invested + live_crypto + live_other_assets - live_credit_liabilities

    # Stored snapshot investable
    stored_investable = _investable(snap_dict, mortgage) if snap_dict else None

    return {
        "snapshot": snap_dict,
        "stored_investable": stored_investable,
        "live_calculation": {
            "plaid_liquid": plaid_liquid,
            "plaid_invested": plaid_invested,
            "plaid_crypto": plaid_crypto,
            "plaid_liabilities": plaid_liabilities,
            "manual_liquid": manual_liquid,
            "manual_invested": manual_invested,
            "manual_crypto": manual_crypto,
            "manual_other_assets": manual_other_assets,
            "manual_liabilities": manual_liabilities,
            "manual_real_estate": manual_real_estate,
            "manual_vehicles": manual_vehicles,
            "mortgage": mortgage,
            "credit_liabilities": live_credit_liabilities,
            "total_liquid": live_liquid,
            "total_invested": live_invested,
            "total_crypto": live_crypto,
            "total_real_estate": live_real_estate,
            "total_vehicles": live_vehicles,
            "total_liabilities": live_liabilities,
            "total": live_total,
            "investable": live_investable,
        },
        "discrepancy": round(live_investable - stored_investable, 2) if stored_investable is not None else None,
        "plaid_accounts": plaid_detail,
        "manual_entries": manual_detail,
    }
