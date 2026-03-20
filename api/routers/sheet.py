"""Google Sheets read-only integration for Fund Financials.

Fetches the wife's Fund Financials Google Sheet (publicly shared, viewer-only)
via CSV export and parses the multi-level header structure into structured data.

Sheet structure (as observed):
  Row 0: Month headers spanning multiple columns (Oct-15, Nov-15, … Mar-26)
  Row 1: Per-person sub-headers (HEATHER, JASON, TOTAL) repeating per month
  Rows 2+: Category rows with dollar values per person per month

Returns the last N months of data for all categories so the frontend can
display current balances and a short trend history.
"""
import csv
import io
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
import requests
from api.dependencies import get_current_user

router = APIRouter(prefix="/api/sheet", tags=["sheet"])

SHEET_ID  = "11-e3Jodhs8YPGZ1CvcVjou8D2GP2iXs7rWLAljf2kDw"
SHEET_GID = "1053339711"
CSV_URL   = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"

# Month label patterns Google Sheets exports: "Oct-15", "Jan-26", etc.
MONTH_RE = re.compile(r"^[A-Za-z]{3}-\d{2}$")


def _parse_dollar(val: str) -> float | None:
    """Parse a dollar string like '$1,234.56' or '(500.00)' → float."""
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
    """Convert 'Mar-26' → sortable int (202603) for chronological ordering."""
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
async def get_sheet_data(_user: str = Depends(get_current_user)):
    """Fetch and parse the Fund Financials Google Sheet.

    Returns:
      months:     ordered list of month labels (most recent last)
      categories: list of {name, rows: [{person, values: {month: amount}}]}
      persons:    list of person labels found (e.g. ['HEATHER', 'JASON', 'TOTAL'])
    """
    try:
        resp = requests.get(CSV_URL, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch sheet: {e}")

    rows = list(csv.reader(io.StringIO(resp.text)))
    if len(rows) < 3:
        raise HTTPException(status_code=502, detail="Sheet data too short to parse")

    # ── Find month header row ─────────────────────────────────────────────────
    # Scan rows to find the one with month labels like "Oct-15"
    month_row_idx = None
    for i, row in enumerate(rows):
        if sum(1 for cell in row if MONTH_RE.match(cell.strip())) >= 3:
            month_row_idx = i
            break

    if month_row_idx is None:
        raise HTTPException(status_code=502, detail="Could not find month header row in sheet")

    month_row = rows[month_row_idx]

    # ── Find person sub-header row ────────────────────────────────────────────
    # The row immediately after the month row typically has HEATHER/JASON/TOTAL
    person_row_idx = month_row_idx + 1
    person_row = rows[person_row_idx] if person_row_idx < len(rows) else []

    # ── Build column index ────────────────────────────────────────────────────
    # Map: column_index → (month_label, person_label)
    # Month labels carry forward across blank cells (merged cell expansion)
    col_map: dict[int, tuple[str, str]] = {}
    current_month = ""
    known_persons: list[str] = []

    for col_i, cell in enumerate(month_row):
        if MONTH_RE.match(cell.strip()):
            current_month = cell.strip()

        person = person_row[col_i].strip() if col_i < len(person_row) else ""
        if current_month and person:
            col_map[col_i] = (current_month, person)
            if person not in known_persons:
                known_persons.append(person)

    # Collect all months in chronological order
    all_months = sorted(
        {m for m, _ in col_map.values()},
        key=_month_sort_key,
    )

    # ── Parse data rows ───────────────────────────────────────────────────────
    data_start = person_row_idx + 1
    categories: list[dict] = []
    current_category: dict | None = None

    for row in rows[data_start:]:
        if not row:
            continue

        # First cell is category name or person sub-row label
        label = row[0].strip() if row else ""

        if not label:
            continue

        # Detect if this is a person row (HEATHER/JASON/TOTAL) or a category header
        if label.upper() in [p.upper() for p in known_persons]:
            # Person data row — belongs to the current category
            if current_category is None:
                continue
            person_label = label.upper()
            values: dict[str, float | None] = {}
            for col_i, (month, person) in col_map.items():
                if person.upper() == person_label and col_i < len(row):
                    values[month] = _parse_dollar(row[col_i])
            current_category["rows"].append({"person": person_label, "values": values})
        else:
            # New category header row
            current_category = {"name": label, "rows": []}
            categories.append(current_category)

    # Filter out empty / header-only categories
    categories = [c for c in categories if c["rows"]]

    # Return only the last 6 months to keep response small
    recent_months = all_months[-6:] if len(all_months) > 6 else all_months

    # Trim values to recent months only
    for cat in categories:
        for row in cat["rows"]:
            row["values"] = {m: row["values"].get(m) for m in recent_months}

    return {
        "months":     recent_months,
        "persons":    known_persons,
        "categories": categories,
    }
