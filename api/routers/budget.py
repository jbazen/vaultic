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
import json
import re
from collections import defaultdict
from datetime import date, datetime
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from typing import List
from api.dependencies import get_current_user
from api.database import get_db

router = APIRouter(prefix="/api/budget", tags=["budget"])


# ── Request models ────────────────────────────────────────────────────────────

class ReorderBody(BaseModel):
    ids: List[int]

class UpdateGroupBody(BaseModel):
    name: str | None = None
    type: str | None = None

class UpdateItemBody(BaseModel):
    name: str


def normalize_merchant(name: str) -> str:
    """Lowercase and strip punctuation for fuzzy merchant comparison."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


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


def _spent_for_item(conn, item_id: int, month: str) -> float:
    """Return total spending for a budget item in a given month.

    Checks both transaction_assignments (direct single-item assignments) and
    transaction_splits (multi-item splits) so the total is always correct
    regardless of how the user categorized each transaction.
    """
    # Direct assignments: use the full transaction amount.
    # Sign is preserved: positive = debit (adds to spending),
    # negative = credit/refund (reduces spending).
    direct = conn.execute("""
        SELECT COALESCE(SUM(t.amount), 0) AS spent
        FROM transaction_assignments ta
        JOIN transactions t ON t.transaction_id = ta.transaction_id
        WHERE ta.item_id = ?
          AND strftime('%Y-%m', t.date) = ?
          AND t.pending = 0
    """, (item_id, month)).fetchone()["spent"]

    # Split assignments: use the per-split amount, not the full transaction amount
    split = conn.execute("""
        SELECT COALESCE(SUM(ts.amount), 0) AS spent
        FROM transaction_splits ts
        JOIN transactions t ON t.transaction_id = ts.transaction_id
        WHERE ts.item_id = ?
          AND strftime('%Y-%m', t.date) = ?
          AND t.pending = 0
    """, (item_id, month)).fetchone()["spent"]

    return float(direct) + float(split)


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


class SplitItem(BaseModel):
    """One leg of a split transaction — item to assign and dollar amount."""
    item_id: int
    amount: float


class SplitsBody(BaseModel):
    """Request body for PUT /transactions/{id}/splits."""
    splits: List[SplitItem]
    check_number: str | None = None  # user-entered check number (optional)
    notes: str | None = None         # free-form note on the transaction (optional)


# ---------------------------------------------------------------------------
# GET /pending-review — all pending_review transactions across all months
# Used by the mobile Review Queue screen (/review) so the user sees every
# transaction that needs action regardless of which month it belongs to.
#
# ROUTE ORDER NOTE: This route and /pending-review/{month} MUST be defined
# BEFORE /{month} below, otherwise FastAPI matches "pending-review" as a
# month parameter and returns a 422 validation error — the same class of bug
# that previously broke group drag-and-drop (/groups/reorder vs /{group_id}).
# ---------------------------------------------------------------------------

@router.get("/pending-review")
async def get_all_pending_review(_user: str = Depends(get_current_user)):
    """Return ALL pending_review transactions across every month.

    Powers the mobile Review Queue page where the user approves transactions
    one by one.  Sorted newest-first so the most recent sync appears at top.
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name, t.amount, t.category,
                   ta.item_id        AS suggested_item_id,
                   bi.name           AS suggested_item_name,
                   bg.name           AS suggested_group_name,
                   ta.confidence     AS confidence,
                   COALESCE(a.display_name, a.name) AS account_name,
                   a.mask            AS account_mask
            FROM transaction_assignments ta
            JOIN transactions t  ON t.transaction_id = ta.transaction_id
            JOIN budget_items bi ON bi.id = ta.item_id
            JOIN budget_groups bg ON bg.id = bi.group_id
            LEFT JOIN accounts a ON a.id = t.account_id
            WHERE t.pending = 0
              AND ta.status = 'pending_review'
              AND (t.budget_deleted IS NULL OR t.budget_deleted = 0)
            ORDER BY t.date DESC
        """).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /pending-review/{month} — Sage-suggested assignments awaiting approval
# ---------------------------------------------------------------------------

@router.get("/pending-review/{month}")
async def get_pending_review(month: str, _user: str = Depends(get_current_user)):
    """Return transactions that Sage auto-categorized during sync but that
    the user has not yet approved or corrected.

    These are stored in transaction_assignments with status='pending_review'.
    They DO count toward spending totals — the user can always move them.
    """
    _validate_month(month)

    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name, t.amount, t.category,
                   ta.item_id        AS suggested_item_id,
                   bi.name           AS suggested_item_name,
                   bg.name           AS suggested_group_name,
                   ta.confidence     AS confidence,
                   COALESCE(a.display_name, a.name) AS account_name,
                   a.mask            AS account_mask
            FROM transaction_assignments ta
            JOIN transactions t ON t.transaction_id = ta.transaction_id
            JOIN budget_items bi ON bi.id = ta.item_id
            JOIN budget_groups bg ON bg.id = bi.group_id
            LEFT JOIN accounts a ON a.id = t.account_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND ta.status = 'pending_review'
              AND (t.budget_deleted IS NULL OR t.budget_deleted = 0)
            ORDER BY t.date DESC
        """, (month,)).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /unassigned — all unassigned transactions across recent months
# Powers the Review Queue so the user can assign new transactions alongside
# the pending-review ones in one place.
#
# ROUTE ORDER NOTE: Must be defined BEFORE /{month} for the same reason as
# /pending-review above — "unassigned" would otherwise be treated as a month.
# ---------------------------------------------------------------------------

@router.get("/unassigned")
async def get_all_unassigned(_user: str = Depends(get_current_user)):
    """Return unassigned transactions for the current month only.

    Powers the Review Queue's "New — Assign Now" section so the user can
    assign this month's unmatched transactions alongside the pending-review ones.
    Older unassigned transactions are handled via the Budget page's Unassigned tab.
    Includes the best auto-rule suggestion (highest match_count) per merchant
    so the Review Queue can offer a one-tap assign for known merchants.
    """
    from datetime import date
    current_month = date.today().strftime("%Y-%m")

    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name, t.amount, t.category,
                   best.item_id  AS suggested_item_id,
                   bi.name       AS suggested_item_name,
                   bg.name       AS suggested_group_name,
                   COALESCE(a.display_name, a.name) AS account_name,
                   a.mask        AS account_mask
            FROM transactions t
            LEFT JOIN accounts a ON a.id = t.account_id
            LEFT JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
            -- Best auto-rule: pick the rule with the highest match_count for this merchant
            LEFT JOIN (
                SELECT merchant, item_id, MAX(match_count) AS match_count
                FROM budget_auto_rules
                GROUP BY merchant
            ) best ON best.merchant = COALESCE(t.merchant_name, t.name)
            LEFT JOIN budget_items  bi ON bi.id  = best.item_id  AND bi.is_deleted = 0
            LEFT JOIN budget_groups bg ON bg.id  = bi.group_id   AND bg.is_deleted = 0
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND (ta.transaction_id IS NULL OR ta.item_id IS NULL)
              AND (t.budget_deleted IS NULL OR t.budget_deleted = 0)
              -- Exclude transactions that have already been split across categories.
              -- Split transactions live in transaction_splits, not transaction_assignments,
              -- so the ta JOIN above sees NULL for them and incorrectly marks them unassigned.
              AND NOT EXISTS (
                  SELECT 1 FROM transaction_splits ts
                  WHERE ts.transaction_id = t.transaction_id
              )
            ORDER BY t.date DESC
        """, (current_month,)).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /deleted — soft-deleted transactions for current month
# GET /deleted/{month} — soft-deleted transactions for a specific month
# DELETE /transactions/{transaction_id} — soft-delete a transaction from budget
# POST /transactions/{transaction_id}/restore — restore a soft-deleted transaction
#
# ROUTE ORDER NOTE: All of these must be defined BEFORE /{month} so FastAPI
# does not match "deleted" or "transactions" as a month string.
# ---------------------------------------------------------------------------

@router.get("/deleted")
async def get_deleted_current_month(_user: str = Depends(get_current_user)):
    """Return soft-deleted transactions for the current month."""
    month = date.today().strftime("%Y-%m")
    return await _get_deleted(month)


@router.get("/deleted/{month}")
async def get_deleted_month(month: str, _user: str = Depends(get_current_user)):
    """Return soft-deleted transactions for a specific month."""
    _validate_month(month)
    return await _get_deleted(month)


async def _get_deleted(month: str):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name,
                   t.amount, t.category,
                   a.name AS account_name, a.mask AS account_mask
            FROM transactions t
            LEFT JOIN accounts a ON a.id = t.account_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.budget_deleted = 1
            ORDER BY t.date DESC
        """, (month,)).fetchall()
        return [dict(r) for r in rows]


@router.delete("/transactions/{transaction_id}")
async def budget_delete_transaction(
    transaction_id: str,
    _user: str = Depends(get_current_user),
):
    """Mark a transaction as budget-deleted so it no longer appears in the
    unassigned or pending-review queues and doesn't count toward spending totals.
    Also removes any existing assignment or split so totals update immediately.
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE transactions SET budget_deleted = 1 WHERE transaction_id = ?",
            (transaction_id,)
        )
        conn.execute(
            "DELETE FROM transaction_assignments WHERE transaction_id = ?",
            (transaction_id,)
        )
        conn.execute(
            "DELETE FROM transaction_splits WHERE transaction_id = ?",
            (transaction_id,)
        )
    return {"ok": True}


@router.post("/transactions/{transaction_id}/restore")
async def budget_restore_transaction(
    transaction_id: str,
    _user: str = Depends(get_current_user),
):
    """Restore a budget-deleted transaction back to the unassigned queue."""
    with get_db() as conn:
        conn.execute(
            "UPDATE transactions SET budget_deleted = 0 WHERE transaction_id = ?",
            (transaction_id,)
        )
    return {"ok": True}


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

        # Pre-fetch all items, planned amounts, and spending in bulk (avoids N+1)
        all_items = conn.execute(
            "SELECT id, name, group_id FROM budget_items"
            " WHERE is_deleted = 0 ORDER BY display_order, name"
        ).fetchall()

        all_amounts = {}
        for row in conn.execute(
            "SELECT item_id, planned FROM budget_amounts WHERE month = ?", (month,)
        ).fetchall():
            all_amounts[row["item_id"]] = float(row["planned"])

        # Aggregate direct assignment spending per item for this month.
        # Sign preserved: positive = debit, negative = credit/refund.
        direct_spent = {}
        for row in conn.execute("""
            SELECT ta.item_id, COALESCE(SUM(t.amount), 0) AS spent
            FROM transaction_assignments ta
            JOIN transactions t ON t.transaction_id = ta.transaction_id
            WHERE strftime('%Y-%m', t.date) = ? AND t.pending = 0
            GROUP BY ta.item_id
        """, (month,)).fetchall():
            direct_spent[row["item_id"]] = float(row["spent"])

        # Aggregate split spending per item for this month
        split_spent = {}
        for row in conn.execute("""
            SELECT ts.item_id, COALESCE(SUM(ts.amount), 0) AS spent
            FROM transaction_splits ts
            JOIN transactions t ON t.transaction_id = ts.transaction_id
            WHERE strftime('%Y-%m', t.date) = ? AND t.pending = 0
            GROUP BY ts.item_id
        """, (month,)).fetchall():
            split_spent[row["item_id"]] = float(row["spent"])

        # Group items by their group_id for fast lookup
        items_by_group = {}
        for item in all_items:
            items_by_group.setdefault(item["group_id"], []).append(item)

        result_groups = []
        total_income_planned = 0.0
        total_expense_planned = 0.0
        total_expense_spent = 0.0

        for group in groups:
            gid = group["id"]
            items_out = []
            group_planned = 0.0
            group_spent = 0.0

            for item in items_by_group.get(gid, []):
                iid = item["id"]
                planned = all_amounts.get(iid, 0.0)
                spent = direct_spent.get(iid, 0.0) + split_spent.get(iid, 0.0)

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
# PATCH /groups/reorder — save new display_order for all groups
# IMPORTANT: must be defined BEFORE /groups/{group_id} so FastAPI doesn't
# match the literal path segment "reorder" as a group_id integer, which
# would cause a 422 Unprocessable Content error on every reorder request.
# ---------------------------------------------------------------------------

@router.patch("/groups/reorder")
async def reorder_groups(body: ReorderBody, _user: str = Depends(get_current_user)):
    """Accept an ordered list of group IDs and write their display_order values.

    Body: {"ids": [3, 1, 4, 2]}  — position in list = new display_order (0-based).
    """
    ids = body.ids
    if not ids:
        raise HTTPException(status_code=400, detail="ids list is required")
    with get_db() as conn:
        for order, gid in enumerate(ids):
            conn.execute(
                "UPDATE budget_groups SET display_order = ? WHERE id = ?",
                (order, gid)
            )
    return {"ok": True}


# ---------------------------------------------------------------------------
# PATCH /items/reorder — save new display_order for items within a group
# IMPORTANT: must be defined BEFORE /items/{item_id} for the same reason.
# ---------------------------------------------------------------------------

@router.patch("/items/reorder")
async def reorder_items(body: ReorderBody, _user: str = Depends(get_current_user)):
    """Accept an ordered list of item IDs (all from the same group) and write
    their display_order values.

    Body: {"ids": [7, 5, 9]}  — position in list = new display_order (0-based).
    """
    ids = body.ids
    if not ids:
        raise HTTPException(status_code=400, detail="ids list is required")
    with get_db() as conn:
        for order, iid in enumerate(ids):
            conn.execute(
                "UPDATE budget_items SET display_order = ? WHERE id = ?",
                (order, iid)
            )
    return {"ok": True}


# ---------------------------------------------------------------------------
# PATCH /groups/{group_id} — rename or change type of a group
# ---------------------------------------------------------------------------

@router.patch("/groups/{group_id}")
async def update_group(group_id: int, body: UpdateGroupBody, _user: str = Depends(get_current_user)):
    """Update a group's name and/or type. Only provided fields are changed."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM budget_groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Group not found")

        name = body.name.strip()[:100] if body.name is not None else row["name"]
        gtype = body.type if body.type is not None else row["type"]

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
    return {"ok": True}


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
async def update_item(item_id: int, body: UpdateItemBody, _user: str = Depends(get_current_user)):
    """Rename a budget line item."""
    name = body.name.strip()[:100]
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
    return {"ok": True}


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
                   bi.name       AS suggested_item_name,
                   COALESCE(a.display_name, a.name) AS account_name,
                   a.mask        AS account_mask
            FROM transactions t
            LEFT JOIN accounts a ON a.id = t.account_id
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
              AND (t.budget_deleted IS NULL OR t.budget_deleted = 0)
              -- Exclude split transactions — they live in transaction_splits, not
              -- transaction_assignments, so the ta JOIN above returns NULL for them
              -- and would incorrectly treat them as unassigned.
              AND NOT EXISTS (
                  SELECT 1 FROM transaction_splits ts
                  WHERE ts.transaction_id = t.transaction_id
              )
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
                   bg.name AS group_name,
                   COALESCE(a.display_name, a.name) AS account_name,
                   a.mask  AS account_mask
            FROM transactions t
            LEFT JOIN accounts a ON a.id = t.account_id
            JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
            JOIN budget_items bi ON bi.id = ta.item_id
            JOIN budget_groups bg ON bg.id = bi.group_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND ta.item_id IS NOT NULL
              AND ta.status != 'pending_review'
              AND (t.budget_deleted IS NULL OR t.budget_deleted = 0)
            ORDER BY t.date DESC
        """, (month,)).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# POST /assign/approve — approve or correct a pending_review assignment
# ---------------------------------------------------------------------------

class ApproveBody(BaseModel):
    transaction_id: str
    item_id: int  # the item to confirm (same as suggested = approve; different = correct)


@router.post("/assign/approve")
async def approve_assignment(body: ApproveBody, _user: str = Depends(get_current_user)):
    """Confirm or correct a Sage-suggested transaction assignment.

    Approve: pass the same item_id that Sage suggested — status → 'approved'.
    Correct: pass a different item_id — re-assigns and sets status → 'approved'.
    In both cases, updates budget_auto_rules so the merchant→item mapping is
    reinforced (approve) or corrected (correction raises match_count for new item).
    """
    with get_db() as conn:
        txn = conn.execute(
            "SELECT COALESCE(merchant_name, name, '') AS merchant"
            " FROM transactions WHERE transaction_id = ?",
            (body.transaction_id,)
        ).fetchone()
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")

        merchant = txn["merchant"]

        # Upsert assignment with approved status
        conn.execute("""
            INSERT INTO transaction_assignments (transaction_id, item_id, status)
            VALUES (?, ?, 'approved')
            ON CONFLICT(transaction_id) DO UPDATE SET
                item_id = excluded.item_id,
                status  = 'approved'
        """, (body.transaction_id, body.item_id))

        # Reinforce or correct the auto-rule for this merchant
        if merchant:
            conn.execute("""
                INSERT INTO budget_auto_rules (merchant, item_id, match_count)
                VALUES (?, ?, 1)
                ON CONFLICT(merchant, item_id)
                DO UPDATE SET match_count = match_count + 1,
                              updated_at  = CURRENT_TIMESTAMP
            """, (merchant, body.item_id))

    return {"status": "approved"}


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
        conn.execute(
            "DELETE FROM transaction_splits WHERE transaction_id = ?",
            (transaction_id,)
        )
    return {"status": "unassigned"}


# ---------------------------------------------------------------------------
# DELETE /assign-all/{month} — remove all assignments for a month (bulk reset)
# ---------------------------------------------------------------------------

@router.delete("/assign-all/{month}")
async def unassign_all(month: str, _user: str = Depends(get_current_user)):
    """Remove every transaction assignment for the given month.

    Useful for bulk-resetting before re-running auto-assign.
    Only removes assignments for transactions whose date falls in the month.
    """
    _validate_month(month)
    with get_db() as conn:
        result = conn.execute("""
            DELETE FROM transaction_assignments
            WHERE transaction_id IN (
                SELECT transaction_id FROM transactions
                WHERE strftime('%Y-%m', date) = ?
            )
        """, (month,))
        deleted = result.rowcount
    return {"status": "ok", "unassigned": deleted}


# ---------------------------------------------------------------------------
# POST /auto-assign/{month} — match unassigned transactions to budget items
#                              by dollar amount using imported budget history
# ---------------------------------------------------------------------------

@router.post("/auto-assign/{month}")
async def auto_assign_from_history(month: str, _user: str = Depends(get_current_user)):
    """Auto-assign unassigned Plaid transactions to budget items using a two-pass strategy.

    Pass 1 — merchant + amount match against budget_history:
      Match each Plaid transaction to a history entry by (merchant, amount).
      This resolves the common case where the same dollar amount appears for
      different merchants (e.g. $5.46 from OpenAI vs Anthropic vs Amazon).
      Only assigns when exactly one Plaid transaction has that (merchant, amount)
      pair AND exactly one distinct budget item is mapped to it in history.

    Pass 2 — merchant-only match via budget_auto_rules:
      For any transaction still unassigned after Pass 1, look up the merchant
      in budget_auto_rules (seeded from CSV import). Assigns only when exactly
      one rule matches (highest match_count wins when there are multiple rules
      for the same merchant pointing to different items).

    Returns the count of transactions assigned and skipped.
    """
    _validate_month(month)

    with get_db() as conn:
        # ── Load all unassigned Plaid transactions for the month ──────────────
        unassigned = conn.execute("""
            SELECT t.transaction_id,
                   ROUND(ABS(t.amount), 2) AS amount,
                   COALESCE(t.merchant_name, t.name, '') AS merchant
            FROM transactions t
            LEFT JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND (ta.transaction_id IS NULL OR ta.item_id IS NULL)
        """, (month,)).fetchall()

        if not unassigned:
            return {"assigned": 0, "skipped": 0, "message": "No unassigned transactions"}

        # ── Load budget_history entries for the month ─────────────────────────
        history = conn.execute("""
            SELECT ROUND(amount, 2) AS amount,
                   COALESCE(merchant, '') AS merchant,
                   item_id
            FROM budget_history
            WHERE month = ? AND item_id IS NOT NULL
        """, (month,)).fetchall()

        # Build maps keyed by (normalized_merchant, amount) → set of item_ids.
        # Also keep an amount-only map as a secondary lookup.
        history_by_merchant_amount: dict[tuple, set] = defaultdict(set)
        history_by_amount: dict[float, set] = defaultdict(set)
        for row in history:
            key = (normalize_merchant(row["merchant"]), row["amount"])
            history_by_merchant_amount[key].add(row["item_id"])
            history_by_amount[row["amount"]].add(row["item_id"])

        # Count how many unassigned transactions share the same (merchant, amount)
        # — if two identical charges exist we still can't tell them apart.
        txn_merchant_amount_counts: dict[tuple, int] = defaultdict(int)
        for txn in unassigned:
            key = (normalize_merchant(txn["merchant"]), txn["amount"])
            txn_merchant_amount_counts[key] += 1

        # ── Load budget_auto_rules for Pass 2 ────────────────────────────────
        # Map: normalized_merchant → best item_id (highest match_count, unique)
        auto_rules_rows = conn.execute("""
            SELECT COALESCE(bi.is_deleted, 0) AS is_deleted,
                   LOWER(ar.merchant) AS merchant, ar.item_id, ar.match_count
            FROM budget_auto_rules ar
            JOIN budget_items bi ON bi.id = ar.item_id
            WHERE bi.is_deleted = 0
        """).fetchall()

        # For each normalized merchant keep only the top-scoring item_id.
        # If two rules tie for the same merchant, skip (ambiguous).
        auto_rules: dict[str, int | None] = {}
        auto_rule_counts: dict[str, dict[int, int]] = defaultdict(dict)
        for row in auto_rules_rows:
            nm = normalize_merchant(row["merchant"])
            auto_rule_counts[nm][row["item_id"]] = row["match_count"]
        for nm, item_scores in auto_rule_counts.items():
            best_count = max(item_scores.values())
            top_items = [iid for iid, cnt in item_scores.items() if cnt == best_count]
            auto_rules[nm] = top_items[0] if len(top_items) == 1 else None

        assigned = 0
        skipped = 0

        for txn in unassigned:
            amt = txn["amount"]
            nm = normalize_merchant(txn["merchant"])
            merchant_amt_key = (nm, amt)

            # ── Pass 1: merchant + amount match ──────────────────────────────
            if txn_merchant_amount_counts[merchant_amt_key] == 1:
                items = history_by_merchant_amount.get(merchant_amt_key, set())
                if len(items) == 1:
                    item_id = next(iter(items))
                    conn.execute("""
                        INSERT OR IGNORE INTO transaction_assignments
                            (transaction_id, item_id, status)
                        VALUES (?, ?, 'auto')
                    """, (txn["transaction_id"], item_id))
                    assigned += 1
                    continue
                # No merchant+amount history match → fall through to Pass 2

            # ── Pass 2: merchant-only match via auto_rules ───────────────────
            item_id = auto_rules.get(nm)
            if item_id is not None:
                conn.execute("""
                    INSERT OR IGNORE INTO transaction_assignments
                        (transaction_id, item_id, status)
                    VALUES (?, ?, 'auto')
                """, (txn["transaction_id"], item_id))
                assigned += 1
                continue

            skipped += 1

    return {"assigned": assigned, "skipped": skipped}


# ---------------------------------------------------------------------------
# GET /auto-assign/{month}/debug — show why transactions were skipped
# ---------------------------------------------------------------------------

@router.get("/auto-assign/{month}/debug")
async def auto_assign_debug(month: str, _user: str = Depends(get_current_user)):
    """Return the predicted outcome for every unassigned transaction using the
    same two-pass logic as POST /auto-assign/{month}.

    Each row includes: amount, merchant, and one of:
      pass1_match      — would be assigned via merchant+amount history match
      pass2_auto_rule  — would be assigned via budget_auto_rules merchant match
      duplicate_pair   — same (merchant, amount) on 2+ Plaid transactions
      no_match         — no history and no auto_rule; needs manual assignment
    """
    _validate_month(month)

    with get_db() as conn:
        unassigned = conn.execute("""
            SELECT t.transaction_id,
                   COALESCE(t.merchant_name, t.name, '') AS merchant,
                   ROUND(ABS(t.amount), 2) AS amount
            FROM transactions t
            LEFT JOIN transaction_assignments ta ON ta.transaction_id = t.transaction_id
            WHERE strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
              AND (ta.transaction_id IS NULL OR ta.item_id IS NULL)
        """, (month,)).fetchall()

        history = conn.execute("""
            SELECT ROUND(amount, 2) AS amount,
                   COALESCE(merchant, '') AS merchant,
                   bh.item_id,
                   bi.name AS item_name
            FROM budget_history bh
            JOIN budget_items bi ON bi.id = bh.item_id
            WHERE bh.month = ? AND bh.item_id IS NOT NULL
        """, (month,)).fetchall()

        auto_rules_rows = conn.execute("""
            SELECT LOWER(ar.merchant) AS merchant, ar.item_id,
                   bi.name AS item_name, ar.match_count
            FROM budget_auto_rules ar
            JOIN budget_items bi ON bi.id = ar.item_id
            WHERE bi.is_deleted = 0
        """).fetchall()

    # Build history map: (norm_merchant, amount) → set of {item_id, item_name}
    history_by_ma: dict[tuple, dict] = defaultdict(dict)
    for row in history:
        key = (normalize_merchant(row["merchant"]), row["amount"])
        history_by_ma[key][row["item_id"]] = row["item_name"]

    # Build auto_rules map: norm_merchant → best item_id (highest match_count, unique)
    auto_rule_counts: dict[str, dict] = defaultdict(dict)
    for row in auto_rules_rows:
        nm = normalize_merchant(row["merchant"])
        auto_rule_counts[nm][row["item_id"]] = (row["match_count"], row["item_name"])
    auto_rules: dict[str, tuple | None] = {}
    for nm, scores in auto_rule_counts.items():
        best = max(scores.values(), key=lambda x: x[0])[0]
        top = [(iid, meta) for iid, meta in scores.items() if meta[0] == best]
        auto_rules[nm] = (top[0][0], top[0][1][1]) if len(top) == 1 else None

    # Count (merchant, amount) pairs across unassigned transactions
    ma_counts: dict[tuple, int] = defaultdict(int)
    for txn in unassigned:
        ma_counts[(normalize_merchant(txn["merchant"]), txn["amount"])] += 1

    results = []
    for txn in unassigned:
        amt = txn["amount"]
        nm = normalize_merchant(txn["merchant"])
        ma_key = (nm, amt)

        if ma_counts[ma_key] > 1:
            reason = "duplicate_pair"
            item_name = None
        elif len(history_by_ma.get(ma_key, {})) == 1:
            item_id, item_name = next(iter(history_by_ma[ma_key].items()))
            reason = "pass1_match"
        elif auto_rules.get(nm) is not None:
            _, item_name = auto_rules[nm]
            reason = "pass2_auto_rule"
        else:
            reason = "no_match"
            item_name = None

        results.append({
            "merchant": txn["merchant"],
            "amount": amt,
            "reason": reason,
            "would_assign_to": item_name,
        })

    return sorted(results, key=lambda r: r["reason"])


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

        # Transactions assigned to this item: either directly (transaction_assignments)
        # or as part of a split (transaction_splits). UNION combines both sources.
        # display_amount is the split amount for splits, full amount for direct assigns.
        # Sign preserved: positive amount = debit/expense, negative = credit/refund.
        # The UI uses the sign to color-code transactions (red debit, green credit).
        txn_rows = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name,
                   t.amount AS full_amount,
                   t.amount AS display_amount,
                   0 AS is_split,
                   a.name AS account_name, a.mask AS account_mask,
                   a.subtype AS account_subtype
            FROM transaction_assignments ta
            JOIN transactions t ON t.transaction_id = ta.transaction_id
            LEFT JOIN accounts a ON a.id = t.account_id
            WHERE ta.item_id = ?
              AND strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
            UNION ALL
            SELECT t.transaction_id, t.date, t.name, t.merchant_name,
                   t.amount AS full_amount,
                   ts.amount AS display_amount,
                   1 AS is_split,
                   a.name AS account_name, a.mask AS account_mask,
                   a.subtype AS account_subtype
            FROM transaction_splits ts
            JOIN transactions t ON t.transaction_id = ts.transaction_id
            LEFT JOIN accounts a ON a.id = t.account_id
            WHERE ts.item_id = ?
              AND strftime('%Y-%m', t.date) = ?
              AND t.pending = 0
            ORDER BY date DESC
        """, (item_id, month, item_id, month)).fetchall()

        spent = sum(float(r["display_amount"]) for r in txn_rows)

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
                "amount": float(r["display_amount"]),
                "full_amount": float(r["full_amount"]),
                "is_split": bool(r["is_split"]),
                "account_name": r["account_name"],
                "account_mask": r["account_mask"],
                "account_subtype": r["account_subtype"],
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
# GET /transactions/{transaction_id} — detail + current assignment/splits
# ---------------------------------------------------------------------------

@router.get("/transactions/{transaction_id}")
async def get_transaction_detail(
    transaction_id: str,
    _user: str = Depends(get_current_user),
):
    """Return full details for a single transaction including current assignment or splits.

    Used by the Edit Expense modal to pre-populate the form. Returns:
      - transaction metadata (date, merchant, amount, account info)
      - current splits list: one entry if directly assigned, multiple if split
    """
    with get_db() as conn:
        txn = conn.execute("""
            SELECT t.transaction_id, t.date, t.name, t.merchant_name,
                   ABS(t.amount) AS amount,
                   t.amount AS raw_amount,
                   a.name AS account_name, a.mask AS account_mask,
                   a.subtype AS account_subtype
            FROM transactions t
            LEFT JOIN accounts a ON a.id = t.account_id
            WHERE t.transaction_id = ?
        """, (transaction_id,)).fetchone()

        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")

        # Check for a direct single-item assignment
        assignment = conn.execute("""
            SELECT ta.item_id, bi.name AS item_name, bg.name AS group_name
            FROM transaction_assignments ta
            JOIN budget_items bi ON bi.id = ta.item_id
            JOIN budget_groups bg ON bg.id = bi.group_id
            WHERE ta.transaction_id = ?
        """, (transaction_id,)).fetchone()

        # Check for split assignments (mutually exclusive with direct assignment)
        split_rows = conn.execute("""
            SELECT ts.item_id, ts.amount, bi.name AS item_name, bg.name AS group_name
            FROM transaction_splits ts
            JOIN budget_items bi ON bi.id = ts.item_id
            JOIN budget_groups bg ON bg.id = bi.group_id
            WHERE ts.transaction_id = ?
            ORDER BY ts.id
        """, (transaction_id,)).fetchall()

        # User-entered check number and notes (if any)
        meta = conn.execute(
            "SELECT check_number, notes FROM transaction_metadata WHERE transaction_id = ?",
            (transaction_id,)
        ).fetchone()

    # Build current splits list for the form
    if split_rows:
        current_splits = [
            {
                "item_id": r["item_id"],
                "amount": float(r["amount"]),
                "item_name": r["item_name"],
                "group_name": r["group_name"],
            }
            for r in split_rows
        ]
    elif assignment:
        # Single assignment — represent as a one-item split list for UI consistency
        current_splits = [{
            "item_id": assignment["item_id"],
            "amount": float(txn["amount"]),
            "item_name": assignment["item_name"],
            "group_name": assignment["group_name"],
        }]
    else:
        current_splits = []

    merchant = txn["merchant_name"] or txn["name"] or "Unknown"
    # description = raw Plaid name (e.g. "EBAY O*21-14309-16607"); shown separately from the
    # cleaned merchant_name. Only include if it differs from the resolved merchant string.
    description = txn["name"] or ""

    return {
        "transaction_id": txn["transaction_id"],
        "date": txn["date"],
        "merchant": merchant,
        "description": description,
        "amount": float(txn["amount"]),
        "is_income": float(txn["raw_amount"]) < 0,  # negative Plaid amount = inflow
        "account_name": txn["account_name"],
        "account_mask": txn["account_mask"],
        "account_subtype": txn["account_subtype"],
        "check_number": meta["check_number"] if meta else None,
        "notes": meta["notes"] if meta else None,
        "splits": current_splits,
    }


# ---------------------------------------------------------------------------
# PUT /transactions/{transaction_id}/splits — save assignment or split
# ---------------------------------------------------------------------------

@router.put("/transactions/{transaction_id}/splits")
async def save_transaction_splits(
    transaction_id: str,
    body: SplitsBody,
    _user: str = Depends(get_current_user),
):
    """Assign a transaction to one or more budget items with specific amounts.

    Single split: stored in transaction_assignments (existing behavior).
    Multiple splits: stored in transaction_splits; transaction_assignments row
    for this transaction is removed.

    Validates that split amounts sum to the full transaction amount (±$0.02
    tolerance for floating-point rounding).
    """
    if not body.splits:
        raise HTTPException(status_code=422, detail="splits list cannot be empty")

    with get_db() as conn:
        txn = conn.execute(
            "SELECT amount FROM transactions WHERE transaction_id = ?",
            (transaction_id,)
        ).fetchone()
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")

        # Validate against the absolute amount — splits are always entered as
        # positive values by the user, regardless of whether the original
        # transaction is a debit or credit/refund.
        total = round(abs(float(txn["amount"])), 2)
        split_sum = round(sum(s.amount for s in body.splits), 2)
        if abs(split_sum - total) > 0.02:
            raise HTTPException(
                status_code=422,
                detail=f"Split amounts ({split_sum:.2f}) must sum to transaction total ({total:.2f})",
            )

        # Clear any existing assignment and splits for this transaction
        conn.execute(
            "DELETE FROM transaction_assignments WHERE transaction_id = ?",
            (transaction_id,)
        )
        conn.execute(
            "DELETE FROM transaction_splits WHERE transaction_id = ?",
            (transaction_id,)
        )

        if len(body.splits) == 1:
            # Single item — use regular transaction_assignments (backward compatible)
            conn.execute(
                "INSERT INTO transaction_assignments (transaction_id, item_id, status)"
                " VALUES (?, ?, 'manual')",
                (transaction_id, body.splits[0].item_id)
            )
        else:
            # Multiple splits — store in transaction_splits with per-split amounts
            for split in body.splits:
                conn.execute(
                    "INSERT INTO transaction_splits (transaction_id, item_id, amount)"
                    " VALUES (?, ?, ?)",
                    (transaction_id, split.item_id, round(split.amount, 2))
                )

        # Persist user-entered metadata (check number and notes) if provided.
        # UPSERT so repeated saves update in place rather than creating duplicates.
        if body.check_number is not None or body.notes is not None:
            conn.execute("""
                INSERT INTO transaction_metadata (transaction_id, check_number, notes, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    check_number = excluded.check_number,
                    notes        = excluded.notes,
                    updated_at   = CURRENT_TIMESTAMP
            """, (transaction_id, body.check_number, body.notes))

        # Record split pattern for future learning (percentage-based so it generalizes across amounts)
        merchant_row = conn.execute(
            "SELECT COALESCE(merchant_name, name, '') AS merchant FROM transactions WHERE transaction_id = ?",
            (transaction_id,)
        ).fetchone()
        if merchant_row and merchant_row["merchant"].strip():
            merchant = merchant_row["merchant"].strip()
            total_amount = sum(s.amount for s in body.splits)
            if total_amount > 0:
                split_pattern = json.dumps([
                    {"item_id": s.item_id, "pct": round(s.amount / total_amount * 100, 2)}
                    for s in body.splits
                ])
                conn.execute("""
                    INSERT INTO split_rules (merchant, splits, use_count)
                    VALUES (?, ?, 1)
                    ON CONFLICT(merchant) DO UPDATE SET
                        splits    = excluded.splits,
                        use_count = use_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                """, (merchant, split_pattern))

    return {"status": "saved", "splits": len(body.splits)}


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
