"""Google Sheets read-only integration for Fund Financials.

Fetches the wife's Fund Financials Google Sheet (publicly shared, viewer-only)
via CSV export and parses the multi-level header structure into structured data.

Actual CSV structure (as observed via curl):
  Row 0:  blank, "TO START", blank, blank, blank, "Oct-15", blank, blank, blank, "Nov-15", ...
          → Month labels appear at the first sub-column of each month block (Saved col).
            Blank cells within a month block are merged-cell expansions.

  Row 1:  blank, "HEATHER", "JASON", "TOTAL", blank, "Saved", "Spent", "Balance", blank, "Saved", ...
          → Cols 1-3 are overall running balances per person (HEATHER/JASON/TOTAL).
            Each month block has: Saved, Spent, [Transfers,] Balance sub-columns.
            Earlier months: 3 sub-cols (Saved, Spent, Balance).
            Later months:   4 sub-cols (Saved, Spent, Transfers, Balance).
            Blank col separates month blocks.

  Data rows:  col 0 = fund name, cols 1-3 = HEATHER/JASON/TOTAL overall balance,
              then per-month values aligned with sub-column headers.

  Section headers (e.g., "CAPITAL ONE 360:") have data in col 0 only — all other
  cols are blank. These are skipped.

Returns the last N months of Balance data for each fund category, plus overall
per-person balances (HEATHER, JASON, TOTAL) for the current state column.
"""
import csv
import io
import re
from fastapi import APIRouter, Depends, HTTPException
import requests
from api.dependencies import get_current_user

router = APIRouter(prefix="/api/sheet", tags=["sheet"])

SHEET_ID  = "11-e3Jodhs8YPGZ1CvcVjou8D2GP2iXs7rWLAljf2kDw"
SHEET_GID = "1053339711"
CSV_URL   = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/export?format=csv&gid={SHEET_GID}"
)

# Matches month labels exported by Google Sheets: "Oct-15", "Jan-26", etc.
MONTH_RE = re.compile(r"^[A-Za-z]{3}-\d{2}$")

# Default months to return if caller doesn't specify
DEFAULT_MONTHS = 6


def _parse_dollar(val: str) -> float | None:
    """Parse a dollar string like '$1,234.56' or '(500.00)' into a float.

    Returns None for blank/dash/em-dash values that represent missing data.
    Parentheses denote negative values (accounting convention).
    """
    if not val or val.strip() in ("", "-", "—"):
        return None
    v = val.strip().replace("$", "").replace(",", "").replace(" ", "")
    negative = v.startswith("(") and v.endswith(")")
    v = v.strip("()")
    try:
        f = float(v)
        return -f if negative else f
    except ValueError:
        return None


def _month_sort_key(label: str) -> int:
    """Convert 'Mar-26' → sortable integer 202603 for chronological ordering.

    Two-digit year is assumed to be 2000+YY (valid through 2099).
    Returns 0 for unparseable labels (sorts to the beginning).
    """
    abbr_to_num = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    try:
        parts = label.split("-")
        mon = abbr_to_num.get(parts[0], 0)
        yr  = int(parts[1]) + 2000
        return yr * 100 + mon
    except Exception:
        return 0


@router.get("/fund-financials")
async def get_sheet_data(
    limit: int = DEFAULT_MONTHS,
    _user: str = Depends(get_current_user),
):
    """Fetch and parse the Fund Financials Google Sheet.

    Returns:
      months:     Ordered list of recent month labels (oldest first, most recent last).
      categories: List of fund rows, each with:
                    name    – fund category name
                    heather – HEATHER's overall running balance (or null)
                    jason   – JASON's overall running balance (or null)
                    total   – combined TOTAL running balance (or null)
                    monthly – dict mapping month label → end-of-month balance (or null)
    """
    try:
        resp = requests.get(CSV_URL, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch sheet: {e}")

    rows = list(csv.reader(io.StringIO(resp.text)))
    if len(rows) < 3:
        raise HTTPException(status_code=502, detail="Sheet data too short to parse")

    # ── Step 1: Locate the month header row ───────────────────────────────────
    # Scan until we find a row that contains at least 3 month-pattern cells.
    month_row_idx = None
    for i, row in enumerate(rows):
        if sum(1 for cell in row if MONTH_RE.match(cell.strip())) >= 3:
            month_row_idx = i
            break

    if month_row_idx is None:
        raise HTTPException(
            status_code=502,
            detail="Could not find month header row in sheet",
        )

    month_row = rows[month_row_idx]
    # The row immediately below the month header contains sub-column labels
    # (HEATHER, JASON, TOTAL, Saved, Spent, [Transfers,] Balance, …)
    sub_row = rows[month_row_idx + 1] if month_row_idx + 1 < len(rows) else []

    # ── Step 2: Locate overall-balance columns for HEATHER, JASON, TOTAL ─────
    # These appear in the sub-header row at columns 1–4 (before any month block).
    PERSON_LABELS = {"HEATHER", "JASON", "TOTAL"}
    person_cols: dict[str, int] = {}  # label → column index
    for col_i in range(1, min(6, len(sub_row))):
        label = sub_row[col_i].strip().upper()
        if label in PERSON_LABELS:
            person_cols[label] = col_i

    # ── Step 3: Find each month's column boundaries and Balance sub-column ────
    # Month label in row 0 appears at the same column as "Saved" in sub_row.
    # The month block extends until the next month label (or end of row).
    # The Balance column is the last sub-column labeled "balance" within the block.
    month_positions: list[tuple[str, int]] = []  # (month_label, start_col)
    for col_i, cell in enumerate(month_row):
        if MONTH_RE.match(cell.strip()):
            month_positions.append((cell.strip(), col_i))

    # Map month label → column index of its Balance sub-column
    month_balance_col: dict[str, int] = {}
    for idx, (month, start_col) in enumerate(month_positions):
        end_col = (
            month_positions[idx + 1][1]
            if idx + 1 < len(month_positions)
            else len(month_row)
        )
        # Scan backwards within [start_col, end_col) to find "Balance"
        for col_i in range(end_col - 1, start_col - 1, -1):
            if col_i < len(sub_row) and sub_row[col_i].strip().lower() == "balance":
                month_balance_col[month] = col_i
                break  # Take the last (rightmost) "Balance" in the block

    # All months for which we found a Balance column, in chronological order
    all_months = sorted(month_balance_col.keys(), key=_month_sort_key)

    # ── Step 4: Parse data rows ───────────────────────────────────────────────
    data_start = month_row_idx + 2  # Skip month header + sub-header rows
    categories: list[dict] = []

    for row in rows[data_start:]:
        if not row:
            continue

        name = row[0].strip()
        if not name:
            continue  # Completely blank row

        # Section headers (e.g. "CAPITAL ONE 360:") have data in col 0 only.
        # Detect them by checking whether cols 1-3 are all blank.
        has_values = any(
            col_i < len(row) and row[col_i].strip()
            for col_i in range(1, 4)
        )
        if not has_values:
            continue  # Skip section headers

        # Overall per-person running balances (cols 1-3 of the sub-header)
        def _get(label: str) -> float | None:
            col_i = person_cols.get(label)
            if col_i is None or col_i >= len(row):
                return None
            return _parse_dollar(row[col_i])

        heather = _get("HEATHER")
        jason   = _get("JASON")
        total   = _get("TOTAL")

        # End-of-month balance for each month (from the Balance sub-column)
        monthly: dict[str, float | None] = {
            month: _parse_dollar(row[col_i] if col_i < len(row) else "")
            for month, col_i in month_balance_col.items()
        }

        categories.append({
            "name":    name,
            "heather": heather,
            "jason":   jason,
            "total":   total,
            "monthly": monthly,
        })

    # ── Step 5: Trim to requested range ──────────────────────────────────────
    # limit=0 means return everything; otherwise return the last N months.
    recent_months = all_months[-limit:] if limit > 0 else all_months

    # Trim each category's monthly dict to the selected months only
    for cat in categories:
        cat["monthly"] = {m: cat["monthly"].get(m) for m in recent_months}

    return {
        "months":     recent_months,
        "categories": categories,
        "total_months": len(all_months),
    }
