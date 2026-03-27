"""
Document Vault — stores and organizes all uploaded financial documents.
Every uploaded PDF (tax docs, paystubs, W-4s, 1040s, investment statements)
is saved here. Files persist year-over-year and can be downloaded at any time.
"""
import io
import json
import os
import logging
import time
from datetime import datetime
from pathlib import Path

import anthropic
import pdfplumber
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from api.database import get_db
from api.dependencies import get_current_user

router = APIRouter()
logger = logging.getLogger("vaultic.vault")

# Gate backfill to run at most once per hour (B8 peer review fix)
_last_backfill: float = 0.0
BACKFILL_INTERVAL = 3600  # seconds

# Root storage directory — lives alongside the SQLite database
VAULT_ROOT = Path(__file__).parent.parent.parent / "data" / "vault"

CATEGORY_LABELS = {
    "tax_return":           "Tax Return (1040)",
    "w2":                   "W-2",
    "1098":                 "1098 Mortgage Interest",
    "1099_int":             "1099-INT Interest",
    "1099_div":             "1099-DIV Dividends",
    "1099_b":               "1099-B Investment Sales",
    "1099_r":               "1099-R Retirement",
    "1099_g":               "1099-G State Refund",
    "giving_statement":     "Charitable Giving Statement",
    "1098_sa":              "1098-SA HSA Distributions",
    "5498_sa":              "5498-SA HSA Contributions",
    "w4":                   "W-4 Withholding",
    "paystub":              "Pay Stub",
    "investment_statement": "Investment Statement",
    "bank_statement":       "Bank Statement",
    "insurance":            "Insurance Document",
    "other":                "Other",
}

# Documents expected each year for this household — used for checklist
EXPECTED_DOCS = [
    {"category": "w2",               "issuer": "Gusto",           "description": "W-2 — Primary employer"},
    {"category": "w2",               "issuer": "Insperity",       "description": "W-2 — Insperity employer"},
    {"category": "1098",             "issuer": "Rocket Mortgage", "description": "1098 — Mortgage interest"},
    {"category": "1099_int",         "issuer": "Chase",           "description": "1099-INT — Bank interest"},
    {"category": "giving_statement", "issuer": None,              "description": "Charitable giving statement"},
    {"category": "5498_sa",          "issuer": "Health Equity",   "description": "5498-SA — HSA contributions"},
]


def _parse_vault_metadata(file_bytes: bytes) -> dict:
    """
    Read the first 5 pages of a PDF and ask Claude Haiku to extract key metadata.
    Returns a dict with any subset of: account_name, account_holder, account_number,
    institution, period_end (YYYY-MM-DD), doc_type (matching CATEGORY_LABELS keys),
    suggested_name (clean display name for the file).
    Returns {} on any failure — caller falls back to manual fields.
    """
    try:
        pages = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages[:5]:
                t = page.extract_text()
                if t:
                    pages.append(t)
        full_text = "\n\n".join(pages)
        if not full_text.strip():
            return {}

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        prompt = f"""You are a financial document classifier. Analyze this PDF text and extract metadata.

Return ONLY a valid JSON object. Use null for any field not found.

{{
  "account_holder": <full name of account owner, e.g. "Heather A Bazen">,
  "account_name": <account/product name, e.g. "Premiere Select Roth IRA">,
  "account_number": <account number, masked is fine, e.g. "B37-601959">,
  "institution": <financial institution name, e.g. "Parker Financial" or "Chase">,
  "period_end": <statement end date as YYYY-MM-DD, e.g. "2025-03-31">,
  "doc_type": <one of: investment_statement | bank_statement | tax_return | w2 | 1099_int | 1099_div | 1099_b | 1099_r | 1099_g | 1098 | giving_statement | 1098_sa | 5498_sa | w4 | paystub | insurance | other>
}}

PDF text (first 5 pages):
{full_text[:6000]}

Return ONLY the JSON object."""

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Vault metadata parse failed: %s", exc)
        return {}


def _smart_filename(meta: dict, original: str) -> str:
    """Build a clean display filename from parsed metadata."""
    parts = []
    if meta.get("account_holder"):
        # Shorten "Heather A Bazen" → "Heather Bazen"
        name_parts = meta["account_holder"].split()
        short = f"{name_parts[0]} {name_parts[-1]}" if len(name_parts) > 1 else name_parts[0]
        parts.append(short)
    if meta.get("account_name"):
        parts.append(meta["account_name"])
    if meta.get("account_number"):
        parts.append(meta["account_number"])
    if meta.get("period_end"):
        try:
            parts.append(meta["period_end"][:7])  # YYYY-MM
        except Exception:
            pass
    if parts:
        safe = " - ".join(parts)
        # Keep only safe filename chars
        safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in safe)
        return safe.strip() + ".pdf"
    return original


def vault_path(year: int, category: str, filename: str) -> Path:
    """Return the full path where a file should be stored."""
    safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in filename)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_name = f"{ts}_{safe_name}"
    dest = VAULT_ROOT / str(year) / category
    dest.mkdir(parents=True, exist_ok=True)
    return dest / unique_name


def save_to_vault(
    conn,
    year: int,
    category: str,
    original_name: str,
    file_bytes: bytes,
    issuer: str = None,
    description: str = None,
    parsed: bool = False,
    related_id: int = None,
    related_table: str = None,
) -> int:
    """Save a file to the vault and insert a database record. Returns vault doc id."""
    dest = vault_path(year, category, original_name)
    dest.write_bytes(file_bytes)
    label = CATEGORY_LABELS.get(category, category)
    cur = conn.execute("""
        INSERT INTO vault_documents (
            year, category, category_label, issuer, description,
            original_name, file_path, file_size, parsed, related_id, related_table
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        year, category, label, issuer, description,
        original_name, str(dest), len(file_bytes),
        1 if parsed else 0, related_id, related_table,
    ))
    logger.info("Vault: saved %s → %s (id=%s)", original_name, dest, cur.lastrowid)
    return cur.lastrowid


def backfill_vault(conn):
    """Create vault records for existing parsed data that predates the vault.
    Files won't be downloadable (no PDF stored), but they show up in the vault
    and satisfy the checklist. Safe to call multiple times — skips duplicates.
    """
    # tax_docs → vault
    rows = conn.execute("SELECT * FROM tax_docs").fetchall()
    for r in rows:
        r = dict(r)
        existing = conn.execute(
            "SELECT id FROM vault_documents WHERE related_table='tax_docs' AND related_id=?",
            (r["id"],)
        ).fetchone()
        if not existing:
            label = CATEGORY_LABELS.get(r.get("doc_type", ""), r.get("doc_type", "other"))
            conn.execute("""
                INSERT OR IGNORE INTO vault_documents
                (year, category, category_label, issuer, description, original_name,
                 file_path, file_size, parsed, related_id, related_table)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r.get("tax_year"), r.get("doc_type", "other"), label,
                r.get("issuer"), label,
                r.get("source_file") or f"{label}.pdf",
                "", 0, 1, r["id"], "tax_docs",
            ))

    # paystubs → vault
    rows = conn.execute("SELECT * FROM paystubs").fetchall()
    for r in rows:
        r = dict(r)
        existing = conn.execute(
            "SELECT id FROM vault_documents WHERE related_table='paystubs' AND related_id=?",
            (r["id"],)
        ).fetchone()
        if not existing:
            pay_year = int(r["pay_date"][:4]) if r.get("pay_date") else 0
            if pay_year:
                conn.execute("""
                    INSERT OR IGNORE INTO vault_documents
                    (year, category, category_label, issuer, description, original_name,
                     file_path, file_size, parsed, related_id, related_table)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    pay_year, "paystub", "Pay Stub", r.get("employer"),
                    f"Pay stub — {r.get('employer')} {r.get('pay_date', '')}",
                    r.get("source_file") or "paystub.pdf",
                    "", 0, 1, r["id"], "paystubs",
                ))

    # w4s → vault
    rows = conn.execute("SELECT * FROM w4s").fetchall()
    for r in rows:
        r = dict(r)
        existing = conn.execute(
            "SELECT id FROM vault_documents WHERE related_table='w4s' AND related_id=?",
            (r["id"],)
        ).fetchone()
        if not existing:
            eff = r.get("effective_date", "")
            w4_year = int(eff[:4]) if eff and eff[:4].isdigit() else datetime.now().year
            conn.execute("""
                INSERT OR IGNORE INTO vault_documents
                (year, category, category_label, issuer, description, original_name,
                 file_path, file_size, parsed, related_id, related_table)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                w4_year, "w4", "W-4 Withholding", r.get("employer"),
                f"W-4 — {r.get('employer')}",
                r.get("source_file") or "w4.pdf",
                "", 0, 1, r["id"], "w4s",
            ))

    # tax_returns (filed 1040s) → vault
    rows = conn.execute("SELECT * FROM tax_returns").fetchall()
    for r in rows:
        r = dict(r)
        existing = conn.execute(
            "SELECT id FROM vault_documents WHERE related_table='tax_returns' AND related_id=?",
            (r["id"],)
        ).fetchone()
        if not existing:
            conn.execute("""
                INSERT OR IGNORE INTO vault_documents
                (year, category, category_label, issuer, description, original_name,
                 file_path, file_size, parsed, related_id, related_table)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r.get("tax_year"), "tax_return", "Tax Return (1040)", "IRS",
                f"{r.get('tax_year')} Form 1040",
                r.get("source_file") or f"1040_{r.get('tax_year')}.pdf",
                "", 0, 1, r["id"], "tax_returns",
            ))


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/backfill")
async def trigger_backfill(_user: str = Depends(get_current_user)):
    """Backfill vault records from existing parsed data (tax_docs, paystubs, w4s, tax_returns)."""
    with get_db() as conn:
        backfill_vault(conn)
    return {"ok": True}


@router.get("/years")
async def list_years(_user: str = Depends(get_current_user)):
    """Return all years that have documents in the vault. Auto-backfills periodically."""
    global _last_backfill
    with get_db() as conn:
        if time.time() - _last_backfill > BACKFILL_INTERVAL:
            backfill_vault(conn)
            _last_backfill = time.time()
        rows = conn.execute(
            "SELECT DISTINCT year FROM vault_documents ORDER BY year DESC"
        ).fetchall()
    return [r["year"] for r in rows]


@router.get("/documents/{year}")
async def list_documents(year: int, _user: str = Depends(get_current_user)):
    """Return all vault documents for a year, grouped by category."""
    global _last_backfill
    with get_db() as conn:
        if time.time() - _last_backfill > BACKFILL_INTERVAL:
            backfill_vault(conn)
            _last_backfill = time.time()
        rows = conn.execute(
            "SELECT * FROM vault_documents WHERE year = ? ORDER BY category, uploaded_at DESC",
            (year,)
        ).fetchall()
    has_file = {}
    docs = []
    for r in rows:
        d = dict(r)
        d["has_file"] = bool(d.get("file_path") and Path(d["file_path"]).exists())
        d.pop("file_path", None)
        docs.append(d)
    return docs


@router.get("/checklist/{year}")
async def get_checklist(year: int, _user: str = Depends(get_current_user)):
    """Return expected documents for the year with received/missing status."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT category, issuer FROM vault_documents WHERE year = ?", (year,)
        ).fetchall()

    received_categories = {r["category"] for r in rows}
    received_issuers = {(r["category"], r["issuer"]) for r in rows}

    checklist = []
    for item in EXPECTED_DOCS:
        cat = item["category"]
        issuer = item["issuer"]
        # Consider received if category matches (issuer matching is best-effort)
        received = cat in received_categories
        checklist.append({
            **item,
            "category_label": CATEGORY_LABELS.get(cat, cat),
            "received": received,
        })

    return {
        "year": year,
        "checklist": checklist,
        "received_count": sum(1 for c in checklist if c["received"]),
        "total_count": len(checklist),
    }


@router.get("/deductions/{year}")
async def get_deduction_tracker(year: int, _user: str = Depends(get_current_user)):
    """Scan transactions and uploaded giving statements for charitable deductions."""
    with get_db() as conn:
        # Sum giving statements uploaded to vault/tax_docs
        doc_total = conn.execute("""
            SELECT COALESCE(SUM(charitable_cash), 0) AS cash,
                   COALESCE(SUM(charitable_noncash), 0) AS noncash
            FROM tax_docs WHERE tax_year = ? AND doc_type = 'giving_statement'
        """, (year,)).fetchone()

        # Transactions assigned to budget items in charitable/giving groups
        tx_rows = conn.execute("""
            SELECT t.merchant_name, t.amount, t.date, bi.name AS item_name, bg.name AS group_name
            FROM transactions t
            JOIN transaction_assignments ta ON t.transaction_id = ta.transaction_id
            JOIN budget_items bi ON ta.item_id = bi.id
            JOIN budget_groups bg ON bi.group_id = bg.id
            WHERE (LOWER(bg.name) LIKE '%charit%' OR LOWER(bg.name) LIKE '%giving%'
                   OR LOWER(bg.name) LIKE '%donat%' OR LOWER(bi.name) LIKE '%charit%'
                   OR LOWER(bi.name) LIKE '%donat%' OR LOWER(bi.name) LIKE '%giving%'
                   OR LOWER(bi.name) LIKE '%church%' OR LOWER(bi.name) LIKE '%tithe%')
              AND strftime('%Y', t.date) = ?
              AND (t.budget_deleted IS NULL OR t.budget_deleted = 0)
            ORDER BY t.date DESC
        """, (str(year),)).fetchall()

        # Prior year for comparison
        prior_doc = conn.execute("""
            SELECT COALESCE(SUM(charitable_cash + charitable_noncash), 0) AS total
            FROM tax_docs WHERE tax_year = ?
        """, (year - 1,)).fetchone()

        prior_tax = conn.execute(
            "SELECT charitable_cash, charitable_noncash FROM tax_returns WHERE tax_year = ?",
            (year - 1,)
        ).fetchone()

    tx_total = sum(abs(r["amount"]) for r in tx_rows if r["amount"] > 0)
    doc_cash = doc_total["cash"] or 0
    doc_noncash = doc_total["noncash"] or 0
    statement_total = doc_cash + doc_noncash

    prior_year_total = 0
    if prior_tax:
        prior_year_total = (prior_tax["charitable_cash"] or 0) + (prior_tax["charitable_noncash"] or 0)
    elif prior_doc:
        prior_year_total = prior_doc["total"] or 0

    return {
        "year": year,
        "from_giving_statements": round(statement_total, 2),
        "from_transactions": round(tx_total, 2),
        "combined_estimate": round(max(statement_total, tx_total), 2),
        "prior_year_total": round(prior_year_total, 2),
        "transactions": [dict(r) for r in tx_rows],
    }


@router.post("/upload")
async def upload_to_vault(
    file: UploadFile = File(...),
    year: int = Form(0),
    category: str = Form("other"),
    issuer: str = Form(None),
    description: str = Form(None),
    auto_parse: bool = Form(False),
    _user: str = Depends(get_current_user),
):
    """Upload any document to the vault. When auto_parse=true (default), Claude reads
    the PDF and fills in year, category, account name, and renames the file automatically."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    file_bytes = await file.read()
    display_name = file.filename
    meta = {}

    if auto_parse:
        meta = _parse_vault_metadata(file_bytes)
        logger.info("Vault auto-parse meta: %s", meta)

        # Use detected doc_type as category if not explicitly set
        if not category or category == "other":
            detected = meta.get("doc_type", "other")
            category = detected if detected in CATEGORY_LABELS else "other"

        # Use detected period_end year if year not explicitly set
        if not year and meta.get("period_end"):
            try:
                year = int(meta["period_end"][:4])
            except Exception:
                pass

        # Fill issuer from institution
        if not issuer and meta.get("institution"):
            issuer = meta["institution"]

        # Build description from account info
        if not description:
            desc_parts = []
            if meta.get("account_name"):
                desc_parts.append(meta["account_name"])
            if meta.get("account_number"):
                desc_parts.append(meta["account_number"])
            if meta.get("period_end"):
                try:
                    dt = datetime.strptime(meta["period_end"], "%Y-%m-%d")
                    desc_parts.append(dt.strftime("%B %Y"))
                except Exception:
                    desc_parts.append(meta["period_end"][:7])
            if desc_parts:
                description = " — ".join(desc_parts)

        # Generate smart display filename
        display_name = _smart_filename(meta, file.filename)

    if not year:
        year = datetime.now().year
    if category not in CATEGORY_LABELS:
        category = "other"

    with get_db() as conn:
        vault_id = save_to_vault(
            conn, year, category, display_name,
            file_bytes, issuer=issuer, description=description, parsed=False,
        )
    return {
        "ok": True,
        "vault_id": vault_id,
        "year": year,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "display_name": display_name,
        "detected": meta if auto_parse else {},
    }


@router.get("/download/{doc_id}")
async def download_document(doc_id: int, _user: str = Depends(get_current_user)):
    """Download a vault document by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM vault_documents WHERE id = ?", (doc_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        path=str(path),
        filename=row["original_name"],
        media_type="application/pdf",
    )


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, _user: str = Depends(get_current_user)):
    """Delete a vault document and its file from disk."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM vault_documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        path = Path(row["file_path"])
        if path.exists():
            path.unlink()
        conn.execute("DELETE FROM vault_documents WHERE id = ?", (doc_id,))
    return {"ok": True}
