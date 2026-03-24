"""
Bulk import tax return PDFs from C:\\Users\\jbaze\\Downloads\\taxes\\

Usage (from repo root):
    TESTING=0 .venv/Scripts/python scripts/import_tax_returns.py

This script calls the same Claude Haiku parser used by the /api/tax/parse-pdf
endpoint and inserts results directly into the SQLite database. It prints a
summary table of what was parsed for each file.
"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# load_dotenv before importing api modules — they read os.environ at import time
load_dotenv()

# Add the repo root to sys.path so `api.*` imports resolve
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from api.database import init_db, get_db
from api.routers.tax import _parse_tax_pdf_with_ai

TAX_DIR = Path(r"C:\Users\jbaze\Downloads\taxes")

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt(v, prefix="$"):
    if v is None:
        return "—"
    return f"{prefix}{v:,.0f}"

def pct(v):
    if v is None:
        return "—"
    return f"{v:.1f}%"

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    init_db()

    pdfs = sorted(TAX_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDF files found in {TAX_DIR}")
        sys.exit(1)

    print(f"Found {len(pdfs)} PDF file(s) in {TAX_DIR}\n")

    results = []
    for pdf_path in pdfs:
        print(f"  Parsing: {pdf_path.name} ...", end="", flush=True)
        try:
            data = _parse_tax_pdf_with_ai(str(pdf_path))
            tax_year = data.get("tax_year")
            if not tax_year:
                print(" ERROR: could not determine tax year")
                results.append({"file": pdf_path.name, "status": "ERROR: no tax_year"})
                continue

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
                    data.get("total_itemized"), pdf_path.name,
                ))

            refund_str = fmt(data.get("refund")) if data.get("refund") else f"-{fmt(data.get('owed'))}"
            print(f" OK  ({tax_year})")
            results.append({
                "file": pdf_path.name,
                "year": tax_year,
                "agi": data.get("agi"),
                "total_tax": data.get("total_tax"),
                "eff_rate": data.get("effective_rate"),
                "deduction": f"{data.get('deduction_method','?').upper()[:4]} {fmt(data.get('deduction_amount'))}",
                "result": refund_str,
                "status": "OK",
            })

        except Exception as e:
            print(f" ERROR: {e}")
            results.append({"file": pdf_path.name, "status": f"ERROR: {e}"})

    # Summary table
    print("\n" + "=" * 90)
    print(f"{'Year':<6} {'AGI':>12} {'Total Tax':>12} {'Eff Rate':>9} {'Deduction':>20} {'Result':>12}  File")
    print("-" * 90)
    for r in results:
        if r.get("status") != "OK":
            print(f"{'?':<6} {'':>12} {'':>12} {'':>9} {'':>20} {'':>12}  {r['file']}  ← {r['status']}")
        else:
            print(
                f"{r['year']:<6} {fmt(r['agi']):>12} {fmt(r['total_tax']):>12} "
                f"{pct(r['eff_rate']):>9} {r['deduction']:>20} {r['result']:>12}  {r['file']}"
            )
    print("=" * 90)
    ok = sum(1 for r in results if r.get("status") == "OK")
    print(f"\nImported {ok} of {len(results)} tax returns successfully.")


if __name__ == "__main__":
    main()
