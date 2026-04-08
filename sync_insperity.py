#!/usr/bin/env python3
"""
Local Insperity 401K sync script.

Scrapes retirement.insperity.com from your machine (where the Cloudflare
cookies are valid), parses all 7 pages, and POSTs the parsed data to
the Vaultic server.

Usage:
    python sync_insperity.py <curl_command_or_file>

    # Paste cURL directly (quote it):
    python sync_insperity.py 'curl "https://retirement.insperity.com/..." -b "..."'

    # Or save to a file and pass the path:
    python sync_insperity.py curl.txt

The script will prompt for your Vaultic credentials on first use.
"""
import asyncio
import json
import sys
import os

# Add project root to path so we can import the client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.insperity_client import (
    InsperityClient,
    parse_curl_cookies,
    SessionExpiredError,
    PageChangedError,
)

# ── Configuration ────────────────────────────────────────────────────────────

VAULTIC_URL = os.environ.get("VAULTIC_URL", "https://vaulticsage.com")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".insperity_token")


def get_token() -> str:
    """Get or prompt for Vaultic auth token."""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return f.read().strip()

    print("First-time setup: enter your Vaultic credentials.")
    import getpass
    import httpx

    username = input("Username: ")
    password = getpass.getpass("Password: ")

    resp = httpx.post(
        f"{VAULTIC_URL}/api/auth/login",
        json={"username": username, "password": password},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"Login failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    token = resp.json()["access_token"]
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    print(f"Token saved to {TOKEN_FILE}")
    return token


async def scrape_all(cookies: dict) -> dict:
    """Scrape all 7 Insperity pages and return parsed data."""
    client = InsperityClient(cookies)
    results = {}
    errors = []

    pages = [
        ("summary", client.get_summary),
        ("holdings", client.get_holdings),
        ("performance", client.get_performance),
        ("contributions", client.get_contributions),
        ("allocations", client.get_allocations),
        ("activity", client.get_activity),
        ("prices", client.get_prices),
    ]

    for name, method in pages:
        try:
            data = await method()
            results[name] = data
            print(f"  OK  {name}")
        except SessionExpiredError as exc:
            print(f"  FAIL {name}: {exc}")
            errors.append(f"{name}: {exc}")
        except PageChangedError as exc:
            print(f"  FAIL {name}: {exc}")
            errors.append(f"{name}: {exc}")
        except ValueError as exc:
            print(f"  FAIL {name}: {exc}")
            errors.append(f"{name}: {exc}")
        except Exception as exc:
            print(f"  FAIL {name}: {exc}")
            errors.append(f"{name}: {exc}")

    return results, errors


def post_to_server(data: dict, token: str) -> dict:
    """POST parsed data to Vaultic sync-local endpoint."""
    import httpx

    resp = httpx.post(
        f"{VAULTIC_URL}/api/insperity/sync-local",
        json=data,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )

    if resp.status_code == 401:
        # Token expired — delete and retry
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        print("Token expired. Run again to re-authenticate.")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Server error: {resp.status_code} {resp.text}")
        sys.exit(1)

    return resp.json()


def main():
    if len(sys.argv) < 2:
        print("Usage: python sync_insperity.py <curl_command_or_file>")
        print('       python sync_insperity.py \'curl "https://..." -b "..."\'')
        sys.exit(1)

    # Read cURL from argument or file
    arg = sys.argv[1]
    if os.path.isfile(arg):
        with open(arg) as f:
            curl_input = f.read()
    else:
        curl_input = " ".join(sys.argv[1:])

    # Parse cookies
    print("Parsing cookies...")
    try:
        cookies = parse_curl_cookies(curl_input)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    print(f"  Found {len(cookies)} cookies")

    # Scrape pages
    print("Scraping Insperity pages...")
    data, errors = asyncio.run(scrape_all(cookies))

    if not data:
        print("All pages failed. Check your session.")
        sys.exit(1)

    # Get auth token
    token = get_token()

    # Post to server
    print("Posting to Vaultic...")
    result = post_to_server(data, token)

    print(f"\nSync {result['status']}!")
    if result.get("total_balance"):
        print(f"  Balance: ${result['total_balance']:,.2f}")
    if result.get("holdings"):
        print(f"  Holdings: {result['holdings']} funds")
    if result.get("duration_ms"):
        print(f"  Server time: {result['duration_ms']}ms")
    if result.get("errors"):
        print(f"  Errors: {result['errors']}")


if __name__ == "__main__":
    main()
