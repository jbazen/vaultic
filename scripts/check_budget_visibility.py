#!/usr/bin/env python3
"""Quick diagnostic: compare old vs new budget visibility for current month.

Run on server: python3 scripts/check_budget_visibility.py
No auth needed — queries SQLite directly.
"""
import sqlite3
import os
from datetime import date

DB_PATH = os.environ.get("DB_PATH", "vaultic.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

month = date.today().strftime("%Y-%m")
print(f"=== Budget Visibility Check — {month} ===\n")

# Fetch all non-deleted groups with archive status
groups = conn.execute(
    "SELECT id, name, type, is_archived FROM budget_groups WHERE is_deleted = 0 ORDER BY display_order, name"
).fetchall()

items = conn.execute(
    "SELECT id, name, group_id, is_archived FROM budget_items WHERE is_deleted = 0 ORDER BY display_order, name"
).fetchall()

amounts = {}
for r in conn.execute("SELECT item_id, planned FROM budget_amounts WHERE month = ?", (month,)).fetchall():
    amounts[r["item_id"]] = float(r["planned"])

direct = {}
for r in conn.execute("""
    SELECT ta.item_id, COALESCE(SUM(t.amount), 0) AS spent
    FROM transaction_assignments ta
    JOIN transactions t ON t.transaction_id = ta.transaction_id
    WHERE strftime('%Y-%m', t.date) = ? AND t.pending = 0
    GROUP BY ta.item_id
""", (month,)).fetchall():
    direct[r["item_id"]] = float(r["spent"])

split_s = {}
for r in conn.execute("""
    SELECT ts.item_id, COALESCE(SUM(ts.amount), 0) AS spent
    FROM transaction_splits ts
    JOIN transactions t ON t.transaction_id = ts.transaction_id
    WHERE strftime('%Y-%m', t.date) = ? AND t.pending = 0
    GROUP BY ts.item_id
""", (month,)).fetchall():
    split_s[r["item_id"]] = float(r["spent"])

items_by_group = {}
for i in items:
    items_by_group.setdefault(i["group_id"], []).append(i)

old_visible = []
new_visible = []
old_hidden = []
new_only = []

for g in groups:
    gid = g["id"]
    group_planned = 0
    group_spent = 0
    gitems = items_by_group.get(gid, [])

    item_details = []
    for item in gitems:
        iid = item["id"]
        planned = amounts.get(iid, 0)
        spent = direct.get(iid, 0) + split_s.get(iid, 0)
        group_planned += planned
        group_spent += spent
        item_details.append((item["name"], planned, spent, bool(item["is_archived"])))

    has_activity = group_planned > 0 or group_spent != 0
    old_vis = has_activity
    new_vis = has_activity or not g["is_archived"]  # current month = always show non-archived

    if old_vis:
        old_visible.append((g["name"], g["type"], group_planned, group_spent, item_details))
    if new_vis and not old_vis:
        new_only.append((g["name"], g["type"], group_planned, group_spent, bool(g["is_archived"]), item_details))
    if not new_vis:
        old_hidden.append((g["name"], g["type"], bool(g["is_archived"]), len(item_details)))

print(f"Total non-deleted groups: {len(groups)}")
print(f"Old filter (activity only): {len(old_visible)} visible")
print(f"New filter (activity + non-archived): {len(old_visible) + len(new_only)} visible")
print(f"Still hidden (archived, no activity): {len(old_hidden)}")
print()

print("--- CURRENTLY VISIBLE (both old and new) ---")
for name, gtype, pl, sp, itms in old_visible:
    print(f"  {gtype:7s} | {name} (planned=${pl:.2f}, spent=${sp:.2f})")
    for iname, ipl, isp, iarch in itms:
        arch_tag = " [ARCHIVED]" if iarch else ""
        print(f"           -> {iname} (${ipl:.2f} / ${isp:.2f}){arch_tag}")

if new_only:
    print()
    print("--- NEWLY VISIBLE (added by new filter) ---")
    for name, gtype, pl, sp, is_arch, itms in new_only:
        print(f"  {gtype:7s} | {name} (is_archived={is_arch})")
        for iname, ipl, isp, iarch in itms:
            arch_tag = " [ARCHIVED]" if iarch else ""
            print(f"           -> {iname}{arch_tag}")

if not new_only:
    print()
    print("--- NO NEW GROUPS ADDED (everything was already visible) ---")

print()
print(f"--- REMOVED GROUPS: 0 (new filter never removes) ---")
print()
print(f"--- STILL HIDDEN ({len(old_hidden)} archived groups with no activity this month) ---")
if old_hidden:
    for name, gtype, is_arch, n_items in old_hidden[:10]:
        print(f"  {gtype:7s} | {name} ({n_items} items, archived={is_arch})")
    if len(old_hidden) > 10:
        print(f"  ... and {len(old_hidden) - 10} more")

conn.close()
