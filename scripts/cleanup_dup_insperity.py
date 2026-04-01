"""One-time: remove duplicate Insperity manual entry, keep the PDF-imported one."""
import sqlite3
from pathlib import Path

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT id, name, account_number, summary_json FROM manual_entries "
    "WHERE LOWER(name) LIKE '%nsperit%' AND category='invested' ORDER BY id"
).fetchall()
if len(rows) > 1:
    keep = next((r for r in rows if r["account_number"]), rows[-1])
    for r in rows:
        if r["id"] != keep["id"]:
            conn.execute("DELETE FROM manual_entries WHERE id = ?", (r["id"],))
            print(f"Deleted duplicate id={r['id']} name={r['name']}")
    conn.commit()
    print(f"Kept id={keep['id']} name={keep['name']} acct={keep['account_number']}")
else:
    print("No duplicate Insperity entries found")
conn.close()
