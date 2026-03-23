"""SQLite database setup and connection management."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

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
]


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        # Run migrations silently (ignore if column/table already exists)
        for migration in MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass


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
