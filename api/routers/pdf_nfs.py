"""
Deterministic parser for Commonwealth Financial Network / NFS (National Financial Services)
monthly account statements. Eliminates Claude Sonnet API costs for Parker Financial PDFs.

Detection: both "COMMONWEALTH FINANCIAL NETWORK" and "National Financial Services" appear
in the full text.

Usage:
    pages = [page.extract_text() for page in pdf.pages]
    full_text = "\n\n".join(p for p in pages if p)
    if is_nfs_statement(full_text):
        result = parse_nfs_statement(pages)
"""
import re
import logging
from datetime import datetime

logger = logging.getLogger("vaultic.pdf_nfs")

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

NFS_MARKERS = ("COMMONWEALTH FINANCIAL NETWORK", "National Financial Services")


def is_nfs_statement(full_text: str) -> bool:
    """Return True if both NFS/Commonwealth markers are present in the PDF text."""
    if not full_text:
        return False
    return all(marker in full_text for marker in NFS_MARKERS)


# ---------------------------------------------------------------------------
# Masked account number detection
# ---------------------------------------------------------------------------

# A masked account number contains a run of 4+ X's (e.g. "XXXX5429", "B37-XXXX5429").
# These must never be stored — they break correlation because the full number
# (e.g. "B37705429") is the canonical correlation key across all tables.
_MASKED_PATTERN = re.compile(r"X{4,}", re.IGNORECASE)


def _is_masked_account_number(value) -> bool:
    """Return True if *value* contains a run of 4+ X's (masking)."""
    if not value:
        return False
    return bool(_MASKED_PATTERN.search(str(value)))


# ---------------------------------------------------------------------------
# Dollar-amount helpers
# ---------------------------------------------------------------------------

# Matches an optional leading minus, optional paren-negative, dollar sign, digits+commas, dot, cents.
# e.g.  $1,500.61  ($8.69)  ($1,277.50)
_DOLLAR_PATTERN = re.compile(r"\(?\$?([\d,]+\.\d{2})\)?")
_DOLLAR_SIGNED = re.compile(r"(\()\$?([\d,]+\.\d{2})\)|\$?([\d,]+\.\d{2})")


def _parse_dollar(s: str):
    """
    Return the first dollar amount found in *s* as a float.
    Parenthesized amounts, e.g. ($915.20), are returned as negative.
    Returns None if no amount is found.
    """
    for m in _DOLLAR_SIGNED.finditer(s):
        if m.group(1):  # opening paren present → negative
            return -float(m.group(2).replace(",", ""))
        raw = m.group(3)
        if raw is not None:
            return float(raw.replace(",", ""))
    return None


def _parse_all_dollars(s: str) -> list:
    """
    Return ALL dollar amounts in *s* as floats (preserving sign from parentheses).
    """
    results = []
    for m in _DOLLAR_SIGNED.finditer(s):
        if m.group(1):
            results.append(-float(m.group(2).replace(",", "")))
        else:
            raw = m.group(3)
            if raw is not None:
                results.append(float(raw.replace(",", "")))
    return results


# ---------------------------------------------------------------------------
# Month name → number
# ---------------------------------------------------------------------------

_MONTHS = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4,
    "MAY": 5, "JUNE": 6, "JULY": 7, "AUGUST": 8,
    "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}


def _parse_date_text(text: str):
    """
    Parse 'DECEMBER 1, 2025' or 'DECEMBER 31, 2025' into 'YYYY-MM-DD'.
    Returns None on failure.
    """
    m = re.match(r"(\w+)\s+(\d{1,2}),\s*(\d{4})", text.strip(), re.IGNORECASE)
    if not m:
        return None
    month_name = m.group(1).upper()
    month = _MONTHS.get(month_name)
    if not month:
        return None
    day = int(m.group(2))
    year = int(m.group(3))
    return f"{year:04d}-{month:02d}-{day:02d}"


# ---------------------------------------------------------------------------
# Header parsing (page 1)
# ---------------------------------------------------------------------------

def _parse_header(full_text: str) -> dict:
    """
    Extract account name, account number, period dates, beginning value, and
    total portfolio value from the statement's first page text.
    """
    result = {
        "account_name": None,
        "account_number": None,
        "period_start": None,
        "period_end": None,
        "beginning_value": None,
        "total_value": None,
    }

    lines = full_text.split("\n")

    for line in lines:
        line_s = line.strip()

        # Account name: "HEATHER A BAZEN - Premiere Select Roth IRA"
        # Appears after "STATEMENT FOR THE PERIOD" section header
        if result["account_name"] is None:
            m = re.match(r"^([A-Z][A-Z &]+?)\s*-\s*(.+)$", line_s)
            if m and ("Premiere" in line_s or "Joint" in line_s or "Rollover" in line_s):
                result["account_name"] = line_s

        # Account Number: B37-705429
        # Skip masked variants (e.g. "XXXX5429") — keep scanning for the full number.
        # Store full numbers only; correlation by account_number breaks if masked.
        if result["account_number"] is None:
            m = re.search(r"Account Number:\s*([A-Z0-9\-]+)", line_s)
            if m and not _is_masked_account_number(m.group(1)):
                result["account_number"] = m.group(1).strip()

        # Period: STATEMENT FOR THE PERIOD DECEMBER 1, 2025 TO DECEMBER 31, 2025
        if result["period_start"] is None:
            m = re.search(
                r"STATEMENT FOR THE PERIOD\s+(.+?)\s+TO\s+(.+?)(?:\s*$)",
                line_s, re.IGNORECASE
            )
            if m:
                result["period_start"] = _parse_date_text(m.group(1))
                result["period_end"] = _parse_date_text(m.group(2))

        # Beginning value
        if result["beginning_value"] is None:
            m = re.search(r"BEGINNING VALUE OF YOUR PORTFOLIO\s+\$([\d,]+\.\d{2})", line_s)
            if m:
                result["beginning_value"] = float(m.group(1).replace(",", ""))

        # Total value
        if result["total_value"] is None:
            m = re.search(r"TOTAL VALUE OF YOUR PORTFOLIO\s+\$([\d,]+\.\d{2})", line_s)
            if m:
                result["total_value"] = float(m.group(1).replace(",", ""))

    return result


# ---------------------------------------------------------------------------
# Overview parsing (page 2) — Change in Account Value block
# ---------------------------------------------------------------------------

def _parse_overview(page2_text: str) -> dict:
    """
    Extract the Change in Account Value table from the Account Overview page.
    The table has two columns: Current Period and Year-to-Date.
    Dollar amounts may be followed by allocation percentage text on the same line
    (pdfplumber merges adjacent columns), so we use _parse_all_dollars and take
    only the first two numeric values found per row.
    """
    ov = {
        "beginning_balance": None,
        "ending_balance": None,
        "additions_withdrawals": None,
        "misc_corporate_actions": None,
        "period_income": None,
        "period_fees": None,
        "net_change": None,
        "ytd_beginning_balance": None,
        "ytd_additions_withdrawals": None,
        "ytd_income": None,
        "ytd_fees": None,
        "ytd_change_in_value": None,
        # The following are not present on page 2 but kept for schema completeness
        "ytd_contributions": None,
        "ytd_distributions": None,
        "total_cost_basis": None,
        "total_estimated_annual_income": None,
        "total_gain_loss_dollars": None,
    }

    lines = page2_text.split("\n")

    for line in lines:
        s = line.strip()
        amounts = _parse_all_dollars(s)

        def _first(amounts, idx=0):
            return amounts[idx] if len(amounts) > idx else None

        def _second(amounts, idx=1):
            return amounts[idx] if len(amounts) > idx else None

        if re.search(r"^BEGINNING VALUE\b", s, re.IGNORECASE):
            if amounts:  # skip header-only line with no amounts
                ov["beginning_balance"] = _first(amounts)
                ov["ytd_beginning_balance"] = _second(amounts)

        elif re.search(r"^Additions and Withdrawals", s, re.IGNORECASE):
            if amounts:
                ov["additions_withdrawals"] = _first(amounts)
                ov["ytd_additions_withdrawals"] = _second(amounts)

        elif re.search(r"^Misc\.?\s*&?\s*Corporate Actions", s, re.IGNORECASE):
            if amounts:
                ov["misc_corporate_actions"] = _first(amounts)

        elif re.search(r"^Income\b", s, re.IGNORECASE):
            if amounts:  # skip "INCOME Current Period Year-to-Date" header line
                ov["period_income"] = _first(amounts)
                ov["ytd_income"] = _second(amounts)

        elif re.search(r"^Taxes,Fees and Expenses", s, re.IGNORECASE):
            if amounts:
                ov["period_fees"] = _first(amounts)
                ov["ytd_fees"] = _second(amounts)

        elif re.search(r"^Change in Value", s, re.IGNORECASE):
            if amounts:
                ov["net_change"] = _first(amounts)
                ov["ytd_change_in_value"] = _second(amounts)

        elif re.search(r"^ENDING VALUE", s, re.IGNORECASE):
            if amounts:
                ov["ending_balance"] = _first(amounts)

    return ov


# ---------------------------------------------------------------------------
# Holdings parsing (pages 4+)
# ---------------------------------------------------------------------------

# Holding line: NAME TICKER QUANTITY $PRICE $VALUE [optional extra $amounts]
# NAME: 2+ ALL-CAPS words, may include digits, ampersand, apostrophe, period, comma, hyphen
# TICKER: 2-8 uppercase alphanumeric, starts with a letter
_HOLDING_RE = re.compile(
    r"^([A-Z][A-Z0-9 &',./\-]+?)\s+"   # fund name (greedy but lazy stop before ticker)
    r"([A-Z][A-Z0-9]{1,7})\s+"           # ticker
    r"([\d,]+\.?\d*)\s+"                 # quantity (may have commas, optional decimals)
    r"\$([\d,]+\.\d{2})\s+"             # price
    r"\$([\d,]+\.\d{2})"                # market value
    r"(.*)"                              # remainder (extra dollar amounts)
)

# Section headers we split on
_SECTION_CASH = re.compile(
    r"CASH AND CASH EQUIVALENTS",
    re.IGNORECASE
)
_SECTION_MF = re.compile(
    r"HOLDINGS\s*>\s*MUTUAL FUNDS(?:\s+continued)?",
    re.IGNORECASE
)
_SECTION_ETP = re.compile(
    r"HOLDINGS\s*>\s*EXCHANGE TRADED PRODUCTS(?:\s+continued)?",
    re.IGNORECASE
)

# Any section header (used for splitting)
_SECTION_SPLIT = re.compile(
    r"(CASH AND CASH EQUIVALENTS|HOLDINGS\s*>\s*MUTUAL FUNDS(?:\s+continued)?|"
    r"HOLDINGS\s*>\s*EXCHANGE TRADED PRODUCTS(?:\s+continued)?)",
    re.IGNORECASE
)

# Stop accumulating holdings when we hit "Activity" section
_ACTIVITY_STOP = re.compile(r"^Activity\s*$", re.MULTILINE)

# Lines to look ahead for metadata
_AVG_UNIT_COST_RE = re.compile(r"Average Unit Cost\s+\$([\d,]+\.\d{2})")
_EST_YIELD_RE = re.compile(r"Estimated Yield\s+([\d.]+)%")

# Total lines that should NOT be parsed as holdings
_TOTAL_LINE_RE = re.compile(r"^Total\s+", re.IGNORECASE)


def _title_case(name: str) -> str:
    """Convert ALL-CAPS fund name to Title Case for readability."""
    # Words to keep lowercase unless first word
    _lower_words = {"and", "or", "of", "the", "for", "in", "a", "an", "to", "at", "by", "&"}
    words = name.strip().split()
    result = []
    for i, w in enumerate(words):
        if i == 0 or w.lower() not in _lower_words:
            result.append(w.capitalize())
        else:
            result.append(w.lower())
    return " ".join(result)


def _classify_asset_class(section_type: str) -> str:
    """Map section type to asset_class string."""
    if section_type == "cash":
        return "cash"
    # Both mutual funds and ETPs are equities in these statements
    return "equities"


def _parse_section(section_text: str, section_type: str) -> list:
    """
    Parse one holdings section (cash / mutual_funds / etp) into a list of holding dicts.
    Stops at "Activity" line.
    """
    # Stop at Activity
    stop_match = _ACTIVITY_STOP.search(section_text)
    if stop_match:
        section_text = section_text[: stop_match.start()]

    lines = section_text.split("\n")
    holdings = []
    asset_class = _classify_asset_class(section_type)

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        # Skip blank lines, total lines, and other non-holding lines
        if not line or _TOTAL_LINE_RE.match(line):
            continue

        m = _HOLDING_RE.match(line)
        if not m:
            continue

        name = m.group(1).strip()
        ticker = m.group(2).strip()
        quantity_str = m.group(3).replace(",", "")
        price_str = m.group(4).replace(",", "")
        value_str = m.group(5).replace(",", "")
        remainder = m.group(6)

        # Parse the remainder dollars: 0=cash, 2=[cost,gain], 3=[income,cost,gain]
        extra_amounts = _parse_all_dollars(remainder)

        annual_income = None
        cost = None
        gain_loss = None

        if len(extra_amounts) == 3:
            annual_income, cost, gain_loss = extra_amounts[0], extra_amounts[1], extra_amounts[2]
        elif len(extra_amounts) == 2:
            cost, gain_loss = extra_amounts[0], extra_amounts[1]
        # 0 or 1 → cash sweep; keep all None

        # Look ahead up to 6 lines for metadata, stopping if another holding line starts
        avg_unit_cost = None
        est_yield = None
        for j in range(i, min(i + 6, len(lines))):
            ahead = lines[j].strip()
            # Stop if we hit the next holding (avoid picking up next fund's yield/cost)
            if _HOLDING_RE.match(ahead):
                break
            ym = _EST_YIELD_RE.search(ahead)
            if ym:
                est_yield = float(ym.group(1))
            am = _AVG_UNIT_COST_RE.search(ahead)
            if am:
                avg_unit_cost = float(am.group(1).replace(",", ""))

        shares = float(quantity_str)
        price = float(price_str)
        value = float(value_str)

        # gain_loss_pct: derived if we have both cost and gain
        gain_loss_pct = None
        if cost and gain_loss is not None and cost != 0:
            gain_loss_pct = round(gain_loss / cost * 100, 2)

        holding = {
            "name": _title_case(name),
            "ticker": ticker,
            "asset_class": asset_class,
            "shares": shares,
            "price": price,
            "value": value,
            "cost": cost,
            "avg_unit_cost": avg_unit_cost,
            "gain_loss_dollars": gain_loss,
            "gain_loss_pct": gain_loss_pct,
            "pct_assets": None,
            "estimated_annual_income": annual_income,
            "estimated_yield_pct": est_yield,
            "notes": None,
        }
        holdings.append(holding)

    return holdings


def _parse_holdings(all_pages_text: str) -> list:
    """
    Split the full multi-page text into section blocks and parse each one.
    'continued' sections on subsequent pages are handled automatically because
    re.split keeps each occurrence as a separate segment.
    """
    # Find the Holdings page start to avoid parsing header/overview
    holdings_start = re.search(r"\nHoldings\s*\n", all_pages_text, re.IGNORECASE)
    if holdings_start:
        search_text = all_pages_text[holdings_start.start():]
    else:
        search_text = all_pages_text

    # Split on section headers (captures the delimiter so we know what section follows)
    parts = _SECTION_SPLIT.split(search_text)

    holdings = []
    i = 0
    while i < len(parts):
        part = parts[i]
        # Check if this part is a section header
        if _SECTION_SPLIT.fullmatch(part.strip()):
            header = part.strip().upper()
            # The content follows immediately after the header
            if i + 1 < len(parts):
                content = parts[i + 1]
                i += 2
            else:
                i += 1
                continue

            if "CASH" in header:
                section_type = "cash"
            elif "EXCHANGE TRADED" in header:
                section_type = "etp"
            else:
                section_type = "mutual_funds"

            holdings.extend(_parse_section(content, section_type))
        else:
            i += 1

    return holdings


# ---------------------------------------------------------------------------
# Holdings totals from TOTAL PORTFOLIO VALUE line
# ---------------------------------------------------------------------------

def _parse_portfolio_totals(all_pages_text: str) -> dict:
    """
    Extract total cost basis, total estimated annual income, and total gain/loss
    from the 'TOTAL PORTFOLIO VALUE' summary line near the end of holdings.
    """
    result = {
        "total_cost_basis": None,
        "total_estimated_annual_income": None,
        "total_gain_loss_dollars": None,
    }
    m = re.search(
        r"TOTAL PORTFOLIO VALUE\s+\$([\d,]+\.\d{2})"
        r"\s+\$([\d,]+\.\d{2})"
        r"\s+\$([\d,]+\.\d{2})"
        r"\s+\$?([\d,]+\.\d{2}|\([\d,]+\.\d{2}\))",
        all_pages_text
    )
    if m:
        result["total_estimated_annual_income"] = float(m.group(2).replace(",", ""))
        result["total_cost_basis"] = float(m.group(3).replace(",", ""))
        gain_raw = m.group(4)
        result["total_gain_loss_dollars"] = _parse_dollar(gain_raw)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_nfs_statement(pages: list) -> list:
    """
    Parse a Commonwealth Financial Network / NFS monthly statement.

    Args:
        pages: list of strings, one per PDF page (as returned by pdfplumber
               page.extract_text()), including None for image-only pages.

    Returns:
        list with exactly one dict matching the schema expected by pdf.py:save_parsed.
    """
    # Build clean page texts
    text_pages = [p if p else "" for p in pages]
    full_text = "\n\n".join(text_pages)

    # --- Header ---
    header = _parse_header(full_text)
    account_name = header["account_name"] or "Unknown Account"
    account_number = header["account_number"] or ""
    period_start = header["period_start"]
    period_end = header["period_end"]
    beginning_value = header["beginning_value"]
    total_value = header["total_value"] or 0.0

    # Derive account_holder and account_type from name string
    # Format: "HEATHER A BAZEN - Premiere Select Roth IRA"
    account_holder = ""
    account_type = ""
    name_m = re.match(r"^(.+?)\s*-\s*(.+)$", account_name)
    if name_m:
        account_holder = name_m.group(1).strip().title()
        account_type = name_m.group(2).strip()

    # --- Overview (page 2) ---
    page2_text = text_pages[1] if len(text_pages) > 1 else ""
    ov = _parse_overview(page2_text)

    # --- Holdings ---
    holdings = _parse_holdings(full_text)

    # --- Portfolio totals ---
    portfolio_totals = _parse_portfolio_totals(full_text)

    # --- Build activity_summary ---
    activity_summary = {
        "account_holder": account_holder,
        "account_number": account_number,
        "institution": "Parker Financial / NFS",
        "account_type": account_type,
        "period_start": period_start,
        "period_end": period_end,
        "beginning_balance": ov.get("beginning_balance") or beginning_value,
        "ending_balance": ov.get("ending_balance") or total_value,
        "additions_withdrawals": ov.get("additions_withdrawals"),
        "misc_corporate_actions": ov.get("misc_corporate_actions"),
        "period_income": ov.get("period_income"),
        "period_fees": ov.get("period_fees"),
        "net_change": ov.get("net_change"),
        "ytd_beginning_balance": ov.get("ytd_beginning_balance"),
        "ytd_additions_withdrawals": ov.get("ytd_additions_withdrawals"),
        "ytd_income": ov.get("ytd_income"),
        "ytd_fees": ov.get("ytd_fees"),
        "ytd_change_in_value": ov.get("ytd_change_in_value"),
        "ytd_contributions": None,
        "ytd_distributions": None,
        "total_cost_basis": portfolio_totals.get("total_cost_basis"),
        "total_estimated_annual_income": portfolio_totals.get("total_estimated_annual_income"),
        "total_gain_loss_dollars": portfolio_totals.get("total_gain_loss_dollars"),
    }

    # --- Notes ---
    period_str = ""
    if period_end:
        period_str = f" as of {period_end}"
    notes = f"Parker Financial / NFS | {account_type} | {account_number}{period_str}"

    return [{
        "name": account_name,
        "category": "invested",
        "value": total_value,
        "notes": notes,
        "activity_summary": activity_summary,
        "holdings": holdings,
        "activity": [],
    }]
