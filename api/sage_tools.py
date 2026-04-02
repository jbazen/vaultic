"""
Sage tool implementations — each tool is a standalone function.

Called by sage._call_tool() via the TOOL_DISPATCH dict. Each function takes
(inputs: dict, username: str) and returns a string result for Claude.
"""
import logging
from pathlib import Path

from api.database import get_db

logger = logging.getLogger("vaultic.sage")

NOTES_DIR = Path(__file__).parent.parent / "data"


def _notes_path(username: str) -> Path:
    """Return the per-user Sage notes file path."""
    safe = "".join(c for c in username if c.isalnum() or c in "-_")
    return NOTES_DIR / f"sage_notes_{safe}.md"


# ── Financial data tools ──────────────────────────────────────────────────────

def tool_get_net_worth(inputs, username):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return "No net worth data yet. Connect accounts and sync to get started."
    return str(dict(row))


def tool_get_net_worth_history(inputs, username):
    days = inputs.get("days", 90)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT snapped_at, total, liquid, invested, real_estate, vehicles, liabilities FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT ?",
            (days,)
        ).fetchall()
    return str([dict(r) for r in rows])


def tool_get_accounts(inputs, username):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT a.name, a.display_name, a.mask, a.type, a.subtype,
                   a.institution_name, b.current, b.available, b.snapped_at
            FROM accounts a
            LEFT JOIN account_balances b ON b.account_id = a.id
                AND b.snapped_at = (SELECT MAX(snapped_at) FROM account_balances WHERE account_id = a.id)
            WHERE a.is_active = 1
            ORDER BY a.institution_name, a.name
        """).fetchall()
    return str([dict(r) for r in rows])


def tool_get_transactions(inputs, username):
    limit = min(inputs.get("limit", 50), 200)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.date, t.amount, t.name, t.merchant_name, t.category, t.pending,
                   a.name AS account_name, a.institution_name
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE a.is_active = 1
            ORDER BY t.date DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return str([dict(r) for r in rows])


def tool_get_manual_entries(inputs, username):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name, category, value, notes, entered_at FROM manual_entries ORDER BY entered_at DESC"
        ).fetchall()
    return str([dict(r) for r in rows])


# ── Notes tools ───────────────────────────────────────────────────────────────

def tool_get_notes(inputs, username):
    np = _notes_path(username)
    if np.exists():
        return np.read_text()
    return "No notes yet."


def tool_update_notes(inputs, username):
    notes = inputs.get("notes", "")
    np = _notes_path(username)
    np.parent.mkdir(exist_ok=True)
    np.write_text(notes)
    return "Notes updated."


# ── Web tools ─────────────────────────────────────────────────────────────────

def tool_web_search(inputs, username):
    from api.sage import _tavily_search
    return _tavily_search(inputs.get("query", ""))


def tool_fetch_page(inputs, username):
    from api.sage import _fetch_page
    return _fetch_page(inputs.get("url", ""))


# ── Budget tools ──────────────────────────────────────────────────────────────

def tool_get_budget(inputs, username):
    from datetime import date as _date
    month = inputs.get("month") or _date.today().strftime("%Y-%m")
    with get_db() as conn:
        groups = conn.execute("""
            SELECT id, name, type FROM budget_groups
            WHERE is_archived = 0 ORDER BY display_order, id
        """).fetchall()

        # Aggregate direct assignment spending per item (sign preserved: refunds reduce spent)
        direct_spent = {}
        for row in conn.execute("""
            SELECT ta.item_id, COALESCE(SUM(t.amount), 0) AS spent
            FROM transaction_assignments ta
            JOIN transactions t ON t.transaction_id = ta.transaction_id
            WHERE strftime('%Y-%m', t.date) = ? AND t.pending = 0
            GROUP BY ta.item_id
        """, (month,)).fetchall():
            direct_spent[row["item_id"]] = float(row["spent"])

        # Aggregate split spending per item
        split_spent = {}
        for row in conn.execute("""
            SELECT ts.item_id, COALESCE(SUM(ts.amount), 0) AS spent
            FROM transaction_splits ts
            JOIN transactions t ON t.transaction_id = ts.transaction_id
            WHERE strftime('%Y-%m', t.date) = ? AND t.pending = 0
            GROUP BY ts.item_id
        """, (month,)).fetchall():
            split_spent[row["item_id"]] = float(row["spent"])

        result = []
        for g in groups:
            items = conn.execute("""
                SELECT bi.id, bi.name, COALESCE(ba.planned, 0) AS planned
                FROM budget_items bi
                LEFT JOIN budget_amounts ba ON ba.item_id = bi.id AND ba.month = ?
                WHERE bi.group_id = ? AND bi.is_archived = 0
                ORDER BY bi.display_order, bi.id
            """, (month, g["id"])).fetchall()
            group_planned = 0.0
            group_spent = 0.0
            items_out = []
            for i in items:
                planned = float(i["planned"])
                spent = direct_spent.get(i["id"], 0) + split_spent.get(i["id"], 0)
                remaining = round(planned - spent, 2)
                group_planned += planned
                group_spent += spent
                items_out.append({
                    "id": i["id"], "name": i["name"],
                    "planned": planned, "spent": round(spent, 2), "remaining": remaining,
                })
            result.append({
                "group": g["name"],
                "type":  g["type"],
                "planned": round(group_planned, 2),
                "spent":   round(group_spent, 2),
                "remaining": round(group_planned - group_spent, 2),
                "items": items_out,
            })
    return f"Budget for {month}:\n" + str(result)


def tool_get_budget_history(inputs, username):
    num_months = inputs.get("months", 12)
    group_filter = inputs.get("group_name", "").strip().lower()
    item_filter  = inputs.get("item_name",  "").strip().lower()

    if not num_months and not group_filter and not item_filter:
        return (
            "Cannot return all history with no filters — that's 3000+ rows and exceeds "
            "the token limit. Retry with group_name or item_name to get full history for "
            "a specific category. For broad multi-category analysis use months=24 or less."
        )

    with get_db() as conn:
        if num_months and num_months > 0:
            date_clause = "WHERE bh.month >= date('now', '-' || ? || ' months', 'start of month')"
            params: list = [num_months]
        else:
            date_clause = "WHERE 1=1"
            params = []
        query = f"""
            SELECT bh.month,
                   bg.name AS group_name,
                   bi.name AS item_name,
                   ROUND(SUM(bh.amount), 2) AS total_spent,
                   COUNT(*) AS num_transactions,
                   COALESCE(ba.planned, 0) AS planned
            FROM budget_history bh
            JOIN budget_items bi  ON bi.id  = bh.item_id
            JOIN budget_groups bg ON bg.id  = bi.group_id
            LEFT JOIN budget_amounts ba ON ba.item_id = bi.id AND ba.month = bh.month
            {date_clause}
        """
        if group_filter:
            query += " AND LOWER(bg.name) LIKE ?"
            params.append(f"%{group_filter}%")
        if item_filter:
            query += " AND LOWER(bi.name) LIKE ?"
            params.append(f"%{item_filter}%")
        query += " GROUP BY bh.month, bi.id ORDER BY bh.month DESC, bg.name, bi.name"
        rows = conn.execute(query, params).fetchall()
    if not rows:
        return "No budget history found for the requested period."
    period_label = "all available history" if not num_months else f"last {num_months} months"
    return (
        f"Budget history — monthly totals by category ({period_label}, "
        f"{len(rows)} category-months):\n"
        + str([dict(r) for r in rows])
    )


def tool_get_unassigned_transactions(inputs, username):
    from datetime import date as _date
    month = inputs.get("month") or _date.today().strftime("%Y-%m")
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name,
                   ROUND(t.amount, 2) AS amount,
                   a.name AS account_name
            FROM transactions t
            LEFT JOIN accounts a ON a.id = t.account_id
            LEFT JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
            LEFT JOIN transaction_splits ts ON ts.transaction_id = t.transaction_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND (t.budget_deleted IS NULL OR t.budget_deleted = 0)
              AND ta.transaction_id IS NULL
              AND ts.transaction_id IS NULL
            ORDER BY t.date DESC
        """, (month,)).fetchall()
    if not rows:
        return f"No unassigned transactions for {month}."
    return (
        f"Unassigned transactions for {month} ({len(rows)} total):\n"
        + str([dict(r) for r in rows])
    )


def tool_assign_transaction(inputs, username):
    txn_id = inputs.get("transaction_id", "").strip()
    item_id = inputs.get("item_id")
    if not txn_id or not item_id:
        return "Error: transaction_id and item_id are required."
    with get_db() as conn:
        txn = conn.execute(
            "SELECT transaction_id FROM transactions WHERE transaction_id = ?",
            (txn_id,)
        ).fetchone()
        if not txn:
            return f"Transaction {txn_id} not found."
        item = conn.execute(
            "SELECT id, name FROM budget_items WHERE id = ? AND is_archived = 0",
            (item_id,)
        ).fetchone()
        if not item:
            return f"Budget item {item_id} not found."
        conn.execute(
            "DELETE FROM transaction_assignments WHERE transaction_id = ?",
            (txn_id,)
        )
        conn.execute(
            "DELETE FROM transaction_splits WHERE transaction_id = ?",
            (txn_id,)
        )
        conn.execute(
            "INSERT INTO transaction_assignments (transaction_id, item_id, status)"
            " VALUES (?, ?, 'auto')",
            (txn_id, item_id)
        )
        merchant = conn.execute(
            "SELECT COALESCE(merchant_name, name, '') AS m FROM transactions WHERE transaction_id = ?",
            (txn_id,)
        ).fetchone()["m"]
        if merchant:
            conn.execute("""
                INSERT INTO budget_auto_rules (merchant, item_id, match_count)
                VALUES (?, ?, 1)
                ON CONFLICT(merchant, item_id)
                DO UPDATE SET match_count = match_count + 1, updated_at = CURRENT_TIMESTAMP
            """, (merchant, item_id))
    return f"Assigned transaction {txn_id} to budget item '{item['name']}'."


def tool_auto_assign_month(inputs, username):
    from datetime import date as _date
    month = inputs.get("month") or _date.today().strftime("%Y-%m")
    try:
        with get_db() as conn:
            unassigned = conn.execute("""
                SELECT t.transaction_id, COALESCE(t.merchant_name, t.name, '') AS merchant,
                       ROUND(t.amount, 2) AS amount
                FROM transactions t
                LEFT JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
                LEFT JOIN transaction_splits ts ON ts.transaction_id = t.transaction_id
                WHERE strftime('%Y-%m', t.date) = ?
                  AND t.pending = 0
                  AND ta.transaction_id IS NULL
                  AND ts.transaction_id IS NULL
            """, (month,)).fetchall()
            if not unassigned:
                return f"No unassigned transactions for {month}."

            auto_rules = {}
            rule_rows = conn.execute("""
                SELECT merchant, item_id FROM budget_auto_rules
                WHERE item_id IS NOT NULL
                ORDER BY match_count DESC
            """).fetchall()
            for r in rule_rows:
                if r["merchant"] not in auto_rules:
                    auto_rules[r["merchant"]] = r["item_id"]

            assigned = 0
            skipped = 0
            for txn in unassigned:
                nm = txn["merchant"].strip()
                item_id = auto_rules.get(nm)
                if item_id:
                    conn.execute("""
                        INSERT OR IGNORE INTO transaction_assignments
                            (transaction_id, item_id, status)
                        VALUES (?, ?, 'auto')
                    """, (txn["transaction_id"], item_id))
                    assigned += 1
                else:
                    skipped += 1
        return (
            f"Auto-assigned {assigned} of {len(unassigned)} transactions for {month}. "
            f"{skipped} could not be matched (no rule for their merchant). "
            f"Use get_unassigned_transactions to see what's left."
        )
    except Exception as e:
        return f"Auto-assign failed: {e}"


def tool_search_budget_history(inputs, username):
    merchant = inputs.get("merchant", "").strip()
    month = inputs.get("month", "").strip()
    amount = inputs.get("amount")
    limit = min(inputs.get("limit", 20), 100)
    with get_db() as conn:
        query = """
            SELECT bh.month, bh.date, bh.merchant, bh.amount, bh.note,
                   bg.name AS group_name, bi.name AS item_name
            FROM budget_history bh
            JOIN budget_items bi  ON bi.id  = bh.item_id
            JOIN budget_groups bg ON bg.id  = bi.group_id
            WHERE 1=1
        """
        params: list = []
        if merchant:
            query += " AND LOWER(bh.merchant) LIKE ?"
            params.append(f"%{merchant.lower()}%")
        if month:
            query += " AND bh.month = ?"
            params.append(month)
        if amount is not None:
            query += " AND ABS(ABS(bh.amount) - ?) < 0.015"
            params.append(float(amount))
        query += " ORDER BY bh.month DESC, bh.date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    if not rows:
        return "No matching transactions found in budget history."
    return (
        f"Found {len(rows)} matching transaction(s):\n"
        + str([dict(r) for r in rows])
    )


# ── Tax tools ─────────────────────────────────────────────────────────────────

def tool_get_paystubs(inputs, username):
    ytd_only = inputs.get("ytd_only", True)
    with get_db() as conn:
        if ytd_only:
            rows = conn.execute("""
                SELECT p.* FROM paystubs p
                INNER JOIN (
                    SELECT employer, MAX(pay_date) AS latest
                    FROM paystubs GROUP BY employer
                ) l ON p.employer = l.employer AND p.pay_date = l.latest
                ORDER BY p.employer
            """).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paystubs ORDER BY pay_date DESC"
            ).fetchall()
    result = [dict(r) for r in rows]
    if not result:
        return "No paystubs have been uploaded yet."
    return str(result)


def tool_get_vault_documents(inputs, username):
    year = inputs.get("year")
    with get_db() as conn:
        if year:
            rows = conn.execute(
                "SELECT year, category, category_label, issuer, description, original_name, uploaded_at FROM vault_documents WHERE year = ? ORDER BY category",
                (year,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT year, category, category_label, issuer, description, original_name, uploaded_at FROM vault_documents ORDER BY year DESC, category"
            ).fetchall()
    if not rows:
        return f"No documents in vault{' for ' + str(year) if year else ''}."
    return str([dict(r) for r in rows])


def tool_optimize_w4(inputs, username):
    from datetime import date as _date
    from api.tax_calc import calc_tax, get_brackets, get_standard_deduction, CHILD_CREDIT_PER_CHILD, NUM_CHILDREN, SALT_CAP
    target_refund = inputs.get("target_refund", 0)
    year = inputs.get("year", 2025)
    pay_periods_remaining = inputs.get("pay_periods_remaining")

    with get_db() as conn:
        stubs = conn.execute("""
            SELECT p.* FROM paystubs p
            INNER JOIN (SELECT employer, MAX(pay_date) AS latest FROM paystubs GROUP BY employer) l
            ON p.employer = l.employer AND p.pay_date = l.latest
        """).fetchall()
        docs = conn.execute("SELECT * FROM tax_docs WHERE tax_year = ?", (year,)).fetchall()
        w4s = conn.execute("SELECT * FROM w4s").fetchall()

    stubs = [dict(s) for s in stubs]
    docs = [dict(d) for d in docs]
    w4s = [dict(w) for w in w4s]

    if not stubs:
        return "No paystubs uploaded — need paystub data to calculate W-4 optimization."

    def _sum(rows, field): return sum((r.get(field) or 0) for r in rows)

    ytd_gross = _sum(stubs, "ytd_gross")
    ytd_federal = _sum(stubs, "ytd_federal")
    latest_date = max(s["pay_date"] for s in stubs if s.get("pay_date"))
    ld = _date.fromisoformat(latest_date)
    frac = max(0.01, ((ld - _date(year, 1, 1)).days + 1) / 365)
    proj_gross = ytd_gross / frac

    std = get_standard_deduction(year)
    if docs:
        mortgage = _sum(docs, "mortgage_interest") + _sum(docs, "mortgage_points")
        salt = min(SALT_CAP, _sum(docs, "property_taxes") + _sum(docs, "w2_state_withheld"))
        charity = _sum(docs, "charitable_cash") + _sum(docs, "charitable_noncash")
        itemized = mortgage + salt + charity
        ded = itemized if itemized > std else std
    else:
        ded = std

    taxable = max(0, proj_gross - ded)
    brackets = get_brackets(year)
    child_credit = NUM_CHILDREN * CHILD_CREDIT_PER_CHILD
    net_tax = max(0, calc_tax(taxable, brackets) - child_credit)

    needed_total = net_tax - target_refund
    proj_withheld = ytd_federal / frac

    gap = needed_total - proj_withheld

    if not pay_periods_remaining:
        days_left = ((_date(year, 12, 31) - ld).days)
        pay_periods_remaining = max(1, round(days_left / 14))

    extra_per_period = round(gap / pay_periods_remaining, 2) if pay_periods_remaining > 0 else 0
    current_extra = sum(w.get("extra_withholding") or 0 for w in w4s)

    return str({
        "year": year,
        "projected_annual_income": round(proj_gross),
        "projected_tax_liability": round(net_tax),
        "projected_withholding": round(proj_withheld),
        "current_over_under": round(proj_withheld - net_tax),
        "target_refund": target_refund,
        "additional_needed_total": round(gap),
        "pay_periods_remaining": pay_periods_remaining,
        "recommended_extra_withholding_per_period": extra_per_period,
        "current_extra_withholding_on_w4": current_extra,
        "w4_step_4c_change": round(extra_per_period - current_extra, 2),
        "note": (
            f"To get a ~${abs(int(target_refund))} {'refund' if target_refund >= 0 else 'payment'} at filing, "
            f"{'increase' if extra_per_period > current_extra else 'decrease'} Step 4c on your W-4 "
            f"by ${abs(round(extra_per_period - current_extra, 2))}/paycheck. "
            f"Currently {'over-withholding' if proj_withheld > net_tax else 'under-withholding'} "
            f"by ${abs(round(proj_withheld - net_tax))} annually."
        )
    })


def tool_get_draft_return(inputs, username):
    from api.tax_calc import calc_tax, calc_az_tax, get_brackets, get_standard_deduction, CHILD_CREDIT_PER_CHILD, NUM_CHILDREN, SALT_CAP
    year = inputs.get("year", 2025)
    with get_db() as conn:
        docs = conn.execute(
            "SELECT * FROM tax_docs WHERE tax_year = ?", (year,)
        ).fetchall()
        filed = conn.execute(
            "SELECT * FROM tax_returns WHERE tax_year = ?", (year,)
        ).fetchone()
    docs = [dict(d) for d in docs]
    if not docs:
        return f"No tax documents uploaded for {year} yet. Ask the user to upload their W-2s, 1099s, and other tax documents on the Tax page."
    doc_types = list({d["doc_type"] for d in docs})
    missing = []
    if "w2" not in doc_types: missing.append("W-2")
    if "1098" not in doc_types: missing.append("1098 (mortgage interest)")
    def _sum(f): return sum((d.get(f) or 0) for d in docs)
    wages = _sum("w2_wages")
    interest = _sum("interest_income")
    ord_div = _sum("ordinary_dividends")
    cap_gains = _sum("net_cap_gains") + _sum("cap_gains_dist")
    retirement = _sum("taxable_distribution")
    total_income = wages + interest + ord_div + cap_gains + retirement
    hsa_ded = max(0, _sum("hsa_contributions") - _sum("w2_hsa_employer"))
    agi = total_income - hsa_ded
    std = get_standard_deduction(year)
    mortgage = _sum("mortgage_interest") + _sum("mortgage_points")
    salt = min(SALT_CAP, _sum("property_taxes") + _sum("w2_state_withheld"))
    charity = _sum("charitable_cash") + _sum("charitable_noncash")
    itemized = mortgage + salt + charity
    ded_method = "itemized" if itemized > std else "standard"
    ded_amt = itemized if itemized > std else std
    taxable = max(0, agi - ded_amt)
    gross_tax = calc_tax(taxable, get_brackets(year))
    child_credit = NUM_CHILDREN * CHILD_CREDIT_PER_CHILD
    net_tax = max(0, gross_tax - child_credit)
    withheld = _sum("w2_fed_withheld") + sum((d.get("fed_withheld") or 0) for d in docs if d.get("doc_type") != "w2")
    state_withheld = _sum("w2_state_withheld")
    delta = withheld - net_tax
    az_tax = calc_az_tax(taxable)
    az_delta = state_withheld - az_tax
    return str({
        "year": year, "docs_uploaded": doc_types,
        "missing_docs": missing,
        "wages": round(wages), "total_income": round(total_income),
        "agi": round(agi), "deduction_method": ded_method,
        "deduction": round(ded_amt), "itemized_total": round(itemized),
        "taxable_income": round(taxable), "gross_tax": round(gross_tax),
        "child_tax_credit": child_credit, "net_tax": round(net_tax),
        "total_withheld": round(withheld),
        "refund": round(delta) if delta >= 0 else None,
        "owed": round(-delta) if delta < 0 else None,
        "effective_rate": round(net_tax / agi * 100, 2) if agi else None,
        "arizona_tax": round(az_tax), "az_state_withheld": round(state_withheld),
        "az_refund": round(az_delta) if az_delta >= 0 else None,
        "az_owed": round(-az_delta) if az_delta < 0 else None,
        "combined_tax": round(net_tax + az_tax),
        "note": f"Missing documents: {missing}" if missing else "All key documents present."
    })


def tool_get_tax_projection(inputs, username):
    try:
        from api.tax_calc import calc_tax, calc_az_tax, get_brackets, get_standard_deduction, CHILD_CREDIT_PER_CHILD, NUM_CHILDREN
        from datetime import date as _date
        with get_db() as conn:
            stubs = conn.execute("""
                SELECT p.* FROM paystubs p
                INNER JOIN (
                    SELECT employer, MAX(pay_date) AS latest FROM paystubs GROUP BY employer
                ) l ON p.employer = l.employer AND p.pay_date = l.latest
            """).fetchall()
            prior = conn.execute("SELECT * FROM tax_returns WHERE tax_year = 2024").fetchone()
        if not stubs:
            return "No paystubs uploaded yet — ask the user to upload a recent paystub from the Tax page."
        stubs = [dict(s) for s in stubs]
        prior = dict(prior) if prior else {}
        ytd_gross = sum(s.get("ytd_gross") or 0 for s in stubs)
        ytd_federal = sum(s.get("ytd_federal") or 0 for s in stubs)
        ytd_state = sum(s.get("ytd_state") or 0 for s in stubs)
        latest_date = max(s["pay_date"] for s in stubs if s.get("pay_date"))
        ld = _date.fromisoformat(latest_date)
        frac = ((ld - _date(2025, 1, 1)).days + 1) / 365
        frac = max(0.01, min(frac, 1.0))
        proj_gross = round(ytd_gross / frac)
        proj_withheld = round(ytd_federal / frac)
        proj_state_withheld = round(ytd_state / frac)
        prior_itemized = prior.get("total_itemized") or 0
        std_ded = get_standard_deduction(2025)
        deduction = prior_itemized if prior_itemized > std_ded else std_ded
        taxable = max(0, proj_gross - deduction)
        gross_tax = calc_tax(taxable, get_brackets(2025))
        child_credit = NUM_CHILDREN * CHILD_CREDIT_PER_CHILD
        net_tax = max(0, gross_tax - child_credit)
        delta = proj_withheld - net_tax
        az_tax = calc_az_tax(taxable)
        az_delta = proj_state_withheld - az_tax
        return str({
            "year": 2025, "as_of": latest_date,
            "proj_gross": proj_gross, "deduction": deduction,
            "taxable_income": round(taxable), "net_tax": round(net_tax),
            "proj_withheld": proj_withheld,
            "refund": round(delta) if delta > 0 else None,
            "owed": round(-delta) if delta < 0 else None,
            "effective_rate": round(net_tax / proj_gross * 100, 2) if proj_gross else None,
            "arizona_tax": round(az_tax), "az_state_withheld": proj_state_withheld,
            "az_refund": round(az_delta) if az_delta > 0 else None,
            "az_owed": round(-az_delta) if az_delta < 0 else None,
            "combined_tax": round(net_tax + az_tax),
            "note": "Projection based on YTD paystub data extrapolated to full year. Includes AZ flat 2.5% state tax."
        })
    except Exception as e:
        return f"Could not compute projection: {e}"


def tool_get_tax_history(inputs, username):
    year = inputs.get("year")
    with get_db() as conn:
        if year:
            rows = conn.execute(
                "SELECT * FROM tax_returns WHERE tax_year = ?", (year,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tax_returns ORDER BY tax_year ASC"
            ).fetchall()
    result = [dict(r) for r in rows]
    if not result:
        return (
            "No tax returns have been imported yet. The user needs to upload their "
            "1040 PDFs in the Tax section to get started."
        )
    return str(result)


# ── Crypto gains tool ────────────────────────────────────────────────────────

def tool_get_crypto_gains(inputs, username):
    """Return realized crypto capital gains/losses for a tax year.

    Uses pre-computed FIFO lots and gains from the crypto_gains table.
    Returns per-currency breakdown with short-term vs long-term totals.
    """
    year = inputs.get("year", 2025)
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    with get_db() as conn:
        gains = conn.execute("""
            SELECT * FROM crypto_gains
            WHERE sale_date >= ? AND sale_date <= ?
            ORDER BY sale_date ASC
        """, (start, end)).fetchall()
        # Also get open lots for unrealized context
        open_lots = conn.execute("""
            SELECT currency, SUM(quantity_remaining) as qty, SUM(quantity_remaining * cost_per_unit) as cost_basis
            FROM crypto_lots WHERE quantity_remaining > 0
            GROUP BY currency
        """).fetchall()

    gains = [dict(g) for g in gains]
    if not gains and not open_lots:
        return f"No crypto trades or gains data for {year}. Sync trades from Coinbase first (POST /api/crypto/sync-trades), then calculate gains (POST /api/crypto/calculate-gains)."

    short_term = [g for g in gains if g["gain_type"] == "short_term"]
    long_term = [g for g in gains if g["gain_type"] == "long_term"]
    st_net = sum(g["gain_loss"] for g in short_term)
    lt_net = sum(g["gain_loss"] for g in long_term)
    total_proceeds = sum(g["proceeds"] for g in gains)
    total_cost = sum(g["cost_basis"] for g in gains)

    open_lots_summary = [dict(r) for r in open_lots] if open_lots else []

    return str({
        "year": year,
        "transaction_count": len(gains),
        "short_term_net": round(st_net, 2),
        "long_term_net": round(lt_net, 2),
        "net_gain_loss": round(st_net + lt_net, 2),
        "total_proceeds": round(total_proceeds, 2),
        "total_cost_basis": round(total_cost, 2),
        "open_lots": open_lots_summary,
        "note": (
            f"{len(short_term)} short-term and {len(long_term)} long-term dispositions. "
            f"Short-term gains are taxed as ordinary income; long-term at preferential rates."
        ),
    })


# ── Calendar tool ─────────────────────────────────────────────────────────────

def tool_get_upcoming_events(inputs, username):
    """Return the next N days of financial calendar events for this user.

    Useful for proactive reminders — call this at the start of a session to
    notice upcoming tax deadlines, budget meetings, or payday markers.
    """
    from datetime import date as _date, timedelta
    days = min(inputs.get("days", 30), 90)
    today = _date.today()
    until = (today + timedelta(days=days)).isoformat()
    today_str = today.isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT title, description, start_dt, end_dt, all_day, event_type, days_until
               FROM (
                 SELECT title, description, start_dt, end_dt, all_day, event_type,
                        CAST(julianday(DATE(start_dt)) - julianday(?) AS INTEGER) AS days_until
                 FROM financial_events
                 WHERE username = ? AND is_active = 1
                   AND DATE(start_dt) BETWEEN ? AND ?
               ) ORDER BY start_dt""",
            (today_str, username, today_str, until),
        ).fetchall()
    if not rows:
        return f"No upcoming financial events in the next {days} days."
    result = [dict(r) for r in rows]
    return f"Upcoming events (next {days} days):\n" + str(result)


def tool_get_ticker_quotes(inputs, username):
    """Return cached price quotes for all held tickers."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT symbol, asset_type, price, change_pct, source, fetched_at "
            "FROM ticker_quotes ORDER BY asset_type, symbol"
        ).fetchall()
    if not rows:
        return "No ticker quotes cached yet. The user should visit the Dashboard to trigger a price refresh, or you can suggest they check back shortly."
    lines = []
    for r in rows:
        chg = f" ({r['change_pct']:+.2f}%)" if r["change_pct"] is not None else ""
        lines.append(f"{r['symbol']} ({r['asset_type']}): ${r['price']:,.2f}{chg} [via {r['source']}, {r['fetched_at']}]")
    return "Current portfolio quotes:\n" + "\n".join(lines)


def tool_get_financial_news(inputs, username):
    """Return cached financial news articles."""
    limit = min(inputs.get("limit", 10), 20)
    topic = inputs.get("topic", "")

    with get_db() as conn:
        if topic:
            rows = conn.execute(
                "SELECT title, url, source_name, snippet, relevance, published_at "
                "FROM news_articles WHERE relevance = ? "
                "ORDER BY fetched_at DESC, published_at DESC LIMIT ?",
                (topic, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT title, url, source_name, snippet, relevance, published_at "
                "FROM news_articles ORDER BY fetched_at DESC, published_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    if not rows:
        return "No cached news articles. The user should visit the Dashboard to trigger a news refresh."
    lines = []
    for r in rows:
        src = f" ({r['source_name']})" if r["source_name"] else ""
        lines.append(f"[{r['relevance']}] {r['title']}{src}\n  {r['snippet'][:200]}\n  {r['url']}")
    return f"Financial news ({len(rows)} articles):\n\n" + "\n\n".join(lines)


# ── Dispatch table ────────────────────────────────────────────────────────────

TOOL_DISPATCH = {
    "get_net_worth":              tool_get_net_worth,
    "get_net_worth_history":      tool_get_net_worth_history,
    "get_accounts":               tool_get_accounts,
    "get_transactions":           tool_get_transactions,
    "get_manual_entries":         tool_get_manual_entries,
    "get_notes":                  tool_get_notes,
    "update_notes":               tool_update_notes,
    "web_search":                 tool_web_search,
    "fetch_page":                 tool_fetch_page,
    "get_budget":                 tool_get_budget,
    "get_budget_history":         tool_get_budget_history,
    "get_unassigned_transactions": tool_get_unassigned_transactions,
    "assign_transaction":         tool_assign_transaction,
    "auto_assign_month":          tool_auto_assign_month,
    "search_budget_history":      tool_search_budget_history,
    "get_paystubs":               tool_get_paystubs,
    "get_vault_documents":        tool_get_vault_documents,
    "optimize_w4":                tool_optimize_w4,
    "get_draft_return":           tool_get_draft_return,
    "get_tax_projection":         tool_get_tax_projection,
    "get_tax_history":            tool_get_tax_history,
    "get_upcoming_events":        tool_get_upcoming_events,
    "get_crypto_gains":           tool_get_crypto_gains,
    "get_ticker_quotes":          tool_get_ticker_quotes,
    "get_financial_news":         tool_get_financial_news,
}
