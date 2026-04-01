"""Net worth investable debug — run on server: python3 scripts/nw_debug.py"""
import sqlite3, os
from datetime import date
from pathlib import Path

db_path = os.environ.get("DB_PATH", str(Path.home() / "vaultic" / "data" / "vaultic.db"))
if not os.path.exists(db_path):
    for p in ["/home/ubuntu/vaultic/data/vaultic.db", "/root/vaultic/data/vaultic.db",
              str(Path.home() / "data" / "vaultic.db")]:
        if os.path.exists(p):
            db_path = p
            break

print(f"DB: {db_path}")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
today = date.today().isoformat()
print(f"Today: {today}\n")

# 1. Latest snapshot
snap = conn.execute("SELECT * FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT 1").fetchone()
if snap:
    print("=== STORED SNAPSHOT ===")
    print(f"  snapped_at:  {snap['snapped_at']}")
    print(f"  total:       ${snap['total']:,.2f}")
    print(f"  liquid:      ${snap['liquid']:,.2f}")
    print(f"  invested:    ${snap['invested']:,.2f}")
    print(f"  crypto:      ${snap['crypto']:,.2f}")
    print(f"  real_estate: ${snap['real_estate']:,.2f}")
    print(f"  vehicles:    ${snap['vehicles']:,.2f}")
    print(f"  liabilities: ${snap['liabilities']:,.2f}")
    print(f"  other_assets:${snap['other_assets']:,.2f}")

    mortgage_row = conn.execute(
        "SELECT COALESCE(value,0) FROM manual_entries WHERE category='other_liability' "
        "AND (exclude_from_net_worth IS NULL OR exclude_from_net_worth=0) "
        "ORDER BY entered_at DESC LIMIT 1"
    ).fetchone()
    mortgage = float(mortgage_row[0]) if mortgage_row else 0.0
    credit_liab = max(0.0, (snap['liabilities'] or 0) - mortgage)
    investable = (snap['liquid'] or 0) + (snap['invested'] or 0) + (snap['crypto'] or 0) + (snap['other_assets'] or 0) - credit_liab
    print(f"\n  mortgage:         ${mortgage:,.2f}")
    print(f"  credit_liab:      ${credit_liab:,.2f}")
    print(f"  STORED INVESTABLE:${investable:,.2f}")
else:
    print("NO SNAPSHOT FOUND")
    mortgage = 0.0

# 2. Plaid accounts
print("\n=== PLAID ACCOUNTS (is_active=1, is_manual=0) ===")
accounts = conn.execute(
    "SELECT * FROM accounts WHERE is_active=1 AND is_manual=0 ORDER BY institution_name, name"
).fetchall()
plaid_liquid = plaid_invested = plaid_crypto = plaid_liab = 0.0
for acct in accounts:
    bal_row = conn.execute(
        "SELECT current, snapped_at FROM account_balances WHERE account_id=? AND snapped_at=?",
        (acct['id'], today)
    ).fetchone()
    if not bal_row or bal_row['current'] is None:
        bal_row = conn.execute(
            "SELECT current, snapped_at FROM account_balances WHERE account_id=? "
            "AND current IS NOT NULL ORDER BY snapped_at DESC LIMIT 1",
            (acct['id'],)
        ).fetchone()

    bal = bal_row['current'] if bal_row else None
    bal_date = bal_row['snapped_at'] if bal_row else 'NONE'
    t, s = acct['type'], (acct['subtype'] or '')

    if bal is None:
        bucket = 'NO_BALANCE'
    elif t == 'crypto':
        plaid_crypto += bal; bucket = 'crypto'
    elif t == 'depository' and s in ('checking', 'savings', 'money market', 'paypal', 'prepaid'):
        plaid_liquid += bal; bucket = 'liquid'
    elif t == 'investment' or s in ('401k', 'ira', 'roth', 'pension'):
        plaid_invested += bal; bucket = 'invested'
    elif t in ('credit', 'loan'):
        plaid_liab += bal; bucket = 'liabilities'
    else:
        plaid_liquid += bal; bucket = 'liquid(catch-all)'

    flag = " *** STALE" if bal_date != today and bal is not None else ""
    print(f"  [{bucket:15}] {(acct['institution_name'] or '?'):20} {acct['name']:30} "
          f"type={t}/{s:15} bal=${bal or 0:>12,.2f}  date={bal_date}{flag}")

print(f"\n  Plaid totals: liquid=${plaid_liquid:,.2f}  invested=${plaid_invested:,.2f}  "
      f"crypto=${plaid_crypto:,.2f}  liabilities=${plaid_liab:,.2f}")

# 3. Manual entries
print("\n=== MANUAL ENTRIES ===")
manuals = conn.execute(
    "SELECT id, name, category, value, exclude_from_net_worth, account_number "
    "FROM manual_entries ORDER BY category, name"
).fetchall()
manual_invested = manual_liquid = manual_crypto = manual_other = 0.0
for m in manuals:
    excl = "EXCLUDED" if m['exclude_from_net_worth'] else "counted"
    val = m['value'] or 0
    cat = m['category']
    if not m['exclude_from_net_worth']:
        if cat == 'invested': manual_invested += val
        elif cat == 'liquid': manual_liquid += val
        elif cat == 'crypto': manual_crypto += val
        elif cat == 'other_asset': manual_other += val
    print(f"  [{excl:8}] {cat:18} {m['name']:40} ${val:>12,.2f}  acct={m['account_number'] or '-'}")

print(f"\n  Manual totals: invested=${manual_invested:,.2f}  liquid=${manual_liquid:,.2f}  "
      f"crypto=${manual_crypto:,.2f}  other_assets=${manual_other:,.2f}")

# 4. Live recalculation
print("\n=== LIVE RECALCULATION ===")
total_liquid = plaid_liquid + manual_liquid
total_invested = plaid_invested + manual_invested
total_crypto = plaid_crypto + manual_crypto
total_other = manual_other
credit_liab_live = max(0.0, plaid_liab - mortgage)
live_investable = total_liquid + total_invested + total_crypto + total_other - credit_liab_live

print(f"  liquid:      ${total_liquid:,.2f}  (plaid ${plaid_liquid:,.2f} + manual ${manual_liquid:,.2f})")
print(f"  invested:    ${total_invested:,.2f}  (plaid ${plaid_invested:,.2f} + manual ${manual_invested:,.2f})")
print(f"  crypto:      ${total_crypto:,.2f}  (plaid ${plaid_crypto:,.2f} + manual ${manual_crypto:,.2f})")
print(f"  other_assets:${total_other:,.2f}")
print(f"  liabilities: ${plaid_liab:,.2f}  (mortgage ${mortgage:,.2f} excluded)")
print(f"  credit_liab: ${credit_liab_live:,.2f}")
print(f"\n  LIVE INVESTABLE: ${live_investable:,.2f}")

if snap:
    diff = live_investable - investable
    print(f"  STORED:          ${investable:,.2f}")
    print(f"  DISCREPANCY:     ${diff:+,.2f}")
    if abs(diff) > 1:
        print(f"\n  *** MISMATCH — stored snapshot does NOT match live data ***")

# 5. Check for accounts with NO balance at all
print("\n=== ACCOUNTS WITH NO BALANCE ===")
no_bal = conn.execute("""
    SELECT a.id, a.name, a.type, a.subtype, a.institution_name, a.source
    FROM accounts a
    WHERE a.is_active=1 AND a.is_manual=0
    AND NOT EXISTS (SELECT 1 FROM account_balances WHERE account_id=a.id)
""").fetchall()
if no_bal:
    for a in no_bal:
        print(f"  *** MISSING: {a['institution_name']} / {a['name']} (type={a['type']}/{a['subtype']}, source={a['source']})")
else:
    print("  (none — all accounts have at least one balance)")

conn.close()
