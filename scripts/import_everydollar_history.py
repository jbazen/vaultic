"""
import_everydollar_history.py
─────────────────────────────
Fetches your EveryDollar budget month-by-month, saves each month as a local
JSON file under imports/everydollar/, and imports each one into Vaultic.

SETUP
-----
1. Log into everydollar.com in your browser.
2. Open DevTools (F12) → Network tab → filter Fetch/XHR → navigate to any
   budget month to trigger the request.
3. Find the getBudgetByDate request → right-click → Copy → Copy as cURL (bash).
4. From that cURL output:
   a) EVERYDOLLAR_COOKIES  = value between quotes after -b '...'
   b) EVERYDOLLAR_CSRF_TOKEN = value after -H 'x-csrf-token: ...'
5. VAULTIC_TOKEN: open vaulticsage.com → DevTools → Console → run:
       localStorage.getItem("vaultic_token")
6. Set START_DATE (script loops from there to today).
7. Run:
       pip install requests python-dateutil
       python scripts/import_everydollar_history.py

RESUME BEHAVIOR
---------------
Each fetched month is saved to imports/everydollar/YYYY-MM.json before being
imported. If the script is interrupted and re-run, months with existing JSON
files are loaded from disk (no re-fetch from EveryDollar) and re-imported into
Vaultic (safe — INSERT OR IGNORE prevents duplicate history rows).

SESSION NOTES
-------------
The EveryDollar session cookie typically expires after ~30 min of inactivity.
If you hit a 401 mid-run, copy fresh cookies from DevTools and re-run — the
script will skip months already saved to disk and pick up from where it failed.
"""

import json
import time
from datetime import date, datetime
from pathlib import Path

import requests
from dateutil.relativedelta import relativedelta

# ── CONFIG — fill these in before running ────────────────────────────────────

# Value between single quotes after -b in the cURL bash output.
# Starts with "SESSION=..." and is one long semicolon-separated string.
EVERYDOLLAR_COOKIES = "PASTE_COOKIE_STRING_HERE"

# Value after -H 'x-csrf-token: ...' in the cURL bash output.
EVERYDOLLAR_CSRF_TOKEN = "PASTE_CSRF_TOKEN_HERE"

# Your Vaultic JWT — run this in browser console on vaulticsage.com:
#   localStorage.getItem("vaultic_token")
VAULTIC_TOKEN = "PASTE_YOUR_VAULTIC_TOKEN_HERE"

# Earliest month to import. EveryDollar data goes back to Oct 2015.
START_DATE = "2015-10-01"

# Vaultic base URL
VAULTIC_BASE_URL = "https://vaulticsage.com"

# Seconds to sleep between EveryDollar fetches.
# 1.0s is plenty — ~2 min total for 10 years of history.
SLEEP_SECONDS = 1.0

# ── END CONFIG ────────────────────────────────────────────────────────────────

EVERYDOLLAR_URL = "https://www.everydollar.com/app/api/budgets/search/getBudgetByDate"

# Local folder where each month's raw JSON is saved before importing.
IMPORTS_DIR = Path(__file__).parent.parent / "imports" / "everydollar"

EVERYDOLLAR_HEADERS = {
    "Cookie": EVERYDOLLAR_COOKIES,
    "x-csrf-token": EVERYDOLLAR_CSRF_TOKEN,
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://www.everydollar.com/app/budget",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

VAULTIC_HEADERS = {
    "Authorization": f"Bearer {VAULTIC_TOKEN}",
    "Content-Type": "application/json",
}


def month_range(start: date, end: date):
    """Yield the 1st of each month from start through end, oldest first."""
    current = start.replace(day=1)
    while current <= end.replace(day=1):
        yield current
        current += relativedelta(months=1)


def load_or_fetch(month_date: date) -> dict | None:
    """Return cached JSON from disk if available, otherwise fetch from EveryDollar.

    Saves the response to disk before returning so future runs can skip the
    network call entirely.
    """
    label = month_date.strftime("%Y-%m")
    cache_file = IMPORTS_DIR / f"{label}.json"

    # ── Use cached file if it exists ─────────────────────────────────────────
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            print(f"→ {label}  (cached) ", end="", flush=True)
            return data
        except json.JSONDecodeError:
            # Corrupt cache — delete and re-fetch
            cache_file.unlink()

    # ── Fetch from EveryDollar ────────────────────────────────────────────────
    date_str = month_date.strftime("%Y-%m-%d")
    print(f"→ {label}  ", end="", flush=True)
    try:
        resp = requests.get(
            EVERYDOLLAR_URL,
            params={"date": date_str},
            headers=EVERYDOLLAR_HEADERS,
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"✗ network error: {e}")
        return None

    if resp.status_code == 401:
        print("✗ 401 Unauthorized — session cookie has expired. Copy fresh cookies and re-run.")
        return None
    if resp.status_code == 404:
        print("○ no budget found")
        return None
    if not resp.ok:
        print(f"✗ HTTP {resp.status_code}")
        return None

    try:
        data = resp.json()
    except json.JSONDecodeError:
        print("✗ response was not valid JSON")
        return None

    # Unwrap if EveryDollar wraps the budget in a top-level key
    if isinstance(data, dict) and "budget" in data and "groups" not in data:
        data = data["budget"]

    # Save to disk before returning
    cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def import_to_vaultic(budget_json: dict) -> dict | None:
    """POST one month's budget JSON to Vaultic. Returns the result dict or None."""
    try:
        resp = requests.post(
            f"{VAULTIC_BASE_URL}/api/budget/import/json",
            json=budget_json,
            headers=VAULTIC_HEADERS,
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"✗ Vaultic network error: {e}")
        return None

    if resp.status_code == 401:
        print("✗ Vaultic 401 — token expired. Grab a fresh one and re-run.")
        return None
    if not resp.ok:
        try:
            detail = resp.json().get("detail", resp.text[:120])
        except Exception:
            detail = resp.text[:120]
        print(f"✗ Vaultic {resp.status_code}: {detail}")
        return None

    return resp.json()


def main():
    # ── Validate config ───────────────────────────────────────────────────────
    if any("PASTE" in v for v in [EVERYDOLLAR_COOKIES, EVERYDOLLAR_CSRF_TOKEN, VAULTIC_TOKEN]):
        print(
            "ERROR: Fill in EVERYDOLLAR_COOKIES, EVERYDOLLAR_CSRF_TOKEN, and "
            "VAULTIC_TOKEN in the script before running."
        )
        return

    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)

    start = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    end = date.today()
    months = list(month_range(start, end))

    cached_count = sum(1 for m in months if (IMPORTS_DIR / f"{m.strftime('%Y-%m')}.json").exists())

    print(f"EveryDollar history import — {len(months)} months "
          f"({start.strftime('%Y-%m')} → {end.strftime('%Y-%m')})")
    print(f"{cached_count} already cached on disk, "
          f"{len(months) - cached_count} will be fetched from EveryDollar")
    print(f"Importing into: {VAULTIC_BASE_URL}")
    print("─" * 65)

    total_txn = 0
    total_rules = 0
    imported = 0
    skipped = 0
    auth_failed = False

    for i, month_date in enumerate(months):
        is_cached = (IMPORTS_DIR / f"{month_date.strftime('%Y-%m')}.json").exists()

        budget = load_or_fetch(month_date)
        if budget is None:
            skipped += 1
            # If we got a 401 from EveryDollar, abort — no point continuing
            if not is_cached:
                auth_failed = True
                break
            continue

        groups = budget.get("groups", [])
        if not groups:
            print("(empty — skipped)")
            skipped += 1
            continue

        result = import_to_vaultic(budget)
        if result is None:
            skipped += 1
            continue

        txn = result.get("rows_imported", 0)
        rules = result.get("rules_seeded", 0)
        gc = result.get("groups_created", 0)
        ic = result.get("items_created", 0)
        total_txn += txn
        total_rules += rules
        imported += 1

        extras = []
        if gc:
            extras.append(f"+{gc} groups")
        if ic:
            extras.append(f"+{ic} items")
        suffix = f" ({', '.join(extras)})" if extras else ""
        print(f"✓  {txn:3d} transactions  {rules:2d} new rules{suffix}")

        # Only sleep before real network fetches, not cached loads
        if not is_cached and i < len(months) - 1:
            time.sleep(SLEEP_SECONDS)

    print("─" * 65)
    print(f"Done: {imported} months imported, {skipped} skipped")
    print(f"Total: {total_txn} transactions, {total_rules} new auto-rules seeded")
    print(f"JSON files saved to: {IMPORTS_DIR}")

    if auth_failed:
        print("\nTIP: Session expired mid-run. Grab fresh cookies from DevTools and re-run.")
        print("     Already-cached months will load from disk — only failed months will re-fetch.")


if __name__ == "__main__":
    main()
