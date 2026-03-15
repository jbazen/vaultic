from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from api.dependencies import get_current_user
from api.database import get_db
from api import security_log

router = APIRouter(prefix="/api/crypto", tags=["crypto"])


@router.post("/sync")
async def sync_holdings(_user: str = Depends(get_current_user)):
    """Manually trigger a Coinbase sync and refresh the net worth snapshot."""
    try:
        from api.coinbase_sync import sync_coinbase
        result = sync_coinbase()

        from api.sync import _take_net_worth_snapshot
        _take_net_worth_snapshot(date.today().isoformat())

        security_log.log_sync_event(f"COINBASE_SYNC  user={_user}  result={result}")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/holdings")
async def get_holdings(_user: str = Depends(get_current_user)):
    """Current Coinbase holdings — full data including native balances and prices."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                a.id, a.name, a.display_name, a.subtype AS currency,
                a.plaid_account_id AS coinbase_uuid,
                b.current AS usd_value,
                b.native_balance,
                b.unit_price,
                b.snapped_at
            FROM accounts a
            LEFT JOIN account_balances b ON b.account_id = a.id
                AND b.snapped_at = (
                    SELECT MAX(snapped_at) FROM account_balances WHERE account_id = a.id
                )
            WHERE a.source = 'coinbase' AND a.is_active = 1
            ORDER BY b.current DESC
        """).fetchall()
    return [dict(r) for r in rows]
