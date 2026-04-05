"""
Investor360 integration — sync Parker Financial / Commonwealth / NFS data.

Endpoints for syncing portfolio data from Investor360's internal JSON API,
retrieving stored snapshots, and monitoring sync health.
"""
import asyncio
import json
import logging
import time
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_current_user
from api.database import get_db
from api.investor360_client import (
    BASE_URL,
    Investor360Client,
    EndpointChangedError,
    SessionExpiredError,
    detect_api_versions,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/investor360", tags=["investor360"])


# ── Request/Response models ────────────────────────────────────────────────

class SyncRequest(BaseModel):
    session_cookie: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _today() -> date:
    return date.today()


def _get_household_id(conn) -> int | None:
    """Get household ID from existing account map, or None for first sync."""
    row = conn.execute(
        "SELECT household_id FROM i360_account_map LIMIT 1"
    ).fetchone()
    return row["household_id"] if row else None


def _upsert_accounts(conn, account_list: dict, holdings_data: dict) -> dict:
    """Create/update Vaultic accounts and i360_account_map from I360 data.

    Returns mapping of i360_account_id -> vaultic account_id.
    """
    mapping = {}

    # Build lookup from holdings data (has account details)
    acct_details = {}
    for acct in holdings_data.get("data", []):
        acct_details[acct["accountId"]] = acct

    # Walk the account list to find all accounts
    all_accounts = []
    for item in account_list.get("accountListItems", []):
        if item.get("accountsList"):
            all_accounts.extend(item["accountsList"])

    for acct in all_accounts:
        # Skip closed accounts
        if acct.get("isClosed"):
            continue
        i360_id = acct["id"]
        acct_number = acct.get("accountNumber", "")
        name = acct.get("name", "")
        reg_type = acct.get("registrationType", "")
        reg_group = acct.get("registrationGroup", "")
        source = acct.get("source", "NFS")
        mask = acct_number[-4:] if acct_number else ""
        inception = acct.get("minInceptionDate", "")
        open_date = inception[:10] if inception else None

        # Determine subtype from registration
        subtype_map = {
            "ROTH": "roth_ira",
            "IRRL": "ira_rollover",
            "TODJ": "brokerage",
        }
        subtype = subtype_map.get(reg_type, "brokerage")

        # Check if we already have this mapped
        existing = conn.execute(
            "SELECT account_id FROM i360_account_map WHERE i360_account_id = ?",
            (i360_id,),
        ).fetchone()

        if existing:
            vaultic_id = existing["account_id"]
        else:
            # Create new account in main accounts table
            # Use a synthetic plaid_account_id to satisfy UNIQUE constraint
            plaid_acct_id = f"i360_{i360_id}"
            conn.execute(
                """INSERT OR IGNORE INTO accounts
                   (plaid_account_id, name, display_name, mask, type, subtype,
                    institution_name, is_manual, source, account_number)
                   VALUES (?, ?, ?, ?, 'investment', ?, ?, 0, 'investor360', ?)""",
                (plaid_acct_id, name, name, mask, subtype,
                 "Parker Financial (Commonwealth/NFS)", acct_number),
            )
            row = conn.execute(
                "SELECT id FROM accounts WHERE plaid_account_id = ?",
                (plaid_acct_id,),
            ).fetchone()
            vaultic_id = row["id"]

            # Determine household_id from the account list
            household_id = None
            for item in account_list.get("accountListItems", []):
                if item.get("groupType") == "Household":
                    household_id = item.get("id")
                    break

            # Create account map entry
            conn.execute(
                """INSERT OR IGNORE INTO i360_account_map
                   (account_id, i360_account_id, account_number, household_id,
                    registration_type, registration_group,
                    registration_description, business_line,
                    investment_objective, open_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (vaultic_id, i360_id, acct_number, household_id or 0,
                 reg_type, reg_group,
                 acct.get("registrationType", ""),
                 "",  # business_line filled from holdings if available
                 "",  # investment_objective filled from holdings if available
                 open_date),
            )

        mapping[i360_id] = vaultic_id

    return mapping


def _store_holdings(conn, holdings_data: dict, acct_mapping: dict, today: date):
    """Store full holdings snapshot."""
    # Build vaultic_id -> account_number lookup
    acct_nums = {}
    for row in conn.execute(
        "SELECT id, account_number FROM accounts WHERE source = 'investor360'"
    ).fetchall():
        acct_nums[row["id"]] = row["account_number"]

    count = 0
    for acct in holdings_data.get("data", []):
        i360_id = acct["accountId"]
        vaultic_id = acct_mapping.get(i360_id)
        if not vaultic_id:
            continue
        acct_number = acct_nums.get(vaultic_id)
        for sec in acct.get("securities", []):
            for h in sec.get("holdings", []):
                conn.execute(
                    """INSERT OR REPLACE INTO i360_holdings
                       (account_id, snapped_at, symbol, cusip, description,
                        product_type, quantity, price, value_dollars,
                        accrued_interest, assets_percentage,
                        asset_type, asset_sub_type, asset_category,
                        primary_asset_class, position_type,
                        est_tax_cost_dollars, est_tax_cost_gain_loss_dollars,
                        est_tax_cost_gain_loss_pct, est_unit_tax_cost,
                        principal_dollars, principal_gain_loss_dollars,
                        principal_gain_loss_pct, unit_principal_cost,
                        previous_day_value, one_day_price_change_pct,
                        one_day_value_change_dollars, one_day_value_change_pct,
                        estimated_annual_income, current_yield_pct,
                        dividend_instructions, cap_gain_instructions,
                        initial_purchase_date, is_core, intraday,
                        i360_holding_id, i360_product_id, account_number)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        vaultic_id, today.isoformat(),
                        h.get("symbol"), h.get("cusip"), h.get("description", ""),
                        h.get("productType"), h.get("quantity"), h.get("price"),
                        h.get("valueDollars"),
                        h.get("accruedInterest", 0), h.get("assetsPercentage"),
                        h.get("assetType"), h.get("assetSubType"),
                        h.get("assetCategory"), h.get("primaryAssetClass"),
                        h.get("positionType"),
                        h.get("estTaxCostDollars"),
                        h.get("estTaxCostGainLossDollars"),
                        h.get("estTaxCostGainLossPercent"),
                        h.get("estUnitTaxCost"),
                        h.get("principalDollars"),
                        h.get("principalGainLossDollars"),
                        h.get("principalGainLossPercent"),
                        h.get("unitPrincipalCost"),
                        h.get("previousDayValue"),
                        h.get("oneDayPriceChangePercent"),
                        h.get("oneDayValueChangeDollars"),
                        h.get("oneDayValueChangePercent"),
                        h.get("estimatedAnnualIncomeDollars"),
                        h.get("currentYieldDistributionRatePercent"),
                        h.get("dividendInstructions"),
                        h.get("capGainInstructions"),
                        h.get("initialPurchaseDate"),
                        1 if h.get("isCore") else 0,
                        1 if h.get("intraday") else 0,
                        h.get("holdingId"), h.get("productId"),
                        acct_number,
                    ),
                )
                count += 1
    return count


def _store_account_balances(
    conn, balances_data: dict, acct_mapping: dict, today: date
):
    """Store per-account balance snapshot + feed net worth pipeline."""
    # Build i360_account_id -> vaultic_id from account_number
    acct_num_to_vaultic = {}
    for row in conn.execute(
        "SELECT account_id, i360_account_id, account_number FROM i360_account_map"
    ).fetchall():
        acct_num_to_vaultic[row["account_number"]] = row["account_id"]
        acct_num_to_vaultic[row["i360_account_id"]] = row["account_id"]

    for bal in balances_data.get("accountBalances", []):
        acct_num = bal.get("accountNumber", "")
        i360_id = bal.get("cfnAccountId")
        vaultic_id = acct_num_to_vaultic.get(acct_num) or acct_num_to_vaultic.get(i360_id)
        if not vaultic_id:
            continue

        market_val = bal.get("accountMarketValue", 0)
        cash_val = bal.get("accountCashValue", 0)
        todays_chg = bal.get("todaysChange", 0)
        total_val = bal.get("totalMarketValue", 0)

        # Look up account_number for this vaultic_id
        acct_num_row = conn.execute(
            "SELECT account_number FROM accounts WHERE id = ?", (vaultic_id,)
        ).fetchone()
        acct_number = acct_num_row["account_number"] if acct_num_row else None

        # Store in i360-specific table
        conn.execute(
            """INSERT OR REPLACE INTO i360_account_balances
               (account_id, snapped_at, market_value, cash_value,
                todays_change, total_portfolio_value, account_number)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (vaultic_id, today.isoformat(), market_val, cash_val,
             todays_chg, total_val, acct_number),
        )

        # Also feed the main account_balances table for net worth pipeline
        conn.execute(
            """INSERT OR REPLACE INTO account_balances
               (account_id, current, available, snapped_at, account_number)
               VALUES (?, ?, ?, ?, ?)""",
            (vaultic_id, market_val, cash_val, today.isoformat(), acct_number),
        )


def _store_performance(conn, perf_data: list, today: date):
    """Store TWR performance returns with benchmarks."""
    for entry in perf_data:
        if entry.get("hideMe"):
            continue
        period = entry.get("timePeriod", "")
        display = entry.get("displayName", "")
        portfolio = float(entry.get("portfolio", 0))

        # Extract benchmarks by name
        sp500 = bond = tbill = None
        for bm in entry.get("benchmarks", []):
            name = bm.get("benchmarkName", "")
            val = float(bm.get("benchmarkValue", 0))
            if "S&P" in name or "SP" in name:
                sp500 = val
            elif "Aggregate" in name or "Bond" in name:
                bond = val
            elif "Treasury" in name or "Bill" in name:
                tbill = val

        conn.execute(
            """INSERT OR REPLACE INTO i360_performance
               (snapped_at, time_period, display_name, portfolio_return,
                benchmark_sp500, benchmark_bond, benchmark_tbill)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (today.isoformat(), period, display, portfolio,
             sp500, bond, tbill),
        )


def _store_asset_allocation(conn, alloc_data: dict, today: date):
    """Store asset allocation breakdown."""
    for entry in alloc_data.get("assetBalances", []):
        conn.execute(
            """INSERT OR REPLACE INTO i360_asset_allocation
               (snapped_at, asset_name, market_value)
               VALUES (?, ?, ?)""",
            (today.isoformat(), entry["assetName"], entry["marketValue"]),
        )


def _store_balance_history(conn, history_data: dict):
    """Store monthly balance history (append-only, inception to present)."""
    for entry in history_data.get("portfolioGrowths", []):
        bal_date = entry["balanceDate"][:10]  # "2019-08-30T00:00:00" -> "2019-08-30"
        conn.execute(
            """INSERT OR REPLACE INTO i360_balance_history
               (balance_date, market_value, net_investment)
               VALUES (?, ?, ?)""",
            (bal_date, entry["marketValue"], entry["netInvestment"]),
        )


def _store_activity_summary(conn, activity_data: list, today: date, start_date: str):
    """Store period activity summary."""
    if not activity_data:
        return
    entry = activity_data[0]
    conn.execute(
        """INSERT OR REPLACE INTO i360_activity_summary
           (snapped_at, start_date, end_date,
            beginning_balance, ending_balance,
            net_contributions_withdrawals, positions_change_in_value,
            interest, cap_gains, management_fee, management_fees_paid,
            net_change, total_gain_loss_after_fee, credits_12b1)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            today.isoformat(), start_date, today.isoformat(),
            entry.get("beginningBalance"),
            entry.get("endingBalance"),
            entry.get("netContributionsWithdrawals"),
            entry.get("positionsChangeInValue"),
            entry.get("interest"),
            entry.get("capGains"),
            entry.get("managementFee"),
            entry.get("managementFeesPaid"),
            entry.get("netChange"),
            entry.get("totalGainLossAfterFee"),
            entry.get("credits12B1"),
        ),
    )


def _store_market_summary(conn, market_data: dict):
    """Store market index data."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for entry in market_data.get("data", []):
        conn.execute(
            """INSERT OR REPLACE INTO i360_market_summary
               (snapped_at, symbol, name, last_trade_amount,
                net_change, percent_change)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                now, entry["symbol"], entry["name"],
                entry.get("lastTradeAmount"),
                entry.get("netChange"),
                entry.get("percentChange"),
            ),
        )


def _remove_superseded_manual_entries(conn):
    """Delete invested manual_entries that I360 now covers — matched by account_number.

    Only deletes a manual_entry if its normalized account_number is in i360_account_map.
    Entries with no account_number, or account_numbers I360 doesn't cover (Insperity,
    Voya, etc.), are preserved. Never does a blanket category-wide DELETE.
    """
    from api.routers.pdf import _normalize_acct

    # Build the set of I360 account_numbers (normalized)
    i360_set = set()
    for row in conn.execute(
        "SELECT account_number FROM i360_account_map "
        "WHERE account_number IS NOT NULL AND account_number != ''"
    ).fetchall():
        normalized = _normalize_acct(row["account_number"])
        if normalized:
            i360_set.add(normalized)

    if not i360_set:
        # No I360 data — don't delete anything
        return

    # Find manual_entries that match an I360 account by account_number
    to_delete = []
    for row in conn.execute(
        "SELECT id, name, account_number FROM manual_entries "
        "WHERE category = 'invested' "
        "AND account_number IS NOT NULL AND account_number != ''"
    ).fetchall():
        normalized = _normalize_acct(row["account_number"])
        if normalized and normalized in i360_set:
            to_delete.append((row["id"], row["name"], row["account_number"]))

    for entry_id, name, acct_num in to_delete:
        conn.execute("DELETE FROM manual_entries WHERE id = ?", (entry_id,))
        logger.info(
            "Deleted manual_entry id=%d name='%s' account_number='%s' (superseded by I360)",
            entry_id, name, acct_num
        )


def _migrate_snapshot_history(conn):
    """Backfill account_balances from manual_entry_snapshots for I360 accounts.

    Matches by normalized account_number only. This preserves the PDF-imported
    balance history so the Performance tab shows historical data for I360 accounts.
    """
    from api.routers.pdf import _normalize_acct

    # Build normalized account_number -> (vaultic_id, account_number) mapping
    acct_map = {}
    for row in conn.execute(
        "SELECT account_id, account_number FROM i360_account_map "
        "WHERE account_number IS NOT NULL AND account_number != ''"
    ).fetchall():
        normalized = _normalize_acct(row["account_number"])
        if normalized:
            acct_map[normalized] = (row["account_id"], row["account_number"])

    if not acct_map:
        return

    # Find snapshots that have account_numbers matching I360 accounts
    migrated = 0
    for row in conn.execute(
        "SELECT account_number, value, snapped_at FROM manual_entry_snapshots "
        "WHERE category = 'invested' AND account_number IS NOT NULL AND account_number != ''"
    ).fetchall():
        normalized = _normalize_acct(row["account_number"])
        if not normalized:
            continue
        match = acct_map.get(normalized)
        if not match:
            continue
        vaultic_id, acct_number = match
        # Don't overwrite existing balances (today's I360 data takes precedence)
        existing = conn.execute(
            "SELECT 1 FROM account_balances WHERE account_number = ? AND snapped_at = ?",
            (acct_number, row["snapped_at"]),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO account_balances (account_id, current, snapped_at, account_number) "
            "VALUES (?, ?, ?, ?)",
            (vaultic_id, row["value"], row["snapped_at"], acct_number),
        )
        migrated += 1

    if migrated:
        logger.info("Migrated %d historical snapshots into account_balances for I360 accounts", migrated)


def _sanity_check(conn, total_value: float) -> list[str]:
    """Validate synced data against last known values."""
    warnings = []

    # Check total portfolio value against last sync
    last = conn.execute(
        """SELECT total_portfolio_value FROM i360_sync_log
           WHERE status = 'success' ORDER BY synced_at DESC LIMIT 1"""
    ).fetchone()
    if last and last["total_portfolio_value"]:
        prev = last["total_portfolio_value"]
        if prev > 0:
            change_pct = abs(total_value - prev) / prev
            if total_value == 0:
                warnings.append(
                    f"CRITICAL: Portfolio value is $0 (was ${prev:,.2f})"
                )
            elif change_pct > 0.30:
                warnings.append(
                    f"Large value change: ${prev:,.2f} -> ${total_value:,.2f} "
                    f"({change_pct:.0%})"
                )

    return warnings


# ── Sync endpoint ──────────────────────────────────────────────────────────

@router.post("/sync")
async def sync(req: SyncRequest, user=Depends(get_current_user)):
    """Sync all data from Investor360 using a session cookie."""
    start_time = time.time()
    client = Investor360Client(req.session_cookie)

    # 1. Validate session
    try:
        remaining = await client.check_session()
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Session check failed: {exc}")
    if remaining < 60:
        raise HTTPException(
            status_code=401,
            detail=f"Session expiring in {remaining}s — please re-login",
        )

    # 2. Get account list first (needed for household ID and account setup)
    try:
        account_list = await client.get_account_list()
    except SessionExpiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except EndpointChangedError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Determine household ID
    household_id = None
    for item in account_list.get("accountListItems", []):
        if item.get("groupType") == "Household":
            household_id = item.get("id")
            break
    if not household_id:
        # Fall back to DB
        with get_db() as conn:
            household_id = _get_household_id(conn)
    if not household_id:
        raise HTTPException(
            status_code=500,
            detail="Could not determine household ID from Investor360",
        )

    today = _today()
    start_date = f"{today.year}-01-01"
    errors = []
    all_warnings = list(client.warnings)

    # 3. Fire remaining API calls in parallel
    async def safe_call(name, coro):
        try:
            return name, await coro
        except (SessionExpiredError, EndpointChangedError) as exc:
            errors.append(f"{name}: {exc}")
            return name, None
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            logger.exception("I360 sync error for %s", name)
            return name, None

    results = await asyncio.gather(
        safe_call("holdings", client.get_holdings()),
        safe_call("balances", client.get_account_balances(household_id, today)),
        safe_call("performance", client.get_performance(household_id, today)),
        safe_call("allocation", client.get_asset_allocation(household_id, today)),
        safe_call("history", client.get_balance_history(household_id)),
        safe_call("activity", client.get_activity_summary(household_id, today)),
        safe_call("market", client.get_market_summary()),
    )

    data = {name: val for name, val in results}
    all_warnings.extend(client.warnings)

    # Determine status
    if all(v is None for v in data.values()):
        status = "failed"
    elif any(v is None for v in data.values()):
        status = "partial"
    else:
        status = "success"

    # 4. Store everything in DB
    holdings_count = 0
    accounts_synced = 0
    total_value = 0

    with get_db() as conn:
        # Upsert accounts
        acct_mapping = {}
        if data.get("holdings"):
            acct_mapping = _upsert_accounts(conn, account_list, data["holdings"])
            accounts_synced = len(acct_mapping)

        # Store holdings
        if data.get("holdings"):
            holdings_count = _store_holdings(
                conn, data["holdings"], acct_mapping, today
            )

        # Store account balances
        if data.get("balances"):
            _store_account_balances(conn, data["balances"], acct_mapping, today)
            # Calculate total portfolio value
            for bal in data["balances"].get("accountBalances", []):
                if bal.get("isActive"):
                    total_value = bal.get("totalMarketValue", 0)
                    break  # totalMarketValue is the same on every row

        # Store performance
        if data.get("performance"):
            _store_performance(conn, data["performance"], today)

        # Store asset allocation
        if data.get("allocation"):
            _store_asset_allocation(conn, data["allocation"], today)

        # Store balance history
        if data.get("history"):
            _store_balance_history(conn, data["history"])

        # Store activity summary
        if data.get("activity"):
            _store_activity_summary(conn, data["activity"], today, start_date)

        # Store market summary
        if data.get("market"):
            _store_market_summary(conn, data["market"])

        # Remove old manual entries now superseded by I360 live data
        if acct_mapping:
            _remove_superseded_manual_entries(conn)
            _migrate_snapshot_history(conn)

        # Sanity checks
        sanity_warnings = _sanity_check(conn, total_value)
        all_warnings.extend(sanity_warnings)

        # Detect API versions
        versions = detect_api_versions(client.urls_called)

        # Log sync
        duration = int((time.time() - start_time) * 1000)
        conn.execute(
            """INSERT INTO i360_sync_log
               (status, accounts_synced, holdings_count, total_portfolio_value,
                duration_ms, error, api_versions, schema_warnings)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                status, accounts_synced, holdings_count, total_value,
                duration, "; ".join(errors) if errors else None,
                json.dumps(versions),
                json.dumps(all_warnings) if all_warnings else None,
            ),
        )

    # Recalculate net worth now that manual entries are cleaned up
    try:
        from api.sync import _take_net_worth_snapshot
        _take_net_worth_snapshot(today.isoformat())
    except Exception as e:
        logger.warning("Net worth recalc after I360 sync failed: %s", e)

    return {
        "ok": status != "failed",
        "status": status,
        "accounts": accounts_synced,
        "holdings": holdings_count,
        "total_value": total_value,
        "duration_ms": int((time.time() - start_time) * 1000),
        "warnings": all_warnings,
        "errors": errors,
    }


# ── Status endpoint ────────────────────────────────────────────────────────

@router.get("/status")
def get_status(user=Depends(get_current_user)):
    """Last sync time, staleness, account count, health."""
    with get_db() as conn:
        last = conn.execute(
            """SELECT synced_at, status, accounts_synced, total_portfolio_value,
                      schema_warnings, error
               FROM i360_sync_log ORDER BY synced_at DESC LIMIT 1"""
        ).fetchone()
        if not last:
            return {"configured": False, "last_sync": None}

        synced_at = last["synced_at"]
        hours_ago = 999
        if synced_at:
            try:
                dt = datetime.fromisoformat(synced_at)
                hours_ago = (datetime.now() - dt).total_seconds() / 3600
            except (ValueError, TypeError):
                pass

        return {
            "configured": True,
            "last_sync": synced_at,
            "hours_since_sync": round(hours_ago, 1),
            "stale": hours_ago > 24,
            "status": last["status"],
            "accounts": last["accounts_synced"],
            "total_value": last["total_portfolio_value"],
            "healthy": last["status"] == "success",
            "warnings": json.loads(last["schema_warnings"]) if last["schema_warnings"] else [],
            "error": last["error"],
        }


# ── Data endpoints ─────────────────────────────────────────────────────────

@router.get("/holdings")
def get_holdings(user=Depends(get_current_user)):
    """All holdings from latest sync, grouped by account."""
    with get_db() as conn:
        # Get latest snapshot date
        row = conn.execute(
            "SELECT MAX(snapped_at) AS d FROM i360_holdings"
        ).fetchone()
        if not row or not row["d"]:
            return {"accounts": [], "totals": {}}

        snap_date = row["d"]

        # Get all holdings for that date
        holdings = conn.execute(
            """SELECT h.*, m.account_number, m.registration_group,
                      m.registration_type, a.name AS account_name
               FROM i360_holdings h
               JOIN i360_account_map m ON m.account_number = h.account_number
               JOIN accounts a ON a.account_number = h.account_number
               WHERE h.snapped_at = ?
               ORDER BY a.name, h.value_dollars DESC""",
            (snap_date,),
        ).fetchall()

        # Group by account
        accounts = {}
        totals = {
            "value": 0, "cost": 0, "gain_loss": 0,
            "annual_income": 0, "holdings_count": 0,
        }
        for h in holdings:
            aid = h["account_id"]
            if aid not in accounts:
                accounts[aid] = {
                    "account_id": aid,
                    "account_name": h["account_name"],
                    "account_number": h["account_number"],
                    "registration_group": h["registration_group"],
                    "registration_type": h["registration_type"],
                    "holdings": [],
                    "subtotals": {"value": 0, "cost": 0, "gain_loss": 0, "annual_income": 0},
                }
            hdict = dict(h)
            accounts[aid]["holdings"].append(hdict)

            val = h["value_dollars"] or 0
            cost = h["est_tax_cost_dollars"] or 0
            gl = h["est_tax_cost_gain_loss_dollars"] or 0
            inc = h["estimated_annual_income"] or 0

            accounts[aid]["subtotals"]["value"] += val
            accounts[aid]["subtotals"]["cost"] += cost
            accounts[aid]["subtotals"]["gain_loss"] += gl
            accounts[aid]["subtotals"]["annual_income"] += inc

            totals["value"] += val
            totals["cost"] += cost
            totals["gain_loss"] += gl
            totals["annual_income"] += inc
            totals["holdings_count"] += 1

        return {
            "snapped_at": snap_date,
            "accounts": list(accounts.values()),
            "totals": totals,
        }


@router.get("/holdings/{account_id}")
def get_account_holdings(account_id: int, user=Depends(get_current_user)):
    """Holdings for one account from latest sync."""
    with get_db() as conn:
        acct = conn.execute(
            "SELECT account_number FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not acct or not acct["account_number"]:
            return {"holdings": [], "totals": {}}

        acct_num = acct["account_number"]
        row = conn.execute(
            "SELECT MAX(snapped_at) AS d FROM i360_holdings WHERE account_number = ?",
            (acct_num,),
        ).fetchone()
        if not row or not row["d"]:
            return {"holdings": [], "totals": {}}

        holdings = conn.execute(
            """SELECT * FROM i360_holdings
               WHERE account_number = ? AND snapped_at = ?
               ORDER BY value_dollars DESC""",
            (acct_num, row["d"]),
        ).fetchall()

        total_val = sum(h["value_dollars"] or 0 for h in holdings)
        total_cost = sum(h["est_tax_cost_dollars"] or 0 for h in holdings)
        total_gl = sum(h["est_tax_cost_gain_loss_dollars"] or 0 for h in holdings)
        total_inc = sum(h["estimated_annual_income"] or 0 for h in holdings)

        return {
            "snapped_at": row["d"],
            "holdings": [dict(h) for h in holdings],
            "totals": {
                "value": total_val,
                "cost": total_cost,
                "gain_loss": total_gl,
                "annual_income": total_inc,
            },
        }


@router.get("/performance")
def get_performance(user=Depends(get_current_user)):
    """Latest TWR performance returns with benchmarks."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(snapped_at) AS d FROM i360_performance"
        ).fetchone()
        if not row or not row["d"]:
            return []
        rows = conn.execute(
            "SELECT * FROM i360_performance WHERE snapped_at = ?",
            (row["d"],),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/asset-allocation")
def get_asset_allocation(user=Depends(get_current_user)):
    """Latest asset allocation breakdown."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(snapped_at) AS d FROM i360_asset_allocation"
        ).fetchone()
        if not row or not row["d"]:
            return []
        rows = conn.execute(
            """SELECT * FROM i360_asset_allocation
               WHERE snapped_at = ? ORDER BY market_value DESC""",
            (row["d"],),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/balance-history")
def get_balance_history(user=Depends(get_current_user)):
    """Full monthly balance history from inception."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM i360_balance_history ORDER BY balance_date"
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/activity-summary")
def get_activity_summary(user=Depends(get_current_user)):
    """Latest activity summary."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(snapped_at) AS d FROM i360_activity_summary"
        ).fetchone()
        if not row or not row["d"]:
            return {}
        result = conn.execute(
            "SELECT * FROM i360_activity_summary WHERE snapped_at = ?",
            (row["d"],),
        ).fetchone()
        return dict(result) if result else {}


@router.get("/market-summary")
def get_market_summary(user=Depends(get_current_user)):
    """Latest market indices (DJI, NASDAQ, S&P500, Treasuries)."""
    with get_db() as conn:
        # Get the most recent snapshot time
        row = conn.execute(
            "SELECT MAX(snapped_at) AS d FROM i360_market_summary"
        ).fetchone()
        if not row or not row["d"]:
            return []
        rows = conn.execute(
            "SELECT * FROM i360_market_summary WHERE snapped_at = ?",
            (row["d"],),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/sync-log")
def get_sync_log(limit: int = 20, user=Depends(get_current_user)):
    """Recent sync history."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM i360_sync_log ORDER BY synced_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Bookmarklet endpoint ──────────────────────────────────────────────────

@router.get("/bookmarklet.js")
def get_bookmarklet(user=Depends(get_current_user)):
    """Return the bookmarklet JavaScript for copying Investor360 session."""
    js = (
        "javascript:void(navigator.clipboard.writeText("
        "document.cookie.match(/CFNSession=([^;]+)/)?.[1]||'')"
        ".then(()=>alert('Session token copied to clipboard! "
        "Paste it in Vaultic to sync.')))"
    )
    return {"bookmarklet": js, "instructions": [
        "Drag the bookmarklet link to your bookmarks bar (one-time setup).",
        "Log into Investor360 at https://my.investor360.com",
        "Click the bookmarklet — it copies your session token.",
        "Go to Vaultic and click 'Sync Parker Financial'.",
        "Paste the token and click Sync.",
    ]}
