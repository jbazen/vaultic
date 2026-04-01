"""One-time: ensure all active budget items have budget_amounts rows for April 2026.
The carryforward fix only runs when NO rows exist for a month. Since April already
had rows from a prior carryforward, items without planned amounts were never seeded."""
import sqlite3
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

month = "2026-04"

# Insert $0 rows for all active items that don't already have a row
result = conn.execute("""
    INSERT OR IGNORE INTO budget_amounts (item_id, month, planned)
    SELECT bi.id, ?, 0
    FROM budget_items bi
    JOIN budget_groups bg ON bg.id = bi.group_id
    WHERE bi.is_deleted = 0 AND bi.is_archived = 0
      AND bg.is_deleted = 0 AND bg.is_archived = 0
      AND bi.id NOT IN (SELECT item_id FROM budget_amounts WHERE month = ?)
""", (month, month))

print(f"Inserted {result.rowcount} missing budget_amounts rows for {month}")

# Show what was added
added = conn.execute("""
    SELECT bi.name, bg.name AS group_name
    FROM budget_items bi
    JOIN budget_groups bg ON bg.id = bi.group_id
    JOIN budget_amounts ba ON ba.item_id = bi.id AND ba.month = ?
    WHERE ba.planned = 0
    ORDER BY bg.name, bi.name
""", (month,)).fetchall()
for r in added:
    print(f"  {r['group_name']:25} | {r['name']}")

conn.commit()
conn.close()
