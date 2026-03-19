"""
import_everydollar_history.py
─────────────────────────────
Fetches your EveryDollar budget month-by-month and imports each one into
Vaultic's /api/budget/import/json endpoint.

SETUP
-----
1. Log into everydollar.com in your browser.
2. Open DevTools (F12) → Network tab → navigate to any budget month.
3. Find the getBudgetByDate request, right-click → Copy → Copy as cURL.
4. From that cURL command, grab the full Cookie header value and paste it
   into EVERYDOLLAR_COOKIE below.
5. Get your Vaultic JWT token from the browser:
   - Open vaulticsage.com, open DevTools → Console, run:
     localStorage.getItem("vaultic_token")
   - Paste the result into VAULTIC_TOKEN below.
6. Set START_DATE to the earliest month you want to import.
7. Run:  python scripts/import_everydollar_history.py

The script prints a summary line for each month and a final total.
It sleeps 1.5 seconds between EveryDollar requests to avoid hammering
their servers.
"""

import time
import json
import requests
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

# ── CONFIG — fill these in ───────────────────────────────────────────────────

# Paste the full Cookie header value from DevTools "Copy as cURL"
# Example: "_everydollar_session=abc123; other_cookie=xyz"
EVERYDOLLAR_COOKIE = "PASTE_YOUR_COOKIE_HERE"

# Your Vaultic JWT token from localStorage.getItem("vaultic_token")
VAULTIC_TOKEN = "PASTE_YOUR_VAULTIC_TOKEN_HERE"

# Earliest month to import (YYYY-MM-DD, will be rounded to 1st of month)
START_DATE = "2023-01-01"

# Your Vaultic base URL
VAULTIC_BASE_URL = "https://vaulticsage.com"

# Seconds to wait between EveryDollar fetches (be polite)
SLEEP_SECONDS = 1.5

# ── END CONFIG ────────────────────────────────────────────────────────────────

EVERYDOLLAR_URL = "https://www.everydollar.com/app/api/budgets/search/getBudgetByDate"

EVERYDOLLAR_HEADERS = {
    "Cookie": EVERYDOLLAR_COOKIE,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.everydollar.com/app/budget",
}

VAULTIC_HEADERS = {
    "Authorization": f"Bearer {VAULTIC_TOKEN}",
    "Content-Type": "application/json",
}


def month_range(start: date, end: date):
    """Yield the first day of each month from start to end, inclusive."""
    current = start.replace(day=1)
    while current <= end.replace(day=1):
        yield current
        current += relativedelta(months=1)


def fetch_everydollar_month(month_date: date) -> dict | None:
    """Fetch a single month from EveryDollar. Returns parsed JSON or None on error."""
    date_str = month_date.strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            EVERYDOLLAR_URL,
            params={"date": date_str},
            headers=EVERYDOLLAR_HEADERS,
            timeout=15,
        )
        if resp.status_code == 401:
            print(f"  ✗ {date_str}: 401 Unauthorized — cookie may have expired")
            return None
        if resp.status_code == 404:
            print(f"  ○ {date_str}: no budget found (month may not exist in EveryDollar)")
            return None
        resp.raise_for_status()
        data = resp.json()
        # EveryDollar may wrap the response; unwrap if necessary
        if isinstance(data, dict) and "budget" in data:
            data = data["budget"]
        return data
    except requests.RequestException as e:
        print(f"  ✗ {date_str}: network error — {e}")
        return None
    except json.JSONDecodeError:
        print(f"  ✗ {date_str}: response was not valid JSON")
        return None


def post_to_vaultic(budget_json: dict) -> dict | None:
    """POST budget JSON to Vaultic's import endpoint. Returns result or None on error."""
    try:
        resp = requests.post(
            f"{VAULTIC_BASE_URL}/api/budget/import/json",
            json=budget_json,
            headers=VAULTIC_HEADERS,
            timeout=30,
        )
        if resp.status_code == 401:
            print("  ✗ Vaultic: 401 — token may have expired")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  ✗ Vaultic import error: {e}")
        return None


def main():
    # ── Validate config ───────────────────────────────────────────────────────
    if "PASTE" in EVERYDOLLAR_COOKIE or "PASTE" in VAULTIC_TOKEN:
        print("ERROR: Fill in EVERYDOLLAR_COOKIE and VAULTIC_TOKEN in the script before running.")
        return

    start = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    end = date.today()
    months = list(month_range(start, end))

    print(f"Importing {len(months)} months ({START_DATE} → {end.strftime('%Y-%m-%d')})")
    print(f"Target: {VAULTIC_BASE_URL}")
    print("─" * 60)

    total_transactions = 0
    total_rules = 0
    skipped = 0
    imported = 0

    for month_date in months:
        label = month_date.strftime("%Y-%m")
        print(f"→ {label} ", end="", flush=True)

        # Fetch from EveryDollar
        budget = fetch_everydollar_month(month_date)
        if budget is None:
            skipped += 1
            continue

        # Check if there's any actual data worth importing
        groups = budget.get("groups", [])
        if not groups:
            print("(empty — skipped)")
            skipped += 1
            continue

        # Post to Vaultic
        result = post_to_vaultic(budget)
        if result is None:
            skipped += 1
            continue

        txn_count = result.get("rows_imported", 0)
        rules_count = result.get("rules_seeded", 0)
        groups_created = result.get("groups_created", 0)
        items_created = result.get("items_created", 0)

        total_transactions += txn_count
        total_rules += rules_count
        imported += 1

        extras = []
        if groups_created:
            extras.append(f"+{groups_created} groups")
        if items_created:
            extras.append(f"+{items_created} items")
        extra_str = f" ({', '.join(extras)})" if extras else ""

        print(f"✓  {txn_count} transactions, {rules_count} new rules{extra_str}")

        # Be polite to EveryDollar's servers
        if month_date != months[-1]:
            time.sleep(SLEEP_SECONDS)

    print("─" * 60)
    print(f"Done: {imported} months imported, {skipped} skipped")
    print(f"Total: {total_transactions} transactions, {total_rules} new auto-rules seeded")


if __name__ == "__main__":
    main()
