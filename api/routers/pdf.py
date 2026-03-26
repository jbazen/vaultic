"""
PDF ingestion: extract text with pdfplumber, parse with Claude Sonnet.

Flow:
  POST /api/pdf/ingest — upload PDF → extract text → Claude Sonnet → return parsed entries
  POST /api/pdf/save   — user confirms parsed entries → upsert manual_entries

Account matching uses a 4-tier strategy (see _find_existing docstring) to reliably
link PDF imports to existing entries across re-imports, renames, and PDF format changes.
Historical imports (statement date older than most recent snapshot) write to the
snapshot tables only and do not overwrite current balances.
"""
import os
import re as _re
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
- Create ONE entry per account shown. Include both individual accounts AND any portfolio-level summary entry (e.g. "Overall Portfolio"). The user will mark the summary as excluded from net worth to avoid double-counting.
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
            model="claude-sonnet-4-6",
            max_tokens=16000,
            messages=[{
                "role": "user",
                "content": f"{PARSE_PROMPT}\n\n---PDF TEXT---\n{full_text[:30000]}"
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


def _normalize_acct(raw) -> str | None:
    """Uppercase, strip all non-alphanumeric. B37-601959 == B37601959 == B37 601959."""
    if not raw:
        return None
    return _re.sub(r"[^A-Z0-9]", "", str(raw).upper()) or None


def _norm_str(s) -> str | None:
    """Lowercase + collapse whitespace for fuzzy string comparison."""
    if not s:
        return None
    return " ".join(str(s).lower().split())


def _find_existing(conn, acct_num: str | None, summary: dict, name: str, category: str,
                   pre_existing_ids: set | None = None):
    """
    Find an existing manual_entries row using multi-point matching.
    Returns (row, tier) where tier is 1–4, or (None, None) if no match.

    Tier 1 — normalized account_number exact match.
              Most reliable; survives renames, institution format changes.

    Tier 2 — institution + account_holder + last-4 of account_number.
              Three independent fields; collision is essentially impossible.
              Works across different PDF name formats for the same account.

    Tier 3 — institution + account_holder + category, only when exactly
              one candidate exists (unambiguous). Handles PDFs that don't
              print the full account number.

    Tier 4 — display name exact match. Fallback for simple manual entries
              (home value, car, credit score) that have no account numbers.

    On any match below Tier 1, writes the normalized account_number back
    to the entry (self-healing) so the next import uses Tier 1 directly.
    """
    # Tier 1: exact account_number
    if acct_num:
        row = conn.execute(
            "SELECT id, name, account_number, summary_json FROM manual_entries WHERE account_number = ?",
            (acct_num,)
        ).fetchone()
        if row:
            return row, 1

    # Tiers 2 & 3 require institution + account_holder from the incoming summary
    inc_inst   = _norm_str(summary.get("institution"))
    inc_holder = _norm_str(summary.get("account_holder"))

    if inc_inst and inc_holder:
        candidates = conn.execute(
            "SELECT id, name, account_number, summary_json FROM manual_entries WHERE category = ?",
            (category,)
        ).fetchall()

        tier2, tier3 = [], []
        for row in candidates:
            sj = json.loads(row["summary_json"]) if row["summary_json"] else {}
            row_inst   = _norm_str(sj.get("institution"))
            row_holder = _norm_str(sj.get("account_holder"))
            if not row_inst or not row_holder:
                continue
            # Institution fuzzy: "parker financial / nfs" contains "nfs" and vice-versa
            inst_match   = inc_inst in row_inst or row_inst in inc_inst
            holder_match = inc_holder == row_holder
            if not (inst_match and holder_match):
                continue

            # Skip entries created during this batch — mid-loop insertions cause
            # Tier 3 false positives when two accounts share institution + holder
            # (e.g. Heather has both a Roth IRA and IRA Rollover at Parker).
            if pre_existing_ids is not None and row["id"] not in pre_existing_ids:
                continue
            tier3.append(row)

            # Tier 2: also require last-4 of account_number to match
            if acct_num and len(acct_num) >= 4:
                last4 = acct_num[-4:]
                row_acct = _normalize_acct(row["account_number"]) or ""
                if row_acct.endswith(last4):
                    tier2.append(row)

        def _heal(row):
            if acct_num and not row["account_number"]:
                conn.execute("UPDATE manual_entries SET account_number=? WHERE id=?",
                             (acct_num, row["id"]))

        if len(tier2) == 1:
            _heal(tier2[0])
            return tier2[0], 2
        if len(tier3) == 1:
            _heal(tier3[0])
            return tier3[0], 3

    # Tier 4: display name exact match
    row = conn.execute(
        "SELECT id, name, account_number, summary_json FROM manual_entries WHERE name = ?",
        (name,)
    ).fetchone()
    if row:
        return row, 4

    return None, None


@router.post("/save")
async def save_parsed(body: SaveParsedRequest, _user: str = Depends(get_current_user)):
    """Save confirmed parsed entries. Matching uses normalized account_number with
    multi-point fallbacks. Historical imports only write snapshots."""
    from datetime import date, datetime as dt
    today = date.today().isoformat()
    saved = 0
    warnings = []

    def _f(obj, k):
        v = obj.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _parse_date(raw):
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
            try:
                return dt.strptime(str(raw), fmt).date().isoformat()
            except ValueError:
                continue
        return None

    with get_db() as conn:
        # Snapshot of entry IDs that exist before this batch. Entries inserted
        # during the loop must not be Tier 3 candidates for later entries.
        pre_existing_ids = {r[0] for r in conn.execute("SELECT id FROM manual_entries").fetchall()}

        for e in body.entries:
            name = str(e.get("name", ""))[:100]
            category = str(e.get("category", "other_asset"))
            value = float(e.get("value", 0))
            notes = str(e.get("notes", ""))[:200]
            summary = e.get("activity_summary") or {}
            summary_json = json.dumps(summary) if summary else None
            if not name:
                continue

            acct_num = _normalize_acct(summary.get("account_number"))

            # Resolve snapshot date from statement period_end. Falls back to today.
            snapshot_date = today
            raw_date = summary.get("period_end") or summary.get("ending_date")
            if _parse_date(raw_date):
                snapshot_date = _parse_date(raw_date)

            # Multi-point match against existing entries
            existing, match_tier = _find_existing(conn, acct_num, summary, name, category, pre_existing_ids)

            # If no match found but the PDF had enough identifying info, flag it.
            # This surfaces potential duplicates rather than silently creating new entries.
            if not existing and (acct_num or (summary.get("institution") and summary.get("account_holder"))):
                warnings.append(
                    f"No existing account matched '{name}' "
                    f"(acct={acct_num or 'unknown'}, "
                    f"institution={summary.get('institution') or '?'}, "
                    f"holder={summary.get('account_holder') or '?'}) — "
                    f"created new entry. Verify this is not a duplicate."
                )
                logger.warning("PDF save: no match for %s acct=%s", name, acct_num)

            # Historical: snapshot_date is older than the most recent snapshot we
            # already have for this account. Only snapshot dates matter — entered_at
            # reflects when the user saved, not the as-of date of the financial data,
            # so including it in the cutoff incorrectly blocks older-but-valid statements.
            is_historical = False
            if existing:
                latest_snap = conn.execute(
                    """SELECT MAX(snapped_at) FROM manual_entry_snapshots
                       WHERE (account_number = ? AND account_number IS NOT NULL)
                          OR (account_number IS NULL AND name = ?)""",
                    (acct_num, existing["name"])
                ).fetchone()[0]
                if latest_snap and snapshot_date < str(latest_snap)[:10]:
                    is_historical = True

            # When historical, still write current holdings if the entry has none yet
            # (e.g. manually restored entry) — empty holdings is always worse.
            force_holdings = False
            if is_historical and existing:
                existing_holdings = conn.execute(
                    "SELECT COUNT(*) FROM manual_holdings WHERE manual_entry_id=?",
                    (existing["id"],)
                ).fetchone()[0]
                if existing_holdings == 0:
                    force_holdings = True

            if is_historical:
                entry_id = existing["id"]
                canonical_name = existing["name"]
            else:
                if existing:
                    conn.execute("DELETE FROM manual_entries WHERE id = ?", (existing["id"],))
                cursor = conn.execute(
                    """INSERT INTO manual_entries
                       (name, category, value, notes, summary_json, account_number, entered_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (name, category, value, notes, summary_json, acct_num, today)
                )
                entry_id = cursor.lastrowid
                canonical_name = name
                saved += 1

            # Account-level balance snapshot — keyed by account_number when available
            # so renames don't orphan history. Clear any same-account/same-date row
            # stored under a different name before inserting.
            if acct_num:
                conn.execute(
                    "DELETE FROM manual_entry_snapshots WHERE account_number=? AND snapped_at=?",
                    (acct_num, snapshot_date)
                )
            conn.execute("""
                INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(name, snapped_at) DO UPDATE SET
                    value=excluded.value, account_number=excluded.account_number
            """, (canonical_name, acct_num, category, value, snapshot_date))

            holdings = e.get("holdings") or []

            # Current holdings replaced when this is the most recent statement,
            # or when the entry has no holdings yet (force_holdings).
            if not is_historical or force_holdings:
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
                        h.get("ticker") or None, h.get("asset_class") or None,
                        _f(h, "shares"), _f(h, "price"), _f(h, "value"), _f(h, "pct_assets"),
                        _f(h, "cost") or _f(h, "principal"),
                        _f(h, "gain_loss_dollars"), _f(h, "gain_loss_pct"),
                        _f(h, "avg_unit_cost"), _f(h, "estimated_annual_income"),
                        _f(h, "estimated_yield_pct"),
                        str(h.get("notes", "") or "")[:200] or None,
                    ))

            # Holdings snapshots — append-only, always written regardless of historical.
            for h in holdings:
                h_name = str(h.get("name", ""))[:200]
                if not h_name:
                    continue
                if acct_num:
                    conn.execute(
                        """DELETE FROM manual_holdings_snapshots
                           WHERE account_number=? AND snapped_at=? AND holding_name=?""",
                        (acct_num, snapshot_date, h_name)
                    )
                conn.execute("""
                    INSERT INTO manual_holdings_snapshots
                        (entry_name, account_number, snapped_at, holding_name, ticker, asset_class,
                         shares, price, value, cost, avg_unit_cost, gain_loss_dollars,
                         gain_loss_pct, pct_assets, estimated_annual_income, estimated_yield_pct)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(entry_name, snapped_at, holding_name) DO UPDATE SET
                        account_number=excluded.account_number,
                        shares=excluded.shares, price=excluded.price, value=excluded.value,
                        cost=excluded.cost, avg_unit_cost=excluded.avg_unit_cost,
                        gain_loss_dollars=excluded.gain_loss_dollars,
                        gain_loss_pct=excluded.gain_loss_pct, pct_assets=excluded.pct_assets,
                        estimated_annual_income=excluded.estimated_annual_income,
                        estimated_yield_pct=excluded.estimated_yield_pct
                """, (
                    canonical_name, acct_num, snapshot_date, h_name,
                    h.get("ticker") or None, h.get("asset_class") or None,
                    _f(h, "shares"), _f(h, "price"), _f(h, "value"),
                    _f(h, "cost") or _f(h, "principal"), _f(h, "avg_unit_cost"),
                    _f(h, "gain_loss_dollars"), _f(h, "gain_loss_pct"), _f(h, "pct_assets"),
                    _f(h, "estimated_annual_income"), _f(h, "estimated_yield_pct"),
                ))

    security_log.log_server_event(f"PDF_SAVED  user={_user}  entries={saved}  warnings={len(warnings)}")
    return {"status": "saved", "count": saved, "warnings": warnings}
