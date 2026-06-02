#!/usr/bin/env python3
"""
Local Voya 401K sync script.

Reads my.voya.com from your machine (where the Cloudflare + IBM ISAM session
cookies are valid — they're IP-bound, so the server can't reuse them), parses
your account balances, and POSTs them to the Vaultic server. Same pattern as
sync_insperity.py.

Usage:
    python sync_voya.py <curl_command_or_file>

    # On my.voya.com: DevTools (F12) -> Network -> Fetch/XHR ->
    #   right-click any row -> "Copy all as cURL", then:
    python sync_voya.py 'curl "https://my.voya.com/..." -b "..."'
    #   or save it to a file:
    python sync_voya.py voya_curl.txt

If Voya's JSON shape differs from what the parser expects, the raw response is
saved to .voya_raw.json so the parser can be hardened — send that file over.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.voya_client import (
    VoyaClient,
    parse_curl,
    SessionExpiredError,
    EndpointChangedError,
    SchemaUnknownError,
)

VAULTIC_URL = os.environ.get("VAULTIC_URL", "https://vaulticsage.com")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".voya_token")
REFRESH_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".voya_refresh_token")
RAW_DUMP_FILE = os.path.join(os.path.dirname(__file__), ".voya_raw.json")


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

    payload = resp.json()
    if payload.get("requires_2fa"):
        print("Account has 2FA enabled — grab the JWT from browser localStorage "
              "('vaultic_token') and write it to .voya_token, or grab the refresh "
              "token ('vaultic_refresh_token') and write it to .voya_refresh_token "
              "(the script will silently refresh).")
        sys.exit(1)

    token = payload["token"]
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    print(f"Token saved to {TOKEN_FILE}")
    return token


def try_refresh() -> str | None:
    """Mint a new access token from the cached refresh token (rotates it)."""
    if not os.path.exists(REFRESH_TOKEN_FILE):
        return None
    import httpx

    with open(REFRESH_TOKEN_FILE) as f:
        refresh = f.read().strip()
    if not refresh:
        return None

    resp = httpx.post(
        f"{VAULTIC_URL}/api/auth/refresh",
        json={"refresh_token": refresh},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"  Refresh failed ({resp.status_code}); cached refresh token "
              "may have been rotated by your browser session.")
        return None

    payload = resp.json()
    with open(TOKEN_FILE, "w") as f:
        f.write(payload["token"])
    with open(REFRESH_TOKEN_FILE, "w") as f:
        f.write(payload["refresh_token"])
    print("  Refreshed access token via cached refresh token.")
    return payload["token"]


def post_to_server(data: dict, token: str) -> dict:
    """POST parsed data to Vaultic; retry once via refresh token on 401."""
    import httpx

    def _post(bearer: str):
        return httpx.post(
            f"{VAULTIC_URL}/api/voya/sync-local",
            json=data,
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=30,
        )

    resp = _post(token)
    if resp.status_code == 401:
        print("Access token expired; attempting refresh...")
        new_token = try_refresh()
        if new_token:
            resp = _post(new_token)
        else:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            print("Token expired and no usable refresh token. Run again to "
                  "re-authenticate.")
            sys.exit(1)

    if resp.status_code != 200:
        print(f"Server error: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()


def main():
    if len(sys.argv) < 2:
        print("Usage: python sync_voya.py <curl_command_or_file>")
        sys.exit(1)

    arg = sys.argv[1]
    if os.path.isfile(arg):
        with open(arg) as f:
            curl_input = f.read()
    else:
        curl_input = " ".join(sys.argv[1:])

    print("Parsing cURL...")
    try:
        cookies, session_token = parse_curl(curl_input)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    print(f"  Found {len(cookies)} cookies"
          + (f", session token {session_token[:8]}..." if session_token else ", no session token"))

    print("Fetching Voya accounts...")
    client = VoyaClient(cookies, session_token)
    try:
        data = client.get_accounts()
    except (SessionExpiredError, EndpointChangedError) as exc:
        print(f"  FAIL: {exc}")
        sys.exit(1)
    except SchemaUnknownError as exc:
        with open(RAW_DUMP_FILE, "w") as f:
            json.dump(exc.payload, f, indent=2, default=str)
        print(f"  FAIL: {exc}")
        print(f"  Raw Voya response saved to {RAW_DUMP_FILE} — send that over so "
              "the parser can be hardened.")
        sys.exit(1)

    print(f"  OK  {len(data['accounts'])} account(s), "
          f"total ${data['total_balance']:,.2f}")
    for a in data["accounts"]:
        print(f"      {a['name']}: ${a['balance']:,.2f}")

    token = get_token()
    print("Posting to Vaultic...")
    result = post_to_server(data, token)

    print(f"\nSync {result['status']}!")
    print(f"  Balance: ${result['total_balance']:,.2f}")
    print(f"  Accounts: {result['accounts']}")
    if result.get("duration_ms"):
        print(f"  Server time: {result['duration_ms']}ms")


if __name__ == "__main__":
    main()
