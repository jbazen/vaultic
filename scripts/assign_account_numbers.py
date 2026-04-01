"""Assign account_numbers to manual entries that are missing them.
Extracts from summary_json where available. Run on server post-deploy."""
import sqlite3, json
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, name, category, account_number, summary_json FROM manual_entries "
    "WHERE account_number IS NULL OR account_number = ''"
).fetchall()

import re

def normalize(raw):
    if not raw:
        return None
    return re.sub(r"[^A-Z0-9]", "", str(raw).upper()) or None

fixed = 0
for r in rows:
    # Try to pull account_number from summary_json
    sj = json.loads(r["summary_json"]) if r["summary_json"] else {}
    acct = normalize(sj.get("account_number"))
    if acct:
        conn.execute("UPDATE manual_entries SET account_number = ? WHERE id = ?", (acct, r["id"]))
        print(f"Fixed: id={r['id']} name={r['name']} -> acct={acct}")
        fixed += 1
    else:
        print(f"No account_number available: id={r['id']} name={r['name']} category={r['category']}")

conn.commit()
print(f"\nFixed {fixed} entries")
conn.close()
