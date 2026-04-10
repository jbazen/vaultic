"""
Insperity 401K retirement portal scraper.

Scrapes retirement.insperity.com (ASP.NET WebForms) using session cookies
obtained from a browser "Copy as cURL" command. Parses HTML tables with
stdlib html.parser — no external dependencies.

All parsed data is validated against expected page structures. Missing
required data raises ValueError; missing optional data logs warnings.
"""
import logging
import re
import shlex
from html.parser import HTMLParser

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://retirement.insperity.com"
REQUEST_TIMEOUT = 15.0

# Cookies required for authenticated requests
REQUIRED_COOKIES = [
    "ASP.NET_SessionId", "ENCRYPT", "cf_clearance", "ESC5ReSync", "ak_bmsc",
]


# ── cURL parsing ─────────────────────────────────────────────────────────────

def parse_curl_cookies(curl_command: str) -> dict:
    """Extract cookies from a browser 'Copy as cURL' string.

    Accepts the full cURL command or just the cookie header value.
    Strips Windows cmd escaping (^ characters) before parsing.
    Returns dict of cookie name → value.
    """
    # Strip Windows cmd ^ escaping
    cleaned = curl_command.replace("^", "")
    cookies = {}

    # Try to extract the -b / --cookie flag value
    # Match: -b "cookie_string" or --cookie "cookie_string"
    cookie_match = re.search(
        r'(?:-b|--cookie)\s+["\']([^"\']+)["\']', cleaned
    )
    if cookie_match:
        raw = cookie_match.group(1)
    elif "=" in cleaned and "curl" not in cleaned.lower():
        # Bare cookie string pasted directly
        raw = cleaned
    else:
        raise ValueError(
            "Could not find cookies in the cURL command. "
            "Right-click a request in DevTools Network tab → Copy → Copy as cURL."
        )

    # Parse cookie pairs: name=value; name2=value2
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        cookies[name.strip()] = value.strip()

    # Validate required cookies are present
    missing = [c for c in REQUIRED_COOKIES if c not in cookies]
    if missing:
        raise ValueError(
            f"Missing required cookies: {', '.join(missing)}. "
            f"Make sure you copy the full cURL from retirement.insperity.com."
        )

    return cookies


# ── HTML table parser ────────────────────────────────────────────────────────

class _TableExtractor(HTMLParser):
    """Extract text content from HTML table cells into row lists."""

    def __init__(self):
        super().__init__()
        self._in_cell = False
        self._current = ""
        self._cells: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._in_cell = True
            self._current = ""

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False
            self._cells.append(self._current.strip())
        elif tag == "tr":
            if self._cells:
                self.rows.append([c for c in self._cells if c])
            self._cells = []

    def handle_data(self, data):
        if self._in_cell:
            self._current += data


def _parse_tables(html: str) -> list[list[str]]:
    """Parse HTML into a list of table rows (each row = list of cell texts).

    Strips non-breaking spaces (\\xa0) from cells and filters out empty cells
    to normalize across different page renderings.
    """
    parser = _TableExtractor()
    parser.feed(html)
    # Normalize: strip \xa0 / &nbsp; and filter empty cells
    cleaned = []
    for row in parser.rows:
        cells = [c.replace("\xa0", " ").strip() for c in row]
        cells = [c for c in cells if c]
        if cells:
            cleaned.append(cells)
    return cleaned


def _parse_dollar(s: str) -> float | None:
    """Parse '$1,234.56' or '1,234.56' into a float."""
    s = s.replace("$", "").replace(",", "").strip()
    if not s or s == ".00":
        return None
    # Handle negative in angle brackets: '<1.87>' → -1.87
    if s.startswith("<") and s.endswith(">"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _parse_pct(s: str) -> float | None:
    """Parse '79.83%' or '.48%' into a float."""
    s = s.replace("%", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── Page parsers ─────────────────────────────────────────────────────────────

def parse_holdings(html: str) -> dict:
    """Parse balance_investment.aspx → holdings + total balance.

    Returns:
        {
            "holdings": [{"fund": str, "balance": float, "shares": float,
                          "price": float, "ratio_pct": float}, ...],
            "total_balance": float,
        }

    Raises ValueError if no holdings found (page structure changed).
    """
    rows = _parse_tables(html)
    holdings = []
    total = None

    for row in rows:
        # Total row: look for "Total" anywhere in the row + a dollar amount
        if any(c.strip().lower() == "total" for c in row):
            for c in row:
                val = _parse_dollar(c)
                if val and val > 0:
                    total = val
                    break
            continue
        # Holding rows have 5+ cells: fund, balance, shares, price, ratio
        if len(row) >= 5 and "$" in row[1]:
            holdings.append({
                "fund": row[0].strip(),
                "balance": _parse_dollar(row[1]),
                "shares": _parse_dollar(row[2]),
                "price": _parse_dollar(row[3]),
                "ratio_pct": _parse_pct(row[4]),
            })

    if not holdings:
        raise ValueError(
            "holdings: no fund rows found — page structure may have changed"
        )
    if total is None:
        raise ValueError(
            "holdings: total balance row not found — page structure may have changed"
        )

    return {"holdings": holdings, "total_balance": total}


def _normalize_period(label: str) -> str:
    """Normalize a period label like '1 Month' or 'Year To Date' into a snake_case key."""
    s = label.strip().lower()
    if s == "year to date":
        return "ytd"
    return s.replace(" ", "_")


def parse_performance(html: str) -> dict:
    """Parse investment_return.aspx → rate of return periods.

    Insperity serves two layouts:
      - Full history: Cumulative + Annualized each have 3 horizontal cells
        (1 Month / 3 Month / YTD).
      - Limited history: each section is one column wide and may use
        different periods (e.g. Cumulative=1 Month, Annualized=1 Year).

    Returns:
        {
            "cumulative": {<period_key>: float, ...},
            "annualized": {<period_key>: float, ...},
            "as_of": str | None,  # e.g. "3/31/2026"
        }

    Period keys are derived from the labels Insperity uses, normalized to
    snake_case (e.g. "1 Month" -> "1_month", "1 Year" -> "1_year").

    Raises ValueError only if neither section has any period/value pairs.
    """
    rows = _parse_tables(html)

    # Find "Available through" date
    as_of = None
    for row in rows:
        for cell in row:
            m = re.search(r"Available through\s+(\d+/\d+/\d+)", cell)
            if m:
                as_of = m.group(1)

    # Walk rows in document order, tracking the current section header.
    # Within each section, accumulate label cells (no %) and value cells (with %)
    # then zip them positionally to build {period: value} pairs.
    sections = {"cumulative": {"labels": [], "values": []},
                "annualized": {"labels": [], "values": []}}
    current = None
    for row in rows:
        # Detect a new section header — single-cell row containing exactly the word
        joined = " ".join(row).strip().lower()
        if joined == "cumulative":
            current = "cumulative"
            continue
        if joined == "annualized":
            current = "annualized"
            continue
        if current is None:
            continue
        # Classify cells: a row may contain a mix, but in practice Insperity
        # puts label cells and value cells in separate <tr>s.
        for cell in row:
            stripped = cell.strip()
            if not stripped:
                continue
            if "%" in stripped:
                sections[current]["values"].append(stripped)
            elif _looks_like_period(stripped):
                sections[current]["labels"].append(stripped)

    cumulative = _zip_periods(sections["cumulative"])
    annualized = _zip_periods(sections["annualized"])

    if not cumulative and not annualized:
        raise ValueError(
            "performance: no Cumulative or Annualized period data found "
            "— page structure may have changed"
        )

    return {"cumulative": cumulative, "annualized": annualized, "as_of": as_of}


def _looks_like_period(s: str) -> bool:
    """True if s looks like a period label such as '1 Month', 'YTD',
    'Year To Date', or '1 Year'."""
    s_low = s.lower().strip()
    if s_low in ("ytd", "year to date"):
        return True
    return bool(re.fullmatch(r"\d+\s+(month|months|year|years|day|days)", s_low))


def _zip_periods(section: dict) -> dict:
    """Zip parallel labels/values lists into {normalized_key: float}."""
    out = {}
    for label, value in zip(section["labels"], section["values"]):
        key = _normalize_period(label)
        out[key] = _parse_pct(value)
    return out


def parse_contributions(html: str) -> dict:
    """Parse contribution_analysis.aspx → rates and YTD contributions.

    Returns:
        {
            "rates": {"pretax_pct": float, "roth_pct": float},
            "ytd": {"employee": float, "employer": float},
            "last": {"employee_amount": float, "employer_amount": float,
                     "employee_date": str, "employer_date": str},
        }

    Raises ValueError if required contribution data not found.
    """
    rows = _parse_tables(html)

    rates = {}
    ytd = {}
    last = {}

    for row in rows:
        if len(row) >= 2:
            label = row[0].strip().lower()
            if label == "pretax":
                rates["pretax_pct"] = _parse_pct(row[1])
            elif label == "roth":
                rates["roth_pct"] = _parse_pct(row[1])
        if len(row) >= 4:
            label = row[0].strip().lower()
            if label == "employee":
                ytd["employee"] = _parse_dollar(row[1])
                last["employee_amount"] = _parse_dollar(row[2])
                last["employee_date"] = row[3].strip()
            elif label == "employer":
                ytd["employer"] = _parse_dollar(row[1])
                last["employer_amount"] = _parse_dollar(row[2])
                last["employer_date"] = row[3].strip()

    if not ytd:
        raise ValueError(
            "contributions: no Employee/Employer YTD rows found — "
            "page structure may have changed"
        )

    return {"rates": rates, "ytd": ytd, "last": last}


def parse_allocations(html: str) -> dict:
    """Parse contribution_allocations.aspx → target allocation by fund.

    Returns:
        {
            "allocations": [{"fund": str, "target_pct": float,
                             "asset_class": str | None}, ...],
        }

    Raises ValueError if no allocation data found.
    """
    rows = _parse_tables(html)
    allocations = []
    current_class = None

    for row in rows:
        # Asset class header: single cell with a percentage like ['Large Cap', '80.0%']
        if len(row) == 2 and "%" in row[1] and not any(c in row[0].lower() for c in ["total", "details"]):
            # Could be an asset class row or a fund row
            # Asset class rows have broader category names
            pass

        # Fund rows: fund name + percentage
        if len(row) >= 2 and "%" in row[-1]:
            name = row[0].strip()
            pct = _parse_pct(row[-1])
            # Skip headers and totals
            if name.lower() in ("total", "details", "investment%", "asset class%"):
                continue
            # Detect asset class vs fund: asset classes don't have "Fund" or "Index"
            # and their % is a category sum. Use position in list.
            if len(row) == 2 and "%" in row[1]:
                # Could be asset class (e.g. "Large Cap  80.0%") or fund ("Ssga... 80%")
                if any(w in name for w in ["Cap", "Bond", "International", "Stable", "Target"]) \
                        and "." in row[1]:
                    # Asset class header (has decimal in pct)
                    current_class = name
                    continue
            allocations.append({
                "fund": name,
                "target_pct": pct,
                "asset_class": current_class,
            })

    if not allocations:
        raise ValueError(
            "allocations: no fund allocation rows found — "
            "page structure may have changed"
        )

    return {"allocations": allocations}


def parse_activity(html: str) -> dict:
    """Parse investment_activity_summary.aspx → period activity.

    Returns:
        {
            "beginning_balance": float,
            "ending_balance": float,
            "contributions": float,
            "transactions": [{"date": str, "description": str, "code": str,
                              "amount": float, "shares": float,
                              "price": float}, ...],
        }

    Raises ValueError if beginning/ending balance not found.
    """
    rows = _parse_tables(html)

    beginning = None
    ending = None
    contributions = None
    transactions = []

    for row in rows:
        if not row:
            continue
        # Find the label — may be at row[0] or shifted by padding cells
        row_text = " ".join(row)

        if "Beginning Balance" in row_text:
            # Find the first dollar-like value after the label
            for c in row:
                val = _parse_dollar(c)
                if val is not None or c.strip() == "$.00":
                    beginning = val or 0
                    break
        elif "Ending Balance" in row_text:
            for c in row:
                val = _parse_dollar(c)
                if val is not None:
                    ending = val
                    break
        elif row[0].strip() == "Contributions" or (
            len(row) >= 2 and row[0].strip() == "Contributions"
        ):
            for c in row[1:]:
                val = _parse_dollar(c)
                if val is not None:
                    contributions = val
                    break
        else:
            # Look for transaction rows: find a date in any cell
            date_cell = None
            date_idx = -1
            for i, c in enumerate(row):
                if re.match(r"\d{2}/\d{2}/\d{4}", c.strip()):
                    date_cell = c.strip()
                    date_idx = i
                    break
            if date_cell and len(row) >= date_idx + 5:
                rest = row[date_idx + 1:]
                transactions.append({
                    "date": date_cell,
                    "description": rest[0].strip() if len(rest) > 0 else "",
                    "code": rest[1].strip() if len(rest) > 1 else "",
                    "amount": _parse_dollar(rest[2]) if len(rest) > 2 else None,
                    "shares": _parse_dollar(rest[3]) if len(rest) > 3 else None,
                    "price": _parse_dollar(rest[4]) if len(rest) > 4 else None,
                })

    if beginning is None and ending is None:
        raise ValueError(
            "activity: no Beginning/Ending Balance found — "
            "page structure may have changed"
        )

    return {
        "beginning_balance": beginning or 0,
        "ending_balance": ending or 0,
        "contributions": contributions or 0,
        "transactions": transactions,
    }


def parse_prices(html: str) -> list[dict]:
    """Parse investment_prices.aspx → all available fund prices.

    Returns:
        [{"fund": str, "asset_class": str, "date": str, "price": float,
          "prior_date": str, "prior_price": float, "change_pct": float}, ...]

    Raises ValueError if no price rows found.
    """
    rows = _parse_tables(html)
    prices = []

    for row in rows:
        # Price rows have 7 cells: fund, class, date, price, prior_date, prior_price, change%
        if len(row) == 7 and "$" in row[3]:
            prices.append({
                "fund": row[0].strip(),
                "asset_class": row[1].strip(),
                "date": row[2].strip(),
                "price": _parse_dollar(row[3]),
                "prior_date": row[4].strip(),
                "prior_price": _parse_dollar(row[5]),
                "change_pct": _parse_pct(row[6]),
            })

    if not prices:
        raise ValueError(
            "prices: no fund price rows found — page structure may have changed"
        )

    return prices


def parse_summary(html: str) -> dict:
    """Parse index.aspx → account balance and vested balance.

    Returns:
        {"account_balance": float, "vested_balance": float}

    Raises ValueError if balances not found.
    """
    rows = _parse_tables(html)

    account_balance = None
    vested_balance = None

    for i, row in enumerate(rows):
        if len(row) == 1:
            val = _parse_dollar(row[0])
            if val and account_balance is None:
                # First standalone dollar amount after "Account Balance" header
                # Check previous rows for context
                if i > 0 and any("Account Balance" in r[0] for r in rows[max(0, i-3):i] if r):
                    account_balance = val
                elif account_balance is None and val:
                    account_balance = val
            elif val and vested_balance is None:
                vested_balance = val

    if account_balance is None:
        raise ValueError(
            "summary: account balance not found — page structure may have changed"
        )

    return {
        "account_balance": account_balance,
        "vested_balance": vested_balance or account_balance,
    }


# ── HTTP Client ──────────────────────────────────────────────────────────────

class InsperityClient:
    """HTTP client for Insperity 401K retirement portal."""

    def __init__(self, cookies: dict):
        """Initialize with parsed cookies dict (from parse_curl_cookies)."""
        self.cookies = cookies
        self.warnings: list[str] = []

    def _headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    async def _get_page(self, page: str) -> str:
        """Fetch an .aspx page and return raw HTML.

        Raises SessionExpiredError on redirect to login.
        Raises PageChangedError on non-200 responses.
        """
        url = f"{BASE_URL}/{page}"
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, follow_redirects=False
        ) as client:
            resp = await client.get(
                url, cookies=self.cookies, headers=self._headers()
            )

        if resp.status_code in (301, 302, 303, 307):
            location = resp.headers.get("location", "")
            if "login" in location.lower():
                raise SessionExpiredError(
                    "Insperity session expired — redirected to login"
                )
            raise PageChangedError(f"Unexpected redirect to {location}")

        if resp.status_code != 200:
            raise PageChangedError(
                f"{page} returned HTTP {resp.status_code}"
            )

        html = resp.text
        # Sanity check: page should have substantial content
        if len(html) < 1000:
            raise PageChangedError(
                f"{page} returned suspiciously small response ({len(html)} bytes)"
            )

        # Check for login form in response body (soft redirect)
        if '<input type="password"' in html.lower() or "login.aspx" in html[:2000].lower():
            raise SessionExpiredError(
                "Insperity session expired — login form detected in response"
            )

        return html

    # ── Data methods ─────────────────────────────────────────────────────

    async def get_summary(self) -> dict:
        """Account balance and vested balance from index.aspx."""
        html = await self._get_page("index.aspx")
        return parse_summary(html)

    async def get_holdings(self) -> dict:
        """Fund holdings from balance_investment.aspx."""
        html = await self._get_page("balance_investment.aspx")
        return parse_holdings(html)

    async def get_performance(self) -> dict:
        """Rate of return periods from investment_return.aspx."""
        html = await self._get_page("investment_return.aspx")
        return parse_performance(html)

    async def get_contributions(self) -> dict:
        """Contribution rates and YTD from contribution_analysis.aspx."""
        html = await self._get_page("contribution_analysis.aspx")
        return parse_contributions(html)

    async def get_allocations(self) -> dict:
        """Target allocations from contribution_allocations.aspx."""
        html = await self._get_page("contribution_allocations.aspx")
        return parse_allocations(html)

    async def get_activity(self) -> dict:
        """Activity summary from investment_activity_summary.aspx."""
        html = await self._get_page("investment_activity_summary.aspx")
        return parse_activity(html)

    async def get_prices(self) -> list[dict]:
        """All fund prices from investment_prices.aspx."""
        html = await self._get_page("investment_prices.aspx")
        return parse_prices(html)


class SessionExpiredError(Exception):
    """Raised when the Insperity session cookies are invalid or expired."""


class PageChangedError(Exception):
    """Raised when an Insperity page structure has changed unexpectedly."""
