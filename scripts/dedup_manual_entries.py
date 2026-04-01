"""One-time: deduplicate manual entries that share a name but one has an account_number.
Keeps the entry WITH an account_number, deletes the one without."""
import sqlite3
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

# Find entries where the same name appears with and without account_number
rows = conn.execute("""
    SELECT id, name, category, value, account_number, entered_at
    FROM manual_entries
    ORDER BY name, account_number DESC
""").fetchall()

by_name = {}
for r in rows:
    key = r["name"].strip().lower()
    by_name.setdefault(key, []).append(r)

deleted = 0
for name, entries in by_name.items():
    if len(entries) < 2:
        continue
    has_acct = [e for e in entries if e["account_number"]]
    no_acct = [e for e in entries if not e["account_number"]]
    if has_acct and no_acct:
        for e in no_acct:
            conn.execute("DELETE FROM manual_entries WHERE id = ?", (e["id"],))
            print(f"Deleted duplicate: id={e['id']} name={e['name']} (no account_number)")
            deleted += 1
        for e in has_acct:
            print(f"  Kept: id={e['id']} name={e['name']} acct={e['account_number']}")

conn.commit()
print(f"\nDeleted {deleted} duplicate entries")
conn.close()
