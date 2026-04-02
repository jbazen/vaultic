"""Force-refresh the net worth snapshot. Run post-deploy to apply calculation fixes."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from datetime import date
from api.database import init_db, get_db
from api.sync import _take_net_worth_snapshot

init_db()
today = date.today().isoformat()
_take_net_worth_snapshot(today)

with get_db() as conn:
    row = conn.execute(
        "SELECT total, liabilities FROM net_worth_snapshots WHERE snapped_at = ?", (today,)
    ).fetchone()
    if row:
        print(f"Snapshot {today}: total=${row['total']:,.2f} liabilities=${row['liabilities']:,.2f}")
    else:
        print(f"ERROR: No snapshot written for {today}")
