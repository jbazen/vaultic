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

PARSE_PROMPT = """You are a financial data extractor. The user has uploaded a PDF financial statement.
Extract ALL accounts, balances, and asset/liability values you can find.

Return ONLY a JSON array with this exact structure (no explanation, no markdown):
[
  {
    "name": "Account or asset name",
    "category": "one of: invested | liquid | real_estate | vehicles | crypto | other_asset | other_liability",
    "value": 12345.67,
    "notes": "brief context like account type, as-of date, institution"
  }
]

Rules:
- Use positive values for assets, positive values for liabilities (the category determines sign)
- "invested" = 401k, IRA, brokerage, mutual funds, investment accounts
- "liquid" = checking, savings, money market, HSA
- "other_liability" = loans, credit card balances, mortgages not tracked elsewhere
- "other_asset" = anything that doesn't fit above
- Include the statement date in notes if visible
- Skip accounts with zero balance unless they seem intentionally zero
- If a value is a range, use the midpoint"""


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
            max_tokens=2048,
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
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Claude JSON parse error: {e} — raw: {raw[:200]}")
        raise HTTPException(status_code=422, detail="Could not parse Claude's response as JSON")
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
    """Save confirmed parsed entries as manual entries."""
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
            conn.execute(
                "INSERT INTO manual_entries (name, category, value, notes, entered_at) VALUES (?,?,?,?,?)",
                (name, category, value, notes, today)
            )
            saved += 1
    security_log.log_server_event(f"PDF_SAVED  user={_user}  entries={saved}")
    return {"status": "saved", "count": saved}
