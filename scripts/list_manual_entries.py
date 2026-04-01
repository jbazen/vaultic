"""Diagnostic: list all manual entries to find duplicates."""
import sqlite3
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT id, name, category, value, account_number,
           CASE WHEN summary_json IS NOT NULL AND summary_json != '' THEN 'YES' ELSE 'NO' END AS has_summary,
           entered_at, exclude_from_net_worth
    FROM manual_entries
    ORDER BY category, name
""").fetchall()

print(f"Total manual entries: {len(rows)}\n")
print(f"{'ID':>5} | {'Category':18} | {'Name':40} | {'Value':>12} | {'Acct#':15} | {'Summary':7} | {'Date':10} | {'Excl'}")
print("-" * 140)
for r in rows:
    print(f"{r['id']:5} | {r['category']:18} | {r['name']:40} | {r['value']:12,.2f} | {(r['account_number'] or ''):15} | {r['has_summary']:7} | {r['entered_at']:10} | {r['exclude_from_net_worth'] or 0}")
