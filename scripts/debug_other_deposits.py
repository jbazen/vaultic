#!/usr/bin/env python3
"""Debug why Other Deposits is not showing."""
import sqlite3, os
conn = sqlite3.connect(os.environ.get("DB_PATH", "data/vaultic.db"))
conn.row_factory = sqlite3.Row

item = conn.execute(
    "SELECT id, name, group_id, is_archived FROM budget_items"
    " WHERE LOWER(name) = 'other deposits' AND is_deleted = 0"
).fetchone()

if not item:
    print("OTHER DEPOSITS NOT FOUND IN DB")
    exit(1)

iid = item["id"]
print(f"Item: id={iid}, name={item['name']}, is_archived={item['is_archived']}")

amt = conn.execute(
    "SELECT planned FROM budget_amounts WHERE item_id = ? AND month = ?",
    (iid, "2026-03")
).fetchone()
print(f"Planned amount row for 2026-03: {dict(amt) if amt else 'NONE'}")

direct = conn.execute("""
    SELECT COALESCE(SUM(t.amount), 0) AS spent
    FROM transaction_assignments ta
    JOIN transactions t ON t.transaction_id = ta.transaction_id
    WHERE ta.item_id = ? AND strftime('%Y-%m', t.date) = '2026-03' AND t.pending = 0
""", (iid,)).fetchone()
print(f"Direct spent: {direct['spent']}")

split = conn.execute("""
    SELECT COALESCE(SUM(ts.amount), 0) AS spent
    FROM transaction_splits ts
    JOIN transactions t ON t.transaction_id = ts.transaction_id
    WHERE ts.item_id = ? AND strftime('%Y-%m', t.date) = '2026-03' AND t.pending = 0
""", (iid,)).fetchone()
print(f"Split spent: {split['spent']}")

total = float(direct["spent"]) + float(split["spent"])
print(f"Total spent: {total}")
print()
print(f"=== FILTER RESULTS ===")
print(f"planned > 0: {(amt['planned'] if amt else 0) > 0}")
print(f"spent != 0: {total != 0}")
print(f"is_archived: {bool(item['is_archived'])}")
print(f"SHOULD SHOW (current month, not archived): YES" if not item["is_archived"] else "SHOULD SHOW: only if has activity")
print(f"SHOULD SHOW (has activity): {'YES' if total != 0 or (amt and amt['planned'] > 0) else 'NO'}")