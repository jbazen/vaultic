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
