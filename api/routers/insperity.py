"""
Insperity 401K integration — sync retirement data via HTML scraping.

Endpoints for syncing 401K data from retirement.insperity.com,
retrieving stored snapshots, and monitoring sync health.
"""
import asyncio
import json
import logging
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_current_user
from api.database import get_db
from api.insperity_client import (
    InsperityClient,
    SessionExpiredError,
    PageChangedError,
    parse_curl_cookies,
)
from api.sync import _take_net_worth_snapshot

logger = logging.getLogger(__name__)

INSPERITY_ACCOUNT_NUMBER = "105001401K"
router = APIRouter(prefix="/api/insperity", tags=["insperity"])


# ── Request models ───────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    curl_command: str


# ── Helpers ──────────────────────────────────────────────────────────────────

def _today() -> date:
    return date.today()


def _store_holdings(conn, holdings: list[dict], snapped_at: date) -> int:
    """Store fund holdings snapshot. Returns count stored."""
    for h in holdings:
        conn.execute(
            """INSERT INTO insperity_holdings
               (snapped_at, fund, balance, shares, price, ratio_pct)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapped_at, fund)
               DO UPDATE SET balance=excluded.balance, shares=excluded.shares,
                            price=excluded.price, ratio_pct=excluded.ratio_pct""",
            (snapped_at.isoformat(), h["fund"], h["balance"],
             h["shares"], h["price"], h["ratio_pct"]),
        )
    return len(holdings)


def _store_performance(conn, perf: dict, snapped_at: date):
    """Store performance return periods.

    Insperity may return any subset of periods depending on account history
    (e.g. 1_month / 3_month / ytd in the full layout, or just 1_month and
    1_year in the limited-history layout). We store the union of period keys
    present in either section, with NULL for whichever side lacks that period.
    """
    cum = perf.get("cumulative") or {}
    ann = perf.get("annualized") or {}
    periods = sorted(set(cum) | set(ann))
    for period in periods:
        conn.execute(
            """INSERT INTO insperity_performance
               (snapped_at, period, cumulative, annualized)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(snapped_at, period)
               DO UPDATE SET cumulative=excluded.cumulative,
                            annualized=excluded.annualized""",
            (snapped_at.isoformat(), period, cum.get(period), ann.get(period)),
        )


def _store_contributions(conn, contrib: dict, snapped_at: date):
    """Store contribution rates and YTD amounts."""
    conn.execute(
        """INSERT INTO insperity_contributions
           (snapped_at, pretax_pct, roth_pct, ytd_employee, ytd_employer,
            last_ee_amount, last_er_amount, last_ee_date, last_er_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(snapped_at) DO UPDATE SET
             pretax_pct=excluded.pretax_pct, roth_pct=excluded.roth_pct,
             ytd_employee=excluded.ytd_employee, ytd_employer=excluded.ytd_employer,
             last_ee_amount=excluded.last_ee_amount, last_er_amount=excluded.last_er_amount,
             last_ee_date=excluded.last_ee_date, last_er_date=excluded.last_er_date""",
        (snapped_at.isoformat(),
         contrib["rates"].get("pretax_pct"),
         contrib["rates"].get("roth_pct"),
         contrib["ytd"].get("employee"),
         contrib["ytd"].get("employer"),
         contrib["last"].get("employee_amount"),
         contrib["last"].get("employer_amount"),
         contrib["last"].get("employee_date"),
         contrib["last"].get("employer_date")),
    )


def _store_activity(conn, activity: dict, snapped_at: date,
                    vested_balance: float | None = None):
    """Store activity summary and individual transactions."""
    conn.execute(
        """INSERT INTO insperity_activity
           (snapped_at, beginning_balance, ending_balance, contributions, vested_balance)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(snapped_at) DO UPDATE SET
             beginning_balance=excluded.beginning_balance,
             ending_balance=excluded.ending_balance,
             contributions=excluded.contributions,
             vested_balance=excluded.vested_balance""",
        (snapped_at.isoformat(), activity["beginning_balance"],
         activity["ending_balance"], activity["contributions"], vested_balance),
    )
    # Store individual transactions (contributions, fees, rebalances)
    for t in activity.get("transactions", []):
        conn.execute(
            """INSERT INTO insperity_transactions
               (snapped_at, txn_date, description, code, amount, shares, price)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapped_at, txn_date, description, code, amount)
               DO UPDATE SET shares=excluded.shares, price=excluded.price""",
            (snapped_at.isoformat(), t["date"], t["description"],
             t.get("code", ""), t.get("amount"), t.get("shares"), t.get("price")),
        )


def _store_allocations(conn, alloc_data: dict, snapped_at: date):
    """Store target contribution allocation percentages."""
    for a in alloc_data.get("allocations", []):
        conn.execute(
            """INSERT INTO insperity_allocations
               (snapped_at, fund, target_pct, asset_class)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(snapped_at, fund)
               DO UPDATE SET target_pct=excluded.target_pct,
                            asset_class=excluded.asset_class""",
            (snapped_at.isoformat(), a["fund"], a.get("target_pct"),
             a.get("asset_class")),
        )


def _store_prices(conn, prices: list[dict], snapped_at: date):
    """Store fund price snapshots."""
    for p in prices:
        conn.execute(
            """INSERT INTO insperity_prices
               (snapped_at, fund, asset_class, price, prior_price, change_pct)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapped_at, fund)
               DO UPDATE SET asset_class=excluded.asset_class,
                            price=excluded.price, prior_price=excluded.prior_price,
                            change_pct=excluded.change_pct""",
            (snapped_at.isoformat(), p["fund"], p["asset_class"],
             p["price"], p["prior_price"], p["change_pct"]),
        )


def _update_manual_entry(conn, total_balance: float | None, today: date):
    """Update the Insperity manual_entries row with latest balance.

    Called inside the sync transaction so the balance is committed atomically
    with the insperity_* table writes. The net worth snapshot is triggered
    separately AFTER this transaction closes (to avoid nested connections).
    """
    if total_balance is None:
        return
    conn.execute(
        "UPDATE manual_entries SET value = ?, entered_at = ? "
        "WHERE account_number = ? AND category = 'invested'",
        (total_balance, today.isoformat(), INSPERITY_ACCOUNT_NUMBER),
    )


# ── Sync endpoint ────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync(req: SyncRequest, user=Depends(get_current_user)):
    """Sync all data from Insperity using cookies from a cURL command."""
    start_time = time.time()

    # Parse cookies from cURL
    try:
        cookies = parse_curl_cookies(req.curl_command)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    client = InsperityClient(cookies)
    errors = []
    warnings = list(client.warnings)

    # Fire all page scrapes in parallel
    async def safe_call(name, coro):
        try:
            return name, await coro
        except SessionExpiredError as exc:
            errors.append(f"{name}: {exc}")
            return name, None
        except PageChangedError as exc:
            errors.append(f"{name}: {exc}")
            return name, None
        except ValueError as exc:
            # Parser validation failure — page structure changed
            errors.append(f"{name}: {exc}")
            return name, None
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            logger.exception("Insperity sync error for %s", name)
            return name, None

    results = await asyncio.gather(
        safe_call("summary", client.get_summary()),
        safe_call("holdings", client.get_holdings()),
        safe_call("performance", client.get_performance()),
        safe_call("contributions", client.get_contributions()),
        safe_call("allocations", client.get_allocations()),
        safe_call("activity", client.get_activity()),
        safe_call("prices", client.get_prices()),
    )

    data = {name: val for name, val in results}
    warnings.extend(client.warnings)

    # Determine status
    if all(v is None for v in data.values()):
        status = "failed"
    elif any(v is None for v in data.values()):
        status = "partial"
    else:
        status = "success"

    # Store everything
    today = _today()
    holdings_count = 0
    total_balance = None

    with get_db() as conn:
        if data.get("holdings"):
            holdings_count = _store_holdings(
                conn, data["holdings"]["holdings"], today
            )
            total_balance = data["holdings"]["total_balance"]

        if data.get("summary"):
            # Use summary balance if holdings didn't provide it
            if total_balance is None:
                total_balance = data["summary"]["account_balance"]

        if data.get("performance"):
            _store_performance(conn, data["performance"], today)

        if data.get("contributions"):
            _store_contributions(conn, data["contributions"], today)

        if data.get("activity"):
            vested = (data["summary"] or {}).get("vested_balance")
            _store_activity(conn, data["activity"], today, vested)

        if data.get("allocations"):
            _store_allocations(conn, data["allocations"], today)

        if data.get("prices"):
            _store_prices(conn, data["prices"], today)

        # Log the sync
        duration_ms = int((time.time() - start_time) * 1000)
        conn.execute(
            """INSERT INTO insperity_sync_log
               (status, holdings_count, total_balance, duration_ms, error, warnings)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (status, holdings_count, total_balance, duration_ms,
             "; ".join(errors) if errors else None,
             json.dumps(warnings) if warnings else None),
        )

        # Update manual entry balance (inside transaction)
        _update_manual_entry(conn, total_balance, today)

    # Snapshot after DB transaction closes (opens its own connection)
    if total_balance is not None:
        _take_net_worth_snapshot(today.isoformat())

    duration_ms = int((time.time() - start_time) * 1000)

    if status == "failed":
        raise HTTPException(
            status_code=502,
            detail=f"All Insperity pages failed: {'; '.join(errors)}",
        )

    return {
        "ok": True,
        "status": status,
        "holdings": holdings_count,
        "total_balance": total_balance,
        "duration_ms": duration_ms,
        "errors": errors if errors else None,
        "warnings": warnings if warnings else None,
    }


# ── Local sync endpoint (accepts pre-parsed data from local script) ──────────

class LocalSyncRequest(BaseModel):
    """Pre-parsed data from local sync script."""
    summary: dict | None = None
    holdings: dict | None = None
    performance: dict | None = None
    contributions: dict | None = None
    allocations: dict | None = None
    activity: dict | None = None
    prices: list | None = None


@router.post("/sync-local")
def sync_local(req: LocalSyncRequest, user=Depends(get_current_user)):
    """Store pre-parsed Insperity data from a local sync script.

    Used when the scrape must run from the user's machine (Cloudflare
    cookies are IP-bound and won't work from the server).
    """
    start_time = time.time()
    today = _today()
    holdings_count = 0
    total_balance = None
    errors = []

    with get_db() as conn:
        if req.holdings:
            try:
                holdings_count = _store_holdings(
                    conn, req.holdings["holdings"], today
                )
                total_balance = req.holdings.get("total_balance")
            except Exception as exc:
                errors.append(f"holdings: {exc}")

        if req.summary:
            if total_balance is None:
                total_balance = req.summary.get("account_balance")

        if req.performance:
            try:
                _store_performance(conn, req.performance, today)
            except Exception as exc:
                errors.append(f"performance: {exc}")

        if req.contributions:
            try:
                _store_contributions(conn, req.contributions, today)
            except Exception as exc:
                errors.append(f"contributions: {exc}")

        if req.activity:
            try:
                vested = (req.summary or {}).get("vested_balance")
                _store_activity(conn, req.activity, today, vested)
            except Exception as exc:
                errors.append(f"activity: {exc}")

        if req.allocations:
            try:
                _store_allocations(conn, req.allocations, today)
            except Exception as exc:
                errors.append(f"allocations: {exc}")

        if req.prices:
            try:
                _store_prices(conn, req.prices, today)
            except Exception as exc:
                errors.append(f"prices: {exc}")

        status = "failed" if not any([
            req.holdings, req.performance, req.contributions,
            req.activity, req.prices,
        ]) else ("partial" if errors else "success")

        duration_ms = int((time.time() - start_time) * 1000)
        conn.execute(
            """INSERT INTO insperity_sync_log
               (status, holdings_count, total_balance, duration_ms, error)
               VALUES (?, ?, ?, ?, ?)""",
            (status, holdings_count, total_balance, duration_ms,
             "; ".join(errors) if errors else None),
        )

        # Update manual entry balance (inside transaction)
        _update_manual_entry(conn, total_balance, today)

    # Snapshot after DB transaction closes (opens its own connection)
    if total_balance is not None:
        _take_net_worth_snapshot(today.isoformat())

    return {
        "ok": True,
        "status": status,
        "holdings": holdings_count,
        "total_balance": total_balance,
        "duration_ms": duration_ms,
        "errors": errors if errors else None,
    }


# ── Data query endpoints ─────────────────────────────────────────────────────

@router.get("/status")
def get_status(user=Depends(get_current_user)):
    """Latest sync status and account balance."""
    with get_db() as conn:
        log = conn.execute(
            "SELECT * FROM insperity_sync_log ORDER BY synced_at DESC LIMIT 1"
        ).fetchone()
        if not log:
            return {"synced": False}
        return {
            "synced": True,
            "last_sync": log["synced_at"],
            "status": log["status"],
            "total_balance": log["total_balance"],
            "holdings_count": log["holdings_count"],
            "duration_ms": log["duration_ms"],
            "error": log["error"],
        }


@router.get("/holdings")
def get_holdings(user=Depends(get_current_user)):
    """Latest fund holdings snapshot."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM insperity_holdings
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM insperity_holdings)
               ORDER BY balance DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/performance")
def get_performance(user=Depends(get_current_user)):
    """Latest performance returns."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM insperity_performance
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM insperity_performance)
               ORDER BY period"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/contributions")
def get_contributions(user=Depends(get_current_user)):
    """Latest contribution rates and YTD totals."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM insperity_contributions
               ORDER BY snapped_at DESC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else {}


@router.get("/activity")
def get_activity(user=Depends(get_current_user)):
    """Latest activity summary with vested balance."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM insperity_activity
               ORDER BY snapped_at DESC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else {}


@router.get("/transactions")
def get_transactions(user=Depends(get_current_user)):
    """Latest activity transactions (contributions, fees, rebalances)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM insperity_transactions
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM insperity_transactions)
               ORDER BY txn_date, description"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/allocations")
def get_allocations(user=Depends(get_current_user)):
    """Latest target contribution allocations by fund."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM insperity_allocations
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM insperity_allocations)
               ORDER BY target_pct DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/prices")
def get_prices(user=Depends(get_current_user)):
    """Latest fund prices for all available funds."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM insperity_prices
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM insperity_prices)
               ORDER BY fund"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/sync-log")
def get_sync_log(limit: int = 20, user=Depends(get_current_user)):
    """Recent sync history."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM insperity_sync_log ORDER BY synced_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
