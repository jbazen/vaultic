"""
Crypto capital gains tracker — fetch Coinbase trade history, compute FIFO
cost basis, and classify short-term vs long-term gains.

Endpoints:
  POST /api/crypto/sync-trades   — fetch fills from Coinbase Advanced Trade API
  GET  /api/crypto/trades        — list stored trades with optional date filter
  GET  /api/crypto/gains/{year}  — realized gains/losses for a tax year
  POST /api/crypto/calculate-gains — recompute FIFO lots and gains from trades
"""
import os
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException

from api.database import get_db
from api.dependencies import get_current_user
from api.coinbase_sync import _coinbase_get

logger = logging.getLogger("vaultic.crypto_gains")

router = APIRouter(prefix="/api/crypto", tags=["crypto"])


# ── Fetch trades from Coinbase ────────────────────────────────────────────────

def _fetch_coinbase_fills(key_name: str, private_key: str) -> list[dict]:
    """Fetch all fills (executed trades) from Coinbase Advanced Trade API.

    Paginates through all results using cursor-based pagination.
    Each fill contains: trade_id, order_id, product_id, side, size, price,
    commission (fee), trade_time.
    """
    all_fills = []
    cursor = ""
    while True:
        path = "/api/v3/brokerage/orders/historical/fills?limit=100"
        if cursor:
            path += f"&cursor={cursor}"
        data = _coinbase_get(key_name, private_key, path)
        fills = data.get("fills", [])
        all_fills.extend(fills)
        cursor = data.get("cursor", "")
        if not cursor or not fills:
            break
    return all_fills


@router.post("/sync-trades")
async def sync_trades(_user: str = Depends(get_current_user)):
    """Fetch all trade fills from Coinbase and store in crypto_trades table.

    Idempotent — uses INSERT OR IGNORE on trade_id so re-syncing is safe.
    Returns count of new trades added.
    """
    key_name = os.environ.get("COINBASE_API_KEY_NAME", "")
    private_key = os.environ.get("COINBASE_API_KEY_PRIVATE", "").replace("\\n", "\n")

    if not key_name or not private_key:
        raise HTTPException(status_code=400, detail="Coinbase API keys not configured")

    try:
        fills = _fetch_coinbase_fills(key_name, private_key)
    except Exception as e:
        logger.error(f"Coinbase fills fetch error: {e}")
        raise HTTPException(status_code=502, detail="Could not fetch trades from Coinbase")

    new_count = 0
    with get_db() as conn:
        for fill in fills:
            trade_id = fill.get("trade_id") or fill.get("entry_id", "")
            if not trade_id:
                continue
            result = conn.execute("""
                INSERT OR IGNORE INTO crypto_trades
                    (trade_id, order_id, product_id, side, size, price, fee, trade_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id,
                fill.get("order_id", ""),
                fill.get("product_id", ""),
                fill.get("side", "").upper(),
                float(fill.get("size", 0) or 0),
                float(fill.get("price", 0) or 0),
                float(fill.get("commission", 0) or 0),
                fill.get("trade_time", ""),
            ))
            if result.rowcount > 0:
                new_count += 1

    return {"ok": True, "total_fetched": len(fills), "new_trades": new_count}


# ── List trades ───────────────────────────────────────────────────────────────

@router.get("/trades")
async def list_trades(
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
    limit: int = Query(default=200, le=1000),
    _user: str = Depends(get_current_user),
):
    """List stored crypto trades, optionally filtered by date range."""
    with get_db() as conn:
        query = "SELECT * FROM crypto_trades WHERE 1=1"
        params = []
        if start_date:
            query += " AND trade_time >= ?"
            params.append(start_date)
        if end_date:
            query += " AND trade_time <= ?"
            params.append(end_date + "T23:59:59")
        query += " ORDER BY trade_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── FIFO lot matching and gain calculation ────────────────────────────────────

def _calculate_fifo_gains(conn):
    """Recompute all crypto lots and gains from trade history using FIFO.

    Clears existing lots and gains, then processes all trades chronologically:
    - BUY: creates a new lot with acquisition date and cost per unit
    - SELL: matches against oldest lots first (FIFO), computes gain/loss,
      classifies as short-term (<= 365 days) or long-term (> 365 days)

    Returns summary dict with counts and totals.
    """
    # Clear existing computed data
    conn.execute("DELETE FROM crypto_lots")
    conn.execute("DELETE FROM crypto_gains")

    # Get all trades sorted chronologically
    trades = conn.execute("""
        SELECT * FROM crypto_trades ORDER BY trade_time ASC
    """).fetchall()
    trades = [dict(t) for t in trades]

    total_buys = 0
    total_sells = 0
    total_gains = 0
    total_gains_amount = 0.0

    for trade in trades:
        side = trade["side"].upper()
        product_id = trade["product_id"]  # e.g. "BTC-USD"
        currency = product_id.split("-")[0] if "-" in product_id else product_id
        size = trade["size"]
        price = trade["price"]
        fee = trade.get("fee") or 0
        trade_time = trade["trade_time"]

        # Extract YYYY-MM-DD from trade_time (may be ISO 8601 with T or date-only)
        trade_date = trade_time[:10]

        if side == "BUY":
            # Create a new lot — cost includes proportional fee
            total_cost = (size * price) + fee
            cost_per_unit = total_cost / size if size > 0 else price
            conn.execute("""
                INSERT INTO crypto_lots
                    (currency, acquisition_date, quantity, quantity_remaining,
                     cost_per_unit, total_cost, source_trade_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (currency, trade_date, size, size, cost_per_unit, total_cost, trade["trade_id"]))
            total_buys += 1

        elif side == "SELL":
            # FIFO: match against oldest lots for this currency
            proceeds = (size * price) - fee
            remaining_to_sell = size
            sale_date_obj = datetime.strptime(trade_date, "%Y-%m-%d").date()

            lots = conn.execute("""
                SELECT id, acquisition_date, quantity_remaining, cost_per_unit
                FROM crypto_lots
                WHERE currency = ? AND quantity_remaining > 0
                ORDER BY acquisition_date ASC
            """, (currency,)).fetchall()

            for lot in lots:
                if remaining_to_sell <= 0:
                    break
                lot = dict(lot)
                matched_qty = min(remaining_to_sell, lot["quantity_remaining"])
                cost_basis = matched_qty * lot["cost_per_unit"]
                lot_proceeds = (matched_qty / size) * proceeds  # proportional proceeds
                gain_loss = lot_proceeds - cost_basis

                # Classify holding period
                acq_date_obj = datetime.strptime(lot["acquisition_date"], "%Y-%m-%d").date()
                holding_days = (sale_date_obj - acq_date_obj).days
                gain_type = "long_term" if holding_days > 365 else "short_term"

                # Record the gain
                conn.execute("""
                    INSERT INTO crypto_gains
                        (currency, sale_trade_id, sale_date, quantity,
                         proceeds, cost_basis, gain_loss, gain_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (currency, trade["trade_id"], trade_date, matched_qty,
                      round(lot_proceeds, 2), round(cost_basis, 2),
                      round(gain_loss, 2), gain_type))

                # Reduce the lot
                new_remaining = lot["quantity_remaining"] - matched_qty
                conn.execute(
                    "UPDATE crypto_lots SET quantity_remaining = ? WHERE id = ?",
                    (new_remaining, lot["id"])
                )
                remaining_to_sell -= matched_qty
                total_gains += 1
                total_gains_amount += gain_loss

            total_sells += 1

    return {
        "total_trades": len(trades),
        "buys": total_buys,
        "sells": total_sells,
        "gains_computed": total_gains,
        "net_gain_loss": round(total_gains_amount, 2),
    }


@router.post("/calculate-gains")
async def calculate_gains(_user: str = Depends(get_current_user)):
    """Recompute FIFO cost basis and gains from all stored trades.

    Clears and rebuilds crypto_lots and crypto_gains tables.
    Call after syncing new trades or to recalculate with corrected data.
    """
    with get_db() as conn:
        result = _calculate_fifo_gains(conn)
    return {"ok": True, **result}


# ── Tax year gains summary ────────────────────────────────────────────────────

@router.get("/gains/{year}")
async def gains_by_year(year: int, _user: str = Depends(get_current_user)):
    """Realized crypto gains/losses for a specific tax year.

    Returns individual gain events plus summary totals broken down by
    short-term vs long-term for Schedule D reporting.
    """
    start = f"{year}-01-01"
    end = f"{year}-12-31"

    with get_db() as conn:
        gains = conn.execute("""
            SELECT * FROM crypto_gains
            WHERE sale_date >= ? AND sale_date <= ?
            ORDER BY sale_date ASC
        """, (start, end)).fetchall()

    gains = [dict(g) for g in gains]

    # Compute summary
    short_term = [g for g in gains if g["gain_type"] == "short_term"]
    long_term = [g for g in gains if g["gain_type"] == "long_term"]

    st_gains = sum(g["gain_loss"] for g in short_term if g["gain_loss"] > 0)
    st_losses = sum(g["gain_loss"] for g in short_term if g["gain_loss"] < 0)
    lt_gains = sum(g["gain_loss"] for g in long_term if g["gain_loss"] > 0)
    lt_losses = sum(g["gain_loss"] for g in long_term if g["gain_loss"] < 0)

    total_proceeds = sum(g["proceeds"] for g in gains)
    total_cost_basis = sum(g["cost_basis"] for g in gains)
    net_gain_loss = sum(g["gain_loss"] for g in gains)

    return {
        "year": year,
        "transactions": gains,
        "summary": {
            "short_term_gains": round(st_gains, 2),
            "short_term_losses": round(st_losses, 2),
            "short_term_net": round(st_gains + st_losses, 2),
            "long_term_gains": round(lt_gains, 2),
            "long_term_losses": round(lt_losses, 2),
            "long_term_net": round(lt_gains + lt_losses, 2),
            "total_proceeds": round(total_proceeds, 2),
            "total_cost_basis": round(total_cost_basis, 2),
            "net_gain_loss": round(net_gain_loss, 2),
            "transaction_count": len(gains),
        },
    }


@router.get("/lots")
async def list_lots(
    currency: str = Query(default=None),
    _user: str = Depends(get_current_user),
):
    """List all cost basis lots, optionally filtered by currency.

    Shows open lots (quantity_remaining > 0) and closed lots for audit.
    """
    with get_db() as conn:
        if currency:
            rows = conn.execute(
                "SELECT * FROM crypto_lots WHERE currency = ? ORDER BY acquisition_date ASC",
                (currency.upper(),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM crypto_lots ORDER BY currency, acquisition_date ASC"
            ).fetchall()
    return [dict(r) for r in rows]
