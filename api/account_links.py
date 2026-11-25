"""Account history links (#181).

When an account moves to a new provider, the old account (the predecessor)
stops getting data and the same money shows up on a new account (the
successor). Parker's Investor360 accounts moved to LPL, which syncs through
Plaid (#179). A link joins the two so the successor's history starts with the
predecessor's.

`account_links` holds one row per pair, keyed by account_number. Linking never
changes or deletes either account's data: unlinking only sets `unlinked_at`,
relinking clears it. A row that was unlinked keeps both accounts out of
automatic matching, so the matcher never undoes the owner's choice.

A link is in effect while it is not unlinked and its successor is an active
investment account (see EFFECTIVE_LINKS_SQL).
"""
import logging

logger = logging.getLogger(__name__)

# Share of the predecessor's positions (by ticker, excluding cash) that must
# hold the same share count in the successor for an automatic match.
MATCH_SHARE = 0.75

# Pairs in effect: not unlinked, successor still an active investment account.
EFFECTIVE_LINKS_SQL = """
    SELECT k.id, k.predecessor_account_number AS pred, k.successor_account_number AS succ
    FROM account_links k
    JOIN accounts s ON s.account_number = k.successor_account_number
    WHERE k.unlinked_at IS NULL AND s.is_active = 1 AND s.type = 'investment'
"""

# Net worth snapshots taken while the Parker accounts were already hidden
# (#179, deployed 2026-11-24) but before the second LPL login had a balance.
# They counted neither account of three pairs. Only these dates are repaired:
# snapshots taken with the link-aware code already count every pair.
CUTOVER_REPAIR_DATES = ("2026-11-24",)
CUTOVER_REPAIR_REASON = "parker-lpl-cutover (#181)"


def effective_links(conn) -> dict[str, str]:
    """{successor account_number: predecessor account_number} for links in effect."""
    return {r["succ"]: r["pred"] for r in conn.execute(EFFECTIVE_LINKS_SQL).fetchall()}


def _same_qty(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.001, 0.005 * max(abs(a), abs(b)))


def _parker_positions(conn, account_number: str) -> dict[str, float]:
    """Latest Investor360 positions {TICKER: shares}, cash left out."""
    rows = conn.execute(
        """SELECT symbol, quantity FROM i360_holdings
           WHERE account_number = ?
             AND snapped_at = (SELECT MAX(snapped_at) FROM i360_holdings WHERE account_number = ?)
             AND symbol IS NOT NULL AND symbol <> '' AND quantity > 0
             AND LOWER(COALESCE(asset_category, '')) NOT IN ('cash and equivalents', 'cash')""",
        (account_number, account_number),
    ).fetchall()
    return {r["symbol"].strip().upper(): r["quantity"] for r in rows}


def _plaid_positions(conn, account_number: str) -> dict[str, float]:
    """Latest Plaid positions {TICKER: shares}, cash left out."""
    rows = conn.execute(
        """SELECT s.ticker_symbol AS ticker, h.quantity
           FROM plaid_holdings h
           JOIN plaid_securities s ON s.security_id = h.security_id
           WHERE h.account_number = ?
             AND h.snapped_at = (SELECT MAX(snapped_at) FROM plaid_holdings WHERE account_number = ?)
             AND s.ticker_symbol IS NOT NULL AND s.ticker_symbol <> '' AND h.quantity > 0
             AND COALESCE(s.type, '') <> 'cash'""",
        (account_number, account_number),
    ).fetchall()
    return {r["ticker"].strip().upper(): r["quantity"] for r in rows}


def _match_score(pred: dict[str, float], succ: dict[str, float]) -> float:
    if not pred:
        return 0.0
    same = sum(1 for t, q in pred.items() if t in succ and _same_qty(q, succ[t]))
    return same / len(pred)


def _latest_balance(conn, account_number: str):
    row = conn.execute(
        "SELECT current FROM account_balances WHERE account_number = ? AND current IS NOT NULL "
        "ORDER BY snapped_at DESC LIMIT 1",
        (account_number,),
    ).fetchone()
    return row["current"] if row else None


def _insert_link(conn, pred: str, succ: str, method: str) -> None:
    conn.execute(
        "INSERT INTO account_links (predecessor_account_number, successor_account_number, method) "
        "VALUES (?, ?, ?)",
        (pred, succ, method),
    )


def auto_link_accounts(conn) -> int:
    """Link hidden Investor360 accounts to the Plaid accounts they moved to.

    1. Holdings: a predecessor links to the candidate holding the same tickers
       with the same share counts (at least MATCH_SHARE of its non-cash
       positions), when each is the other's single best match.
    2. Empty pair: at a bank that already got a holdings match, when exactly
       one predecessor and one candidate are left and both are empty (no
       positions, balance 0 or none), they are linked.

    Accounts that appear in any account_links row (linked or unlinked) are
    skipped. Returns the number of links created. Idempotent.
    """
    preds = conn.execute(
        """SELECT account_number, institution_name FROM accounts
           WHERE source = 'investor360' AND is_active = 0 AND type = 'investment'
             AND account_number IS NOT NULL
             AND account_number NOT IN (SELECT predecessor_account_number FROM account_links)
             AND account_number NOT IN (SELECT successor_account_number FROM account_links)"""
    ).fetchall()
    cands = conn.execute(
        """SELECT account_number, institution_name FROM accounts
           WHERE COALESCE(source, 'plaid') = 'plaid' AND is_active = 1 AND type = 'investment'
             AND account_number IS NOT NULL
             AND account_number NOT IN (SELECT successor_account_number FROM account_links)
             AND account_number NOT IN (SELECT predecessor_account_number FROM account_links)"""
    ).fetchall()
    if not preds or not cands:
        return 0

    pred_pos = {p["account_number"]: _parker_positions(conn, p["account_number"]) for p in preds}
    cand_pos = {c["account_number"]: _plaid_positions(conn, c["account_number"]) for c in cands}
    scores = {(p, c): _match_score(pp, cp)
              for p, pp in pred_pos.items() for c, cp in cand_pos.items()}

    def best(key_index: int, key: str):
        ranked = sorted(((s, pc) for pc, s in scores.items() if pc[key_index] == key), reverse=True)
        if not ranked or ranked[0][0] < MATCH_SHARE:
            return None
        if len(ranked) > 1 and ranked[1][0] == ranked[0][0]:
            return None  # tie: no single best match
        return ranked[0][1]

    created = 0
    linked_preds, linked_cands = set(), set()
    for p in pred_pos:
        pair = best(0, p)
        if pair and best(1, pair[1]) == pair:
            _insert_link(conn, p, pair[1], "auto-holdings")
            linked_preds.add(p)
            linked_cands.add(pair[1])
            created += 1

    # Empty pair left over at a bank that already got a holdings match.
    inst = {c["account_number"]: c["institution_name"] for c in cands}
    pred_inst = {p["account_number"]: p["institution_name"] for p in preds}
    matched = conn.execute(
        """SELECT DISTINCT p.institution_name AS from_inst, s.institution_name AS to_inst
           FROM account_links k
           JOIN accounts p ON p.account_number = k.predecessor_account_number
           JOIN accounts s ON s.account_number = k.successor_account_number
           WHERE k.method = 'auto-holdings' AND k.unlinked_at IS NULL"""
    ).fetchall()
    for m in matched:
        left_p = [p for p in pred_pos if p not in linked_preds and pred_inst[p] == m["from_inst"]]
        left_c = [c for c in cand_pos if c not in linked_cands and inst[c] == m["to_inst"]]
        if len(left_p) != 1 or len(left_c) != 1:
            continue
        p, c = left_p[0], left_c[0]
        if pred_pos[p] or cand_pos[c]:
            continue
        if (_latest_balance(conn, p) or 0) != 0 or (_latest_balance(conn, c) or 0) != 0:
            continue
        _insert_link(conn, p, c, "auto-empty")
        linked_preds.add(p)
        linked_cands.add(c)
        created += 1

    if created:
        logger.info("Linked %d account(s) to their earlier history (#181)", created)
    return created


def repair_cutover_snapshots(conn, dates=CUTOVER_REPAIR_DATES,
                             reason: str = CUTOVER_REPAIR_REASON) -> float:
    """Add the hidden predecessors' value to snapshots that counted neither account.

    For each date, a pair in effect whose predecessor is hidden and whose
    successor had no balance on or before that date was counted by neither
    account; its predecessor's last balance on or before the date is added to
    `invested` and `total`. The amount is recorded in
    net_worth_snapshot_repairs, so each date is repaired once and the change
    can be reversed by subtracting it. Returns the total added.
    """
    added_all = 0.0
    for d in dates:
        snap = conn.execute(
            "SELECT 1 FROM net_worth_snapshots WHERE snapped_at = ?", (d,)
        ).fetchone()
        done = conn.execute(
            "SELECT 1 FROM net_worth_snapshot_repairs WHERE snapped_at = ? AND reason = ?",
            (d, reason),
        ).fetchone()
        if not snap or done:
            continue
        added = conn.execute(
            f"""SELECT COALESCE(SUM((
                    SELECT b.current FROM account_balances b
                    WHERE b.account_number = l.pred AND b.current IS NOT NULL
                      AND date(b.snapped_at) <= date(?)
                    ORDER BY b.snapped_at DESC LIMIT 1
                )), 0) AS added
                FROM ({EFFECTIVE_LINKS_SQL}) l
                JOIN accounts p ON p.account_number = l.pred AND p.is_active = 0
                WHERE NOT EXISTS (
                    SELECT 1 FROM account_balances sb
                    WHERE sb.account_number = l.succ AND sb.current IS NOT NULL
                      AND date(sb.snapped_at) <= date(?)
                )""",
            (d, d),
        ).fetchone()["added"]
        if not added:
            continue  # nothing to add yet (e.g. not linked yet): try again next start
        conn.execute(
            "INSERT INTO net_worth_snapshot_repairs (snapped_at, reason, amount) VALUES (?, ?, ?)",
            (d, reason, added),
        )
        conn.execute(
            "UPDATE net_worth_snapshots SET invested = invested + ?, total = total + ? "
            "WHERE snapped_at = ?",
            (added, added, d),
        )
        logger.info("Net worth snapshot %s: added %.2f for linked accounts (#181)", d, added)
        added_all += added
    return added_all


# ── Asset categories for Plaid holdings ─────────────────────────────────────
# Plaid gives only a security type (mutual fund, etf, ...). Investor360 gave
# each fund a category (Large-Cap Growth, Emerging Markets, ...) and an asset
# class (Domestic Equity, ...). The LPL accounts hold the same funds, so a
# Plaid holding takes the category Parker last gave its ticker; cash is
# Cash and Equivalents; anything Parker never held falls back to the Plaid type.

CASH_CATEGORY = "Cash and Equivalents"
_PLAID_TYPE_LABELS = {
    "mutual fund": "Mutual Fund", "etf": "ETF", "equity": "Stock",
    "fixed income": "Fixed Income", "derivative": "Derivative",
    "cryptocurrency": "Crypto", "loan": "Loan",
}


def parker_classes(conn) -> dict[str, tuple[str, str | None]]:
    """{TICKER: (asset_category, primary_asset_class)} from the latest Investor360 row."""
    out: dict[str, tuple[str, str | None]] = {}
    for r in conn.execute(
        """SELECT symbol, asset_category, primary_asset_class FROM i360_holdings
           WHERE symbol IS NOT NULL AND TRIM(symbol) <> ''
             AND asset_category IS NOT NULL AND TRIM(asset_category) <> ''
           ORDER BY snapped_at ASC, id ASC"""
    ).fetchall():
        out[r["symbol"].strip().upper()] = (r["asset_category"], r["primary_asset_class"])
    return out


def classified_holdings(conn, account_numbers: list[str]) -> list[dict]:
    """Latest Plaid holdings of these accounts, in US dollars, with category and class.

    Holdings in another currency are left out: they can't be added to dollar
    totals without exchange rates (#141).
    """
    if not account_numbers:
        return []
    classes = parker_classes(conn)
    marks = ",".join("?" * len(account_numbers))
    rows = conn.execute(
        f"""SELECT h.account_number, s.ticker_symbol, s.name, s.type, h.institution_value
            FROM plaid_holdings h
            LEFT JOIN plaid_securities s ON s.security_id = h.security_id
            WHERE h.account_number IN ({marks})
              AND h.snapped_at = (SELECT MAX(snapped_at) FROM plaid_holdings x
                                  WHERE x.account_number = h.account_number)
              AND h.institution_value IS NOT NULL
              AND (h.iso_currency_code = 'USD'
                   OR (h.iso_currency_code IS NULL AND h.unofficial_currency_code IS NULL))""",
        account_numbers,
    ).fetchall()
    out = []
    for r in rows:
        ticker = (r["ticker_symbol"] or "").strip().upper()
        sec_type = (r["type"] or "").strip().lower()
        if sec_type == "cash":
            category, asset_class = CASH_CATEGORY, CASH_CATEGORY
        elif ticker in classes:
            category, asset_class = classes[ticker]
        else:
            category = _PLAID_TYPE_LABELS.get(sec_type, "Other")
            asset_class = None
        out.append({
            "account_number": r["account_number"], "symbol": ticker or None,
            "description": r["name"] or ticker or "Unknown", "value_dollars": r["institution_value"],
            "category": category, "asset_class": asset_class or "Other",
        })
    return out


def allocation_from(holdings: list[dict], key: str) -> list[dict]:
    """[{asset_name, market_value}] summed by `key`, largest first (I360 allocation shape)."""
    sums: dict[str, float] = {}
    for h in holdings:
        sums[h[key]] = sums.get(h[key], 0.0) + h["value_dollars"]
    return [{"asset_name": k, "market_value": v}
            for k, v in sorted(sums.items(), key=lambda kv: -kv[1])]
