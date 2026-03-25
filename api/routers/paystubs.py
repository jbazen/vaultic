"""
Paystubs module — upload and parse paystub PDFs using Claude Haiku.
Stores current-period and YTD figures for income tracking and tax projection.
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
logger = logging.getLogger("vaultic.paystubs")


def _parse_paystub_with_ai(pdf_path: str) -> dict:
    """Extract structured paystub data from a PDF using Claude Haiku."""
    text_pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:5]:
            t = page.extract_text()
            if t:
                text_pages.append(t)

    full_text = "\n\n".join(text_pages)
    if not full_text.strip():
        raise ValueError("Could not extract text from PDF")

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    prompt = f"""You are a paystub parser. Extract structured data from this employee paystub.

Return ONLY a valid JSON object with these exact fields (use null for missing values, numbers without $ or commas):

{{
  "employer": <employer/company name>,
  "pay_date": <pay date as YYYY-MM-DD>,
  "period_start": <pay period start as YYYY-MM-DD>,
  "period_end": <pay period end as YYYY-MM-DD>,
  "gross_pay": <current period gross pay>,
  "net_pay": <current period net/take-home pay>,
  "federal_income_tax": <current period federal income tax withheld>,
  "state_income_tax": <current period state income tax withheld>,
  "social_security": <current period Social Security withheld>,
  "medicare": <current period Medicare withheld>,
  "other_deductions": <current period other deductions total (health, 401k, etc)>,
  "ytd_gross": <year-to-date gross pay>,
  "ytd_federal": <year-to-date federal income tax withheld>,
  "ytd_state": <year-to-date state income tax withheld>,
  "ytd_social_security": <year-to-date Social Security withheld>,
  "ytd_medicare": <year-to-date Medicare withheld>,
  "ytd_net": <year-to-date net pay>
}}

Paystub text:
{full_text[:8000]}

Return ONLY the JSON object, no explanation."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    logger.info("Paystub raw AI response: %s", raw)
    data = json.loads(raw)
    return data


@router.get("")
async def list_paystubs(_user: str = Depends(get_current_user)):
    """Return all paystubs sorted by pay date descending."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM paystubs ORDER BY pay_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/ytd")
async def get_ytd_summary(_user: str = Depends(get_current_user)):
    """Return YTD totals from the most recent paystub per employer."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.*
            FROM paystubs p
            INNER JOIN (
                SELECT employer, MAX(pay_date) AS latest
                FROM paystubs
                GROUP BY employer
            ) latest ON p.employer = latest.employer AND p.pay_date = latest.latest
            ORDER BY p.employer
        """).fetchall()
    return [dict(r) for r in rows]


@router.post("/upload")
async def upload_paystub(
    file: UploadFile = File(...),
    _user: str = Depends(get_current_user),
):
    """Upload and parse a paystub PDF. Stores results in paystubs table."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    stub_file_bytes = await file.read()
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(stub_file_bytes)
        tmp_path = tmp.name

    parse_error = None
    data = {}
    try:
        data = _parse_paystub_with_ai(tmp_path)
    except Exception as e:
        logger.error(f"Paystub PDF parse failed: {e}")
        parse_error = str(e)
    finally:
        os.unlink(tmp_path)

    # Always vault the file
    pay_year = int(data["pay_date"][:4]) if data.get("pay_date") else __import__("datetime").datetime.now().year
    from api.routers.vault import save_to_vault
    with get_db() as conn:
        save_to_vault(conn, pay_year, "paystub", file.filename, stub_file_bytes,
                      issuer=data.get("employer"),
                      description=f"Pay stub — {data.get('employer', '')} {data.get('pay_date', '')}".strip(" —"),
                      parsed=not bool(parse_error))

    if parse_error:
        raise HTTPException(status_code=422, detail=f"File saved to vault but could not parse: {parse_error}")
    if not data.get("pay_date"):
        raise HTTPException(status_code=422, detail="File saved to vault but could not determine pay date")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO paystubs (
                employer, pay_date, period_start, period_end,
                gross_pay, net_pay, federal_income_tax, state_income_tax,
                social_security, medicare, other_deductions,
                ytd_gross, ytd_federal, ytd_state, ytd_social_security,
                ytd_medicare, ytd_net, source_file
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(employer, pay_date) DO UPDATE SET
                gross_pay=excluded.gross_pay, net_pay=excluded.net_pay,
                federal_income_tax=excluded.federal_income_tax,
                state_income_tax=excluded.state_income_tax,
                social_security=excluded.social_security,
                medicare=excluded.medicare,
                other_deductions=excluded.other_deductions,
                ytd_gross=excluded.ytd_gross, ytd_federal=excluded.ytd_federal,
                ytd_state=excluded.ytd_state,
                ytd_social_security=excluded.ytd_social_security,
                ytd_medicare=excluded.ytd_medicare, ytd_net=excluded.ytd_net,
                source_file=excluded.source_file, parsed_at=CURRENT_TIMESTAMP
        """, (
            data.get("employer"), data.get("pay_date"), data.get("period_start"),
            data.get("period_end"), data.get("gross_pay"), data.get("net_pay"),
            data.get("federal_income_tax"), data.get("state_income_tax"),
            data.get("social_security"), data.get("medicare"),
            data.get("other_deductions"), data.get("ytd_gross"),
            data.get("ytd_federal"), data.get("ytd_state"),
            data.get("ytd_social_security"), data.get("ytd_medicare"),
            data.get("ytd_net"), file.filename,
        ))
    return {"ok": True, "pay_date": data.get("pay_date"), "employer": data.get("employer"), "data": data}
