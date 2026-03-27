"""
Sage — AI financial advisor powered by Claude Haiku.
Tool-use loop with access to all financial data, persistent notes, and web search.
"""
import os
import logging
from pathlib import Path
import anthropic
import httpx

from api.database import get_db

logger = logging.getLogger("vaultic.sage")

NOTES_PATH = Path(__file__).parent.parent / "data" / "sage_notes.md"
MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are Sage — a personal CFO, wealth-building advisor, tax expert, and financial thought partner. You are a man. You live inside Vaultic, the user's personal financial command center.

You have direct, real-time access to their complete financial picture through your tools. Before answering any question about their finances, ALWAYS call the relevant tools to get current data — never make up numbers or estimate when you can look it up.

You also have full internet access via web_search and fetch_page. Use these to:
- Look up current stock prices, crypto prices, market data
- Research current tax law, IRS rules, contribution limits, bracket thresholds
- Find current mortgage rates, CD rates, HYSA rates
- Research any financial topic the user asks about
- Get news about their holdings or institutions

## Tax expertise
You are their personal tax advisor. You know their complete tax history (2019-2024) and have access to all uploaded tax documents for the current year. When answering tax questions:
- Always call get_tax_history first to see their actual historical numbers
- Call get_draft_return for the current year if they've uploaded documents
- Call get_paystubs for YTD income and withholding data
- Call get_tax_projection for a full-year estimate
- Use web_search to verify current IRS rules, limits, and brackets — tax law changes every year
- Give specific, actionable advice: exact dollar amounts, which lines to change on their W-4, whether to itemize
- Proactively flag things they haven't asked about: over-withholding, missed deductions, opportunities

Their tax profile:
- Filing status: Married Filing Jointly (Jason + Heather)
- Dependents: two sons, John and Milo (child tax credit eligible)
- Income: high W-2 earners (~$280k+), Jason is Software Developer, Heather is Stay-at-Home Parent
- Location: Phoenix, AZ
- Always itemized deductions 2019-2024 (mortgage interest + SALT + charitable giving consistently beat standard)
- Consistently over-withholding — large refunds every year ($7k-$13k range)
- Accounts: Vanguard/Voya/Insperity 401ks, Rocket Mortgage, Health Equity/Optum HSA

## Your personality
- Direct and confident — give specific advice, not "consult a tax professional" disclaimers
- Proactive — notice things they haven't asked about (over-withholding, deduction opportunities, etc.)
- Educational — explain the why behind your recommendations
- Personal — always use their actual numbers, never hypotheticals

## Financial tools
- All bank accounts, credit cards, mortgage (via Plaid)
- 401k accounts: Vanguard, Voya, Insperity
- Brokerage: Robinhood; Crypto: Coinbase
- Manual entries: home value ($655k), car, credit score
- Parker Financial IRAs and college fund
- Full budget and transaction history
- Tax returns 2019-2024, current-year documents, paystubs, W-4s, draft return

On every new conversation, read your notes first — they contain important context about the user's goals and preferences. Update notes whenever you learn something worth remembering.

Keep responses concise but substantive. Use their actual numbers. Be the trusted financial advisor and tax expert they never have to pay for."""

TOOLS = [
    {
        "name": "get_net_worth",
        "description": "Get the latest net worth snapshot with full breakdown by category (liquid, invested, real estate, vehicles, crypto, liabilities)",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_net_worth_history",
        "description": "Get net worth history over time for trend analysis",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Days of history to return (default 90)"}
            },
            "required": [],
        },
    },
    {
        "name": "get_accounts",
        "description": "Get all connected accounts with current balances, types, and institution names",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_transactions",
        "description": "Get recent transactions across all accounts for spending analysis",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of transactions to return (default 50, max 200)"}
            },
            "required": [],
        },
    },
    {
        "name": "get_manual_entries",
        "description": "Get manually entered assets and values (home value, car value, credit score, other assets/liabilities)",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_notes",
        "description": "Read your persistent notes about the user's financial goals, preferences, situation, and anything important to remember across sessions",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_notes",
        "description": "Update your persistent notes. Use this to remember goals, decisions, important context, or anything the user wants you to keep in mind. This replaces the full notes file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "notes": {"type": "string", "description": "Complete updated notes in markdown format"}
            },
            "required": ["notes"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for current financial data, market prices, tax rules, interest rates, news, or any information you need. Use this whenever the user asks about current prices, rates, or anything requiring up-to-date information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": "Fetch the text content of a specific web page — useful for reading a full article, IRS page, or detailed financial data after a web search returns relevant URLs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_budget",
        "description": "Get the full zero-based budget for a specific month — all groups (Income, Housing, Food, etc.), their line items, planned amounts, and actual spending. Use this to answer questions about budget targets, spending vs. plan, or whether the user is over/under budget in any category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month in YYYY-MM format (e.g. '2026-03'). Defaults to current month if omitted."}
            },
            "required": [],
        },
    },
    {
        "name": "get_budget_history",
        "description": (
            "Get historical spending totals by budget category — data goes back to October 2015 (10+ years). "
            "Returns monthly aggregated totals (group, item, total_spent, num_transactions) per category-month. "
            "Use group_name and/or item_name filters to narrow results. "
            "IMPORTANT: pass months=0 ONLY when also providing group_name or item_name — "
            "requesting all history with no filter returns 3000+ rows and will exceed token limits. "
            "For a specific item across all time (e.g. 'Jason Income since 2015'), use months=0 + item_name. "
            "For trends in one group, use months=0 + group_name. "
            "For broad multi-category analysis, use a month range (e.g. months=24)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "months": {"type": "integer", "description": "Months of history to return (default 12, pass 0 for all history back to Oct 2015 — requires group_name or item_name filter when using 0)"},
                "group_name": {"type": "string", "description": "Filter to a specific budget group (e.g. 'Food', 'Housing', 'Income')"},
                "item_name": {"type": "string", "description": "Filter to a specific budget line item by name (e.g. 'Jason Income', 'Mortgage', 'Groceries') — partial match"}
            },
            "required": [],
        },
    },
    {
        "name": "get_unassigned_transactions",
        "description": "Get transactions that have not yet been assigned to a budget item for a given month. Use this to see what needs to be categorized, then call assign_transaction for each one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month in YYYY-MM format (e.g. '2026-03'). Defaults to current month."}
            },
            "required": [],
        },
    },
    {
        "name": "assign_transaction",
        "description": "Assign a single Plaid transaction to a budget line item. Use this to categorize transactions. Get item IDs from get_budget. Get transaction IDs from get_unassigned_transactions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "The transaction_id from get_unassigned_transactions"},
                "item_id": {"type": "integer", "description": "The budget item id from get_budget to assign this transaction to"}
            },
            "required": ["transaction_id", "item_id"],
        },
    },
    {
        "name": "auto_assign_month",
        "description": "Run automatic categorization on all unassigned transactions for a month using learned merchant→budget-item rules from historical data. This is the fastest way to bulk-assign transactions. After running, check remaining unassigned with get_unassigned_transactions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month in YYYY-MM format (e.g. '2026-03'). Defaults to current month."}
            },
            "required": [],
        },
    },
    {
        "name": "search_budget_history",
        "description": "Search budget history for specific transactions by merchant name and/or amount. Use this when the user asks about a specific charge — e.g. 'which budget item did Amazon $8.47 go to in April 2022?' Returns individual matching rows with the merchant, amount, date, and which budget item it was assigned to. Much more efficient than get_budget_history for targeted lookups.",
        "input_schema": {
            "type": "object",
            "properties": {
                "merchant": {"type": "string", "description": "Merchant name to search for (case-insensitive, partial match — e.g. 'amazon', 'target', 'netflix')"},
                "month": {"type": "string", "description": "Optional: narrow to a specific month in YYYY-MM format"},
                "amount": {"type": "number", "description": "Optional: filter by exact dollar amount (absolute value, e.g. 8.47)"},
                "limit": {"type": "integer", "description": "Max rows to return (default 20, max 100)"}
            },
            "required": [],
        },
    },
    {
        "name": "get_paystubs",
        "description": "Get paystub data including YTD gross income, federal/state withholding, Social Security, and Medicare. Use when the user asks about their paycheck, YTD earnings, current withholding, or wants to know how much they've made or withheld so far this year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ytd_only": {
                    "type": "boolean",
                    "description": "If true, return only the most recent paystub per employer (which has the YTD totals). Default true."
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_vault_documents",
        "description": "List all documents stored in the document vault, optionally filtered by year. Shows what financial documents have been uploaded (tax returns, W-2s, 1099s, paystubs, investment statements, etc.) and what's missing from the checklist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Filter by tax year. Omit for all years."}
            },
            "required": [],
        },
    },
    {
        "name": "optimize_w4",
        "description": "Calculate optimal W-4 withholding adjustments. Given a target refund/owed amount, computes what Step 4c (extra withholding per paycheck) should be set to on the employee's W-4. Uses actual draft return or projection data. Use when the user asks about adjusting withholding, W-4 changes, or wants to stop over/under withholding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_refund": {"type": "number", "description": "Desired refund at filing (e.g. 0 for break-even, 500 for small refund). Default 0."},
                "year": {"type": "integer", "description": "Tax year to optimize for. Default 2025."},
                "pay_periods_remaining": {"type": "integer", "description": "Pay periods left in the year. If omitted, calculated from current date."},
            },
            "required": [],
        },
    },
    {
        "name": "get_draft_return",
        "description": "Calculate a complete draft 1040 for a given tax year using all uploaded tax documents (W-2s, 1099s, 1098s, giving statements). Returns line-by-line income, deductions, tax, withholding, and estimated refund or amount owed. Use when the user wants to know their tax situation for a specific year based on actual documents they've uploaded.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Tax year (e.g. 2025)"}
            },
            "required": ["year"],
        },
    },
    {
        "name": "get_tax_projection",
        "description": "Get a projected tax liability estimate for 2025 using YTD paystub data extrapolated to a full year. Returns projected gross income, deduction, taxable income, estimated tax, projected withholding, and estimated refund or amount owed. Use when the user asks about their 2025 taxes, whether they owe money, if they're on track with withholding, or wants a tax estimate.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_tax_history",
        "description": "Get the user's tax return history. Returns year-over-year income, AGI, effective tax rate, deductions, refunds/owed, and key deduction items (mortgage interest, charitable giving, SALT). Use this when the user asks about taxes, their tax history, withholding, whether they should itemize, W-4 adjustments, or any tax-related question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "Specific tax year to retrieve (e.g. 2024). Omit to get all years."
                }
            },
            "required": [],
        },
    },
]


def _call_tool(name: str, inputs: dict) -> str:
    try:
        if name == "get_net_worth":
            with get_db() as conn:
                row = conn.execute(
                    "SELECT * FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT 1"
                ).fetchone()
            if not row:
                return "No net worth data yet. Connect accounts and sync to get started."
            return str(dict(row))

        elif name == "get_net_worth_history":
            days = inputs.get("days", 90)
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT snapped_at, total, liquid, invested, real_estate, vehicles, liabilities FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT ?",
                    (days,)
                ).fetchall()
            return str([dict(r) for r in rows])

        elif name == "get_accounts":
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

        elif name == "get_transactions":
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

        elif name == "get_manual_entries":
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT name, category, value, notes, entered_at FROM manual_entries ORDER BY entered_at DESC"
                ).fetchall()
            return str([dict(r) for r in rows])

        elif name == "get_notes":
            if NOTES_PATH.exists():
                return NOTES_PATH.read_text()
            return "No notes yet."

        elif name == "update_notes":
            notes = inputs.get("notes", "")
            NOTES_PATH.parent.mkdir(exist_ok=True)
            NOTES_PATH.write_text(notes)
            return "Notes updated."

        elif name == "web_search":
            return _tavily_search(inputs.get("query", ""))

        elif name == "fetch_page":
            return _fetch_page(inputs.get("url", ""))

        elif name == "get_budget":
            from datetime import date as _date
            month = inputs.get("month") or _date.today().strftime("%Y-%m")
            with get_db() as conn:
                groups = conn.execute("""
                    SELECT id, name, type FROM budget_groups
                    WHERE is_deleted = 0 ORDER BY display_order, id
                """).fetchall()
                result = []
                for g in groups:
                    items = conn.execute("""
                        SELECT bi.id, bi.name,
                               COALESCE(ba.planned, 0) AS planned,
                               COALESCE(
                                   (SELECT ROUND(SUM(ABS(t.amount)), 2)
                                    FROM transaction_assignments ta
                                    JOIN transactions t ON t.transaction_id = ta.transaction_id
                                    WHERE ta.item_id = bi.id
                                      AND strftime('%Y-%m', t.date) = ?
                                      AND t.pending = 0), 0
                               ) + COALESCE(
                                   (SELECT ROUND(SUM(ts.amount), 2)
                                    FROM transaction_splits ts
                                    JOIN transactions t ON t.transaction_id = ts.transaction_id
                                    WHERE ts.item_id = bi.id
                                      AND strftime('%Y-%m', t.date) = ?
                                      AND t.pending = 0), 0
                               ) AS spent
                        FROM budget_items bi
                        LEFT JOIN budget_amounts ba ON ba.item_id = bi.id AND ba.month = ?
                        WHERE bi.group_id = ? AND bi.is_deleted = 0
                        ORDER BY bi.display_order, bi.id
                    """, (month, month, month, g["id"])).fetchall()
                    group_planned = sum(i["planned"] for i in items)
                    group_spent   = sum(i["spent"]   for i in items)
                    result.append({
                        "group": g["name"],
                        "type":  g["type"],
                        "planned": group_planned,
                        "spent":   group_spent,
                        "remaining": group_planned - group_spent,
                        "items": [dict(i) for i in items],
                    })
            return f"Budget for {month}:\n" + str(result)

        elif name == "get_budget_history":
            # Returns monthly totals aggregated by group+item — NOT individual transactions.
            # Raw rows would be thousands of records (10+ years × 50 txns/month) which
            # blows up the input token budget. Aggregated totals are ~10-20x smaller.
            # months=0 means all history, but requires a group or item filter to stay
            # within Claude's 200K token context limit.
            num_months = inputs.get("months", 12)
            group_filter = inputs.get("group_name", "").strip().lower()
            item_filter  = inputs.get("item_name",  "").strip().lower()

            # Guard: all-history with no filter = ~3000+ rows = token overflow.
            # Fall back to 24 months so Sage at least gets something useful,
            # and tell it to retry with a filter for the full range.
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
                           ROUND(SUM(ABS(bh.amount)), 2) AS total_spent,
                           COUNT(*) AS num_transactions
                    FROM budget_history bh
                    JOIN budget_items bi  ON bi.id  = bh.item_id
                    JOIN budget_groups bg ON bg.id  = bi.group_id
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

        elif name == "get_unassigned_transactions":
            from datetime import date as _date
            month = inputs.get("month") or _date.today().strftime("%Y-%m")
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT t.transaction_id, t.date, t.name, t.merchant_name,
                           ROUND(ABS(t.amount), 2) AS amount,
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

        elif name == "assign_transaction":
            txn_id = inputs.get("transaction_id", "").strip()
            item_id = inputs.get("item_id")
            if not txn_id or not item_id:
                return "Error: transaction_id and item_id are required."
            with get_db() as conn:
                # Verify transaction exists
                txn = conn.execute(
                    "SELECT transaction_id FROM transactions WHERE transaction_id = ?",
                    (txn_id,)
                ).fetchone()
                if not txn:
                    return f"Transaction {txn_id} not found."
                # Verify item exists
                item = conn.execute(
                    "SELECT id, name FROM budget_items WHERE id = ? AND is_deleted = 0",
                    (item_id,)
                ).fetchone()
                if not item:
                    return f"Budget item {item_id} not found."
                # Assign (upsert — replaces any prior assignment)
                conn.execute(
                    "DELETE FROM transaction_assignments WHERE transaction_id = ?",
                    (txn_id,)
                )
                conn.execute(
                    "INSERT INTO transaction_assignments (transaction_id, item_id, status)"
                    " VALUES (?, ?, 'auto')",
                    (txn_id, item_id)
                )
                # Update or insert auto-rule so this merchant→item mapping is learned
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

        elif name == "auto_assign_month":
            from datetime import date as _date
            import httpx as _httpx
            month = inputs.get("month") or _date.today().strftime("%Y-%m")
            # Call the internal auto-assign endpoint via HTTP (reuses the full algorithm)
            # This is intentionally a local call — no auth needed within the same process.
            try:
                with get_db() as conn:
                    # Run auto-assign logic directly (inline to avoid circular imports)
                    unassigned = conn.execute("""
                        SELECT t.transaction_id, COALESCE(t.merchant_name, t.name, '') AS merchant,
                               ROUND(ABS(t.amount), 2) AS amount
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

                    # Load auto-rules: merchant → highest-match-count item_id
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

        elif name == "search_budget_history":
            # Targeted transaction search — for specific merchant/amount questions.
            # Much cheaper than get_budget_history for "which item did Amazon $8.47 go to?"
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
                    # Match within a cent to handle float rounding
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

        elif name == "get_paystubs":
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

        elif name == "get_vault_documents":
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

        elif name == "optimize_w4":
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

            # Get projected figures
            ytd_gross = _sum(stubs, "ytd_gross")
            ytd_federal = _sum(stubs, "ytd_federal")
            latest_date = max(s["pay_date"] for s in stubs if s.get("pay_date"))
            ld = _date.fromisoformat(latest_date)
            frac = max(0.01, ((ld - _date(year, 1, 1)).days + 1) / 365)
            proj_gross = ytd_gross / frac

            # Deduction
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

            # How much total withholding is needed
            needed_total = net_tax - target_refund
            proj_withheld = ytd_federal / frac

            # Gap between projected withholding and needed
            gap = needed_total - proj_withheld  # positive = need more, negative = over-withholding

            # Pay periods remaining
            if not pay_periods_remaining:
                days_left = ((_date(year, 12, 31) - ld).days)
                pay_periods_remaining = max(1, round(days_left / 14))  # assume bi-weekly

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

        elif name == "get_draft_return":
            from api.routers.tax import get_draft_return as _draft
            year = inputs.get("year", 2025)
            from api.tax_calc import calc_tax, get_brackets, get_standard_deduction, CHILD_CREDIT_PER_CHILD, NUM_CHILDREN, SALT_CAP
            from api.routers.tax import _DOC_TYPE_LABELS
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
            delta = withheld - net_tax
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
                "note": f"Missing documents: {missing}" if missing else "All key documents present."
            })

        elif name == "get_tax_projection":
            try:
                from api.tax_calc import calc_tax, get_brackets, get_standard_deduction, CHILD_CREDIT_PER_CHILD, NUM_CHILDREN
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
                latest_date = max(s["pay_date"] for s in stubs if s.get("pay_date"))
                ld = _date.fromisoformat(latest_date)
                frac = ((ld - _date(2025, 1, 1)).days + 1) / 365
                frac = max(0.01, min(frac, 1.0))
                proj_gross = round(ytd_gross / frac)
                proj_withheld = round(ytd_federal / frac)
                prior_itemized = prior.get("total_itemized") or 0
                std_ded = get_standard_deduction(2025)
                deduction = prior_itemized if prior_itemized > std_ded else std_ded
                taxable = max(0, proj_gross - deduction)
                gross_tax = calc_tax(taxable, get_brackets(2025))
                child_credit = NUM_CHILDREN * CHILD_CREDIT_PER_CHILD
                net_tax = max(0, gross_tax - child_credit)
                delta = proj_withheld - net_tax
                return str({
                    "year": 2025, "as_of": latest_date,
                    "proj_gross": proj_gross, "deduction": deduction,
                    "taxable_income": round(taxable), "net_tax": round(net_tax),
                    "proj_withheld": proj_withheld,
                    "refund": round(delta) if delta > 0 else None,
                    "owed": round(-delta) if delta < 0 else None,
                    "effective_rate": round(net_tax / proj_gross * 100, 2) if proj_gross else None,
                    "note": "Projection based on YTD paystub data extrapolated to full year. Deduction estimated from prior year Schedule A if itemized > standard."
                })
            except Exception as e:
                return f"Could not compute projection: {e}"

        elif name == "get_tax_history":
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

        return f"Unknown tool: {name}"
    except Exception as e:
        logger.error(f"Tool {name} error: {e}")
        return f"Error calling {name}: {e}"


def _tavily_search(query: str) -> str:
    """
    Search the web via Tavily's AI-native search API.

    Why Tavily instead of Brave or Google Custom Search?
    - Tavily returns an AI-synthesized direct answer in addition to raw results,
      which lets Claude skip follow-up fetch_page calls for common questions
      (e.g. "what is the 2025 401k contribution limit?").
    - `include_answer: True` asks Tavily to generate a concise answer string
      from its own model. Claude sees this at the top of the result, reducing
      round-trips and token usage.
    - We intentionally do NOT fire parallel fetches for each result URL here —
      that would be slow and expensive for the vast majority of queries where
      Tavily's snippets + direct answer are sufficient. Claude can call
      fetch_page() explicitly if it needs the full body of a specific URL.
    """
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return "Web search unavailable — TAVILY_API_KEY not configured in .env"
    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                # include_answer: Tavily generates a short answer synthesized from
                # search results — saves Claude from needing to infer it from snippets
                "include_answer": True,
                "max_results": 5,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        lines = [f"Search results for: {query}\n"]
        if data.get("answer"):
            lines.append(f"**Direct answer:** {data['answer']}\n")
        for r in data.get("results", []):
            lines.append(f"**{r.get('title', '')}**")
            lines.append(f"URL: {r.get('url', '')}")
            lines.append(r.get("content", ""))
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Tavily search error: {e}")
        return f"Search failed: {e}"


def _is_url_safe(url: str) -> bool:
    """Block requests to private/internal networks (SSRF prevention)."""
    from urllib.parse import urlparse
    import ipaddress
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname or ""
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1", ""):
        return False
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass  # Not an IP literal — hostname is fine
    return True


def _fetch_page(url: str) -> str:
    if not _is_url_safe(url):
        return "Blocked: cannot fetch private/internal URLs"
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Vaultic/1.0)"},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        # Strip HTML tags simply
        import re
        text = re.sub(r"<style[^>]*>.*?</style>", "", resp.text, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{3,}", "\n\n", text)
        return text[:8000]  # cap at 8k chars
    except Exception as e:
        logger.error(f"fetch_page error for {url}: {e}")
        return f"Could not fetch page: {e}"


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """
    Remove orphaned tool_use/tool_result pairs from message history.

    The Anthropic API requires that every assistant message containing tool_use
    blocks is immediately followed by a user message containing matching
    tool_result blocks. This invariant can break when a request is interrupted
    mid-tool-call (e.g. network error, server restart) — the tool_use gets
    stored in sessionStorage but its tool_result never arrives.

    On the next turn, Claude sees the dangling tool_use and returns a 400.
    This function walks the history and drops any assistant message whose
    tool_use IDs don't have a complete matching tool_result in the next message.
    We also drop the following message if it's a partial/mismatched tool_result
    turn, to keep the history coherent.
    """
    sanitized = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        content = msg.get("content", [])

        if msg["role"] == "assistant" and isinstance(content, list):
            tool_use_ids = {
                b["id"] for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            }
            if tool_use_ids:
                next_msg = messages[i + 1] if i + 1 < len(messages) else None
                next_content = next_msg.get("content", []) if next_msg else []
                result_ids = {
                    b["tool_use_id"] for b in next_content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                } if isinstance(next_content, list) else set()

                if tool_use_ids == result_ids:
                    # Valid complete pair — keep both
                    sanitized.append(msg)
                    sanitized.append(next_msg)
                    i += 2
                    continue
                else:
                    # Orphaned tool_use — drop this message and the next if it
                    # looks like a partial tool_result turn
                    logger.warning(
                        f"Dropping orphaned tool_use message (ids={tool_use_ids}, "
                        f"got results for={result_ids})"
                    )
                    if next_msg and isinstance(next_content, list) and any(
                        b.get("type") == "tool_result" for b in next_content
                        if isinstance(b, dict)
                    ):
                        i += 2  # drop both
                    else:
                        i += 1  # drop just the orphaned assistant message
                    continue

        sanitized.append(msg)
        i += 1
    return sanitized


def _truncate_history_tool_results(messages: list[dict], max_chars: int = 800) -> list[dict]:
    """
    Cap the content length of tool_result blocks in old history messages.

    Large tool responses (e.g. a get_budget_history call that returned 200 rows)
    get stored in the conversation history and then resent verbatim to the API on
    every subsequent turn. With a 20-message window and a couple of budget calls,
    this easily blows the 50K TPM rate limit.

    This function truncates any tool_result content in the history that exceeds
    max_chars, appending a "[truncated — use a tool to re-fetch if needed]" marker
    so Claude knows the data was cut. We apply this AFTER trim so only the messages
    that survive trimming are charged against the token budget.

    The most recent turn is NOT truncated — only messages that are already in
    history before the new user message is appended.
    """
    result = []
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue
        new_content = []
        changed = False
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and isinstance(block.get("content"), str)
                and len(block["content"]) > max_chars
            ):
                truncated = block["content"][:max_chars] + " [truncated — use a tool to re-fetch if needed]"
                new_content.append({**block, "content": truncated})
                changed = True
            else:
                new_content.append(block)
        result.append({**msg, "content": new_content} if changed else msg)
    return result


def _trim_history(messages: list[dict], keep: int = 20) -> list[dict]:
    """
    Trim message history to at most `keep` recent messages, always starting
    on a plain user message (not a tool_result turn) so the resulting history
    is structurally valid before sanitize runs.

    Order of operations in chat():
      1. trim   — drop old messages, find a safe start boundary
      2. sanitize — remove any orphaned tool_use/tool_result pairs
      3. append user message — add the new turn
    Sanitize MUST run after trim so it can fix any pairs that were split by
    the trim boundary. Running sanitize before trim is what caused the
    messages.1 orphan bug — trim would then create new orphans that sanitize
    never saw.
    """
    if len(messages) <= keep:
        return messages
    trimmed = messages[-keep:]
    # Advance past any leading tool_result-only user turns (second half of a
    # tool pair) to ensure we start on a genuine conversational user message.
    for i, msg in enumerate(trimmed):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            continue  # this is a tool_result turn, not a safe start
        return trimmed[i:]
    return trimmed


def chat(messages: list[dict], user_message: str, attachments: list[dict] | None = None) -> tuple[str, list[dict]]:
    """
    Run one turn of conversation with Sage.
    Returns (sage_response_text, updated_messages).
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # Order matters: trim → truncate old tool results → sanitize → append new user message.
    # Truncating large tool_result content in history prevents token accumulation
    # across turns (a 200-row budget response resent on every turn quickly blows
    # the 50K TPM rate limit even after we fixed get_budget_history to aggregate).
    messages = _trim_history(list(messages))
    messages = _truncate_history_tool_results(messages)
    messages = _sanitize_messages(messages)

    # Build user content — plain string for text-only, list of blocks when
    # attachments are present (images go as vision blocks, text files as text).
    if attachments:
        content_blocks = []
        for att in attachments:
            if att.get("type") == "image":
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": att["media_type"],
                        "data": att["content"],
                    },
                })
            elif att.get("type") == "text":
                snippet = att["content"][:8000]  # cap per attachment to avoid blowing context
                truncation_note = " [truncated]" if att.get("truncated") else ""
                content_blocks.append({
                    "type": "text",
                    "text": f"[Attached file: {att['filename']}{truncation_note}]\n\n{snippet}",
                })
        if user_message:
            content_blocks.append({"type": "text", "text": user_message})
        messages = messages + [{"role": "user", "content": content_blocks}]
    else:
        messages = messages + [{"role": "user", "content": user_message}]

    while True:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # model_dump() converts each Anthropic SDK content block (a typed Pydantic
        # object like TextBlock or ToolUseBlock) into a plain dict. This is required
        # for two reasons:
        #  1. FastAPI/Pydantic cannot serialize SDK-typed objects when they appear
        #     inside the `history` list returned to the frontend.
        #  2. On subsequent turns, the messages list is passed back to
        #     client.messages.create(). The Anthropic SDK accepts plain dicts for
        #     message content, but NOT its own typed objects from a prior call.
        # Without model_dump() here, Sage would 500 on any multi-turn conversation
        # or any turn that involved a tool call.
        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})

        if resp.stop_reason == "end_turn":
            text = ""
            for block in resp.content:
                if hasattr(block, "text"):
                    text += block.text
            return text.strip(), messages

        if resp.stop_reason == "tool_use":
            # Claude requested one or more tool calls in this turn.
            # The Anthropic API allows multiple tool_use blocks in a single response
            # (e.g. Claude might call get_accounts AND get_net_worth simultaneously).
            # We execute all of them and bundle the results into a single "user" turn
            # with multiple tool_result blocks — this is the required API shape for
            # returning tool results back to the model.
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    result = _call_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,   # must match the id from the tool_use block
                        "content": result,
                    })
            # Append all results as a single user turn — the loop then calls Claude
            # again with this updated context so it can generate its final response.
            messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop_reason (most likely "max_tokens" — response was
            # cut off). Return whatever text was generated rather than a generic
            # error — a partial answer is more useful than nothing.
            text = ""
            for block in resp.content:
                if hasattr(block, "text"):
                    text += block.text
            if text.strip():
                if resp.stop_reason == "max_tokens":
                    text = text.strip() + " *(response cut off — ask me to continue)*"
                return text.strip(), messages
            break

    return "I encountered an unexpected issue. Please try again.", messages
