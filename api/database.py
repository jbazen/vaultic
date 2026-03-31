"""SQLite database setup and connection management."""
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "vaultic.db"
DB_PATH.parent.mkdir(exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active     INTEGER DEFAULT 1,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY,
    username   TEXT NOT NULL,
    ip         TEXT NOT NULL,
    success    INTEGER NOT NULL,
    attempted_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plaid_items (
    id                INTEGER PRIMARY KEY,
    item_id           TEXT UNIQUE NOT NULL,
    institution_id    TEXT,
    institution_name  TEXT,
    access_token_enc  TEXT NOT NULL,
    cursor            TEXT,
    last_synced_at    DATETIME,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id               INTEGER PRIMARY KEY,
    plaid_account_id TEXT UNIQUE,
    plaid_item_id    INTEGER REFERENCES plaid_items(id),
    name             TEXT NOT NULL,
    display_name     TEXT,
    official_name    TEXT,
    mask             TEXT,
    type             TEXT NOT NULL,
    subtype          TEXT,
    institution_name TEXT,
    is_manual        INTEGER DEFAULT 0,
    is_active        INTEGER DEFAULT 1,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS account_balances (
    id                INTEGER PRIMARY KEY,
    account_id        INTEGER NOT NULL REFERENCES accounts(id),
    current           REAL,
    available         REAL,
    limit_amount      REAL,
    iso_currency_code TEXT DEFAULT 'USD',
    snapped_at        DATE NOT NULL,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, snapped_at)
);

CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY,
    transaction_id TEXT UNIQUE NOT NULL,
    account_id     INTEGER NOT NULL REFERENCES accounts(id),
    amount         REAL NOT NULL,
    date           TEXT NOT NULL,
    name           TEXT,
    merchant_name  TEXT,
    category       TEXT,
    pending        INTEGER DEFAULT 0,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS net_worth_snapshots (
    id           INTEGER PRIMARY KEY,
    snapped_at   DATE UNIQUE NOT NULL,
    total        REAL NOT NULL,
    liquid       REAL DEFAULT 0,
    invested     REAL DEFAULT 0,
    crypto       REAL DEFAULT 0,
    real_estate  REAL DEFAULT 0,
    vehicles     REAL DEFAULT 0,
    liabilities  REAL DEFAULT 0,
    other_assets REAL DEFAULT 0,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manual_entries (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT NOT NULL,
    category              TEXT NOT NULL,
    value                 REAL NOT NULL,
    notes                 TEXT,
    summary_json          TEXT,
    entered_at            DATE NOT NULL,
    exclude_from_net_worth INTEGER DEFAULT 0,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manual_holdings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_entry_id   INTEGER NOT NULL REFERENCES manual_entries(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    ticker            TEXT,
    asset_class       TEXT,
    shares            REAL,
    price             REAL,
    value             REAL,
    pct_assets        REAL,
    principal         REAL,
    gain_loss_dollars REAL,
    gain_loss_pct     REAL,
    notes             TEXT,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plaid_securities (
    id                INTEGER PRIMARY KEY,
    security_id       TEXT UNIQUE NOT NULL,
    name              TEXT,
    ticker_symbol     TEXT,
    type              TEXT,
    close_price       REAL,
    close_price_as_of TEXT,
    iso_currency_code TEXT DEFAULT 'USD',
    cusip             TEXT,
    isin              TEXT,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plaid_holdings (
    id                       INTEGER PRIMARY KEY,
    account_id               INTEGER NOT NULL REFERENCES accounts(id),
    security_id              TEXT NOT NULL,
    institution_value        REAL,
    institution_price        REAL,
    institution_price_as_of  TEXT,
    quantity                 REAL,
    cost_basis               REAL,
    iso_currency_code        TEXT DEFAULT 'USD',
    snapped_at               DATE NOT NULL,
    created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, security_id, snapped_at)
);

CREATE TABLE IF NOT EXISTS plaid_investment_transactions (
    id                        INTEGER PRIMARY KEY,
    investment_transaction_id TEXT UNIQUE NOT NULL,
    account_id                INTEGER NOT NULL REFERENCES accounts(id),
    security_id               TEXT,
    date                      TEXT NOT NULL,
    name                      TEXT,
    quantity                  REAL,
    amount                    REAL,
    fees                      REAL,
    type                      TEXT,
    subtype                   TEXT,
    cancel_transaction_id     TEXT,
    iso_currency_code         TEXT DEFAULT 'USD',
    created_at                DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# Run these migrations on existing databases to add new columns
MIGRATIONS = [
    "ALTER TABLE accounts ADD COLUMN display_name TEXT",
    "ALTER TABLE accounts ADD COLUMN mask TEXT",
    "CREATE TABLE IF NOT EXISTS login_attempts (id INTEGER PRIMARY KEY, username TEXT NOT NULL, ip TEXT NOT NULL, success INTEGER NOT NULL, attempted_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, is_active INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
    "ALTER TABLE users ADD COLUMN two_fa_enabled INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN totp_secret TEXT",
    "ALTER TABLE users ADD COLUMN totp_pending_secret TEXT",
    "ALTER TABLE accounts ADD COLUMN source TEXT DEFAULT 'plaid'",
    "ALTER TABLE account_balances ADD COLUMN native_balance REAL",
    "ALTER TABLE account_balances ADD COLUMN unit_price REAL",
    "CREATE TABLE IF NOT EXISTS manual_holdings (id INTEGER PRIMARY KEY AUTOINCREMENT, manual_entry_id INTEGER NOT NULL REFERENCES manual_entries(id) ON DELETE CASCADE, name TEXT NOT NULL, ticker TEXT, asset_class TEXT, shares REAL, price REAL, value REAL, pct_assets REAL, principal REAL, gain_loss_dollars REAL, gain_loss_pct REAL, notes TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
    "ALTER TABLE manual_entries ADD COLUMN summary_json TEXT",
    "ALTER TABLE manual_entries ADD COLUMN exclude_from_net_worth INTEGER DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS plaid_securities (
        id INTEGER PRIMARY KEY, security_id TEXT UNIQUE NOT NULL,
        name TEXT, ticker_symbol TEXT, type TEXT, close_price REAL,
        close_price_as_of TEXT, iso_currency_code TEXT DEFAULT 'USD',
        cusip TEXT, isin TEXT, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS plaid_holdings (
        id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
        security_id TEXT NOT NULL, institution_value REAL, institution_price REAL,
        institution_price_as_of TEXT, quantity REAL, cost_basis REAL,
        iso_currency_code TEXT DEFAULT 'USD', snapped_at DATE NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_id, security_id, snapped_at))""",
    """CREATE TABLE IF NOT EXISTS plaid_investment_transactions (
        id INTEGER PRIMARY KEY, investment_transaction_id TEXT UNIQUE NOT NULL,
        account_id INTEGER NOT NULL REFERENCES accounts(id), security_id TEXT,
        date TEXT NOT NULL, name TEXT, quantity REAL, amount REAL, fees REAL,
        type TEXT, subtype TEXT, cancel_transaction_id TEXT,
        iso_currency_code TEXT DEFAULT 'USD',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""",
    # User-written description shown inline on Dashboard/Accounts pages (editable via pencil icon)
    "ALTER TABLE accounts ADD COLUMN notes TEXT",
    # Append-only balance history for PDF-imported manual entries — one row per (name, date).
    # Separate from manual_entries so re-imports don't destroy history used for performance charts.
    """CREATE TABLE IF NOT EXISTS manual_entry_snapshots (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        name      TEXT NOT NULL,
        category  TEXT NOT NULL,
        value     REAL NOT NULL,
        snapped_at DATE NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(name, snapped_at)
    )""",
    # ── Budget module ──────────────────────────────────────────────────────────
    # budget_groups: named category groups (Income, Housing, Food, etc.)
    """CREATE TABLE IF NOT EXISTS budget_groups (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        type          TEXT NOT NULL DEFAULT 'expense',
        display_order INTEGER DEFAULT 0,
        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # budget_items: individual line items within a group (Mortgage, Groceries, etc.)
    """CREATE TABLE IF NOT EXISTS budget_items (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id      INTEGER NOT NULL REFERENCES budget_groups(id) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        display_order INTEGER DEFAULT 0,
        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # budget_amounts: planned dollar amount per (item, month). YYYY-MM month key.
    """CREATE TABLE IF NOT EXISTS budget_amounts (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL REFERENCES budget_items(id) ON DELETE CASCADE,
        month   TEXT NOT NULL,
        planned REAL DEFAULT 0,
        UNIQUE(item_id, month)
    )""",
    # transaction_assignments: maps a Plaid transaction to a budget line item.
    # item_id SET NULL so deleting an item un-assigns its transactions without
    # losing the assignment row — they surface again as unassigned transactions.
    """CREATE TABLE IF NOT EXISTS transaction_assignments (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT NOT NULL UNIQUE,
        item_id        INTEGER REFERENCES budget_items(id) ON DELETE SET NULL,
        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # ── Fund Financials (sinking funds tracker) ────────────────────────────────
    # funds: named savings buckets (Vacation, Clothes, Gifts, etc.)
    """CREATE TABLE IF NOT EXISTS funds (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        description   TEXT,
        target_amount REAL,
        display_order INTEGER DEFAULT 0,
        is_active     INTEGER DEFAULT 1,
        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # fund_transactions: deposits (+) and withdrawals (-) for each fund.
    # Balance = SUM(amount) computed live — no stored balance to go stale.
    """CREATE TABLE IF NOT EXISTS fund_transactions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_id     INTEGER NOT NULL REFERENCES funds(id) ON DELETE CASCADE,
        date        TEXT NOT NULL,
        amount      REAL NOT NULL,
        description TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # ── Budget CSV import ──────────────────────────────────────────────────────
    # budget_history: transactions imported from external budget CSV exports.
    # These are NOT linked to Plaid transaction IDs — they come from a separate
    # budgeting system and live in their own table for historical spending
    # analysis and Sage queries.
    """CREATE TABLE IF NOT EXISTS budget_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        group_name  TEXT NOT NULL,
        item_id     INTEGER REFERENCES budget_items(id) ON DELETE SET NULL,
        item_name   TEXT NOT NULL,
        month       TEXT NOT NULL,
        date        TEXT NOT NULL,
        merchant    TEXT,
        amount      REAL NOT NULL,
        note        TEXT,
        type        TEXT,
        source_file TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # budget_auto_rules: merchant-to-budget-item mapping learned from imported
    # CSVs and user corrections. Used by Sage to auto-categorize incoming
    # Plaid transactions without manual assignment.
    """CREATE TABLE IF NOT EXISTS budget_auto_rules (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        merchant    TEXT NOT NULL,
        item_id     INTEGER REFERENCES budget_items(id) ON DELETE CASCADE,
        match_count INTEGER DEFAULT 1,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(merchant, item_id)
    )""",
    # transaction_splits: stores split assignments when a single Plaid transaction
    # is divided across multiple budget line items. A transaction with splits will
    # have rows here but NOT in transaction_assignments — the two are mutually
    # exclusive. When a transaction is assigned to a single item (no split),
    # transaction_assignments is used (existing behavior). When split across 2+
    # items, rows go here with a per-split amount that sums to the full txn amount.
    """CREATE TABLE IF NOT EXISTS transaction_splits (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT NOT NULL,
        item_id        INTEGER REFERENCES budget_items(id) ON DELETE SET NULL,
        amount         REAL NOT NULL,
        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(transaction_id, item_id)
    )""",
    # Add status column to transaction_assignments so rows can be distinguished
    # between manually assigned ('manual') and auto-categorized ('auto') entries.
    "ALTER TABLE transaction_assignments ADD COLUMN status TEXT DEFAULT 'manual'",
    # Add missing UNIQUE constraints — without these, INSERT OR IGNORE never fires
    # and every import creates duplicate groups/items. These are safe to run on
    # existing data only if duplicates have been removed first (see cleanup script).
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_budget_groups_name ON budget_groups(name)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_budget_items_group_name ON budget_items(group_id, name)",
    # Soft-delete flags: instead of hard-deleting groups/items (which would destroy
    # budget_amounts and auto_rules history), we set is_deleted=1. The GET /{month}
    # endpoint filters these out so deleted entries never appear in the UI, but
    # budget_history and auto-rule data remain intact for Sage queries.
    "ALTER TABLE budget_groups ADD COLUMN is_deleted INTEGER DEFAULT 0",
    "ALTER TABLE budget_items ADD COLUMN is_deleted INTEGER DEFAULT 0",
    # Confidence score (0-100) for auto-categorized assignments. Set by the sync
    # pipeline based on how many times we've seen this merchant→item pairing.
    # NULL = manually assigned (no confidence concept applies).
    "ALTER TABLE transaction_assignments ADD COLUMN confidence INTEGER DEFAULT NULL",
    # User-entered metadata for transactions — check number and free-form notes.
    # Keyed by transaction_id so it survives reassignments/splits.
    """CREATE TABLE IF NOT EXISTS transaction_metadata (
        transaction_id TEXT PRIMARY KEY,
        check_number   TEXT,
        notes          TEXT,
        updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # ── Web Push subscriptions ─────────────────────────────────────────────────
    # Stores browser PushSubscription objects so the server can send encrypted
    # Web Push notifications (RFC 8291 / VAPID) to subscribed devices.
    # Soft-delete (is_active=0) rather than hard-delete so expired subscriptions
    # can be audited and don't re-appear if re-added with the same endpoint.
    """CREATE TABLE IF NOT EXISTS push_subscriptions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint     TEXT NOT NULL UNIQUE,
        p256dh       TEXT NOT NULL,
        auth         TEXT NOT NULL,
        device_token TEXT,
        is_active    INTEGER DEFAULT 1,
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Add device_token to existing push_subscriptions rows (migration for existing installs)
    "ALTER TABLE push_subscriptions ADD COLUMN device_token TEXT",
    # Store which user subscribed so device-auth issues JWT for the correct user
    "ALTER TABLE push_subscriptions ADD COLUMN username TEXT",
    # split_rules: stores percentage-based split patterns per merchant so future
    # transactions from the same merchant can be pre-populated with the same split.
    # splits column is JSON: [{"item_id": 1, "pct": 60.0}, {"item_id": 2, "pct": 40.0}]
    # use_count increments each time the user saves a split for this merchant so the
    # most-used pattern is always kept current.
    """CREATE TABLE IF NOT EXISTS split_rules (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        merchant    TEXT NOT NULL UNIQUE,
        splits      TEXT NOT NULL,
        use_count   INTEGER DEFAULT 1,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Soft-delete transactions from the budget view without losing the Plaid record.
    # budget_deleted=1 hides the transaction from all budget queues (unassigned,
    # pending-review, assigned) and excludes it from spending totals. Use case:
    # account transfers, credits, and other non-spending items that Sage can't
    # reliably categorize and that the user wants to permanently dismiss.
    "ALTER TABLE transactions ADD COLUMN budget_deleted INTEGER DEFAULT 0",
    # ── Tax module ─────────────────────────────────────────────────────────────
    # tax_returns: one row per tax year, storing all key 1040 line items parsed
    # from uploaded PDFs via Claude Haiku. UNIQUE on tax_year so re-importing
    # a corrected PDF uses ON CONFLICT DO UPDATE to overwrite the prior parse.
    """CREATE TABLE IF NOT EXISTS tax_returns (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tax_year        INTEGER NOT NULL UNIQUE,
        filing_status   TEXT DEFAULT 'married_filing_jointly',
        -- Income
        wages_w2        REAL,
        taxable_interest REAL,
        qualified_dividends REAL,
        ordinary_dividends REAL,
        capital_gains   REAL,
        ira_distributions REAL,
        other_income    REAL,
        total_income    REAL,
        adjustments_to_income REAL,
        agi             REAL,
        -- Deductions
        deduction_method TEXT,
        deduction_amount REAL,
        qbi_deduction   REAL,
        taxable_income  REAL,
        -- Tax & Credits
        total_tax       REAL,
        child_tax_credit REAL,
        other_credits   REAL,
        total_credits   REAL,
        -- Payments & Result
        w2_withheld     REAL,
        total_payments  REAL,
        refund          REAL,
        owed            REAL,
        effective_rate  REAL,
        -- Itemized deduction breakdown
        salt_deduction  REAL,
        mortgage_interest REAL,
        charitable_cash REAL,
        charitable_noncash REAL,
        mortgage_insurance REAL,
        total_itemized  REAL,
        -- Metadata
        source_file     TEXT,
        parsed_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
        notes           TEXT
    )""",
    # tax_documents: stores metadata about uploaded tax document files.
    # parsed_data holds the raw JSON returned by Claude for audit/debug purposes.
    """CREATE TABLE IF NOT EXISTS tax_documents (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tax_year        INTEGER,
        doc_type        TEXT NOT NULL,
        filename        TEXT NOT NULL,
        file_path       TEXT NOT NULL,
        parsed_data     TEXT,
        uploaded_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",

    # paystubs: one row per uploaded paystub PDF.
    # Stores current-period and YTD figures extracted by Claude Haiku.
    """CREATE TABLE IF NOT EXISTS paystubs (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        employer                TEXT,
        pay_date                TEXT,
        period_start            TEXT,
        period_end              TEXT,
        gross_pay               REAL,
        net_pay                 REAL,
        federal_income_tax      REAL,
        state_income_tax        REAL,
        social_security         REAL,
        medicare                REAL,
        other_deductions        REAL,
        ytd_gross               REAL,
        ytd_federal             REAL,
        ytd_state               REAL,
        ytd_social_security     REAL,
        ytd_medicare            REAL,
        ytd_net                 REAL,
        source_file             TEXT,
        parsed_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(employer, pay_date)
    )""",

    # w4s: one row per W-4 on file per employer.
    # Stores key withholding elections so the W-4 optimizer knows current setup.
    """CREATE TABLE IF NOT EXISTS w4s (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        employer            TEXT,
        employee_name       TEXT,
        filing_status       TEXT,
        multiple_jobs       INTEGER DEFAULT 0,
        dependents_amount   REAL,
        other_income        REAL,
        deductions          REAL,
        extra_withholding   REAL,
        effective_date      TEXT,
        source_file         TEXT,
        parsed_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(employer, effective_date)
    )""",

    # tax_docs: universal tax document store. One row per uploaded document.
    # doc_type: w2, 1098, 1099_int, 1099_div, 1099_b, 1099_r, 1099_g,
    #           giving_statement, 1098_sa, 5498_sa
    # parsed_data: full JSON returned by Claude for the document
    # Key fields are denormalized for fast aggregation in the draft return calc.
    """CREATE TABLE IF NOT EXISTS tax_docs (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        tax_year            INTEGER NOT NULL,
        doc_type            TEXT NOT NULL,
        issuer              TEXT,
        source_file         TEXT,
        parsed_data         TEXT,
        -- W-2 fields
        w2_wages            REAL,
        w2_fed_withheld     REAL,
        w2_state_withheld   REAL,
        w2_ss_withheld      REAL,
        w2_medicare_withheld REAL,
        w2_401k             REAL,
        w2_hsa_employer     REAL,
        -- 1098 fields
        mortgage_interest   REAL,
        mortgage_points     REAL,
        property_taxes      REAL,
        -- 1099-INT fields
        interest_income     REAL,
        -- 1099-DIV fields
        ordinary_dividends  REAL,
        qualified_dividends REAL,
        cap_gains_dist      REAL,
        -- 1099-B fields (stored as JSON array in parsed_data; total here)
        proceeds            REAL,
        cost_basis          REAL,
        net_cap_gains       REAL,
        -- 1099-R fields
        gross_distribution  REAL,
        taxable_distribution REAL,
        distribution_code   TEXT,
        -- 1099-G fields
        state_refund        REAL,
        unemployment        REAL,
        -- giving statement fields
        charitable_cash     REAL,
        charitable_noncash  REAL,
        -- HSA fields (1098-SA / 5498-SA)
        hsa_distributions   REAL,
        hsa_contributions   REAL,
        -- withholding (any 1099 with box 4)
        fed_withheld        REAL,
        parsed_at           DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",

    # manual_holdings_snapshots: append-only dated holdings snapshots.
    # Every PDF import writes one row per holding per statement date so we keep
    # full historical holdings data — what was held, at what price, cost basis,
    # gain/loss — for every month we have a statement for.
    # UNIQUE on (entry_name, snapped_at, holding_name) so re-importing the same
    # statement period overwrites rather than duplicates.
    """CREATE TABLE IF NOT EXISTS manual_holdings_snapshots (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_name              TEXT NOT NULL,
        snapped_at              DATE NOT NULL,
        holding_name            TEXT NOT NULL,
        ticker                  TEXT,
        asset_class             TEXT,
        shares                  REAL,
        price                   REAL,
        value                   REAL,
        cost                    REAL,
        avg_unit_cost           REAL,
        gain_loss_dollars       REAL,
        gain_loss_pct           REAL,
        pct_assets              REAL,
        estimated_annual_income REAL,
        estimated_yield_pct     REAL,
        created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(entry_name, snapped_at, holding_name)
    )""",
    # Add new holding-level fields to manual_holdings (current-snapshot table)
    "ALTER TABLE manual_holdings ADD COLUMN avg_unit_cost REAL",
    "ALTER TABLE manual_holdings ADD COLUMN estimated_annual_income REAL",
    "ALTER TABLE manual_holdings ADD COLUMN estimated_yield_pct REAL",

    # account_number as a first-class indexed column on all manual account tables.
    # Stored normalized (uppercase, non-alphanumeric stripped) so B37-601959 == B37601959.
    # Used for matching across re-imports regardless of display name changes.
    "ALTER TABLE manual_entries ADD COLUMN account_number TEXT",
    "ALTER TABLE manual_entry_snapshots ADD COLUMN account_number TEXT",
    "ALTER TABLE manual_holdings_snapshots ADD COLUMN account_number TEXT",
    "CREATE INDEX IF NOT EXISTS idx_manual_entries_acct ON manual_entries(account_number)",
    "CREATE INDEX IF NOT EXISTS idx_manual_entry_snaps_acct ON manual_entry_snapshots(account_number)",
    "CREATE INDEX IF NOT EXISTS idx_manual_holdings_snaps_acct ON manual_holdings_snapshots(account_number)",

    # vault_documents: every uploaded file stored in the document vault.
    # The actual file lives at file_path on the server filesystem.
    # category covers both tax and non-tax documents.
    """CREATE TABLE IF NOT EXISTS vault_documents (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        year            INTEGER NOT NULL,
        category        TEXT NOT NULL,
        category_label  TEXT,
        issuer          TEXT,
        description     TEXT,
        original_name   TEXT NOT NULL,
        file_path       TEXT NOT NULL,
        file_size       INTEGER,
        parsed          INTEGER DEFAULT 0,
        related_id      INTEGER,
        related_table   TEXT,
        uploaded_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",

    # RBAC: admin flag for user management and security log access.
    # Existing users (jbazen, hbazen) are set to admin; new users default to non-admin.
    "ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0",

    # ── Financial Calendar ─────────────────────────────────────────────────────
    # financial_events: one row per calendar event per user.
    # auto_generated=1 rows are seeded by the system (tax deadlines, budget meetings).
    # all_day=1: start_dt is "YYYY-MM-DD"; all_day=0: start_dt is "YYYY-MM-DDTHH:MM:SS".
    # recurring is informational metadata — expansion into future instances is done
    # at seed time (one row per concrete occurrence) rather than on-the-fly.
    """CREATE TABLE IF NOT EXISTS financial_events (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        username             TEXT NOT NULL,
        title                TEXT NOT NULL,
        description          TEXT,
        start_dt             TEXT NOT NULL,
        end_dt               TEXT,
        all_day              INTEGER DEFAULT 1,
        event_type           TEXT NOT NULL DEFAULT 'custom',
        recurring            TEXT NOT NULL DEFAULT 'none',
        reminder_days_before INTEGER DEFAULT 3,
        auto_generated       INTEGER DEFAULT 0,
        is_active            INTEGER DEFAULT 1,
        created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Index for fast date-range queries (the most common access pattern)
    "CREATE INDEX IF NOT EXISTS idx_financial_events_user_date ON financial_events(username, start_dt, is_active)",

    # ── Refresh tokens (mobile "keep me signed in") ─────────────────────────
    # Only the SHA-256 hash of the raw token is stored — never the raw value.
    # Rotation: each /auth/refresh call revokes the old row and inserts a new one.
    # Logout: sets revoked=1 so even a stolen copy becomes useless immediately.
    """CREATE TABLE IF NOT EXISTS refresh_tokens (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE,
        username   TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked    INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",

    # ── Crypto Capital Gains Tracking ────────────────────────────────────────
    # crypto_trades: individual fills fetched from Coinbase Advanced Trade API.
    # Each row represents a single fill (partial or complete) of an order.
    # side='BUY' or 'SELL'; product_id like 'BTC-USD'.
    """CREATE TABLE IF NOT EXISTS crypto_trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id        TEXT UNIQUE NOT NULL,
        order_id        TEXT,
        product_id      TEXT NOT NULL,
        side            TEXT NOT NULL,
        size            REAL NOT NULL,
        price           REAL NOT NULL,
        fee             REAL DEFAULT 0,
        trade_time      TEXT NOT NULL,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # crypto_lots: FIFO cost basis tracking. One row per acquisition lot.
    # quantity_remaining decreases as lots are sold; fully sold lots have 0.
    """CREATE TABLE IF NOT EXISTS crypto_lots (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        currency            TEXT NOT NULL,
        acquisition_date    DATE NOT NULL,
        quantity            REAL NOT NULL,
        quantity_remaining  REAL NOT NULL,
        cost_per_unit       REAL NOT NULL,
        total_cost          REAL NOT NULL,
        source_trade_id     TEXT,
        created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # crypto_gains: one row per realized gain/loss event (disposal).
    # gain_type = 'short_term' (held <= 1 year) or 'long_term' (held > 1 year).
    """CREATE TABLE IF NOT EXISTS crypto_gains (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        currency        TEXT NOT NULL,
        sale_trade_id   TEXT NOT NULL,
        sale_date       DATE NOT NULL,
        quantity        REAL NOT NULL,
        proceeds        REAL NOT NULL,
        cost_basis      REAL NOT NULL,
        gain_loss       REAL NOT NULL,
        gain_type       TEXT NOT NULL,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_crypto_trades_time ON crypto_trades(trade_time)",
    "CREATE INDEX IF NOT EXISTS idx_crypto_lots_currency ON crypto_lots(currency, quantity_remaining)",
    "CREATE INDEX IF NOT EXISTS idx_crypto_gains_year ON crypto_gains(sale_date)",
    # Store Plaid's detailed personal finance category (e.g. "GENERAL_SERVICES_REFUNDS_AND_RETURNS")
    # so we can reliably detect refunds even when Plaid's amount sign is wrong.
    "ALTER TABLE transactions ADD COLUMN category_detail TEXT",
    # Additional Plaid fields for richer transaction data.
    # payment_channel: "online", "in store", or "other" — spending channel analysis
    "ALTER TABLE transactions ADD COLUMN payment_channel TEXT",
    # authorized_date: when the bank authorized the transaction (vs date = posted date)
    "ALTER TABLE transactions ADD COLUMN authorized_date TEXT",
    # original_description: raw bank description before Plaid's cleanup/normalization
    "ALTER TABLE transactions ADD COLUMN original_description TEXT",
    # merchant_entity_id: Plaid's normalized merchant identifier for cross-transaction linking
    "ALTER TABLE transactions ADD COLUMN merchant_entity_id TEXT",
    # check_number: check number from Plaid (also stored in transaction_metadata if user-entered)
    "ALTER TABLE transactions ADD COLUMN check_number TEXT",
    # logo_url: merchant logo for UI display
    "ALTER TABLE transactions ADD COLUMN logo_url TEXT",
    # website: merchant website URL
    "ALTER TABLE transactions ADD COLUMN website TEXT",
    # transaction_code: Plaid-specific code (e.g. for refunds, direct deposits)
    "ALTER TABLE transactions ADD COLUMN transaction_code TEXT",
]


def _migrate_set_existing_users_admin(conn):
    """One-time migration: set all existing users as admin.

    The is_admin column defaults to 0, so this promotes the original users (jbazen, hbazen)
    who were created before the column existed. Future users created via the API will
    default to non-admin.
    """
    conn.execute("UPDATE users SET is_admin = 1 WHERE is_admin = 0 AND is_active = 1")


def _migrate_encrypt_totp_secrets(conn):
    """One-time migration: encrypt any plaintext TOTP secrets with Fernet.

    Plaintext base32 secrets are ~32 chars; Fernet ciphertexts are ~120+ chars.
    Only rows with short values (< 80 chars) are migrated to avoid double-encrypting.
    """
    from api.encryption import encrypt
    rows = conn.execute(
        "SELECT id, totp_secret, totp_pending_secret FROM users"
    ).fetchall()
    for row in rows:
        uid = row["id"]
        updates = {}
        if row["totp_secret"] and len(row["totp_secret"]) < 80:
            updates["totp_secret"] = encrypt(row["totp_secret"])
        if row["totp_pending_secret"] and len(row["totp_pending_secret"]) < 80:
            updates["totp_pending_secret"] = encrypt(row["totp_pending_secret"])
        if updates:
            cols = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*updates.values(), uid))


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        # Run migrations silently (ignore if column/table already exists)
        _expected_msg_fragments = (
            "duplicate column name",
            "already exists",
            "table already exists",
        )
        for migration in MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if not any(frag in msg for frag in _expected_msg_fragments):
                    logger.warning("Unexpected migration error: %s | SQL: %.120s", exc, migration)
        # One-time: promote existing users to admin after is_admin column is added
        try:
            _migrate_set_existing_users_admin(conn)
        except Exception as exc:
            logger.warning("Admin promotion migration failed: %s", exc)
        # One-time: encrypt any plaintext TOTP secrets still in the DB
        try:
            _migrate_encrypt_totp_secrets(conn)
        except Exception as exc:
            logger.warning("TOTP encryption migration failed: %s", exc)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
