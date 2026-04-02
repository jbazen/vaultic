"""Debug: replicate the net worth calculation and show every component."""
import sqlite3
from pathlib import Path
from datetime import date, datetime

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

today = date.today().isoformat()
print(f"Server date.today() = {today}")
print(f"Server datetime.now() = {datetime.now()}")

# Show ALL recent snapshots
print("\n=== ALL RECENT SNAPSHOTS ===")
snaps = conn.execute(
    "SELECT * FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT 5"
).fetchall()
for s in snaps:
    print(f"  {s['snapped_at']}: total={s['total']:>12,.2f}  liquid={s['liquid']:,.2f}  invested={s['invested']:,.2f}  liabilities={s['liabilities']:,.2f}")

# What the /api/net-worth/latest endpoint returns
latest = snaps[0] if snaps else None
if latest:
    # Get mortgage for investable calc
    mort = conn.execute(
        "SELECT COALESCE(value, 0) FROM manual_entries "
        "WHERE category = 'other_liability' "
        "AND (exclude_from_net_worth IS NULL OR exclude_from_net_worth = 0) "
        "ORDER BY entered_at DESC LIMIT 1"
    ).fetchone()
    mortgage = abs(float(mort[0])) if mort else 0.0
    credit_liabilities = max(0.0, (latest["liabilities"] or 0) - mortgage)
    investable = (latest["liquid"] or 0) + (latest["invested"] or 0) + (latest["crypto"] or 0) + (latest["other_assets"] or 0) - credit_liabilities
    print(f"\n=== WHAT DASHBOARD SHOWS ===")
    print(f"  Latest snapshot date: {latest['snapped_at']}")
    print(f"  Total net worth:  {latest['total']:>12,.2f}")
    print(f"  Mortgage (abs):   {mortgage:>12,.2f}")
    print(f"  Credit liab:      {credit_liabilities:>12,.2f}")
    print(f"  Investable:       {investable:>12,.2f}")

# Now recalculate what it SHOULD be
accounts = conn.execute(
    "SELECT id, name, type, subtype FROM accounts WHERE is_active = 1 AND is_manual = 0"
).fetchall()

liquid = invested = crypto = liabilities = 0.0

for acct in accounts:
    row = conn.execute(
        "SELECT current FROM account_balances WHERE account_id = ? AND snapped_at = ?",
        (acct["id"], today),
    ).fetchone()
    if not row or row["current"] is None:
        row = conn.execute(
            "SELECT current FROM account_balances WHERE account_id = ? "
            "AND current IS NOT NULL ORDER BY snapped_at DESC LIMIT 1",
            (acct["id"],),
        ).fetchone()
    if not row or row["current"] is None:
        continue
    bal = row["current"]
    t, s = acct["type"], (acct["subtype"] or "")
    if t == "crypto":
        crypto += bal
    elif t == "depository" and s in ("checking", "savings", "money market", "paypal", "prepaid"):
        liquid += bal
    elif t == "investment" or s in ("401k", "ira", "roth", "pension"):
        invested += bal
    elif t in ("credit", "loan"):
        liabilities += bal
    else:
        liquid += bal

def _latest(category):
    r = conn.execute(
        "SELECT value FROM manual_entries WHERE category = ? AND (exclude_from_net_worth IS NULL OR exclude_from_net_worth = 0) ORDER BY entered_at DESC LIMIT 1",
        (category,),
    ).fetchone()
    return r["value"] if r else 0.0

def _sum_manual(category):
    r = conn.execute(
        "SELECT COALESCE(SUM(value), 0) FROM manual_entries WHERE category = ? AND (exclude_from_net_worth IS NULL OR exclude_from_net_worth = 0)",
        (category,),
    ).fetchone()
    return float(r[0]) if r else 0.0

real_estate = _latest("home_value")
vehicles = _latest("car_value")
liabilities += abs(_sum_manual("other_liability"))
other_assets = _sum_manual("other_asset")
invested += _sum_manual("invested")
liquid += _sum_manual("liquid")
crypto += _sum_manual("crypto")

total = liquid + invested + crypto + real_estate + vehicles + other_assets - liabilities

print(f"\n=== CORRECT CALCULATION ===")
print(f"  TOTAL:        {total:>12,.2f}")
if latest and abs(latest['total'] - total) > 1:
    print(f"  *** STALE SNAPSHOT: stored={latest['total']:,.2f} expected={total:,.2f} diff={latest['total'] - total:,.2f} ***")
else:
    print(f"  OK — snapshot matches")

conn.close()
