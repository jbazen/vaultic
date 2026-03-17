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

PARSE_PROMPT = """You are a financial data extractor. Extract ALL accounts, holdings, and balances from this PDF financial statement.

Return ONLY a JSON array with this exact structure (no explanation, no markdown):
[
  {
    "name": "Account or asset name",
    "category": "one of: invested | liquid | real_estate | vehicles | crypto | other_asset | other_liability",
    "value": 12345.67,
    "notes": "brief context: account type, institution, as-of date",
    "holdings": [
      {
        "name": "Security or fund name",
        "ticker": "VTSAX",
        "asset_class": "equities | fixed_income | cash | alternatives | other",
        "shares": 123.456,
        "price": 45.67,
        "value": 5641.23,
        "pct_assets": 12.5,
        "principal": 5000.00,
        "gain_loss_dollars": 641.23,
        "gain_loss_pct": 12.82,
        "notes": "any other relevant detail or null"
      }
    ]
  }
]

Rules:
- holdings array may be [] if no detailed holdings are visible for that account
- Use positive values for assets; positive values for liabilities (category determines sign in the UI)
- invested = 401k, IRA, brokerage, mutual funds, investment accounts
- liquid = checking, savings, money market, HSA
- other_liability = loans, credit card balances, mortgages not tracked elsewhere
- Include the statement date in notes if visible
- Skip accounts with zero balance unless intentionally zero
- Use null for numeric fields not present in the PDF
- asset_class mappings: equities (stocks/ETFs/equity mutual funds), fixed_income (bonds/bond funds), cash (money market/cash/cash equivalents), alternatives (real estate funds/commodities/hedge funds), other (anything else)
- If a value is a range, use the midpoint"""


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
    """Save confirmed parsed entries as manual entries, including holdings."""
    from datetime import date
    today = date.today().isoformat()
    saved = 0
    with get_db() as conn:
        for e in body.entries:
            name = str(e.get("name", ""))[:100]
            category = str(e.get("category", "other_asset"))
            value = float(e.get("value", 0))
            notes = str(e.get("notes", ""))[:200]
            if not name:
                continue
            cursor = conn.execute(
                "INSERT INTO manual_entries (name, category, value, notes, entered_at) VALUES (?,?,?,?,?)",
                (name, category, value, notes, today)
            )
            entry_id = cursor.lastrowid
            saved += 1

            # Save holdings if present
            holdings = e.get("holdings") or []
            for h in holdings:
                h_name = str(h.get("name", ""))[:200]
                if not h_name:
                    continue
                def _f(k):
                    v = h.get(k)
                    try:
                        return float(v) if v is not None else None
                    except (TypeError, ValueError):
                        return None
                conn.execute("""
                    INSERT INTO manual_holdings
                        (manual_entry_id, name, ticker, asset_class, shares, price, value,
                         pct_assets, principal, gain_loss_dollars, gain_loss_pct, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    entry_id,
                    h_name,
                    h.get("ticker") or None,
                    h.get("asset_class") or None,
                    _f("shares"),
                    _f("price"),
                    _f("value"),
                    _f("pct_assets"),
                    _f("principal"),
                    _f("gain_loss_dollars"),
                    _f("gain_loss_pct"),
                    str(h.get("notes", "") or "")[:200] or None,
                ))

    security_log.log_server_event(f"PDF_SAVED  user={_user}  entries={saved}")
    return {"status": "saved", "count": saved}
