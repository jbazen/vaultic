"""Check which budget items did NOT carry forward from March to April."""
import sqlite3
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

march = "2026-03"
april = "2026-04"

# Get all items with planned amounts in March
march_amounts = conn.execute("""
    SELECT ba.item_id, ba.planned, bi.name, bi.is_archived, bi.is_deleted,
           bg.name AS group_name, bg.type, bg.is_archived AS group_archived, bg.is_deleted AS group_deleted
    FROM budget_amounts ba
    JOIN budget_items bi ON bi.id = ba.item_id
    JOIN budget_groups bg ON bg.id = bi.group_id
    WHERE ba.month = ?
    ORDER BY bg.type, bg.name, bi.name
""", (march,)).fetchall()

# Get all items with planned amounts in April
april_amounts = {}
for r in conn.execute("SELECT item_id, planned FROM budget_amounts WHERE month = ?", (april,)).fetchall():
    april_amounts[r["item_id"]] = r["planned"]

print(f"=== MARCH ({march}) BUDGET: {len(march_amounts)} items with planned amounts ===")
print(f"=== APRIL ({april}) BUDGET: {len(april_amounts)} items carried forward ===\n")

missing = []
carried = []
for r in march_amounts:
    item_id = r["item_id"]
    if item_id in april_amounts:
        carried.append(r)
    else:
        missing.append(r)

if missing:
    print("=== MISSING FROM APRIL (did NOT carry forward) ===")
    for r in missing:
        flags = []
        if r["is_archived"]: flags.append("ITEM_ARCHIVED")
        if r["is_deleted"]: flags.append("ITEM_DELETED")
        if r["group_archived"]: flags.append("GROUP_ARCHIVED")
        if r["group_deleted"]: flags.append("GROUP_DELETED")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {r['type']:7} | {r['group_name']:25} | {r['name']:30} | planned=${r['planned']:>10,.2f}{flag_str}")
else:
    print("All March items carried forward to April.")

print(f"\n=== CARRIED FORWARD: {len(carried)} items ===")
for r in carried:
    apr_val = april_amounts[r["item_id"]]
    match = "OK" if apr_val == r["planned"] else f"CHANGED ${r['planned']:.2f} -> ${apr_val:.2f}"
    print(f"  {r['type']:7} | {r['group_name']:25} | {r['name']:30} | ${apr_val:>10,.2f}  {match}")

# Also check: items that exist but are hidden (archived/deleted)
print("\n=== ITEMS MARKED ARCHIVED (hidden on current month) ===")
archived = conn.execute("""
    SELECT bi.name, bi.is_archived, bg.name AS group_name, bg.is_archived AS group_archived
    FROM budget_items bi
    JOIN budget_groups bg ON bg.id = bi.group_id
    WHERE bi.is_deleted = 0 AND (bi.is_archived = 1 OR bg.is_archived = 1)
    ORDER BY bg.name, bi.name
""").fetchall()
for r in archived:
    reason = "item" if r["is_archived"] else "group"
    print(f"  {r['group_name']:25} | {r['name']:30} | archived_by={reason}")

conn.close()
