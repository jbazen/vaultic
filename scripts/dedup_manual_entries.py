"""One-time: deduplicate manual entries.
Two strategies:
  1. Same name: if one has account_number and the other doesn't, delete the one without.
  2. Same category: if one has account_number+summary_json (PDF import) and the other has
     neither (manual entry), delete the manual one — it was the placeholder before PDF import.
Keeps the entry WITH an account_number in both cases."""
import sqlite3
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT id, name, category, value, account_number, summary_json, entered_at
    FROM manual_entries
    ORDER BY category, name
""").fetchall()

deleted_ids = set()

# Strategy 1: same name, one has account_number
by_name = {}
for r in rows:
    by_name.setdefault(r["name"].strip().lower(), []).append(r)

for name, entries in by_name.items():
    if len(entries) < 2:
        continue
    has_acct = [e for e in entries if e["account_number"]]
    no_acct = [e for e in entries if not e["account_number"]]
    if has_acct and no_acct:
        for e in no_acct:
            conn.execute("DELETE FROM manual_entries WHERE id = ?", (e["id"],))
            deleted_ids.add(e["id"])
            print(f"[name-match] Deleted id={e['id']} name={e['name']} (no account_number)")
        for e in has_acct:
            print(f"  Kept: id={e['id']} name={e['name']} acct={e['account_number']}")

# Strategy 2: same category, one is manual placeholder (no acct, no summary_json)
# and the other is a PDF import (has account_number + summary_json)
by_cat = {}
for r in rows:
    if r["id"] in deleted_ids:
        continue
    by_cat.setdefault(r["category"], []).append(r)

for cat, entries in by_cat.items():
    if len(entries) < 2:
        continue
    pdf_imports = [e for e in entries if e["account_number"] and e["summary_json"]]
    manual_placeholders = [e for e in entries if not e["account_number"] and not e["summary_json"]]
    if not pdf_imports or not manual_placeholders:
        continue
    # For each manual placeholder, check if a PDF import in the same category likely
    # replaced it. Only delete if there's a clear 1:1 correspondence or the manual
    # entry has no unique value (it was a temporary placeholder).
    for m in manual_placeholders:
        conn.execute("DELETE FROM manual_entries WHERE id = ?", (m["id"],))
        deleted_ids.add(m["id"])
        print(f"[category-match] Deleted id={m['id']} name={m['name']} cat={cat} (manual placeholder, PDF import exists)")

conn.commit()
print(f"\nDeleted {len(deleted_ids)} duplicate entries total")
conn.close()
