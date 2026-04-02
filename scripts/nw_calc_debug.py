"""Debug: replicate the net worth calculation and show every component."""
import sqlite3
from pathlib import Path
from datetime import date

db = Path.home() / "vaultic" / "data" / "vaultic.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

today = date.today().isoformat()

# Plaid accounts
accounts = conn.execute(
    "SELECT id, name, type, subtype FROM accounts WHERE is_active = 1 AND is_manual = 0"
).fetchall()

liquid = invested = crypto = liabilities = 0.0

print("=== PLAID ACCOUNTS ===")
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
        print(f"  SKIP {acct['name']} ({acct['type']}/{acct['subtype']}) — no balance")
        continue
    bal = row["current"]
    t, s = acct["type"], (acct["subtype"] or "")

    bucket = "?"
    if t == "crypto":
        crypto += bal; bucket = "crypto"
    elif t == "depository" and s in ("checking", "savings", "money market", "paypal", "prepaid"):
        liquid += bal; bucket = "liquid"
    elif t == "investment" or s in ("401k", "ira", "roth", "pension"):
        invested += bal; bucket = "invested"
    elif t in ("credit", "loan"):
        liabilities += bal; bucket = "liabilities"
    else:
        liquid += bal; bucket = "liquid(catch-all)"

    print(f"  {bucket:15} {bal:>12,.2f}  {acct['name']} ({t}/{s})")

print(f"\n  Plaid totals: liquid={liquid:,.2f} invested={invested:,.2f} crypto={crypto:,.2f} liabilities={liabilities:,.2f}")

# Manual entries
print("\n=== MANUAL ENTRIES ===")
manual = conn.execute(
    "SELECT id, name, category, value, account_number, exclude_from_net_worth FROM manual_entries ORDER BY category"
).fetchall()
for m in manual:
    excl = "EXCLUDED" if m["exclude_from_net_worth"] else ""
    print(f"  {m['category']:18} {m['value']:>12,.2f}  {m['name']}  {excl}")

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
real_estate += _sum_manual("real_estate")
vehicles += _sum_manual("vehicles")

total = liquid + invested + crypto + real_estate + vehicles + other_assets - liabilities

print(f"\n=== FINAL CALCULATION ===")
print(f"  liquid:       {liquid:>12,.2f}")
print(f"  invested:     {invested:>12,.2f}")
print(f"  crypto:       {crypto:>12,.2f}")
print(f"  real_estate:  {real_estate:>12,.2f}")
print(f"  vehicles:     {vehicles:>12,.2f}")
print(f"  other_assets: {other_assets:>12,.2f}")
print(f"  liabilities:  {liabilities:>12,.2f}")
print(f"  TOTAL:        {total:>12,.2f}")

# What's currently stored
snap = conn.execute(
    "SELECT * FROM net_worth_snapshots WHERE snapped_at = ?", (today,)
).fetchone()
if snap:
    print(f"\n=== STORED SNAPSHOT FOR {today} ===")
    print(f"  total:        {snap['total']:>12,.2f}")
    print(f"  liquid:       {snap['liquid']:>12,.2f}")
    print(f"  invested:     {snap['invested']:>12,.2f}")
    print(f"  liabilities:  {snap['liabilities']:>12,.2f}")
    if abs(snap['total'] - total) > 0.01:
        print(f"  *** MISMATCH: stored={snap['total']:,.2f} vs calculated={total:,.2f} ***")
    else:
        print(f"  OK — stored matches calculated")

conn.close()
