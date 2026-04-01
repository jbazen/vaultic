"""One-time: for singleton categories (home_value, car_value, credit_score),
keep only the most recent entry and delete all older ones."""
import sqlite3
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

SINGLETON = ("home_value", "car_value", "credit_score")
deleted = 0

for cat in SINGLETON:
    rows = conn.execute(
        "SELECT id, name, value, entered_at FROM manual_entries "
        "WHERE category = ? ORDER BY entered_at DESC", (cat,)
    ).fetchall()
    if len(rows) <= 1:
        continue
    keep = rows[0]
    print(f"[{cat}] Keeping: id={keep['id']} name={keep['name']} value={keep['value']} date={keep['entered_at']}")
    for r in rows[1:]:
        conn.execute("DELETE FROM manual_entries WHERE id = ?", (r["id"],))
        print(f"  Deleted: id={r['id']} name={r['name']} value={r['value']} date={r['entered_at']}")
        deleted += 1

conn.commit()
print(f"\nDeleted {deleted} duplicate singleton entries")
conn.close()
