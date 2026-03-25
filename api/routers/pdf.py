"""PDF ingestion: extract text with pdfplumber, parse with Claude Haiku."""
import os
import logging
import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
import pdfplumber
import anthropic
import io

from api.dependencies import get_current_user
from api.database import get_db
from api import security_log

logger = logging.getLogger("vaultic.pdf")
router = APIRouter(prefix="/api/pdf", tags=["pdf"])

PARSE_PROMPT = """You are a financial data extractor. Extract ALL data from this PDF financial statement. Capture every number — missing data can never be recovered later.

Return ONLY a JSON array with this exact structure (no explanation, no markdown):
[
  {
    "name": "Account or portfolio name — use the exact name from the PDF",
    "category": "one of: invested | liquid | real_estate | vehicles | crypto | other_asset | other_liability",
    "value": 12345.67,
    "notes": "institution name, account type, account number (masked ok), as-of date",
    "activity_summary": {
      "account_holder": "Heather A Bazen",
      "account_number": "B37-601959",
      "institution": "Parker Financial / NFS",
      "period_start": "2026-02-01",
      "period_end": "2026-02-28",
      "beginning_balance": 150715.45,
      "ending_balance": 151965.11,
      "additions_withdrawals": 0.00,
      "misc_corporate_actions": 0.00,
      "period_income": 0.04,
      "period_fees": 0.00,
      "net_change": 1249.62,
      "ytd_beginning_balance": 148170.79,
      "ytd_additions_withdrawals": 0.00,
      "ytd_income": 0.40,
      "ytd_fees": -333.38,
      "ytd_change_in_value": 4127.30,
      "ytd_contributions": 0.00,
      "ytd_distributions": 0.00,
      "total_cost_basis": 124446.91,
      "total_estimated_annual_income": 1081.07,
      "total_gain_loss_dollars": 26890.92
    },
    "holdings": [
      {
        "name": "EXACT fund/security name from PDF",
        "ticker": "FXAIX",
        "asset_class": "equities | fixed_income | cash | alternatives | other",
        "shares": 129.121,
        "price": 239.33,
        "value": 30902.53,
        "cost": 27630.30,
        "avg_unit_cost": 213.99,
        "gain_loss_dollars": 3272.23,
        "gain_loss_pct": null,
        "pct_assets": null,
        "estimated_annual_income": 341.91,
        "estimated_yield_pct": 1.10,
        "notes": null
      }
    ],
    "activity": [
      {
        "date": "2026-02-27",
        "type": "reinvestment | buy | sell | dividend | interest | fee | transfer | other",
        "description": "Advisory Retirement Sweep Program Net Int Reinvest",
        "ticker": null,
        "quantity": 0.04,
        "amount": -0.04
      }
    ]
  }
]

Rules:
- Create ONE entry per individual account. If the PDF shows a portfolio-level summary AND individual accounts, create entries for each individual account only (skip the rolled-up portfolio entry to avoid double-counting).
- activity_summary: capture ALL numeric fields visible — current period AND year-to-date. Use null only if the field genuinely does not appear in the PDF.
- holdings: extract EVERY holding shown. Use the EXACT name from the PDF — do NOT normalize or rename. Include avg_unit_cost (shown as "Average Unit Cost $X.XX" per fund), estimated_yield_pct (shown as "Estimated Yield X.XX%"), and estimated_annual_income.
- activity: capture every transaction listed in the Activity section (buy, sell, dividend, reinvestment, fee, transfer, etc.)
- asset_class: equities (stocks/ETFs/equity mutual funds/any growth/value/blend/cap/international category), fixed_income (bonds/bond funds), cash (money market/bank sweep/cash equivalents), alternatives (infrastructure/real estate funds/commodities), other
- category: invested = 401k/IRA/brokerage/mutual funds; liquid = checking/savings/money market/HSA; other_liability = loans/credit cards/mortgages
- Use positive values for assets. Use null for any numeric field not present in the PDF.
- Skip zero-balance holdings. Include statement date in notes."""


def _salvage_json(raw: str) -> list:
    """
    Extract all complete top-level JSON objects from a possibly-truncated array string.
    Walks char-by-char tracking brace depth and string state so it handles any
    truncation point cleanly — entries cut off mid-object are simply skipped.
    """
    depth = 0
    in_string = False
    escape_next = False
    obj_start = None
    entries = []

    for i, c in enumerate(raw):
        if escape_next:
            escape_next = False
            continue
        if c == "\\" and in_string:
            escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    entries.append(json.loads(raw[obj_start : i + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = None

    return entries


@router.post("/ingest")
async def ingest_pdf(
    file: UploadFile = File(...),
    _user: str = Depends(get_current_user),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    security_log.log_server_event(f"PDF_UPLOAD  user={_user}  file={file.filename!r}")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:  # 20MB limit
        raise HTTPException(status_code=413, detail="PDF too large (max 20MB)")

    # Extract text with pdfplumber
    try:
        text_pages = []
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            for i, page in enumerate(pdf.pages[:30]):  # cap at 30 pages
                text = page.extract_text()
                if text:
                    text_pages.append(f"--- Page {i+1} ---\n{text}")
        full_text = "\n\n".join(text_pages)
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        raise HTTPException(status_code=422, detail=f"Could not read PDF: {e}")

    if not full_text.strip():
        raise HTTPException(status_code=422, detail="No text found in PDF — it may be a scanned image. OCR not yet supported.")

    # Parse with Claude Haiku
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8096,
            messages=[{
                "role": "user",
                "content": f"{PARSE_PROMPT}\n\n---PDF TEXT---\n{full_text[:15000]}"
            }]
        )
        raw = resp.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            # Response was cut off mid-JSON (max_tokens). Walk the raw string and
            # collect every complete top-level object so partial results aren't lost.
            logger.warning(f"JSON truncated at char {e.pos}, attempting salvage")
            parsed = _salvage_json(raw)
            if not parsed:
                logger.error(f"Salvage failed — raw: {raw[:200]}")
                raise HTTPException(status_code=422, detail="Could not parse Claude's response as JSON")
            logger.info(f"Salvaged {len(parsed)} entries from truncated response")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    security_log.log_server_event(
        f"PDF_PARSED  user={_user}  file={file.filename!r}  entries={len(parsed)}"
    )
    return {"filename": file.filename, "parsed": parsed, "pages": len(text_pages)}


class SaveParsedRequest(BaseModel):
    entries: list[dict]


@router.post("/save")
async def save_parsed(body: SaveParsedRequest, _user: str = Depends(get_current_user)):
    """Save confirmed parsed entries as manual entries, including holdings and activity summary.
    Replaces any existing manual entry with the same name to prevent duplicates
    when re-importing an updated statement."""
    from datetime import date, datetime as dt
    today = date.today().isoformat()
    saved = 0

    def _f(obj, k):
        v = obj.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _parse_date(raw):
        """Parse a date string in any common format to YYYY-MM-DD, or return None."""
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
            try:
                return dt.strptime(str(raw), fmt).date().isoformat()
            except ValueError:
                continue
        return None

    with get_db() as conn:
        for e in body.entries:
            name = str(e.get("name", ""))[:100]
            category = str(e.get("category", "other_asset"))
            value = float(e.get("value", 0))
            notes = str(e.get("notes", ""))[:200]
            summary = e.get("activity_summary")
            summary_json = json.dumps(summary) if summary else None
            if not name:
                continue

            # Delete existing entry with same name OR same account_number so re-imports don't
            # double-count in net worth. Account_number match handles name variations across
            # different PDF formats for the same account.
            # ON DELETE CASCADE removes stale holdings; history is preserved in snapshots tables.
            conn.execute("DELETE FROM manual_entries WHERE name = ?", (name,))
            acct_num = (summary or {}).get("account_number")
            if acct_num:
                conn.execute(
                    "DELETE FROM manual_entries WHERE json_extract(summary_json, '$.account_number') = ?",
                    (acct_num,)
                )
            cursor = conn.execute(
                "INSERT INTO manual_entries (name, category, value, notes, summary_json, entered_at) VALUES (?,?,?,?,?,?)",
                (name, category, value, notes, summary_json, today)
            )
            entry_id = cursor.lastrowid
            saved += 1

            # Determine snapshot date from the statement period_end field (new prompt)
            # or ending_balance date fields (old prompt). Falls back to today.
            snapshot_date = today
            if summary:
                raw = summary.get("period_end") or summary.get("ending_date")
                parsed = _parse_date(raw)
                if parsed:
                    snapshot_date = parsed

            # Account-level balance snapshot (for net worth / performance chart history)
            conn.execute("""
                INSERT INTO manual_entry_snapshots (name, category, value, snapped_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name, snapped_at) DO UPDATE SET value = excluded.value
            """, (name, category, value, snapshot_date))

            # Holdings — current snapshot (replaces previous holdings for this entry)
            holdings = e.get("holdings") or []
            for h in holdings:
                h_name = str(h.get("name", ""))[:200]
                if not h_name:
                    continue
                conn.execute("""
                    INSERT INTO manual_holdings
                        (manual_entry_id, name, ticker, asset_class, shares, price, value,
                         pct_assets, principal, gain_loss_dollars, gain_loss_pct,
                         avg_unit_cost, estimated_annual_income, estimated_yield_pct, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    entry_id, h_name,
                    h.get("ticker") or None,
                    h.get("asset_class") or None,
                    _f(h, "shares"), _f(h, "price"), _f(h, "value"),
                    _f(h, "pct_assets"),
                    _f(h, "cost") or _f(h, "principal"),   # support both field names
                    _f(h, "gain_loss_dollars"), _f(h, "gain_loss_pct"),
                    _f(h, "avg_unit_cost"),
                    _f(h, "estimated_annual_income"),
                    _f(h, "estimated_yield_pct"),
                    str(h.get("notes", "") or "")[:200] or None,
                ))

            # Holdings snapshot — append-only dated record so we keep full history
            # even after re-importing a newer statement overwrites manual_holdings.
            for h in holdings:
                h_name = str(h.get("name", ""))[:200]
                if not h_name:
                    continue
                conn.execute("""
                    INSERT INTO manual_holdings_snapshots
                        (entry_name, snapped_at, holding_name, ticker, asset_class,
                         shares, price, value, cost, avg_unit_cost,
                         gain_loss_dollars, gain_loss_pct, pct_assets,
                         estimated_annual_income, estimated_yield_pct)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(entry_name, snapped_at, holding_name)
                    DO UPDATE SET
                        shares=excluded.shares, price=excluded.price, value=excluded.value,
                        cost=excluded.cost, avg_unit_cost=excluded.avg_unit_cost,
                        gain_loss_dollars=excluded.gain_loss_dollars,
                        gain_loss_pct=excluded.gain_loss_pct,
                        pct_assets=excluded.pct_assets,
                        estimated_annual_income=excluded.estimated_annual_income,
                        estimated_yield_pct=excluded.estimated_yield_pct
                """, (
                    name, snapshot_date, h_name,
                    h.get("ticker") or None,
                    h.get("asset_class") or None,
                    _f(h, "shares"), _f(h, "price"), _f(h, "value"),
                    _f(h, "cost") or _f(h, "principal"),
                    _f(h, "avg_unit_cost"),
                    _f(h, "gain_loss_dollars"), _f(h, "gain_loss_pct"),
                    _f(h, "pct_assets"),
                    _f(h, "estimated_annual_income"),
                    _f(h, "estimated_yield_pct"),
                ))

    security_log.log_server_event(f"PDF_SAVED  user={_user}  entries={saved}")
    return {"status": "saved", "count": saved}
