"""Linked account endpoints (#181).

Parker's accounts moved to LPL (#179). Each LPL account is linked to its old
Parker account (api/account_links.py). These endpoints show and toggle the
links, give one account's asset allocation, and build the portfolio summary
the Parker section used to show, from the LPL holdings plus Parker's history.

All endpoints require a valid JWT (via get_current_user dependency).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.account_links import (
    EFFECTIVE_LINKS_SQL, allocation_from, classified_holdings,
)
from api.database import get_db
from api.dependencies import get_current_user

router = APIRouter(prefix="/api/linked-accounts", tags=["linked-accounts"])


class LinkStateBody(BaseModel):
    active: bool


def _d(v) -> str:
    """A DATE value as 'YYYY-MM-DD' (date object under PARSE_DECLTYPES, str otherwise)."""
    return str(v)[:10]


def _series(conn, account_number: str) -> list[tuple[str, float]]:
    return [(_d(r["snapped_at"]), r["current"]) for r in conn.execute(
        "SELECT snapped_at, current FROM account_balances "
        "WHERE account_number = ? AND current IS NOT NULL ORDER BY snapped_at ASC",
        (account_number,),
    ).fetchall()]


def _pair_series(conn, pred: str, succ: str) -> list[tuple[str, float]]:
    """The predecessor's balances before the successor's first one, then the successor's."""
    own = _series(conn, succ)
    start = own[0][0] if own else "9999-12-31"
    return [p for p in _series(conn, pred) if p[0] < start] + own


def _as_of_sum(series_list: list[list[tuple[str, float]]], dates: list[str]) -> list[float]:
    """For each date, the sum of each series' last value on or before it (0 before its first)."""
    out = []
    idx = [0] * len(series_list)
    last = [0.0] * len(series_list)
    for d in dates:
        for i, s in enumerate(series_list):
            while idx[i] < len(s) and s[idx[i]][0] <= d:
                last[i] = s[idx[i]][1]
                idx[i] += 1
        out.append(sum(last))
    return out


def _balance_history(conn, series: list[list[tuple[str, float]]], parker_only: bool) -> list[dict]:
    """Combined balance history of a group of linked pairs.

    When the group holds every Parker account, Parker's own portfolio history
    (monthly from inception, with net investment) runs up to its last sync;
    after that the group's daily totals follow. Otherwise the daily totals
    of the pairs cover the whole range. `series` is one _pair_series per pair.
    """
    dates = sorted({d for s in series for d, _ in s})
    history: list[dict] = []
    cutover = ""
    if parker_only:
        rows = conn.execute(
            "SELECT balance_date, market_value, net_investment FROM i360_balance_history "
            "ORDER BY balance_date ASC"
        ).fetchall()
        history = [{"balance_date": _d(r["balance_date"]), "market_value": r["market_value"],
                    "net_investment": r["net_investment"]} for r in rows]
        if history:
            cutover = history[-1]["balance_date"]
    dates = [d for d in dates if d > cutover]
    history += [{"balance_date": d, "market_value": v, "net_investment": None}
                for d, v in zip(dates, _as_of_sum(series, dates))]
    return history


@router.get("")
def list_links(_user: str = Depends(get_current_user)):
    """Every link, in effect or not, with both accounts' names."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT k.id, k.method, k.linked_at, k.unlinked_at,
                      p.id AS predecessor_id, COALESCE(p.display_name, p.name) AS predecessor_name,
                      p.mask AS predecessor_mask, p.institution_name AS predecessor_institution,
                      s.id AS successor_id, COALESCE(s.display_name, s.name) AS successor_name,
                      s.mask AS successor_mask, s.institution_name AS successor_institution
               FROM account_links k
               LEFT JOIN accounts p ON p.account_number = k.predecessor_account_number
               LEFT JOIN accounts s ON s.account_number = k.successor_account_number
               ORDER BY k.id"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/summary")
def linked_summary(_user: str = Depends(get_current_user)):
    """Portfolio summary per bank of linked successor accounts.

    Each group: the successors' account ids and total, asset category and
    asset class allocation and top 10 holdings from their Plaid holdings, the
    combined balance history, and where the earlier history came from.
    """
    with get_db() as conn:
        links = conn.execute(
            f"""SELECT l.pred, l.succ, s.id AS succ_id, s.institution_name AS inst,
                       p.institution_name AS pred_inst, p.source AS pred_source
                FROM ({EFFECTIVE_LINKS_SQL}) l
                JOIN accounts s ON s.account_number = l.succ
                JOIN accounts p ON p.account_number = l.pred
                ORDER BY s.institution_name, s.id"""
        ).fetchall()
        parker_accounts = {r["account_number"] for r in conn.execute(
            "SELECT DISTINCT a.account_number FROM accounts a "
            "JOIN account_balances b ON b.account_number = a.account_number "
            "WHERE a.source = 'investor360'"
        ).fetchall()}

        groups: dict[str, list] = {}
        for r in links:
            groups.setdefault(r["inst"] or "Linked accounts", []).append(r)

        out = []
        for inst, rows in groups.items():
            pairs = [(r["pred"], r["succ"]) for r in rows]
            preds = {r["pred"] for r in rows}
            parker_only = (all(r["pred_source"] == "investor360" for r in rows)
                           and bool(parker_accounts) and parker_accounts <= preds)
            holdings = classified_holdings(conn, [s for _, s in pairs])
            by_holding: dict[str, dict] = {}
            for h in holdings:
                key = h["symbol"] or h["description"]
                agg = by_holding.setdefault(key, {"symbol": h["symbol"], "description": h["description"],
                                                  "value_dollars": 0.0})
                agg["value_dollars"] += h["value_dollars"]
            series = [_pair_series(conn, p, s) for p, s in pairs]
            pred_as_of = max((_d(r["snapped_at"]) for r in conn.execute(
                f"SELECT MAX(snapped_at) AS snapped_at FROM account_balances "
                f"WHERE account_number IN ({','.join('?' * len(preds))})", list(preds)
            ).fetchall() if r["snapped_at"]), default=None)
            out.append({
                "institution": inst,
                "account_ids": [r["succ_id"] for r in rows],
                "total_value": sum(s[-1][1] for s in series if s),
                "categories": allocation_from(holdings, "category"),
                "asset_classes": allocation_from(holdings, "asset_class"),
                "top_holdings": sorted(by_holding.values(), key=lambda h: -h["value_dollars"])[:10],
                "balance_history": _balance_history(conn, series, parker_only),
                "predecessor_institution": rows[0]["pred_inst"],
                "predecessor_as_of": pred_as_of,
                "parker_only": parker_only,
            })
    return {"groups": out}


@router.get("/allocation/{account_id}")
def account_allocation(account_id: int, _user: str = Depends(get_current_user)):
    """Asset category and asset class allocation of one account's latest Plaid holdings."""
    with get_db() as conn:
        acct = conn.execute(
            "SELECT account_number FROM accounts WHERE id = ? AND is_active = 1", (account_id,)
        ).fetchone()
        if not acct or not acct["account_number"]:
            raise HTTPException(status_code=404, detail="Account not found")
        holdings = classified_holdings(conn, [acct["account_number"]])
    return {
        "categories": allocation_from(holdings, "category"),
        "asset_classes": allocation_from(holdings, "asset_class"),
    }


@router.patch("/{link_id}")
def set_link_state(link_id: int, body: LinkStateBody, _user: str = Depends(get_current_user)):
    """Unlink (active=false) or relink (active=true). Changes no account data."""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM account_links WHERE id = ?", (link_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Link not found")
        conn.execute(
            "UPDATE account_links SET unlinked_at = CASE WHEN ? THEN NULL "
            "ELSE COALESCE(unlinked_at, CURRENT_TIMESTAMP) END WHERE id = ?",
            (body.active, link_id),
        )
    return {"ok": True, "active": body.active}
