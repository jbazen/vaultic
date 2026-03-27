"""
Tax module — store and analyze tax return history.
Parses 1040 PDFs using Claude Haiku and provides year-over-year analytics.
"""
import os
import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path

import anthropic
import pdfplumber
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

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

    file_bytes = await file.read()
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    parse_error = None
    data = {}
    try:
        data = _parse_tax_pdf_with_ai(tmp_path)
    except Exception as e:
        logger.error(f"Tax PDF parse failed: {e}")
        parse_error = str(e)
    finally:
        os.unlink(tmp_path)

    tax_year = data.get("tax_year")

    # Always vault the file
    from api.routers.vault import save_to_vault as _save_to_vault
    _vault_year = tax_year or datetime.now().year
    with get_db() as conn:
        _save_to_vault(conn, _vault_year, "tax_return", file.filename, file_bytes,
                       issuer="IRS",
                       description=f"{_vault_year} Form 1040" if tax_year else "Tax Return (parse failed)",
                       parsed=not bool(parse_error))

    if parse_error:
        raise HTTPException(status_code=422, detail=f"File saved to vault but could not parse: {parse_error}")
    if not tax_year:
        raise HTTPException(status_code=422, detail="File saved to vault but could not determine tax year")

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


# ── W-4 endpoints ─────────────────────────────────────────────────────────────

@router.post("/upload-w4")
async def upload_w4(
    file: UploadFile = File(...),
    _user: str = Depends(get_current_user),
):
    """Upload and parse a W-4 PDF. Stores withholding elections in w4s table."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    text_pages = []
    w4_file_bytes = await file.read()
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(w4_file_bytes)
        tmp_path = tmp.name

    try:
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages[:3]:
                t = page.extract_text()
                if t:
                    text_pages.append(t)
        full_text = "\n\n".join(text_pages)
        if not full_text.strip():
            raise ValueError("Could not extract text from PDF")

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        prompt = f"""You are a W-4 parser. Extract withholding election data from this IRS Form W-4.

Return ONLY a valid JSON object (numbers without $ or commas, null for missing):
{{
  "employer": <employer name if shown, otherwise null>,
  "employee_name": <employee full name>,
  "filing_status": "single", "married_filing_jointly", or "head_of_household",
  "multiple_jobs": <true if Step 2 checkbox is checked, else false>,
  "dependents_amount": <Step 3 total dollar amount for dependents/credits, or null>,
  "other_income": <Step 4a other income amount, or null>,
  "deductions": <Step 4b deductions amount, or null>,
  "extra_withholding": <Step 4c additional withholding per pay period, or null>,
  "effective_date": <date signed as YYYY-MM-DD, or null>
}}

W-4 text:
{full_text[:6000]}

Return ONLY the JSON object."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        logger.info("W-4 parsed: %s", data)
    except Exception as e:
        logger.error(f"W-4 parse failed: {e}")
        raise HTTPException(status_code=422, detail=f"Could not parse W-4: {e}")
    finally:
        os.unlink(tmp_path)

    employer = data.get("employer") or Path(file.filename).stem
    eff_date = data.get("effective_date") or "unknown"

    with get_db() as conn:
        conn.execute("""
            INSERT INTO w4s (
                employer, employee_name, filing_status, multiple_jobs,
                dependents_amount, other_income, deductions,
                extra_withholding, effective_date, source_file
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(employer, effective_date) DO UPDATE SET
                filing_status=excluded.filing_status,
                multiple_jobs=excluded.multiple_jobs,
                dependents_amount=excluded.dependents_amount,
                other_income=excluded.other_income,
                deductions=excluded.deductions,
                extra_withholding=excluded.extra_withholding,
                source_file=excluded.source_file,
                parsed_at=CURRENT_TIMESTAMP
        """, (
            employer, data.get("employee_name"), data.get("filing_status"),
            1 if data.get("multiple_jobs") else 0,
            data.get("dependents_amount"), data.get("other_income"),
            data.get("deductions"), data.get("extra_withholding"),
            eff_date, file.filename,
        ))
        from api.routers.vault import save_to_vault
        w4_year = int(eff_date[:4]) if eff_date and eff_date[:4].isdigit() else datetime.now().year
        save_to_vault(conn, w4_year, "w4", file.filename, w4_file_bytes,
                      issuer=employer, description=f"W-4 — {employer}", parsed=True)

    return {"ok": True, "employer": employer, "data": data}


@router.get("/w4s")
async def list_w4s(_user: str = Depends(get_current_user)):
    """Return all W-4s on file."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM w4s ORDER BY parsed_at DESC").fetchall()
    return [dict(r) for r in rows]


# ── Tax projection ─────────────────────────────────────────────────────────────

from api.tax_calc import (
    BRACKETS_2025_MFJ, BRACKETS_2024_MFJ, BRACKETS_BY_YEAR_AND_STATUS,
    STANDARD_DEDUCTIONS as _STANDARD_DEDUCTIONS_SHARED,
    CHILD_CREDIT_PER_CHILD as _CHILD_CREDIT_SHARED,
    NUM_CHILDREN as _NUM_CHILDREN_SHARED,
    SALT_CAP, calc_tax, get_brackets, get_standard_deduction,
)

# Aliases for backward compatibility within this file
_BRACKETS_2025_MFJ = BRACKETS_2025_MFJ
_calc_tax = calc_tax


@router.get("/projection/{year}")
async def get_tax_projection(year: int, _user: str = Depends(get_current_user)):
    """Project tax liability for the given year using YTD paystub data.

    Extrapolates YTD gross and withholding to full-year figures, applies
    the MFJ tax brackets, and estimates refund or amount owed.
    """
    from datetime import date as _date

    if year != 2025:
        raise HTTPException(status_code=400, detail="Projection only supported for 2025 currently")

    # MFJ standard deduction and child tax credit for 2025
    STANDARD_DEDUCTION = 30000
    CHILD_TAX_CREDIT_PER_CHILD = 2000
    NUM_CHILDREN = 2  # John and Milo

    with get_db() as conn:
        # Most recent paystub per employer for YTD totals
        stubs = conn.execute("""
            SELECT p.* FROM paystubs p
            INNER JOIN (
                SELECT employer, MAX(pay_date) AS latest FROM paystubs GROUP BY employer
            ) l ON p.employer = l.employer AND p.pay_date = l.latest
        """).fetchall()

        # Prior year tax return for deduction baseline
        prior = conn.execute(
            "SELECT * FROM tax_returns WHERE tax_year = ?", (year - 1,)
        ).fetchone()

    if not stubs:
        raise HTTPException(status_code=404, detail="No paystubs uploaded — cannot project")

    stubs = [dict(s) for s in stubs]
    prior = dict(prior) if prior else {}

    # Sum YTD figures across all employers
    ytd_gross = sum(s.get("ytd_gross") or 0 for s in stubs)
    ytd_federal = sum(s.get("ytd_federal") or 0 for s in stubs)

    # Determine fraction of year elapsed from most recent pay date
    latest_pay_date_str = max(s["pay_date"] for s in stubs if s.get("pay_date"))
    latest_pay_date = _date.fromisoformat(latest_pay_date_str)
    year_start = _date(year, 1, 1)
    year_end = _date(year, 12, 31)
    days_elapsed = (latest_pay_date - year_start).days + 1
    days_in_year = (year_end - year_start).days + 1
    year_fraction = days_elapsed / days_in_year

    # Extrapolate to full year
    proj_gross = round(ytd_gross / year_fraction) if year_fraction > 0 else ytd_gross
    proj_federal_withheld = round(ytd_federal / year_fraction) if year_fraction > 0 else ytd_federal

    # Deduction: use prior year itemized if it beat standard, else standard
    prior_itemized = prior.get("total_itemized") or 0
    if prior_itemized > STANDARD_DEDUCTION:
        deduction_method = "itemized (estimated from prior year)"
        deduction_amount = prior_itemized  # conservative — use prior year as estimate
    else:
        deduction_method = "standard"
        deduction_amount = STANDARD_DEDUCTION

    # Taxable income and tax
    taxable_income = max(0, proj_gross - deduction_amount)
    gross_tax = _calc_tax(taxable_income, _BRACKETS_2025_MFJ)
    child_credit = NUM_CHILDREN * CHILD_TAX_CREDIT_PER_CHILD
    net_tax = max(0, gross_tax - child_credit)

    # Result
    delta = proj_federal_withheld - net_tax
    refund = round(delta) if delta > 0 else None
    owed = round(-delta) if delta < 0 else None
    effective_rate = round(net_tax / proj_gross * 100, 2) if proj_gross > 0 else None

    return {
        "year": year,
        "year_fraction_elapsed": round(year_fraction, 3),
        "as_of_pay_date": latest_pay_date_str,
        "ytd_gross": round(ytd_gross),
        "proj_gross": proj_gross,
        "deduction_method": deduction_method,
        "deduction_amount": deduction_amount,
        "taxable_income": round(taxable_income),
        "gross_tax": gross_tax,
        "child_tax_credit": child_credit,
        "net_tax": round(net_tax),
        "proj_federal_withheld": proj_federal_withheld,
        "refund": refund,
        "owed": owed,
        "effective_rate": effective_rate,
        "employers": [{"employer": s["employer"], "ytd_gross": s.get("ytd_gross"), "ytd_federal": s.get("ytd_federal"), "pay_date": s.get("pay_date")} for s in stubs],
    }


# ── Quarterly Estimated Tax Payments (1040-ES) ────────────────────────────────

# Fixed due dates for estimated payments by tax year.
# When a date falls on a weekend/holiday the IRS moves it to the next business day.
_EST_DUE_DATES = {
    2025: [
        {"quarter": 1, "label": "Q1", "period": "Jan 1 – Mar 31", "due": "2025-04-15"},
        {"quarter": 2, "label": "Q2", "period": "Apr 1 – May 31", "due": "2025-06-16"},
        {"quarter": 3, "label": "Q3", "period": "Jun 1 – Aug 31", "due": "2025-09-15"},
        {"quarter": 4, "label": "Q4", "period": "Sep 1 – Dec 31", "due": "2026-01-15"},
    ],
    2024: [
        {"quarter": 1, "label": "Q1", "period": "Jan 1 – Mar 31", "due": "2024-04-15"},
        {"quarter": 2, "label": "Q2", "period": "Apr 1 – May 31", "due": "2024-06-17"},
        {"quarter": 3, "label": "Q3", "period": "Jun 1 – Aug 31", "due": "2024-09-16"},
        {"quarter": 4, "label": "Q4", "period": "Sep 1 – Dec 31", "due": "2025-01-15"},
    ],
}


@router.get("/estimated-payments/{year}")
async def get_estimated_payments(
    year: int,
    other_income: float = 0.0,  # non-W2 income not subject to withholding (optional query param)
    _user: str = Depends(get_current_user),
):
    """Calculate 1040-ES quarterly estimated tax payment amounts.

    Uses two IRS safe harbor methods and recommends whichever requires less
    cash out-of-pocket while still protecting against the underpayment penalty.

    Safe harbor A — Current year: pay at least 90% of this year's projected tax.
    Safe harbor B — Prior year:   pay at least 100% of last year's tax
                                  (110% if prior year AGI > $150k).

    The recommended quarterly payment is the lower of the two safe harbor amounts
    divided equally across the four quarters, after subtracting expected W-2
    withholding. Equal quarterly payments are standard for W-2 households;
    the annualized installment method (for uneven income) is not implemented.
    """
    from datetime import date as _date

    if year not in (2024, 2025):
        raise HTTPException(status_code=400, detail="Estimated payments only supported for 2024–2025")

    today = _date.today()

    # ── Pull paystub data ───────────────────────────────────────────────────
    with get_db() as conn:
        stubs = conn.execute("""
            SELECT p.* FROM paystubs p
            INNER JOIN (
                SELECT employer, MAX(pay_date) AS latest FROM paystubs GROUP BY employer
            ) l ON p.employer = l.employer AND p.pay_date = l.latest
        """).fetchall()

        prior = conn.execute(
            "SELECT * FROM tax_returns WHERE tax_year = ?", (year - 1,)
        ).fetchone()

    if not stubs:
        raise HTTPException(status_code=404, detail="No paystubs uploaded — cannot calculate")

    stubs = [dict(s) for s in stubs]
    prior = dict(prior) if prior else {}

    # ── Project current year income & withholding from YTD paystubs ─────────
    ytd_gross   = sum(s.get("ytd_gross")   or 0 for s in stubs)
    ytd_federal = sum(s.get("ytd_federal") or 0 for s in stubs)

    latest_pay_date_str = max(s["pay_date"] for s in stubs if s.get("pay_date"))
    latest_pay_date = _date.fromisoformat(latest_pay_date_str)
    year_start  = _date(year, 1, 1)
    year_end    = _date(year, 12, 31)
    days_elapsed = (latest_pay_date - year_start).days + 1
    days_in_year = (year_end - year_start).days + 1
    year_frac   = days_elapsed / days_in_year

    proj_gross    = (ytd_gross   / year_frac) if year_frac > 0 else ytd_gross
    proj_withheld = (ytd_federal / year_frac) if year_frac > 0 else ytd_federal

    # Add any non-W2 income the user provided (crypto gains, self-employment, etc.)
    total_proj_income = proj_gross + other_income

    # Deduction — use prior year itemized if it beat standard, else standard
    std_ded = _STANDARD_DEDUCTIONS.get(year, _STANDARD_DEDUCTIONS[2025]).get("married_filing_jointly", 30000)
    prior_itemized = prior.get("total_itemized") or 0
    deduction = max(std_ded, prior_itemized)

    brackets = _BRACKETS.get(year, _BRACKETS[2025]).get("married_filing_jointly")
    child_credit = 2 * 2000  # John and Milo

    taxable = max(0.0, total_proj_income - deduction)
    proj_tax = max(0.0, _calc_tax(taxable, brackets) - child_credit)

    # ── Safe harbor A: 90% of current year projected tax ────────────────────
    safe_harbor_a_total   = proj_tax * 0.90
    safe_harbor_a_needed  = max(0.0, safe_harbor_a_total  - proj_withheld)
    safe_harbor_a_per_qtr = round(safe_harbor_a_needed / 4, 2)

    # ── Safe harbor B: 100% / 110% of prior year actual tax ─────────────────
    prior_tax = prior.get("total_tax") or prior.get("net_tax") or 0
    prior_agi = prior.get("agi") or 0
    safe_harbor_b_rate   = 1.10 if prior_agi > 150000 else 1.00
    safe_harbor_b_total  = prior_tax * safe_harbor_b_rate
    safe_harbor_b_needed = max(0.0, safe_harbor_b_total - proj_withheld)
    safe_harbor_b_per_qtr = round(safe_harbor_b_needed / 4, 2)

    # ── Recommended method ───────────────────────────────────────────────────
    # Choose whichever requires less cash. If no prior year data, use current year.
    if prior_tax > 0:
        if safe_harbor_b_per_qtr <= safe_harbor_a_per_qtr:
            recommended_method = "prior_year"
            recommended_per_qtr = safe_harbor_b_per_qtr
            recommended_total   = safe_harbor_b_needed
        else:
            recommended_method = "current_year"
            recommended_per_qtr = safe_harbor_a_per_qtr
            recommended_total   = safe_harbor_a_needed
    else:
        recommended_method = "current_year"
        recommended_per_qtr = safe_harbor_a_per_qtr
        recommended_total   = safe_harbor_a_needed

    # ── Quarter status: past / current / upcoming ────────────────────────────
    quarters = []
    for q in _EST_DUE_DATES.get(year, []):
        due = _date.fromisoformat(q["due"])
        if today > due:
            status = "past"
        elif today >= _date.fromisoformat(q["due"][:7] + "-01"):
            status = "current"
        else:
            status = "upcoming"
        quarters.append({
            **q,
            "amount": recommended_per_qtr,
            "status": status,
            "days_until_due": (due - today).days,
        })

    # ── Notes ────────────────────────────────────────────────────────────────
    def fmt_dollars(v):
        return f"${round(v):,}"

    notes = []
    shortfall = proj_tax - proj_withheld
    if shortfall < 1000:
        notes.append("Your projected withholding likely covers your tax liability — you may not need estimated payments at all (IRS de minimis threshold: owe < $1,000 after withholding).")
    if recommended_per_qtr == 0:
        notes.append("No estimated payments appear necessary based on current data.")
    if other_income > 0:
        notes.append(f"Includes ${round(other_income):,} of additional non-withheld income you provided.")
    if not prior_tax:
        notes.append("No prior year return on file — calculation uses current year projection only. Upload your prior year 1040 for a more conservative safe harbor estimate.")
    if recommended_method == "prior_year":
        notes.append(f"Using prior year safe harbor ({int(safe_harbor_b_rate*100)}% of {year-1} tax of {fmt_dollars(prior_tax)}). This locks in your payment regardless of how this year's income changes.")
    else:
        notes.append("Using current year method (90% of projected tax). Update after each paystub upload as your projection changes.")

    return {
        "year": year,
        "as_of": latest_pay_date_str,
        "projection": {
            "proj_gross": round(proj_gross),
            "other_income": round(other_income),
            "total_income": round(total_proj_income),
            "deduction": round(deduction),
            "taxable_income": round(taxable),
            "proj_tax": round(proj_tax),
            "proj_withheld": round(proj_withheld),
            "shortfall": round(shortfall),
        },
        "safe_harbor_a": {
            "method": "current_year_90pct",
            "label": "90% of projected current year tax",
            "total_needed": round(safe_harbor_a_needed),
            "per_quarter": safe_harbor_a_per_qtr,
        },
        "safe_harbor_b": {
            "method": "prior_year",
            "label": f"{int(safe_harbor_b_rate*100)}% of {year-1} actual tax" if prior_tax else "No prior year data",
            "prior_year_tax": round(prior_tax),
            "safe_harbor_rate": safe_harbor_b_rate,
            "total_needed": round(safe_harbor_b_needed),
            "per_quarter": safe_harbor_b_per_qtr,
        },
        "recommended_method": recommended_method,
        "recommended_per_quarter": recommended_per_qtr,
        "recommended_total": round(recommended_total),
        "quarters": quarters,
        "notes": notes,
    }


# ── Universal tax document upload ─────────────────────────────────────────────

_DOC_TYPE_LABELS = {
    "w2": "W-2",
    "1098": "1098 Mortgage Interest",
    "1099_int": "1099-INT Interest",
    "1099_div": "1099-DIV Dividends",
    "1099_b": "1099-B Investment Sales",
    "1099_r": "1099-R Retirement",
    "1099_g": "1099-G State Refund",
    "giving_statement": "Charitable Giving Statement",
    "1098_sa": "1098-SA HSA Distributions",
    "5498_sa": "5498-SA HSA Contributions",
}

_DOC_PARSE_PROMPT = """You are a tax document parser. First identify the document type, then extract all key fields.

Return ONLY a valid JSON object (numbers without $ or commas, null for missing values):

{{
  "doc_type": one of: "w2", "1098", "1099_int", "1099_div", "1099_b", "1099_r", "1099_g", "giving_statement", "1098_sa", "5498_sa",
  "tax_year": <4-digit year the document covers>,
  "issuer": <company/institution name that issued this document>,

  // W-2 fields (if doc_type is "w2"):
  "w2_wages": <Box 1 wages>,
  "w2_fed_withheld": <Box 2 federal income tax withheld>,
  "w2_ss_withheld": <Box 4 Social Security tax withheld>,
  "w2_medicare_withheld": <Box 6 Medicare tax withheld>,
  "w2_state_withheld": <Box 17 state income tax withheld>,
  "w2_401k": <Box 12 code D - 401k contributions>,
  "w2_hsa_employer": <Box 12 code W - employer HSA contributions>,

  // 1098 mortgage interest (if doc_type is "1098"):
  "mortgage_interest": <Box 1 mortgage interest received>,
  "mortgage_points": <Box 6 points paid>,
  "property_taxes": <Box 10 real estate taxes>,

  // 1099-INT (if doc_type is "1099_int"):
  "interest_income": <Box 1 interest income>,
  "fed_withheld": <Box 4 federal income tax withheld>,

  // 1099-DIV (if doc_type is "1099_div"):
  "ordinary_dividends": <Box 1a total ordinary dividends>,
  "qualified_dividends": <Box 1b qualified dividends>,
  "cap_gains_dist": <Box 2a total capital gain distributions>,
  "fed_withheld": <Box 4 federal income tax withheld>,

  // 1099-B investment sales (if doc_type is "1099_b"):
  "proceeds": <total proceeds from all sales>,
  "cost_basis": <total cost basis from all sales>,
  "net_cap_gains": <proceeds minus cost basis — positive = gain, negative = loss>,
  "fed_withheld": <any federal tax withheld>,

  // 1099-R retirement (if doc_type is "1099_r"):
  "gross_distribution": <Box 1 gross distribution>,
  "taxable_distribution": <Box 2a taxable amount>,
  "distribution_code": <Box 7 distribution code>,
  "fed_withheld": <Box 4 federal income tax withheld>,

  // 1099-G state refund (if doc_type is "1099_g"):
  "state_refund": <Box 2 state or local income tax refund>,
  "unemployment": <Box 1 unemployment compensation>,
  "fed_withheld": <Box 4 federal income tax withheld>,

  // Charitable giving statement:
  "charitable_cash": <total cash/check/card donations>,
  "charitable_noncash": <total non-cash donations>,

  // 1098-SA HSA distributions:
  "hsa_distributions": <Box 1 total distributions>,

  // 5498-SA HSA contributions:
  "hsa_contributions": <Box 2 total contributions>
}}

Document text:
{text}

Return ONLY the JSON object, no explanation."""


def _parse_tax_doc_with_ai(pdf_path: str) -> dict:
    """Auto-detect and parse any tax document using Claude Haiku."""
    text_pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:8]:
            t = page.extract_text()
            if t:
                text_pages.append(t)
    full_text = "\n\n".join(text_pages)
    if not full_text.strip():
        raise ValueError("Could not extract text from PDF")

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    prompt = _DOC_PARSE_PROMPT.format(text=full_text[:12000])
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=768,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    logger.info("Tax doc raw AI response: %s", raw)
    return json.loads(raw)


@router.post("/docs/upload")
async def upload_tax_doc(
    file: UploadFile = File(...),
    _user: str = Depends(get_current_user),
):
    """Upload any tax document PDF — auto-detects type and parses fields."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    doc_file_bytes = await file.read()
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(doc_file_bytes)
        tmp_path = tmp.name

    parse_error = None
    data = {}
    try:
        data = _parse_tax_doc_with_ai(tmp_path)
    except Exception as e:
        logger.error(f"Tax doc parse failed: {e}")
        parse_error = str(e)
    finally:
        os.unlink(tmp_path)

    doc_type = data.get("doc_type")
    tax_year = data.get("tax_year")

    # Always vault the file regardless of parse success
    from api.routers.vault import save_to_vault
    vault_year = tax_year or datetime.now().year
    with get_db() as conn:
        save_to_vault(conn, vault_year, doc_type or "other", file.filename, doc_file_bytes,
                      issuer=data.get("issuer"),
                      description=_DOC_TYPE_LABELS.get(doc_type, "Tax Document") if doc_type else "Tax Document (parse failed)",
                      parsed=not bool(parse_error))

    if parse_error:
        raise HTTPException(status_code=422, detail=f"File saved to vault but could not parse: {parse_error}")
    if not doc_type or not tax_year:
        raise HTTPException(status_code=422, detail="Could not determine document type or tax year")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO tax_docs (
                tax_year, doc_type, issuer, source_file, parsed_data,
                w2_wages, w2_fed_withheld, w2_state_withheld, w2_ss_withheld,
                w2_medicare_withheld, w2_401k, w2_hsa_employer,
                mortgage_interest, mortgage_points, property_taxes,
                interest_income, ordinary_dividends, qualified_dividends,
                cap_gains_dist, proceeds, cost_basis, net_cap_gains,
                gross_distribution, taxable_distribution, distribution_code,
                state_refund, unemployment, charitable_cash, charitable_noncash,
                hsa_distributions, hsa_contributions, fed_withheld
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            tax_year, doc_type, data.get("issuer"), file.filename,
            json.dumps(data),
            data.get("w2_wages"), data.get("w2_fed_withheld"),
            data.get("w2_state_withheld"), data.get("w2_ss_withheld"),
            data.get("w2_medicare_withheld"), data.get("w2_401k"),
            data.get("w2_hsa_employer"), data.get("mortgage_interest"),
            data.get("mortgage_points"), data.get("property_taxes"),
            data.get("interest_income"), data.get("ordinary_dividends"),
            data.get("qualified_dividends"), data.get("cap_gains_dist"),
            data.get("proceeds"), data.get("cost_basis"),
            data.get("net_cap_gains"), data.get("gross_distribution"),
            data.get("taxable_distribution"), data.get("distribution_code"),
            data.get("state_refund"), data.get("unemployment"),
            data.get("charitable_cash"), data.get("charitable_noncash"),
            data.get("hsa_distributions"), data.get("hsa_contributions"),
            data.get("fed_withheld"),
        ))

    return {
        "ok": True,
        "doc_type": doc_type,
        "doc_type_label": _DOC_TYPE_LABELS.get(doc_type, doc_type),
        "tax_year": tax_year,
        "issuer": data.get("issuer"),
        "data": data,
    }


@router.get("/docs/{year}")
async def list_tax_docs(year: int, _user: str = Depends(get_current_user)):
    """List all uploaded tax documents for a given year."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tax_docs WHERE tax_year = ? ORDER BY doc_type, issuer",
            (year,)
        ).fetchall()
    docs = []
    for r in rows:
        d = dict(r)
        d["doc_type_label"] = _DOC_TYPE_LABELS.get(d["doc_type"], d["doc_type"])
        d.pop("parsed_data", None)  # don't send raw JSON to frontend
        docs.append(d)
    return docs


@router.delete("/docs/{doc_id}")
async def delete_tax_doc(doc_id: int, _user: str = Depends(get_current_user)):
    """Remove an uploaded tax document."""
    with get_db() as conn:
        conn.execute("DELETE FROM tax_docs WHERE id = ?", (doc_id,))
    return {"ok": True}


@router.get("/draft/{year}")
async def get_draft_return(year: int, _user: str = Depends(get_current_user)):
    """Calculate a complete draft 1040 from all uploaded documents for the year.

    Aggregates W-2s, 1099s, 1098s, and charitable giving to produce
    line-by-line figures for a complete federal return.
    """
    std_ded_amount = get_standard_deduction(year)
    CHILD_CREDIT_PER_CHILD = _CHILD_CREDIT_SHARED
    NUM_CHILDREN = _NUM_CHILDREN_SHARED
    brackets = get_brackets(year)

    with get_db() as conn:
        docs = conn.execute(
            "SELECT * FROM tax_docs WHERE tax_year = ?", (year,)
        ).fetchall()
        # Also pull actual filed return if available (for comparison)
        filed = conn.execute(
            "SELECT * FROM tax_returns WHERE tax_year = ?", (year,)
        ).fetchone()

    docs = [dict(d) for d in docs]
    filed = dict(filed) if filed else None

    def _sum(field):
        return sum((d.get(field) or 0) for d in docs)

    # ── Income ────────────────────────────────────────────────────────────────
    wages = _sum("w2_wages")
    interest = _sum("interest_income")
    ordinary_div = _sum("ordinary_dividends")
    qualified_div = _sum("qualified_dividends")
    cap_gains = _sum("net_cap_gains") + _sum("cap_gains_dist")
    retirement_dist = _sum("taxable_distribution")
    state_refund = _sum("state_refund")  # only taxable if itemized prior year
    unemployment = _sum("unemployment")

    total_income = wages + interest + ordinary_div + cap_gains + retirement_dist + unemployment

    # ── Adjustments ───────────────────────────────────────────────────────────
    # HSA deduction: employee contributions (contributions - employer portion)
    hsa_contributions = _sum("hsa_contributions")
    hsa_employer = _sum("w2_hsa_employer")
    hsa_deduction = max(0, hsa_contributions - hsa_employer)
    adjustments = hsa_deduction
    agi = total_income - adjustments

    # ── Deductions ────────────────────────────────────────────────────────────
    std_ded = std_ded_amount
    mortgage_interest = _sum("mortgage_interest") + _sum("mortgage_points")
    charitable_cash = _sum("charitable_cash")
    charitable_noncash = _sum("charitable_noncash")
    property_taxes = _sum("property_taxes")
    # SALT capped at $10,000
    salt = min(10000, property_taxes + _sum("w2_state_withheld"))
    total_itemized = mortgage_interest + charitable_cash + charitable_noncash + salt

    if total_itemized > std_ded:
        deduction_method = "itemized"
        deduction_amount = total_itemized
    else:
        deduction_method = "standard"
        deduction_amount = std_ded

    taxable_income = max(0, agi - deduction_amount)

    # ── Tax calculation ───────────────────────────────────────────────────────
    gross_tax = _calc_tax(taxable_income, brackets)
    child_credit = min(NUM_CHILDREN * CHILD_CREDIT_PER_CHILD, gross_tax)
    net_tax = max(0, gross_tax - child_credit)

    # ── Withholding ───────────────────────────────────────────────────────────
    w2_withheld = _sum("w2_fed_withheld")
    other_withheld = sum(
        (d.get("fed_withheld") or 0) for d in docs
        if d.get("doc_type") != "w2"
    )
    total_withheld = w2_withheld + other_withheld

    # ── Result ────────────────────────────────────────────────────────────────
    delta = total_withheld - net_tax
    refund = round(delta) if delta >= 0 else None
    owed = round(-delta) if delta < 0 else None
    effective_rate = round(net_tax / agi * 100, 2) if agi > 0 else None

    doc_summary = {}
    for d in docs:
        dt = d["doc_type"]
        doc_summary.setdefault(dt, []).append(d.get("issuer") or d.get("source_file") or dt)

    return {
        "year": year,
        "has_docs": len(docs) > 0,
        "doc_summary": doc_summary,
        "income": {
            "wages": round(wages, 2),
            "interest": round(interest, 2),
            "ordinary_dividends": round(ordinary_div, 2),
            "qualified_dividends": round(qualified_div, 2),
            "capital_gains": round(cap_gains, 2),
            "retirement_distributions": round(retirement_dist, 2),
            "unemployment": round(unemployment, 2),
            "total": round(total_income, 2),
        },
        "adjustments": round(adjustments, 2),
        "agi": round(agi, 2),
        "deductions": {
            "method": deduction_method,
            "amount": round(deduction_amount, 2),
            "standard_deduction": std_ded,
            "itemized_breakdown": {
                "mortgage_interest": round(mortgage_interest, 2),
                "salt": round(salt, 2),
                "charitable_cash": round(charitable_cash, 2),
                "charitable_noncash": round(charitable_noncash, 2),
                "total": round(total_itemized, 2),
            },
        },
        "taxable_income": round(taxable_income, 2),
        "gross_tax": round(gross_tax, 2),
        "child_tax_credit": child_credit,
        "net_tax": round(net_tax, 2),
        "withholding": {
            "w2_withheld": round(w2_withheld, 2),
            "other_withheld": round(other_withheld, 2),
            "total": round(total_withheld, 2),
        },
        "refund": refund,
        "owed": owed,
        "effective_rate": effective_rate,
        "filed_return": filed,
    }


# ── W-4 Multi-Job Optimization Wizard ─────────────────────────────────────────

# Use shared tax constants from api/tax_calc.py
_BRACKETS = BRACKETS_BY_YEAR_AND_STATUS
_STANDARD_DEDUCTIONS = _STANDARD_DEDUCTIONS_SHARED
_CHILD_CREDIT_PER_CHILD = _CHILD_CREDIT_SHARED

_PAY_PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}


class _W4Job(BaseModel):
    employer: str
    annual_income: float
    pay_frequency: str = "biweekly"          # weekly | biweekly | semimonthly | monthly
    current_extra_per_period: float = 0.0    # current Step 4c value per period


class W4WizardInput(BaseModel):
    year: int = 2025
    filing_status: str = "married_filing_jointly"
    num_children: int = 2
    other_credits: float = 0.0      # non-child credits (education, EV, etc.)
    other_income: float = 0.0       # non-job income not subject to withholding
    extra_deductions: float = 0.0   # deductions above standard (mortgage interest, charitable…)
    jobs: list[_W4Job]


def _marginal_rate(taxable_income: float, brackets: list) -> float:
    """Return the marginal rate that applies to the last dollar of taxable_income."""
    prev = 0.0
    for ceiling, rate in brackets:
        if taxable_income <= ceiling:
            return rate
        prev = ceiling
    return brackets[-1][1]


@router.get("/w4-wizard/prefill")
async def w4_wizard_prefill(_user: str = Depends(get_current_user)):
    """Return pre-fill data for the W-4 wizard from the latest paystub per employer.

    Annualizes YTD gross from the most recent paystub so the wizard form
    can start with real numbers instead of blanks.
    """
    from datetime import date as _date

    with get_db() as conn:
        stubs = conn.execute("""
            SELECT p.* FROM paystubs p
            INNER JOIN (
                SELECT employer, MAX(pay_date) AS latest FROM paystubs GROUP BY employer
            ) l ON p.employer = l.employer AND p.pay_date = l.latest
        """).fetchall()
        w4s = conn.execute("SELECT * FROM w4s ORDER BY parsed_at DESC").fetchall()

    stubs = [dict(s) for s in stubs]
    w4s_by_employer = {}
    for w in w4s:
        wd = dict(w)
        emp = (wd.get("employer") or "").strip().lower()
        if emp not in w4s_by_employer:
            w4s_by_employer[emp] = wd

    jobs = []
    for s in stubs:
        ytd_gross = s.get("ytd_gross") or 0
        pay_date = s.get("pay_date")
        annual_income = ytd_gross

        # Annualize from YTD if we have a pay date
        if pay_date and ytd_gross:
            try:
                pd = _date.fromisoformat(pay_date)
                year_start = _date(pd.year, 1, 1)
                year_end = _date(pd.year, 12, 31)
                days_elapsed = (pd - year_start).days + 1
                days_in_year = (year_end - year_start).days + 1
                frac = days_elapsed / days_in_year
                if frac > 0:
                    annual_income = round(ytd_gross / frac)
            except Exception:
                pass

        # Pull current extra withholding from most recent W-4 on file for this employer
        emp_key = (s.get("employer") or "").strip().lower()
        current_extra = 0.0
        w4_match = w4s_by_employer.get(emp_key)
        if w4_match:
            # W-4 extra_withholding is per-period already
            current_extra = float(w4_match.get("extra_withholding") or 0)

        jobs.append({
            "employer": s.get("employer") or "Unknown",
            "annual_income": annual_income,
            "pay_frequency": "biweekly",   # default — user can correct
            "current_extra_per_period": current_extra,
            "ytd_gross": round(ytd_gross),
            "pay_date": pay_date,
        })

    return {"jobs": jobs}


@router.post("/w4-wizard")
async def w4_wizard(body: W4WizardInput, _user: str = Depends(get_current_user)):
    """Calculate optimal W-4 Step 4c (extra withholding per period) for each job.

    How the math works
    ------------------
    Each employer withholds federal income tax based ONLY on the income they pay.
    With multiple jobs, each employer independently applies the standard deduction
    and (if Step 3 is filled in) the dependent credit — causing double-counting.
    The result is under-withholding: you owe at filing.

    The wizard calculates:
      1. Total household tax on combined income.
      2. What each employer would withhold in isolation (single-job simulation).
      3. The gap between those two numbers.
      4. How to distribute the gap as extra withholding (Step 4c) across jobs.

    Dependent credits are recommended only on the HIGHEST-paying job to avoid
    double-claiming. Extra withholding is distributed proportionally to income so
    no single paycheck is hit harder than necessary.
    """
    year = body.year
    fs = body.filing_status
    jobs = body.jobs

    if not jobs:
        raise HTTPException(status_code=400, detail="At least one job required")

    brackets = _BRACKETS.get(year, _BRACKETS[2025]).get(fs)
    if not brackets:
        raise HTTPException(status_code=400, detail=f"Unsupported filing status: {fs}")

    std_deduction = _STANDARD_DEDUCTIONS.get(year, _STANDARD_DEDUCTIONS[2025]).get(fs, 30000)
    dependent_credits = min(body.num_children * _CHILD_CREDIT_PER_CHILD, 2000 * body.num_children)

    # ── Step 1: Total household tax ───────────────────────────────────────────
    total_job_income = sum(j.annual_income for j in jobs)
    total_income = total_job_income + body.other_income

    # Deduction: standard + any user-provided extra (mortgage interest, charitable, etc.)
    total_deduction = std_deduction + body.extra_deductions
    taxable_income = max(0.0, total_income - total_deduction)

    gross_tax = _calc_tax(taxable_income, brackets)
    total_credits = dependent_credits + body.other_credits
    net_tax = max(0.0, gross_tax - total_credits)
    effective_rate = round(net_tax / total_income * 100, 2) if total_income > 0 else 0
    marginal_rate = _marginal_rate(taxable_income, brackets) * 100

    # ── Step 2: Per-job withholding in isolation ──────────────────────────────
    # Sort jobs by income descending so we know which is "highest" for dependent claim
    sorted_jobs = sorted(jobs, key=lambda j: j.annual_income, reverse=True)

    job_auto_withholding = []
    for idx, job in enumerate(sorted_jobs):
        # Only the highest-income job claims dependent credits (Step 3)
        credits_this_job = dependent_credits if idx == 0 else 0.0
        job_taxable = max(0.0, job.annual_income - std_deduction)
        job_gross_tax = _calc_tax(job_taxable, brackets)
        job_net_tax = max(0.0, job_gross_tax - credits_this_job)
        job_auto_withholding.append(job_net_tax)

    total_auto_withheld = sum(job_auto_withholding)

    # ── Step 3: Gap and distribution ─────────────────────────────────────────
    # Positive gap → under-withheld → need more Step 4c
    # Negative gap → over-withheld → can reduce withholding
    gap = net_tax - total_auto_withheld

    # Distribute gap proportionally to each job's share of total income
    recommendations = []
    for idx, job in enumerate(sorted_jobs):
        periods = _PAY_PERIODS.get(job.pay_frequency, 26)
        income_share = job.annual_income / total_job_income if total_job_income > 0 else 1.0
        extra_annual_needed = gap * income_share
        extra_per_period_needed = extra_annual_needed / periods if periods > 0 else 0

        # Net recommended = needed on top of whatever is currently in Step 4c
        total_recommended_per_period = max(0.0, round(extra_per_period_needed, 2))
        change = round(total_recommended_per_period - job.current_extra_per_period, 2)

        # Step 3 dependent credit recommendation
        claim_dependents_here = idx == 0  # only on highest-paying job
        recommended_step3 = dependent_credits if claim_dependents_here else 0.0

        recommendations.append({
            "employer": job.employer,
            "annual_income": job.annual_income,
            "pay_frequency": job.pay_frequency,
            "pay_periods": periods,
            "auto_withheld_annual": round(job_auto_withholding[idx], 2),
            "extra_annual_needed": round(extra_annual_needed, 2),
            "current_extra_per_period": job.current_extra_per_period,
            "recommended_extra_per_period": total_recommended_per_period,
            "change_per_period": change,
            "recommended_step3_dependents": recommended_step3,
            "claim_dependents_here": claim_dependents_here,
        })

    # ── Step 4: Notes ─────────────────────────────────────────────────────────
    notes = []
    if gap > 500:
        notes.append(f"Without changes you are on track to owe ~${round(gap):,} at filing.")
    elif gap < -500:
        notes.append(f"Without changes you are on track for a ~${round(-gap):,} refund. "
                     "Consider reducing withholding to keep more money each paycheck.")
    else:
        notes.append("Your current withholding is close to your projected tax liability.")

    if body.extra_deductions == 0 and body.other_income == 0:
        notes.append("Add mortgage interest, charitable contributions, and other non-job income "
                     "for a more accurate calculation.")

    if gap > 0:
        notes.append("Enter the recommended Step 4c amount on your W-4 for each employer. "
                     "Claim dependents (Step 3) only on your highest-paying job.")

    # Current annual withholding from Step 4c already in place
    current_extra_annual = sum(
        j.current_extra_per_period * _PAY_PERIODS.get(j.pay_frequency, 26)
        for j in jobs
    )

    return {
        "year": year,
        "filing_status": fs,
        "household": {
            "total_income": round(total_income, 2),
            "total_deduction": round(total_deduction, 2),
            "deduction_method": "itemized" if body.extra_deductions > 0 else "standard",
            "taxable_income": round(taxable_income, 2),
            "gross_tax": round(gross_tax, 2),
            "dependent_credits": round(dependent_credits, 2),
            "other_credits": round(body.other_credits, 2),
            "net_tax": round(net_tax, 2),
            "effective_rate_pct": effective_rate,
            "marginal_rate_pct": marginal_rate,
        },
        "withholding": {
            "auto_withheld_annual": round(total_auto_withheld, 2),
            "current_extra_annual": round(current_extra_annual, 2),
            "total_currently_withheld": round(total_auto_withheld + current_extra_annual, 2),
            "gap": round(gap, 2),       # positive = owe, negative = refund
            "gap_after_changes": round(gap - sum(
                r["extra_annual_needed"] for r in recommendations
            ), 2),
        },
        "recommendations": recommendations,
        "notes": notes,
    }
