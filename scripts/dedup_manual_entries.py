"""One-time: deduplicate manual entries.
For each category, if multiple entries exist and some have account_numbers while
others don't, delete the ones without account_numbers (they're placeholders that
were replaced by PDF imports)."""
import sqlite3
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT id, name, category, value, account_number, summary_json,
           entered_at, exclude_from_net_worth
    FROM manual_entries
    ORDER BY category, name
""").fetchall()

deleted_ids = set()

# Group by category
by_cat = {}
for r in rows:
    by_cat.setdefault(r["category"], []).append(r)

for cat, entries in by_cat.items():
    if len(entries) < 2:
        continue
    has_acct = [e for e in entries if e["account_number"]]
    no_acct = [e for e in entries if not e["account_number"]]
    # Skip categories where no entries have account numbers (home_value, car_value, etc.)
    if not has_acct:
        continue
    # If there are entries WITH account numbers and entries WITHOUT, the ones without
    # are manual placeholders that were superseded by PDF imports
    for e in no_acct:
        # Don't delete the Overall Portfolio summary (it's intentionally excluded)
        if e["exclude_from_net_worth"]:
            continue
        conn.execute("DELETE FROM manual_entries WHERE id = ?", (e["id"],))
        deleted_ids.add(e["id"])
        print(f"Deleted: id={e['id']} name={e['name']} cat={cat} (no account_number, PDF import exists)")

conn.commit()
print(f"\nDeleted {len(deleted_ids)} duplicate entries total")
conn.close()
