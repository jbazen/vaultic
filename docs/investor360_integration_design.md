# Investor360 Integration — Full Design

## Overview

Bring all Parker Financial / Commonwealth / NFS data into Vaultic via Investor360's internal JSON API. Session-based auth (manual login required), on-demand sync, full history tracking, breakage detection, and rich UI presentation.

---

## 1. Data Flow

```
User logs into Investor360 in browser
        |
Clicks bookmarklet -> copies CFNSession cookie to clipboard
        |
Pastes into Vaultic "Sync Parker" modal
        |
POST /api/investor360/sync
        |
Vaultic backend calls 7 Investor360 endpoints in parallel
        |
Validates response schemas + sanity checks
        |
Stores everything in i360_* tables
        |
Upserts accounts + account_balances (feeds net worth pipeline)
        |
Marks old manual_entries as exclude_from_net_worth (no double-count)
```

---

## 2. Authentication

- **Session cookie**: `CFNSession` (UUID) — obtained from manual Investor360 login
- **Session lifetime**: ~15 min idle timeout, pingable via `/Session/v2/remainingTime`
- **MFA**: Required ~monthly, handled by user during interactive login
- **Bookmarklet**: Copies CFNSession from investor360.com cookies to clipboard
- **No stored credentials**: Vaultic never stores Investor360 username/password

---

## 3. Investor360 API Endpoints Used

| Endpoint | Method | Data |
|----------|--------|------|
| `/api/trading/accounts/v2/holdings?grouping=Account&cacheHoldings=true` | GET | Full holdings — every position across all accounts (43 fields) |
| `/api/trading/accounts/v1/balances?cacheHoldings=true` | GET | Per-account market value, cash available |
| `/Applications/Reports/AccountBalances/GetAccountBalances` | POST | Per-account values, cash, today's change |
| `/Applications/Reports/AssetAllocation/GetAssetAllocation` | POST | Asset class breakdown |
| `/Applications/Reports/Performance/GetTWRPerformance` | POST | TWR returns: MTD/QTD/YTD/1/3/5yr/inception vs S&P 500, Bond, T-Bill benchmarks |
| `/Applications/Reports/BalanceHistory/GetBalanceHistory` | POST | Monthly portfolio value + net investment back to inception (2019) |
| `/Applications/Reports/ActivitySummary/RetrieveActivitySummary` | POST | Period: beginning/ending balance, contributions, withdrawals, fees |
| `/api/trading/products/v1/marketSummaries?realtime=false` | GET | DJI, NASDAQ, S&P 500, 10yr/30yr Treasury |
| `/Applications/WebServices/A360.Web.Header.Services/v1/GetAccountList` | GET | Account hierarchy: household -> groups -> individual accounts |
| `/Applications/WebServices/SecurityService/api/Session/v2/remainingTime` | GET | Session TTL in seconds |

### POST Body Templates

All POST endpoints use `householdId: 1682851` (Bazen household). Key parameters:
- `accountSelectionGroupType: 0, accountSelectionValue: 1682851, groupName: "All Accounts"`
- `asOfDate`: today's date
- `startDate` / `endDate`: varies by widget (YTD default)

---

## 4. Database Schema

### i360_sync_log
```sql
CREATE TABLE i360_sync_log (
    id                    INTEGER PRIMARY KEY,
    synced_at             DATETIME DEFAULT CURRENT_TIMESTAMP,
    status                TEXT NOT NULL,        -- success, partial, failed
    accounts_synced       INTEGER DEFAULT 0,
    holdings_count        INTEGER DEFAULT 0,
    total_portfolio_value REAL,
    duration_ms           INTEGER,
    error                 TEXT,
    api_versions          TEXT,                 -- JSON: detected URL versions
    schema_warnings       TEXT                  -- JSON: unexpected field changes
);
```

### i360_account_map
```sql
CREATE TABLE i360_account_map (
    id                       INTEGER PRIMARY KEY,
    account_id               INTEGER NOT NULL REFERENCES accounts(id),
    i360_account_id          INTEGER NOT NULL UNIQUE,   -- cfnAccountId
    account_number           TEXT NOT NULL,              -- B37705429
    household_id             INTEGER NOT NULL,           -- 1682851
    registration_type        TEXT,                       -- TODJ, ROTH, IRRL
    registration_group       TEXT,                       -- Joint, Retirement
    registration_description TEXT,
    business_line            TEXT,
    investment_objective     TEXT,
    open_date                TEXT,
    created_at               DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### i360_holdings
```sql
CREATE TABLE i360_holdings (
    id                              INTEGER PRIMARY KEY,
    account_id                      INTEGER NOT NULL REFERENCES accounts(id),
    snapped_at                      DATE NOT NULL,
    symbol                          TEXT,
    cusip                           TEXT,
    description                     TEXT NOT NULL,
    product_type                    TEXT,
    quantity                        REAL,
    price                           REAL,
    value_dollars                   REAL,
    accrued_interest                REAL DEFAULT 0,
    assets_percentage               REAL,
    asset_type                      TEXT,
    asset_sub_type                  TEXT,
    asset_category                  TEXT,
    primary_asset_class             TEXT,
    position_type                   TEXT,
    est_tax_cost_dollars            REAL,
    est_tax_cost_gain_loss_dollars  REAL,
    est_tax_cost_gain_loss_pct      REAL,
    est_unit_tax_cost               REAL,
    principal_dollars               REAL,
    principal_gain_loss_dollars     REAL,
    principal_gain_loss_pct         REAL,
    unit_principal_cost             REAL,
    previous_day_value              REAL,
    one_day_price_change_pct        REAL,
    one_day_value_change_dollars    REAL,
    one_day_value_change_pct        REAL,
    estimated_annual_income         REAL,
    current_yield_pct               REAL,
    dividend_instructions           TEXT,
    cap_gain_instructions           TEXT,
    initial_purchase_date           TEXT,
    is_core                         INTEGER DEFAULT 0,
    intraday                        INTEGER DEFAULT 1,
    i360_holding_id                 INTEGER,
    i360_product_id                 INTEGER,
    UNIQUE(account_id, snapped_at, i360_holding_id)
);
```

### i360_account_balances
```sql
CREATE TABLE i360_account_balances (
    id                    INTEGER PRIMARY KEY,
    account_id            INTEGER NOT NULL REFERENCES accounts(id),
    snapped_at            DATE NOT NULL,
    market_value          REAL,
    cash_value            REAL,
    todays_change         REAL,
    total_portfolio_value REAL,
    UNIQUE(account_id, snapped_at)
);
```

### i360_balance_history
```sql
CREATE TABLE i360_balance_history (
    id              INTEGER PRIMARY KEY,
    balance_date    DATE NOT NULL UNIQUE,
    market_value    REAL NOT NULL,
    net_investment  REAL NOT NULL
);
```

### i360_performance
```sql
CREATE TABLE i360_performance (
    id               INTEGER PRIMARY KEY,
    snapped_at       DATE NOT NULL,
    time_period      TEXT NOT NULL,
    display_name     TEXT,
    portfolio_return REAL,
    benchmark_sp500  REAL,
    benchmark_bond   REAL,
    benchmark_tbill  REAL,
    UNIQUE(snapped_at, time_period)
);
```

### i360_asset_allocation
```sql
CREATE TABLE i360_asset_allocation (
    id           INTEGER PRIMARY KEY,
    snapped_at   DATE NOT NULL,
    asset_name   TEXT NOT NULL,
    market_value REAL NOT NULL,
    UNIQUE(snapped_at, asset_name)
);
```

### i360_activity_summary
```sql
CREATE TABLE i360_activity_summary (
    id                            INTEGER PRIMARY KEY,
    snapped_at                    DATE NOT NULL,
    start_date                    DATE NOT NULL,
    end_date                      DATE NOT NULL,
    beginning_balance             REAL,
    ending_balance                REAL,
    net_contributions_withdrawals REAL,
    positions_change_in_value     REAL,
    interest                      REAL,
    cap_gains                     REAL,
    management_fee                REAL,
    management_fees_paid          REAL,
    net_change                    REAL,
    total_gain_loss_after_fee     REAL,
    credits_12b1                  REAL,
    UNIQUE(snapped_at, start_date)
);
```

### i360_market_summary
```sql
CREATE TABLE i360_market_summary (
    id                INTEGER PRIMARY KEY,
    snapped_at        DATETIME NOT NULL,
    symbol            TEXT NOT NULL,
    name              TEXT NOT NULL,
    last_trade_amount REAL,
    net_change        REAL,
    percent_change    REAL,
    UNIQUE(snapped_at, symbol)
);
```

---

## 5. Backend API Endpoints

```
POST /api/investor360/sync              -- Accepts session cookie, syncs all data
GET  /api/investor360/status            -- Last sync time, staleness, health
GET  /api/investor360/holdings          -- All holdings, latest sync
GET  /api/investor360/holdings/{id}     -- Holdings for one account
GET  /api/investor360/performance       -- TWR returns + benchmarks
GET  /api/investor360/asset-allocation  -- Asset class breakdown
GET  /api/investor360/balance-history   -- Monthly values from inception
GET  /api/investor360/activity-summary  -- Period contributions/fees
GET  /api/investor360/market-summary    -- Market indices
GET  /api/investor360/sync-log          -- Sync history
```

---

## 6. Breakage Detection

| Check | When | Action |
|-------|------|--------|
| HTTP 401/403 | Every request | Abort, return "session expired" |
| HTTP 404 | Every request | Flag endpoint as broken |
| Missing required response fields | Every sync | Hard fail that endpoint, continue others |
| New unexpected fields | Every sync | Log as info (additive = safe) |
| API version in URL changed | Between syncs | Warning in sync result |
| Portfolio value = $0 | After sync | Abort, sanity check failure |
| Portfolio value changed >30% | Between syncs | Warn but save |
| Account count mismatch | Between syncs | Warn but save |
| Session < 60s remaining | Before sync | Abort with message |

---

## 7. Frontend Components

### Dashboard
- **Market Summary Bar** — DJI, NASDAQ, S&P 500, Treasuries (top of page)
- **Stale Sync Banner** — shown when > 24h since last I360 sync
- **Parker Financial Section** — asset allocation donut, balance history chart, performance vs benchmarks, top holdings, activity summary, account breakdown

### Sync Flow
- Bookmarklet copies CFNSession to clipboard
- "Sync Parker" button opens modal with paste field
- Shows progress/results after sync

### Account Detail
- Full holdings table (43 fields)
- Dual cost basis toggle (tax vs principal)
- Per-holding daily change, yield, purchase date
- Dividend/cap gain instruction summary

---

## 8. Sage Tools

- `get_parker_holdings` — full holdings data
- `get_parker_performance` — returns vs benchmarks
- `get_parker_allocation` — asset class breakdown
- `get_market_summary` — market indices

---

## 9. Data Unique to Vaultic (not on Investor360 UI)

- Aggregated annual income across all holdings
- Yield-sorted holdings
- Dual cost basis comparison
- Position change tracking between syncs
- "Held since" analysis
- Reinvestment instruction summary
- Today's per-holding movers

---

## 10. Implementation Order

1. Database tables + migration
2. investor360_client.py (API client + schema validation)
3. routers/investor360.py (sync + data endpoints)
4. Bookmarklet + sync modal UI
5. Manual entry migration (exclude from net worth)
6. Dashboard Parker section
7. Market summary widget
8. Account detail enhancements
9. Sage tools
10. Stale sync notification
11. Tests

---

## Key Constants

- Household ID: 1682851
- Base URL: https://my.investor360.com
- Session cookie name: CFNSession
- Session idle timeout: ~15 minutes
- Accounts: 5 (2 Joint TODJ, 1 IRA Rollover IRRL, 2 Roth IRA ROTH)
- Account numbers: B37705429, B37601962, B37653447, B37601959, B37601960
