"""
Document Vault — stores and organizes all uploaded financial documents.
Every uploaded PDF (tax docs, paystubs, W-4s, 1040s, investment statements)
is saved here. Files persist year-over-year and can be downloaded at any time.
"""
import os
import shutil
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from api.database import get_db
from api.dependencies import get_current_user

router = APIRouter()
logger = logging.getLogger("vaultic.vault")

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


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/years")
async def list_years(_user: str = Depends(get_current_user)):
    """Return all years that have documents in the vault."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT year FROM vault_documents ORDER BY year DESC"
        ).fetchall()
    return [r["year"] for r in rows]


@router.get("/documents/{year}")
async def list_documents(year: int, _user: str = Depends(get_current_user)):
    """Return all vault documents for a year, grouped by category."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM vault_documents WHERE year = ? ORDER BY category, uploaded_at DESC",
            (year,)
        ).fetchall()
    docs = [dict(r) for r in rows]
    # Remove file_path from response (internal detail)
    for d in docs:
        d.pop("file_path", None)
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
    year: int = Form(...),
    category: str = Form(...),
    issuer: str = Form(None),
    description: str = Form(None),
    _user: str = Depends(get_current_user),
):
    """Upload any document directly to the vault without parsing."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    if category not in CATEGORY_LABELS:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category}")

    file_bytes = await file.read()
    with get_db() as conn:
        vault_id = save_to_vault(
            conn, year, category, file.filename,
            file_bytes, issuer=issuer, description=description, parsed=False,
        )
    return {
        "ok": True,
        "vault_id": vault_id,
        "year": year,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
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
