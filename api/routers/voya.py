"""
Voya 401K integration — store retirement balances synced from a local script.

Voya's portal is behind Cloudflare + IBM ISAM (IP-bound), so the scrape runs on
the user's machine via sync_voya.py and POSTs the parsed result here. Mirrors the
Insperity /sync-local pattern. The total balance is upserted into manual_entries
(account_number 861956, category 'invested') with a snapshot for history.
"""
import logging
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_current_user
from api.database import get_db
from api.sync import _take_net_worth_snapshot

logger = logging.getLogger(__name__)

VOYA_ACCOUNT_NUMBER = "861956"  # real Voya plan account number — see #53
VOYA_ENTRY_NAME = "Voya 401(k)"
router = APIRouter(prefix="/api/voya", tags=["voya"])


class VoyaAccount(BaseModel):
    name: str
    plan_id: str | None = None
    balance: float


class VoyaHolding(BaseModel):
    fund_name: str
    balance: float | None = None
    units: float | None = None
    unit_price: float | None = None
    ytd_pct: float | None = None
    pct_of_account: float | None = None


class VoyaTransaction(BaseModel):
    trade_date: str
    activity: str
    fund_name: str | None = None
    fund_id: str | None = None
    amount: float | None = None
    units: float | None = None
    unit_price: float | None = None


class VoyaPerformance(BaseModel):
    personal_ror_ytd: float | None = None
    total_balance: float | None = None
    as_of: str | None = None
    balance_start: float | None = None
    balance_end: float | None = None
    growth: float | None = None


class VoyaAllocation(BaseModel):
    asset_class: str
    pct: float | None = None
    color: str | None = None


class VoyaFundPerf(BaseModel):
    fund_code: str
    fund_name: str | None = None
    benchmark: str | None = None
    one_month: float | None = None
    three_month: float | None = None
    ytd: float | None = None
    one_year: float | None = None
    three_year: float | None = None
    five_year: float | None = None
    ten_year: float | None = None
    inception: float | None = None


class VoyaContribSource(BaseModel):
    source_id: str | None = None
    name: str
    current: float | None = None
    actual: float | None = None


class VoyaContributions(BaseModel):
    contrib_type: str | None = None
    total_pct: float | None = None
    catchup: float | None = None
    sources: list[VoyaContribSource] = []


class LocalSyncRequest(BaseModel):
    """Pre-parsed Voya data from the local sync script."""
    total_balance: float
    accounts: list[VoyaAccount] = []
    holdings: list[VoyaHolding] = []
    transactions: list[VoyaTransaction] = []
    performance: VoyaPerformance | None = None
    allocations: list[VoyaAllocation] = []
    fund_performance: list[VoyaFundPerf] = []
    contributions: VoyaContributions | None = None


def _store_fund_performance(conn, rows, today: str):
    conn.execute("DELETE FROM voya_fund_performance WHERE snapped_at = ?", (today,))
    for r in rows:
        conn.execute(
            """INSERT INTO voya_fund_performance
                   (snapped_at, fund_code, fund_name, benchmark, one_month, three_month,
                    ytd, one_year, three_year, five_year, ten_year, inception)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapped_at, fund_code) DO UPDATE SET
                   fund_name=excluded.fund_name, benchmark=excluded.benchmark,
                   one_month=excluded.one_month, three_month=excluded.three_month,
                   ytd=excluded.ytd, one_year=excluded.one_year,
                   three_year=excluded.three_year, five_year=excluded.five_year,
                   ten_year=excluded.ten_year, inception=excluded.inception""",
            (today, r.fund_code, r.fund_name, r.benchmark, r.one_month, r.three_month,
             r.ytd, r.one_year, r.three_year, r.five_year, r.ten_year, r.inception),
        )


def _store_contributions(conn, contrib, today: str):
    conn.execute("DELETE FROM voya_contributions WHERE snapped_at = ?", (today,))
    for s in contrib.sources:
        conn.execute(
            """INSERT INTO voya_contributions
                   (snapped_at, source_id, name, current_pct, actual_pct, contrib_type)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapped_at, name) DO UPDATE SET
                   source_id=excluded.source_id, current_pct=excluded.current_pct,
                   actual_pct=excluded.actual_pct, contrib_type=excluded.contrib_type""",
            (today, s.source_id, s.name, s.current, s.actual, contrib.contrib_type),
        )


def _store_holdings(conn, holdings, today: str):
    """Replace today's holdings snapshot with the supplied fund rows."""
    conn.execute("DELETE FROM voya_holdings WHERE snapped_at = ?", (today,))
    for h in holdings:
        conn.execute(
            """INSERT INTO voya_holdings
                   (snapped_at, fund_name, balance, units, unit_price, ytd_pct, pct_of_account)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapped_at, fund_name) DO UPDATE SET
                   balance=excluded.balance, units=excluded.units,
                   unit_price=excluded.unit_price, ytd_pct=excluded.ytd_pct,
                   pct_of_account=excluded.pct_of_account""",
            (today, h.fund_name, h.balance, h.units, h.unit_price,
             h.ytd_pct, h.pct_of_account),
        )


def _store_transactions(conn, txns):
    """Append transactions; UNIQUE tuple makes re-syncs idempotent (no dupes)."""
    for t in txns:
        conn.execute(
            """INSERT INTO voya_transactions
                   (trade_date, activity, fund_name, fund_id, amount, units, unit_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(trade_date, activity, fund_id, amount, units, unit_price)
               DO UPDATE SET fund_name=excluded.fund_name""",
            (t.trade_date, t.activity, t.fund_name, t.fund_id,
             t.amount, t.units, t.unit_price),
        )


def _store_performance(conn, perf, today: str):
    conn.execute(
        """INSERT INTO voya_performance
               (snapped_at, personal_ror_ytd, total_balance, as_of,
                balance_start, balance_end, growth)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(snapped_at) DO UPDATE SET
               personal_ror_ytd=excluded.personal_ror_ytd,
               total_balance=excluded.total_balance, as_of=excluded.as_of,
               balance_start=excluded.balance_start, balance_end=excluded.balance_end,
               growth=excluded.growth""",
        (today, perf.personal_ror_ytd, perf.total_balance, perf.as_of,
         perf.balance_start, perf.balance_end, perf.growth),
    )


def _store_allocations(conn, allocations, today: str):
    conn.execute("DELETE FROM voya_allocations WHERE snapped_at = ?", (today,))
    for a in allocations:
        conn.execute(
            """INSERT INTO voya_allocations (snapped_at, asset_class, pct, color)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(snapped_at, asset_class) DO UPDATE SET
                   pct=excluded.pct, color=excluded.color""",
            (today, a.asset_class, a.pct, a.color),
        )


def _upsert_manual_entry(conn, total_balance: float, today: str):
    """Insert or update the Voya manual_entries row + snapshot history.

    Keyed on account_number so it correlates the same way every sync (see #48).
    """
    existing = conn.execute(
        "SELECT id, name FROM manual_entries WHERE account_number = ?",
        (VOYA_ACCOUNT_NUMBER,),
    ).fetchone()
    name = existing["name"] if existing else VOYA_ENTRY_NAME
    if existing:
        conn.execute(
            "UPDATE manual_entries SET value = ?, entered_at = ? WHERE id = ?",
            (total_balance, today, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO manual_entries (name, category, value, entered_at, account_number) "
            "VALUES (?, 'invested', ?, ?, ?)",
            (name, total_balance, today, VOYA_ACCOUNT_NUMBER),
        )
    # Snapshot for balance history (one row per account_number/day).
    conn.execute(
        "DELETE FROM manual_entry_snapshots WHERE account_number = ? AND snapped_at = ?",
        (VOYA_ACCOUNT_NUMBER, today),
    )
    conn.execute(
        """INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at)
           VALUES (?, ?, 'invested', ?, ?)
           ON CONFLICT(name, snapped_at) DO UPDATE SET
               value=excluded.value, account_number=excluded.account_number""",
        (name, VOYA_ACCOUNT_NUMBER, total_balance, today),
    )


@router.post("/sync-local")
def sync_local(req: LocalSyncRequest, user=Depends(get_current_user)):
    """Store pre-parsed Voya balances from the local sync script."""
    start_time = time.time()
    today = date.today().isoformat()

    with get_db() as conn:
        for a in req.accounts:
            conn.execute(
                """INSERT INTO voya_accounts (snapped_at, name, plan_id, balance)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(snapped_at, name)
                   DO UPDATE SET plan_id=excluded.plan_id, balance=excluded.balance""",
                (today, a.name, a.plan_id, a.balance),
            )
        _upsert_manual_entry(conn, req.total_balance, today)

        # Full-data sections (holdings / transactions / performance / allocation).
        if req.holdings:
            _store_holdings(conn, req.holdings, today)
        if req.transactions:
            _store_transactions(conn, req.transactions)
        if req.performance:
            _store_performance(conn, req.performance, today)
        if req.allocations:
            _store_allocations(conn, req.allocations, today)
        if req.fund_performance:
            _store_fund_performance(conn, req.fund_performance, today)
        if req.contributions:
            _store_contributions(conn, req.contributions, today)

        duration_ms = int((time.time() - start_time) * 1000)
        conn.execute(
            """INSERT INTO voya_sync_log
               (status, account_count, total_balance, duration_ms, error)
               VALUES ('success', ?, ?, ?, NULL)""",
            (len(req.accounts), req.total_balance, duration_ms),
        )

    # Net worth snapshot outside the DB transaction (opens its own connection).
    _take_net_worth_snapshot(today)

    return {
        "ok": True,
        "status": "success",
        "accounts": len(req.accounts),
        "total_balance": req.total_balance,
        "duration_ms": int((time.time() - start_time) * 1000),
    }


@router.get("/status")
def get_status(user=Depends(get_current_user)):
    """Latest sync status and balance."""
    with get_db() as conn:
        log = conn.execute(
            "SELECT * FROM voya_sync_log ORDER BY synced_at DESC LIMIT 1"
        ).fetchone()
        if not log:
            return {"synced": False}
        return {
            "synced": True,
            "last_sync": log["synced_at"],
            "status": log["status"],
            "total_balance": log["total_balance"],
            "account_count": log["account_count"],
            "duration_ms": log["duration_ms"],
            "error": log["error"],
        }


@router.get("/accounts")
def get_accounts(user=Depends(get_current_user)):
    """Latest per-account balances."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM voya_accounts
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM voya_accounts)
               ORDER BY balance DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/sync-log")
def get_sync_log(limit: int = 20, user=Depends(get_current_user)):
    """Recent sync history."""
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1–100")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM voya_sync_log ORDER BY synced_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/holdings")
def get_holdings(user=Depends(get_current_user)):
    """Latest fund holdings snapshot (most recent day)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT fund_name, balance, units, unit_price, ytd_pct, pct_of_account
               FROM voya_holdings
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM voya_holdings)
               ORDER BY balance DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/transactions")
def get_transactions(limit: int = 100, user=Depends(get_current_user)):
    """Recent transactions/activity, newest first."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be 1–500")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT trade_date, activity, fund_name, fund_id, amount, units, unit_price
               FROM voya_transactions
               ORDER BY trade_date DESC, id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/performance")
def get_performance(user=Depends(get_current_user)):
    """Latest performance snapshot (personal rate of return + period balances)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM voya_performance ORDER BY snapped_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else {}


@router.get("/allocations")
def get_allocations(user=Depends(get_current_user)):
    """Latest asset-class allocation snapshot."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT asset_class, pct, color
               FROM voya_allocations
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM voya_allocations)
               ORDER BY pct DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/fund-performance")
def get_fund_performance(user=Depends(get_current_user)):
    """Latest per-fund multi-timeframe returns (1M/3M/YTD/1Y/3Y/5Y/10Y + benchmark)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT fund_code, fund_name, benchmark, one_month, three_month, ytd,
                      one_year, three_year, five_year, ten_year, inception
               FROM voya_fund_performance
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM voya_fund_performance)
               ORDER BY ytd DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/contributions")
def get_contributions(user=Depends(get_current_user)):
    """Latest contribution rate + sources, plus YTD contributions from activity."""
    with get_db() as conn:
        sources = conn.execute(
            """SELECT source_id, name, current_pct, actual_pct, contrib_type
               FROM voya_contributions
               WHERE snapped_at = (SELECT MAX(snapped_at) FROM voya_contributions)
               ORDER BY current_pct DESC"""
        ).fetchall()
        # YTD employee contributions = sum of 'Contribution' activity this year.
        ytd = conn.execute(
            """SELECT COALESCE(SUM(amount), 0) AS ytd
               FROM voya_transactions
               WHERE activity LIKE '%Contribution%'
                 AND strftime('%Y', trade_date) = strftime('%Y', 'now', 'localtime')"""
        ).fetchone()["ytd"]
        rows = [dict(s) for s in sources]
        return {
            "contrib_type": rows[0]["contrib_type"] if rows else None,
            "total_pct": sum(r["current_pct"] or 0 for r in rows) if rows else None,
            "sources": rows,
            "ytd_contributions": round(ytd, 2),
        }
