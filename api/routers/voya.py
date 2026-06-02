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


class LocalSyncRequest(BaseModel):
    """Pre-parsed Voya data from the local sync script."""
    total_balance: float
    accounts: list[VoyaAccount] = []


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
