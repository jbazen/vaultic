"""Zero-based monthly budgeting router.

Endpoints are organized around the zero-based philosophy: every dollar of income
is assigned to a named spending or saving category. The month string 'YYYY-MM'
is the primary key for all planned amounts.

Tables used (created by migration — not here):
  budget_groups         — named groups of line items (Income, Housing, Food, …)
  budget_items          — individual line items within a group
  budget_amounts        — planned dollar amount per (item, month)
  transaction_assignments — links a Plaid transaction_id to a budget item
  transactions          — Plaid transactions; amount > 0 = outflow, amount < 0 = inflow
"""
import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from typing import List
from api.dependencies import get_current_user
from api.database import get_db

router = APIRouter(prefix="/api/budget", tags=["budget"])

# ---------------------------------------------------------------------------
# Default template — groups and items seeded on first use
# ---------------------------------------------------------------------------
TEMPLATE = [
    {"name": "Income",         "type": "income",   "items": ["Paycheck 1", "Paycheck 2", "Side Income"]},
    {"name": "Giving",         "type": "expense",  "items": ["Charitable Giving"]},
    {"name": "Savings",        "type": "expense",  "items": ["Emergency Fund", "Retirement", "Other Savings"]},
    {"name": "Housing",        "type": "expense",  "items": ["Mortgage / Rent", "Electricity", "Water", "Gas", "Internet", "Home Insurance"]},
    {"name": "Food",           "type": "expense",  "items": ["Groceries", "Dining Out"]},
    {"name": "Transportation", "type": "expense",  "items": ["Car Payment", "Gas", "Car Insurance", "Car Maintenance"]},
    {"name": "Personal",       "type": "expense",  "items": ["Clothing", "Haircuts", "Personal Care"]},
    {"name": "Lifestyle",      "type": "expense",  "items": ["Entertainment", "Subscriptions"]},
    {"name": "Health",         "type": "expense",  "items": ["Health Insurance", "Doctor / Dental", "Gym", "Prescriptions"]},
    {"name": "Debt",           "type": "expense",  "items": ["Credit Card Payments", "Student Loans"]},
]

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _validate_month(month: str):
    """Raise 422 if month does not match YYYY-MM."""
    if not _MONTH_RE.match(month):
        raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")


# ---------------------------------------------------------------------------
# Pydantic request bodies
# ---------------------------------------------------------------------------

class GroupCreate(BaseModel):
    name: str
    type: str  # 'income' or 'expense'


class ItemCreate(BaseModel):
    name: str


class AmountSet(BaseModel):
    month: str
    planned: float


class AssignBody(BaseModel):
    transaction_id: str
    item_id: int


# ---------------------------------------------------------------------------
# GET /{month} — full budget for a given month
# ---------------------------------------------------------------------------

@router.get("/{month}")
async def get_budget(month: str, _user: str = Depends(get_current_user)):
    """Return the complete budget for a month: all groups, all items, planned vs spent."""
    _validate_month(month)

    with get_db() as conn:
        # ── Carryforward: if this month has no planned amounts at all, copy them
        # from the most recent prior month that does. This means navigating to a
        # new month always starts with last month's budget as a baseline rather
        # than a blank slate — the zero-based approach requires every month to
        # be planned, so seeding from the prior month saves significant re-entry.
        has_any_planned = conn.execute(
            "SELECT 1 FROM budget_amounts WHERE month = ? LIMIT 1", (month,)
        ).fetchone()

        if not has_any_planned:
            # Find the most recent month before this one that has planned amounts
            prior_month = conn.execute(
                "SELECT month FROM budget_amounts WHERE month < ? ORDER BY month DESC LIMIT 1",
                (month,)
            ).fetchone()
            if prior_month:
                # Copy every planned amount from the prior month into this month.
                # INSERT OR IGNORE so if the user has already set some amounts
                # for this month (partial carryforward) we don't overwrite them.
                conn.execute("""
                    INSERT OR IGNORE INTO budget_amounts (item_id, month, planned)
                    SELECT item_id, ?, planned
                    FROM budget_amounts
                    WHERE month = ?
                """, (month, prior_month["month"]))

        # Exclude soft-deleted groups — is_deleted=1 means the user "deleted" the
        # group from the UI; the row stays in DB to preserve budget_history references.
        groups = conn.execute(
            "SELECT id, name, type, display_order FROM budget_groups"
            " WHERE is_deleted = 0 ORDER BY display_order, name"
        ).fetchall()

        result_groups = []
        total_income_planned = 0.0
        total_expense_planned = 0.0
        total_expense_spent = 0.0

        for group in groups:
            gid = group["id"]

            # Exclude soft-deleted items — same logic as groups above.
            items_rows = conn.execute(
                "SELECT id, name FROM budget_items"
                " WHERE group_id = ? AND is_deleted = 0 ORDER BY display_order, name",
                (gid,)
            ).fetchall()

            items_out = []
            group_planned = 0.0
            group_spent = 0.0

            for item in items_rows:
                iid = item["id"]

                # Planned amount for this month (0 if no row exists yet)
                amt_row = conn.execute(
                    "SELECT planned FROM budget_amounts WHERE item_id = ? AND month = ?",
                    (iid, month)
                ).fetchone()
                planned = float(amt_row["planned"]) if amt_row else 0.0

                # Spent = sum of absolute transaction amounts assigned to this item
                # within the target month, excluding pending transactions.
                # ABS() handles the Plaid sign convention (positive = outflow).
                spent_row = conn.execute("""
                    SELECT COALESCE(SUM(ABS(t.amount)), 0) AS spent
                    FROM transaction_assignments ta
                    JOIN transactions t ON t.transaction_id = ta.transaction_id
                    WHERE ta.item_id = ?
                      AND strftime('%Y-%m', t.date) = ?
                      AND t.pending = 0
                """, (iid, month)).fetchone()
                spent = float(spent_row["spent"])

                items_out.append({
                    "id": iid,
                    "name": item["name"],
                    "planned": planned,
                    "spent": spent,
                    "remaining": planned - spent,
                })

                group_planned += planned
                group_spent += spent

            result_groups.append({
                "id": gid,
                "name": group["name"],
                "type": group["type"],
                "display_order": group["display_order"],
                "total_planned": group_planned,
                "total_spent": group_spent,
                "items": items_out,
            })

            # Accumulate summary totals by group type
            if group["type"] == "income":
                total_income_planned += group_planned
            else:
                total_expense_planned += group_planned
                total_expense_spent += group_spent

    return {
        "month": month,
        "groups": result_groups,
        "summary": {
            "total_income_planned": total_income_planned,
            "total_expense_planned": total_expense_planned,
            "total_expense_spent": total_expense_spent,
            # How much income is left after all planned expenses are accounted for.
            # A positive value means income exceeds planned spending (good).
            # A negative value means expenses are over-budgeted relative to income.
            "remaining_to_budget": total_income_planned - total_expense_planned,
        },
    }


# ---------------------------------------------------------------------------
# POST /groups — create a budget group
# ---------------------------------------------------------------------------

@router.post("/groups")
async def create_group(body: GroupCreate, _user: str = Depends(get_current_user)):
    """Create a new top-level budget group (e.g. Housing, Food)."""
    if body.type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="type must be 'income' or 'expense'")

    name = body.name.strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    with get_db() as conn:
        # Place new group at the end of the current display order
        max_row = conn.execute("SELECT COALESCE(MAX(display_order), 0) AS m FROM budget_groups").fetchone()
        order = max_row["m"] + 1

        cur = conn.execute(
            "INSERT INTO budget_groups (name, type, display_order) VALUES (?, ?, ?)",
            (name, body.type, order)
        )
        gid = cur.lastrowid

    return {
        "id": gid,
        "name": name,
        "type": body.type,
        "display_order": order,
        "total_planned": 0.0,
        "total_spent": 0.0,
        "items": [],
    }


# ---------------------------------------------------------------------------
# PATCH /groups/{group_id} — rename or change type of a group
# ---------------------------------------------------------------------------

@router.patch("/groups/{group_id}")
async def update_group(group_id: int, body: dict, _user: str = Depends(get_current_user)):
    """Update a group's name and/or type. Only provided fields are changed."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM budget_groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Group not found")

        name = str(body["name"]).strip()[:100] if "name" in body else row["name"]
        gtype = body["type"] if "type" in body else row["type"]

        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        if gtype not in ("income", "expense"):
            raise HTTPException(status_code=400, detail="type must be 'income' or 'expense'")

        conn.execute(
            "UPDATE budget_groups SET name = ?, type = ? WHERE id = ?",
            (name, gtype, group_id)
        )

    return {"id": group_id, "name": name, "type": gtype}


# ---------------------------------------------------------------------------
# DELETE /groups/{group_id} — remove a group and all its items
# ---------------------------------------------------------------------------

@router.delete("/groups/{group_id}")
async def delete_group(group_id: int, _user: str = Depends(get_current_user)):
    """Soft-delete a group and all its items.

    Sets is_deleted=1 on the group and cascades the same flag to all child items.
    Hard deletion is intentionally avoided: budget_amounts, budget_history, and
    budget_auto_rules rows all reference item IDs — destroying them would corrupt
    historical month views and Sage's spending knowledge.
    """
    with get_db() as conn:
        # Soft-delete all child items first so they stop appearing in any month's GET.
        conn.execute(
            "UPDATE budget_items SET is_deleted = 1 WHERE group_id = ?", (group_id,)
        )
        conn.execute(
            "UPDATE budget_groups SET is_deleted = 1 WHERE id = ?", (group_id,)
        )
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# POST /groups/{group_id}/items — add a line item to a group
# ---------------------------------------------------------------------------

@router.post("/groups/{group_id}/items")
async def create_item(group_id: int, body: ItemCreate, _user: str = Depends(get_current_user)):
    """Add a budget line item under an existing group."""
    name = body.name.strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    with get_db() as conn:
        group = conn.execute("SELECT id FROM budget_groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        max_row = conn.execute(
            "SELECT COALESCE(MAX(display_order), 0) AS m FROM budget_items WHERE group_id = ?",
            (group_id,)
        ).fetchone()
        order = max_row["m"] + 1

        cur = conn.execute(
            "INSERT INTO budget_items (group_id, name, display_order) VALUES (?, ?, ?)",
            (group_id, name, order)
        )
        iid = cur.lastrowid

    return {"id": iid, "name": name, "planned": 0.0, "spent": 0.0, "remaining": 0.0}


# ---------------------------------------------------------------------------
# PATCH /items/{item_id} — rename a line item
# ---------------------------------------------------------------------------

@router.patch("/items/{item_id}")
async def update_item(item_id: int, body: dict, _user: str = Depends(get_current_user)):
    """Rename a budget line item."""
    name = str(body.get("name", "")).strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    with get_db() as conn:
        row = conn.execute("SELECT id FROM budget_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Item not found")
        conn.execute("UPDATE budget_items SET name = ? WHERE id = ?", (name, item_id))

    return {"id": item_id, "name": name}


# ---------------------------------------------------------------------------
# DELETE /items/{item_id} — remove a line item
# ---------------------------------------------------------------------------

@router.delete("/items/{item_id}")
async def delete_item(item_id: int, _user: str = Depends(get_current_user)):
    """Soft-delete a line item.

    Sets is_deleted=1 so the item no longer appears in any month's GET response.
    The row and all associated budget_amounts / budget_auto_rules data are kept
    intact so historical month views and Sage's spending knowledge remain correct.
    transaction_assignments are not touched — those transactions will resurface in
    the Unassigned panel since their item_id still points to the (now-hidden) item.
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE budget_items SET is_deleted = 1 WHERE id = ?", (item_id,)
        )
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# PUT /items/{item_id}/amount — set planned amount for a month
# ---------------------------------------------------------------------------

@router.put("/items/{item_id}/amount")
async def set_amount(item_id: int, body: AmountSet, _user: str = Depends(get_current_user)):
    """Upsert the planned dollar amount for a line item in a given month."""
    _validate_month(body.month)

    with get_db() as conn:
        row = conn.execute("SELECT id FROM budget_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Item not found")

        # INSERT OR REPLACE handles both the first-time set and any subsequent edits
        conn.execute("""
            INSERT INTO budget_amounts (item_id, month, planned)
            VALUES (?, ?, ?)
            ON CONFLICT(item_id, month) DO UPDATE SET planned = excluded.planned
        """, (item_id, body.month, body.planned))

    return {"item_id": item_id, "month": body.month, "planned": body.planned}


# ---------------------------------------------------------------------------
# GET /unassigned/{month} — transactions not yet assigned to any item
# ---------------------------------------------------------------------------

@router.get("/unassigned/{month}")
async def get_unassigned(month: str, _user: str = Depends(get_current_user)):
    """Return non-pending transactions for this month that have no budget assignment.

    Also returns suggested_item_id / suggested_item_name from budget_auto_rules
    for the merchant with the highest match_count, so the UI can show one-click
    assignment badges (e.g. "+ Groceries").
    """
    _validate_month(month)

    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name, t.amount, t.category,
                   best.item_id  AS suggested_item_id,
                   bi.name       AS suggested_item_name
            FROM transactions t
            LEFT JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
            -- Best auto-rule: pick the rule with the highest match_count for the merchant
            LEFT JOIN (
                SELECT merchant, item_id, MAX(match_count) AS match_count
                FROM budget_auto_rules
                GROUP BY merchant
            ) best ON best.merchant = COALESCE(t.merchant_name, t.name)
            LEFT JOIN budget_items bi ON bi.id = best.item_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND (ta.transaction_id IS NULL OR ta.item_id IS NULL)
            ORDER BY t.date DESC
        """, (month,)).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /assigned/{month} — transactions that have been assigned to a budget item
# ---------------------------------------------------------------------------

@router.get("/assigned/{month}")
async def get_assigned(month: str, _user: str = Depends(get_current_user)):
    """Return non-pending transactions for this month that ARE assigned to a budget item.

    Includes item_name and group_name so the UI can display "Housing / Mortgage"
    without a second round-trip.
    """
    _validate_month(month)

    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name, t.amount, t.category,
                   ta.item_id,
                   bi.name AS item_name,
                   bg.name AS group_name
            FROM transactions t
            JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
            JOIN budget_items bi ON bi.id = ta.item_id
            JOIN budget_groups bg ON bg.id = bi.group_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND ta.item_id IS NOT NULL
            ORDER BY t.date DESC
        """, (month,)).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# POST /assign — assign a transaction to a budget item
# ---------------------------------------------------------------------------

@router.post("/assign")
async def assign_transaction(body: AssignBody, _user: str = Depends(get_current_user)):
    """Link a transaction to a budget line item.

    Uses INSERT OR REPLACE so re-assigning a transaction to a different item
    is a single idempotent call — no need to unassign first.
    """
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO transaction_assignments (transaction_id, item_id)
            VALUES (?, ?)
        """, (body.transaction_id, body.item_id))

    return {"status": "assigned"}


# ---------------------------------------------------------------------------
# DELETE /assign/{transaction_id} — remove a transaction assignment
# ---------------------------------------------------------------------------

@router.delete("/assign/{transaction_id}")
async def unassign_transaction(transaction_id: str, _user: str = Depends(get_current_user)):
    """Remove the budget assignment for a transaction (moves it back to unassigned)."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM transaction_assignments WHERE transaction_id = ?",
            (transaction_id,)
        )
    return {"status": "unassigned"}


# ---------------------------------------------------------------------------
# POST /auto-assign/{month} — match unassigned transactions to budget items
#                              by dollar amount using imported budget history
# ---------------------------------------------------------------------------

@router.post("/auto-assign/{month}")
async def auto_assign_from_history(month: str, _user: str = Depends(get_current_user)):
    """Auto-assign unassigned Plaid transactions using dollar-amount matching
    against budget_history rows imported from the external budget app.

    Strategy:
      For each unassigned Plaid transaction, find a budget_history entry for
      the same month with the same dollar amount. Create the assignment only
      when the match is unambiguous — exactly one Plaid transaction has that
      amount AND exactly one history entry has that amount for the month.
      Duplicate amounts on either side are skipped to avoid mismatches.

    Returns the count of transactions assigned and skipped.
    """
    _validate_month(month)

    with get_db() as conn:
        # All unassigned (or un-categorized) Plaid transactions for the month.
        # Plaid amounts: positive = outflow (expense), negative = inflow (income).
        unassigned = conn.execute("""
            SELECT t.transaction_id, ABS(t.amount) AS amount
            FROM transactions t
            LEFT JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND (ta.transaction_id IS NULL OR ta.item_id IS NULL)
        """, (month,)).fetchall()

        if not unassigned:
            return {"assigned": 0, "skipped": 0, "message": "No unassigned transactions"}

        # All budget_history entries for the month that are linked to a budget item.
        # These were imported from the external budget app and represent the
        # "correct" assignment for each historical transaction.
        history = conn.execute("""
            SELECT ROUND(amount, 2) AS amount, item_id
            FROM budget_history
            WHERE month = ? AND item_id IS NOT NULL
        """, (month,)).fetchall()

        # Build a map: dollar_amount → list of item_ids from history.
        # If an amount maps to multiple history entries we can't safely pick one.
        history_by_amount: dict[float, list[int]] = defaultdict(list)
        for row in history:
            history_by_amount[row["amount"]].append(row["item_id"])

        # Count how many unassigned Plaid transactions share each dollar amount.
        # If two transactions have the same amount we can't tell which is which.
        txn_amount_counts: dict[float, int] = defaultdict(int)
        for txn in unassigned:
            txn_amount_counts[round(txn["amount"], 2)] += 1

        assigned = 0
        skipped = 0

        for txn in unassigned:
            amt = round(txn["amount"], 2)

            # Skip if multiple Plaid transactions share this amount (ambiguous).
            if txn_amount_counts[amt] > 1:
                skipped += 1
                continue

            # Deduplicate history matches by item_id — multiple budget_history rows
            # can share the same dollar amount if the same charge appeared across
            # different months' imports. What matters is whether they all point to
            # the same item. If they do, the match is still unambiguous.
            raw_matches = history_by_amount.get(amt, [])
            unique_items = list(set(raw_matches))

            if len(unique_items) != 1:
                # Zero matches = no history entry for this amount.
                # 2+ distinct items = genuinely ambiguous, can't pick safely.
                skipped += 1
                continue

            item_id = unique_items[0]
            conn.execute("""
                INSERT OR IGNORE INTO transaction_assignments (transaction_id, item_id, status)
                VALUES (?, ?, 'auto')
            """, (txn["transaction_id"], item_id))
            assigned += 1

    return {
        "assigned": assigned,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# GET /items/{item_id}/detail — full detail for an item (modal data)
# ---------------------------------------------------------------------------

@router.get("/items/{item_id}/detail")
async def get_item_detail(
    item_id: int,
    month: str,
    _user: str = Depends(get_current_user),
):
    """Return everything needed to populate the item detail modal:

      - Item name and group name
      - Planned, spent, and remaining for the requested month
      - Transactions assigned to this item this month (from Plaid)
      - Last 4 months of spending from budget_history for the mini bar chart
    """
    _validate_month(month)

    with get_db() as conn:
        item = conn.execute("""
            SELECT bi.id, bi.name, bg.name AS group_name, bg.type AS group_type
            FROM budget_items bi
            JOIN budget_groups bg ON bg.id = bi.group_id
            WHERE bi.id = ? AND bi.is_deleted = 0
        """, (item_id,)).fetchone()

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        # Planned amount for the requested month
        amt_row = conn.execute(
            "SELECT planned FROM budget_amounts WHERE item_id = ? AND month = ?",
            (item_id, month)
        ).fetchone()
        planned = float(amt_row["planned"]) if amt_row else 0.0

        # Plaid transactions assigned to this item this month
        txn_rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name,
                   ABS(t.amount) AS amount
            FROM transaction_assignments ta
            JOIN transactions t ON t.transaction_id = ta.transaction_id
            WHERE ta.item_id = ?
              AND strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
            ORDER BY t.date DESC
        """, (item_id, month)).fetchall()

        spent = sum(float(r["amount"]) for r in txn_rows)

        # Last 4 months of spending from imported budget history — used for the
        # mini bar chart so the user can see their spending trend at a glance.
        # Includes the current month if it has any budget_history entries.
        history_rows = conn.execute("""
            SELECT month, ROUND(SUM(amount), 2) AS total
            FROM budget_history
            WHERE item_id = ? AND month <= ?
            GROUP BY month
            ORDER BY month DESC
            LIMIT 4
        """, (item_id, month)).fetchall()

    return {
        "item_id": item_id,
        "name": item["name"],
        "group_name": item["group_name"],
        "group_type": item["group_type"],
        "month": month,
        "planned": planned,
        "spent": spent,
        "remaining": planned - spent,
        "transactions": [
            {
                "transaction_id": r["transaction_id"],
                "date": r["date"],
                "merchant": r["merchant_name"] or r["name"] or "Unknown",
                "amount": float(r["amount"]),
            }
            for r in txn_rows
        ],
        # Oldest-first so bar chart renders left-to-right chronologically
        "monthly_history": [
            {"month": r["month"], "spent": float(r["total"])}
            for r in reversed(history_rows)
        ],
    }


# ---------------------------------------------------------------------------
# POST /template — seed default budget categories
# ---------------------------------------------------------------------------

@router.post("/template")
async def seed_template(_user: str = Depends(get_current_user)):
    """Seed the budget with a standard set of categories.

    Only runs when budget_groups is completely empty — safe to call multiple
    times without duplicating data. Returns immediately if groups already exist.
    """
    with get_db() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM budget_groups").fetchone()
        if existing["n"] > 0:
            return {"status": "already_exists"}

        groups_created = 0
        for order, group_def in enumerate(TEMPLATE, start=1):
            cur = conn.execute(
                "INSERT INTO budget_groups (name, type, display_order) VALUES (?, ?, ?)",
                (group_def["name"], group_def["type"], order)
            )
            gid = cur.lastrowid
            groups_created += 1

            for item_order, item_name in enumerate(group_def["items"], start=1):
                conn.execute(
                    "INSERT INTO budget_items (group_id, name, display_order) VALUES (?, ?, ?)",
                    (gid, item_name, item_order)
                )

    return {"status": "seeded", "groups_created": groups_created}


# ---------------------------------------------------------------------------
# POST /import/csv — bulk import historical budget transactions from CSV files
# ---------------------------------------------------------------------------

@router.post("/import/csv")
async def import_csv(
    files: List[UploadFile] = File(...),
    _user: str = Depends(get_current_user),
):
    """Import historical budget transactions from one or more CSV files.

    Expected CSV columns (in any order, header row required):
        Group, Item, Type, Date, Merchant, Amount, Note

    Column semantics:
      - Group   : top-level spending category (e.g. Housing, Food)
      - Item    : line item within the group (e.g. Groceries, Mortgage / Rent)
      - Type    : 'income', 'expense', or 'debt' ('debt' is mapped to 'expense')
      - Date    : MM/DD/YYYY
      - Merchant: payee name (used to build auto-categorization rules)
      - Amount  : numeric; sign is ignored — all amounts are stored as positive
      - Note    : optional free-text note

    Side effects:
      1. Creates budget_groups / budget_items that don't already exist.
      2. Appends every parsed row to budget_history (no dedup — caller controls
         which files to upload).
      3. Seeds / increments budget_auto_rules for each unique (merchant, item_id)
         pair so Sage can auto-categorize future Plaid transactions.
      4. Inserts budget_amounts rows (planned = average monthly spend per item)
         via INSERT OR IGNORE — existing planned amounts set by the user are
         never overwritten.
    """
    rows_imported = 0
    groups_created = 0
    items_created = 0
    rules_seeded = 0
    months_seen: set[str] = set()

    # merchant → item_id → count of occurrences seen across all uploaded files.
    # Accumulated here so we can issue a single upsert per (merchant, item_id)
    # pair after all files are processed.
    merchant_item_counts: dict[tuple[str, int], int] = defaultdict(int)

    # (item_id, month) → list of amounts — used later to compute average
    # monthly spend for seeding planned amounts.
    item_month_amounts: dict[tuple[int, str], list[float]] = defaultdict(list)

    with get_db() as conn:
        for upload in files:
            raw_bytes = await upload.read()
            text = raw_bytes.decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))

            for row in reader:
                # Skip repeated header rows and blank group rows that some
                # export formats insert as section separators.
                raw_group = row.get("Group", "").strip()
                if not raw_group or raw_group == "Group":
                    continue

                # Normalise group name — treat blank/untitled as "Other".
                group_name = raw_group if raw_group.lower() != "untitled" else "Other"

                item_name = row.get("Item", "").strip()
                if not item_name:
                    continue  # Line items without a name carry no useful data.

                # Map 'debt' to 'expense' so the budget type taxonomy stays
                # binary (income / expense) throughout the rest of the app.
                raw_type = row.get("Type", "").strip().lower()
                type_val = "expense" if raw_type == "debt" else raw_type or "expense"

                # Parse MM/DD/YYYY → YYYY-MM-DD.
                raw_date = row.get("Date", "").strip()
                try:
                    dt = datetime.strptime(raw_date, "%m/%d/%Y")
                except ValueError:
                    continue  # Skip rows with unparseable dates.
                date_str = dt.strftime("%Y-%m-%d")
                month = dt.strftime("%Y-%m")
                months_seen.add(month)

                # Store amounts as positive regardless of the export sign convention.
                raw_amount = row.get("Amount", "0").strip().replace(",", "")
                try:
                    amount = abs(float(raw_amount))
                except ValueError:
                    amount = 0.0

                merchant = row.get("Merchant", "").strip() or None
                note = row.get("Note", "").strip() or None

                # ── Ensure the group exists ──────────────────────────────────
                # Group type: 'income' for the Income group, 'expense' for all others.
                group_type = "income" if group_name.lower() == "income" else "expense"
                conn.execute(
                    "INSERT OR IGNORE INTO budget_groups (name, type, display_order) "
                    "SELECT ?, ?, COALESCE(MAX(display_order), 0) + 1 FROM budget_groups",
                    (group_name, group_type),
                )
                group_row = conn.execute(
                    "SELECT id FROM budget_groups WHERE name = ?", (group_name,)
                ).fetchone()
                gid = group_row["id"]

                # Track whether the INSERT above actually created a new row.
                # SQLite's changes() returns 1 if the INSERT fired, 0 if IGNORE fired.
                if conn.execute("SELECT changes() AS c").fetchone()["c"] > 0:
                    groups_created += 1

                # ── Ensure the line item exists within that group ─────────────
                conn.execute(
                    "INSERT OR IGNORE INTO budget_items (group_id, name, display_order) "
                    "SELECT ?, ?, COALESCE(MAX(display_order), 0) + 1 "
                    "FROM budget_items WHERE group_id = ?",
                    (gid, item_name, gid),
                )
                item_row = conn.execute(
                    "SELECT id FROM budget_items WHERE group_id = ? AND name = ?",
                    (gid, item_name),
                ).fetchone()
                iid = item_row["id"]

                if conn.execute("SELECT changes() AS c").fetchone()["c"] > 0:
                    items_created += 1

                # ── Append to history ─────────────────────────────────────────
                conn.execute(
                    """INSERT INTO budget_history
                       (group_name, item_id, item_name, month, date,
                        merchant, amount, note, type, source_file)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (group_name, iid, item_name, month, date_str,
                     merchant, amount, note, type_val, upload.filename),
                )
                rows_imported += 1

                # ── Accumulate merchant → item mapping counts ─────────────────
                if merchant:
                    merchant_item_counts[(merchant, iid)] += 1

                # ── Accumulate per-item monthly amounts for planned seeding ───
                item_month_amounts[(iid, month)].append(amount)

        # ── Upsert auto-categorization rules ─────────────────────────────────
        # One SQL round-trip per unique (merchant, item_id) pair seen across
        # all uploaded files.
        for (merchant, iid), count in merchant_item_counts.items():
            existing = conn.execute(
                "SELECT id FROM budget_auto_rules WHERE merchant = ? AND item_id = ?",
                (merchant, iid),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE budget_auto_rules "
                    "SET match_count = match_count + ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE merchant = ? AND item_id = ?",
                    (count, merchant, iid),
                )
            else:
                conn.execute(
                    "INSERT INTO budget_auto_rules (merchant, item_id, match_count) "
                    "VALUES (?, ?, ?)",
                    (merchant, iid, count),
                )
                rules_seeded += 1

        # ── Seed planned amounts (average monthly spend per item) ─────────────
        # Collapse item_month_amounts into (item_id → list of monthly totals),
        # compute the average, then INSERT OR IGNORE so we never overwrite
        # planned amounts the user has already set manually.
        item_monthly_totals: dict[int, list[float]] = defaultdict(list)
        for (iid, _month), amounts in item_month_amounts.items():
            item_monthly_totals[iid].append(sum(amounts))

        for iid, monthly_totals in item_monthly_totals.items():
            avg_planned = sum(monthly_totals) / len(monthly_totals)
            # Seed one planned amount row per (item, month) that was present in
            # the import using INSERT OR IGNORE — existing rows are left intact.
            for (item_id, month), amounts in item_month_amounts.items():
                if item_id != iid:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO budget_amounts (item_id, month, planned) "
                    "VALUES (?, ?, ?)",
                    (iid, month, round(avg_planned, 2)),
                )

    return {
        "files_processed": len(files),
        "rows_imported": rows_imported,
        "groups_created": groups_created,
        "items_created": items_created,
        "months_covered": sorted(months_seen),
        "rules_seeded": rules_seeded,
    }


# ---------------------------------------------------------------------------
# POST /import/json — import one month from external budget app Network-tab JSON
# ---------------------------------------------------------------------------

@router.post("/import/json")
async def import_budget_json(
    payload: dict,
    _user: str = Depends(get_current_user),
):
    """Import a single month's budget from an external budget app API JSON response.

    To get this JSON:
      1. Open your budget app and navigate to any budget month.
      2. Open DevTools (F12) → Network → Fetch/XHR → refresh the page.
      3. Find the request that returns your budget data (look for a response
         containing "groups" and "budgetItems").
      4. Click it → Response tab → copy the entire JSON body.
      5. Paste it into the Vaultic import UI.

    JSON structure expected:
      {
        "id": "...",
        "date": "YYYY-MM-DD",        ← month derived from this
        "groups": [
          {
            "label": "Food",
            "type": "income" | "expense" | "debt",
            "budgetItems": [
              {
                "label": "Groceries",
                "amountBudgeted": 120000,   ← cents; planned amount
                "type": "expense" | "income" | "debt" | "sinking_fund",
                "allocations": [
                  {
                    "date": "YYYY-MM-DD",
                    "merchant": "Walmart",
                    "amount": -10460        ← cents; negative = outflow
                  }
                ]
              }
            ]
          }
        ]
      }

    Side effects — same as CSV import:
      1. Creates budget_groups / budget_items if they don't exist.
      2. Appends every allocation to budget_history.
      3. Seeds budget_amounts with the exact planned amounts
         (amountBudgeted / 100) via INSERT OR IGNORE.
      4. Seeds / increments budget_auto_rules for each (merchant, item_id) pair.
    """
    # ── Validate top-level shape ──────────────────────────────────────────────
    if "groups" not in payload or "date" not in payload:
        raise HTTPException(
            status_code=422,
            detail="Invalid JSON: expected budget format with 'date' and 'groups' keys.",
        )

    # Derive YYYY-MM month string from the budget's date field.
    try:
        month = payload["date"][:7]   # "2026-03-01" → "2026-03"
        datetime.strptime(month, "%Y-%m")
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Cannot parse 'date' field as a valid month.")

    rows_imported = 0
    groups_created = 0
    items_created = 0
    rules_seeded = 0
    merchant_item_counts: dict[tuple[str, int], int] = defaultdict(int)

    with get_db() as conn:
        for group in payload.get("groups", []):
            raw_group_label = (group.get("label") or "").strip()
            if not raw_group_label:
                continue

            # Map "Untitled" → "Other"; treat "debt" group type as "expense".
            group_name = raw_group_label if raw_group_label.lower() != "untitled" else "Other"
            raw_group_type = (group.get("type") or "expense").lower()
            group_type = "income" if raw_group_type == "income" else "expense"

            # ── Ensure group exists ───────────────────────────────────────────
            conn.execute(
                "INSERT OR IGNORE INTO budget_groups (name, type, display_order) "
                "SELECT ?, ?, COALESCE(MAX(display_order), 0) + 1 FROM budget_groups",
                (group_name, group_type),
            )
            if conn.execute("SELECT changes() AS c").fetchone()["c"] > 0:
                groups_created += 1
            gid = conn.execute(
                "SELECT id FROM budget_groups WHERE name = ?", (group_name,)
            ).fetchone()["id"]

            for item in group.get("budgetItems", []):
                item_name = (item.get("label") or "").strip()
                if not item_name:
                    continue

                # Normalise item type: sinking_fund and debt → expense.
                raw_item_type = (item.get("type") or "expense").lower()
                item_type = "income" if raw_item_type == "income" else "expense"

                # Planned amount is stored in cents — convert to dollars.
                # amountBudgeted is always positive; sign meaning comes from type.
                planned_cents = item.get("amountBudgeted") or 0
                planned_dollars = round(abs(planned_cents) / 100, 2)

                # ── Ensure item exists ────────────────────────────────────────
                conn.execute(
                    "INSERT OR IGNORE INTO budget_items (group_id, name, display_order) "
                    "SELECT ?, ?, COALESCE(MAX(display_order), 0) + 1 "
                    "FROM budget_items WHERE group_id = ?",
                    (gid, item_name, gid),
                )
                if conn.execute("SELECT changes() AS c").fetchone()["c"] > 0:
                    items_created += 1
                iid = conn.execute(
                    "SELECT id FROM budget_items WHERE group_id = ? AND name = ?",
                    (gid, item_name),
                ).fetchone()["id"]

                # ── Seed planned amount for this month ────────────────────────
                # INSERT OR IGNORE — never overwrite amounts the user set manually.
                if planned_dollars > 0:
                    conn.execute(
                        "INSERT OR IGNORE INTO budget_amounts (item_id, month, planned) "
                        "VALUES (?, ?, ?)",
                        (iid, month, planned_dollars),
                    )

                # ── Import each allocation as a budget_history row ────────────
                for alloc in item.get("allocations", []):
                    alloc_date = (alloc.get("date") or "").strip()
                    if not alloc_date:
                        continue
                    # Validate date format — skip malformed rows.
                    try:
                        datetime.strptime(alloc_date, "%Y-%m-%d")
                    except ValueError:
                        continue

                    merchant = (alloc.get("merchant") or "").strip() or None
                    # Amounts in cents, negative = outflow; store as positive dollars.
                    amount_cents = alloc.get("amount") or 0
                    amount_dollars = round(abs(amount_cents) / 100, 2)

                    conn.execute(
                        """INSERT INTO budget_history
                           (group_name, item_id, item_name, month, date,
                            merchant, amount, note, type, source_file)
                           VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'budget_json')""",
                        (group_name, iid, item_name, month, alloc_date,
                         merchant, amount_dollars, item_type),
                    )
                    rows_imported += 1

                    if merchant:
                        merchant_item_counts[(merchant, iid)] += 1

        # ── Upsert auto-categorization rules ─────────────────────────────────
        for (merchant, iid), count in merchant_item_counts.items():
            existing = conn.execute(
                "SELECT id FROM budget_auto_rules WHERE merchant = ? AND item_id = ?",
                (merchant, iid),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE budget_auto_rules "
                    "SET match_count = match_count + ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE merchant = ? AND item_id = ?",
                    (count, merchant, iid),
                )
            else:
                conn.execute(
                    "INSERT INTO budget_auto_rules (merchant, item_id, match_count) "
                    "VALUES (?, ?, ?)",
                    (merchant, iid, count),
                )
                rules_seeded += 1

    return {
        "month": month,
        "rows_imported": rows_imported,
        "groups_created": groups_created,
        "items_created": items_created,
        "rules_seeded": rules_seeded,
    }
