"""
Tax module — store and analyze tax return history.
Parses 1040 PDFs using Claude Haiku and provides year-over-year analytics.
"""
import os
import json
import logging
import tempfile
from pathlib import Path

import anthropic
import pdfplumber
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from api.database import get_db
from api.dependencies import get_current_user

router = APIRouter()
logger = logging.getLogger("vaultic.tax")


def _parse_tax_pdf_with_ai(pdf_path: str) -> dict:
    """Extract structured tax return data from a 1040 PDF using Claude Haiku.

    Reads the PDF with pdfplumber, sends the text to Claude Haiku, and gets
    back a structured JSON object with all key 1040 line items.
    """
    # Extract text from first 10 pages (covers 1040 + Schedule A + Schedule 1)
    text_pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:15]:
            t = page.extract_text()
            if t:
                text_pages.append(t)

    full_text = "\n\n".join(text_pages)
    if not full_text.strip():
        raise ValueError("Could not extract text from PDF")

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    prompt = f"""You are a tax return parser. Extract structured data from this IRS Form 1040 tax return.

Return ONLY a valid JSON object with these exact fields (use null for missing values, numbers without $ or commas):

{{
  "tax_year": <4-digit year integer>,
  "filing_status": "married_filing_jointly",
  "wages_w2": <line 1 or 1a - W-2 wages>,
  "taxable_interest": <line 2b>,
  "qualified_dividends": <line 3a>,
  "ordinary_dividends": <line 3b>,
  "capital_gains": <line 7 - capital gains/losses>,
  "ira_distributions": <line 4b taxable IRA distributions, exclude rollovers>,
  "other_income": <Schedule 1 additional income>,
  "total_income": <total income line>,
  "adjustments_to_income": <Schedule 1 adjustments>,
  "agi": <adjusted gross income>,
  "deduction_method": "itemized" if Schedule A is attached and the itemized total was used, otherwise "standard" — look for the Schedule A page and its total; if Schedule A total matches the deduction line, it is itemized,
  "deduction_amount": <line 12 or 9 depending on year - total deduction taken>,
  "qbi_deduction": <qualified business income deduction if any>,
  "taxable_income": <taxable income line>,
  "total_tax": <total tax line>,
  "child_tax_credit": <child tax credit amount>,
  "other_credits": <other credits from Schedule 3>,
  "total_credits": <sum of all credits>,
  "w2_withheld": <federal income tax withheld from W-2s>,
  "total_payments": <total payments line>,
  "refund": <refund amount, null if they owed>,
  "owed": <amount owed, null if they got a refund>,
  "salt_deduction": <Schedule A state and local taxes - capped at 10000 after 2017>,
  "mortgage_interest": <Schedule A mortgage interest>,
  "charitable_cash": <Schedule A cash charitable contributions>,
  "charitable_noncash": <Schedule A non-cash charitable contributions>,
  "mortgage_insurance": <Schedule A mortgage insurance premiums>,
  "total_itemized": <Schedule A total itemized deductions>
}}

Tax return text:
{full_text[:16000]}

Return ONLY the JSON object, no explanation."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    logger.info("Tax PDF raw AI response: %s", raw)
    data = json.loads(raw)
    logger.info("Tax PDF parsed data: %s", data)

    # MFJ standard deduction by year — if deduction_amount doesn't match, it's itemized.
    MFJ_STANDARD = {2019: 24400, 2020: 24800, 2021: 25100, 2022: 25900, 2023: 27700, 2024: 29200}
    year = data.get("tax_year")
    ded = data.get("deduction_amount")
    if year and ded:
        std = MFJ_STANDARD.get(year)
        if std and abs(ded - std) > 50:
            # Deduction amount doesn't match standard — must be itemized
            data["deduction_method"] = "itemized"
            logger.info("Tax %s: deduction %s != std %s → forcing itemized", year, ded, std)

    # Also override if Schedule A total is present
    if data.get("total_itemized") and data["total_itemized"] > 0:
        data["deduction_method"] = "itemized"

    # Calculate effective rate
    if data.get("total_tax") and data.get("agi") and data["agi"] > 0:
        data["effective_rate"] = round(data["total_tax"] / data["agi"] * 100, 2)
    else:
        data["effective_rate"] = None

    return data


@router.get("/returns")
async def list_tax_returns(_user: str = Depends(get_current_user)):
    """Return all parsed tax returns sorted newest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tax_returns ORDER BY tax_year DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/returns/{year}")
async def get_tax_return(year: int, _user: str = Depends(get_current_user)):
    """Return a single tax year's full detail."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tax_returns WHERE tax_year = ?", (year,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Tax return not found")
    return dict(row)


@router.get("/summary")
async def get_tax_summary(_user: str = Depends(get_current_user)):
    """Year-over-year tax summary for charts and Sage context."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT tax_year, wages_w2, agi, taxable_income, total_tax,
                   effective_rate, deduction_method, deduction_amount,
                   refund, owed, w2_withheld, total_payments,
                   child_tax_credit, mortgage_interest, charitable_cash,
                   charitable_noncash, salt_deduction
            FROM tax_returns
            ORDER BY tax_year ASC
        """).fetchall()
    return [dict(r) for r in rows]


@router.post("/parse-pdf")
async def parse_tax_pdf(
    file: UploadFile = File(...),
    _user: str = Depends(get_current_user),
):
    """Upload and parse a 1040 PDF tax return. Stores results in tax_returns table."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    # Save to temp file
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        data = _parse_tax_pdf_with_ai(tmp_path)
    except Exception as e:
        logger.error(f"Tax PDF parse failed: {e}")
        raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")
    finally:
        os.unlink(tmp_path)

    tax_year = data.get("tax_year")
    if not tax_year:
        raise HTTPException(status_code=422, detail="Could not determine tax year from PDF")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO tax_returns (
                tax_year, filing_status, wages_w2, taxable_interest,
                qualified_dividends, ordinary_dividends, capital_gains,
                ira_distributions, other_income, total_income,
                adjustments_to_income, agi, deduction_method, deduction_amount,
                qbi_deduction, taxable_income, total_tax, child_tax_credit,
                other_credits, total_credits, w2_withheld, total_payments,
                refund, owed, effective_rate, salt_deduction, mortgage_interest,
                charitable_cash, charitable_noncash, mortgage_insurance,
                total_itemized, source_file
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tax_year) DO UPDATE SET
                wages_w2=excluded.wages_w2, agi=excluded.agi,
                taxable_income=excluded.taxable_income, total_tax=excluded.total_tax,
                effective_rate=excluded.effective_rate, deduction_method=excluded.deduction_method,
                deduction_amount=excluded.deduction_amount, refund=excluded.refund,
                owed=excluded.owed, w2_withheld=excluded.w2_withheld,
                total_payments=excluded.total_payments, child_tax_credit=excluded.child_tax_credit,
                mortgage_interest=excluded.mortgage_interest, charitable_cash=excluded.charitable_cash,
                salt_deduction=excluded.salt_deduction, total_itemized=excluded.total_itemized,
                source_file=excluded.source_file, parsed_at=CURRENT_TIMESTAMP
        """, (
            tax_year, data.get("filing_status"), data.get("wages_w2"),
            data.get("taxable_interest"), data.get("qualified_dividends"),
            data.get("ordinary_dividends"), data.get("capital_gains"),
            data.get("ira_distributions"), data.get("other_income"),
            data.get("total_income"), data.get("adjustments_to_income"),
            data.get("agi"), data.get("deduction_method"), data.get("deduction_amount"),
            data.get("qbi_deduction"), data.get("taxable_income"), data.get("total_tax"),
            data.get("child_tax_credit"), data.get("other_credits"), data.get("total_credits"),
            data.get("w2_withheld"), data.get("total_payments"), data.get("refund"),
            data.get("owed"), data.get("effective_rate"), data.get("salt_deduction"),
            data.get("mortgage_interest"), data.get("charitable_cash"),
            data.get("charitable_noncash"), data.get("mortgage_insurance"),
            data.get("total_itemized"), file.filename,
        ))

    return {"ok": True, "tax_year": tax_year, "data": data}
