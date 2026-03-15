# Vaultic

**Your personal financial command center. Powered by Sage.**

Vaultic is a self-hosted personal finance dashboard that aggregates all your accounts in one place — bank accounts, investments, retirement, crypto, mortgage, and manually tracked assets — with a built-in AI financial advisor named Sage who has real-time access to your complete financial picture and full internet access for live market data.

---

## Table of Contents

1. [Overview](#overview)
2. [Tech Stack](#tech-stack)
3. [Project Structure](#project-structure)
4. [Architecture](#architecture)
5. [Database Schema](#database-schema)
6. [API Reference](#api-reference)
7. [Sage AI Advisor](#sage-ai-advisor)
8. [Security](#security)
9. [Local Development Setup](#local-development-setup)
10. [Environment Variables](#environment-variables)
11. [Deployment](#deployment)
12. [CI/CD Pipeline](#cicd-pipeline)
13. [Running Tests](#running-tests)
14. [Test Coverage](#test-coverage)
15. [Costs](#costs)
16. [Roadmap](#roadmap)

---

## Overview

Vaultic is a **personal-use, self-hosted** application. It is not a commercial SaaS product. It runs on a single Oracle Cloud A1 (always-free) instance and is accessed from any device via browser.

### What It Does

- **Unified dashboard** — net worth, all accounts, credit score, home value, car value, recent transactions on one screen
- **Plaid integration** — connect Chase, Vanguard, Voya, Insperity, Robinhood, Rocket Mortgage, and any other Plaid-supported institution
- **Sage AI advisor** — conversational AI financial advisor with access to your live data, persistent memory, and internet search
- **PDF import** — parse Investor360/NFS account statements using Claude AI extraction (for Parker Financial IRA/college fund accounts that Plaid cannot reach)
- **Manual entries** — home value, car value, credit score, and any other asset or liability
- **Net worth history** — daily snapshots, historical charts
- **Voice interface** — "Hey Sage" wake word, hands-free voice mode, OpenAI TTS (ballad voice) or free browser TTS
- **2FA (TOTP)** — Google Authenticator / Authy enrollment
- **Security logging** — verbose audit log of every login, API call, sync event, and Sage query, with live tail viewer in the UI

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12, FastAPI, Uvicorn |
| **Database** | SQLite (WAL mode) |
| **Frontend** | React 18, Vite |
| **Account linking** | Plaid Python SDK + Plaid Link (React) |
| **AI advisor** | Anthropic Claude Haiku (`claude-haiku-4-5-20251001`) |
| **AI voice** | OpenAI TTS (`tts-1`, `ballad` voice) — optional |
| **Web search** | Tavily API (AI-optimized search for Sage) |
| **PDF parsing** | pdfplumber + Claude Haiku extraction |
| **Auth** | JWT (PyJWT) + bcrypt password hashing |
| **2FA** | TOTP via pyotp + QR code via qrcode |
| **Encryption** | Fernet symmetric encryption (cryptography library) for Plaid tokens at rest |
| **Scheduling** | APScheduler (daily 2am sync) |
| **Hosting** | Oracle Cloud A1 (always-free ARM instance) |
| **CI/CD** | GitHub Actions → SSH deploy |

---

## Project Structure

```
vaultic/
├── api/
│   ├── main.py              # FastAPI app, middleware, router registration
│   ├── database.py          # SQLite schema, migrations, connection context manager
│   ├── auth.py              # JWT, bcrypt, TOTP (pyotp), user management
│   ├── dependencies.py      # get_current_user, get_client_ip FastAPI deps
│   ├── encryption.py        # Fernet encrypt/decrypt for Plaid access tokens
│   ├── sage.py              # Sage AI engine: tool definitions, tool execution, Claude loop
│   ├── security_log.py      # Audit logger: logins, API calls, syncs, Sage queries
│   ├── sync.py              # Plaid transaction sync (cursor-based), net worth snapshots
│   ├── rate_limit.py        # In-memory sliding window: 60 Sage msgs/hr, 5 syncs/5min
│   └── routers/
│       ├── auth.py          # /api/auth/* — login, 2FA, users, password, security log
│       ├── plaid.py         # /api/plaid/* — link token, exchange, sync, items
│       ├── accounts.py      # /api/accounts/* — list, balances, transactions, rename
│       ├── net_worth.py     # /api/net-worth/* — latest snapshot, history
│       ├── manual.py        # /api/manual/* — CRUD for manual asset entries
│       ├── sage.py          # /api/sage/* — chat endpoint, TTS endpoint
│       └── pdf.py           # /api/pdf/* — PDF ingestion (pdfplumber + Haiku), save
├── ui/
│   ├── public/
│   │   └── favicon.png      # Tab icon + sidebar logo
│   ├── src/
│   │   ├── App.jsx          # Shell: sidebar nav, routes, auth gate, SageChat mount
│   │   ├── App.css          # Global CSS variables, layout, components
│   │   ├── api.js           # All API calls (apiFetch wrapper with JWT + 401 handling)
│   │   ├── pages/
│   │   │   ├── Login.jsx        # Two-step login: credentials → 2FA code
│   │   │   ├── Dashboard.jsx    # Master view: net worth, all accounts, charts, transactions
│   │   │   ├── Accounts.jsx     # Account list with balance history charts
│   │   │   ├── Transactions.jsx # Transaction browser
│   │   │   ├── Manual.jsx       # Manual entry CRUD
│   │   │   ├── PDFImport.jsx    # Drag & drop PDF import, preview, confirm save
│   │   │   └── Settings.jsx     # Password, 2FA enrollment, user management, security log
│   │   └── components/
│   │       ├── SageChat.jsx     # Floating AI chat panel with Hey Sage, voice, session persistence
│   │       ├── NetWorthChart.jsx
│   │       └── PlaidLink.jsx
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── tests/
│   ├── conftest.py          # In-memory SQLite test DB, TestClient, auth fixtures
│   ├── test_auth.py         # Auth endpoint tests
│   └── test_accounts.py     # Account, net worth, manual entry tests
├── data/                    # Created at runtime (gitignored)
│   ├── vaultic.db           # SQLite database
│   └── sage_notes.md        # Sage's persistent memory across sessions
├── .github/
│   └── workflows/
│       └── deploy.yml       # GitHub Actions: test → deploy on push to main
├── .env                     # Local secrets (never committed)
├── .env.example             # Template for all required environment variables
├── requirements.txt
└── pytest.ini
```

---

## Architecture

### Request Flow

```
Browser → Vite dev server (port 5173) → proxy → FastAPI (port 8000)
                                    OR
Browser → Nginx (production) → static files (Vite build) + proxy /api/* → FastAPI
```

### Authentication Flow

```
POST /api/auth/login
  → bcrypt verify password
  → if 2FA enabled: return {requires_2fa: true}
  → frontend shows 6-digit TOTP input
  → POST /api/auth/verify-2fa → verify pyotp.TOTP code
  → return JWT (24hr expiry by default)

Every protected request:
  → Authorization: Bearer <jwt>
  → get_current_user dependency decodes + validates JWT
  → 401 on failure → frontend clears token + fires auth:logout event
```

### Plaid Data Flow

```
1. PlaidLink (React) → createLinkToken → /api/plaid/link-token
2. User completes Plaid Link UI (OAuth or credential-based)
3. exchangeToken → /api/plaid/exchange → stores encrypted access token in plaid_items
4. triggerSync → /api/plaid/sync → sync.sync_all()
   → for each plaid_item:
     → fetch accounts + balances → upsert into accounts + account_balances
     → cursor-based transaction sync → upsert into transactions
   → compute net worth snapshot → insert into net_worth_snapshots
5. APScheduler runs sync_all() daily at 2am
```

### Sage AI Loop

```
User message → POST /api/sage/chat (rate limited: 60/hr)
  → rate_limit check
  → sage.chat(history, message)
    → client.messages.create(model=Haiku, tools=TOOLS, messages=history+[user_msg])
    → if stop_reason == "tool_use":
        → execute tool(s): DB queries, Tavily search, fetch_page, notes read/write
        → append tool_results as user message
        → loop (call Claude again with tool results)
    → if stop_reason == "end_turn":
        → return text response + updated history
  → response + serialized history returned to frontend
  → frontend persists history in sessionStorage (survives panel close/reopen)
```

### PDF Import Flow

```
User drags PDF → /api/pdf/ingest (multipart, 20MB limit, 30 pages max)
  → pdfplumber extracts text from all pages
  → Claude Haiku parse prompt: extract account name, category, value, notes as JSON
  → return parsed entries to frontend for review
User reviews/edits → clicks Save → /api/pdf/save
  → save confirmed entries as manual_entries rows
```

---

## Database Schema

### `users`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| username | TEXT UNIQUE | |
| password_hash | TEXT | bcrypt |
| is_active | INTEGER | 1/0 |
| two_fa_enabled | INTEGER | 1/0 |
| totp_secret | TEXT | Active TOTP secret (encrypted via Fernet) |
| totp_pending_secret | TEXT | Pending secret during enrollment |
| created_at | DATETIME | |

### `plaid_items`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| item_id | TEXT UNIQUE | Plaid item ID |
| institution_id | TEXT | |
| institution_name | TEXT | |
| access_token_enc | TEXT | Fernet-encrypted Plaid access token |
| cursor | TEXT | Transaction sync cursor |
| last_synced_at | DATETIME | |

### `accounts`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| plaid_account_id | TEXT UNIQUE | Null for manual accounts |
| plaid_item_id | INTEGER FK | References plaid_items |
| name | TEXT | Official Plaid name |
| display_name | TEXT | User-set rename |
| mask | TEXT | Last 4 digits |
| type | TEXT | depository, investment, credit, loan |
| subtype | TEXT | checking, savings, 401k, etc. |
| institution_name | TEXT | |
| is_manual | INTEGER | 1 if manually created |
| is_active | INTEGER | 0 = soft-deleted/disconnected |

### `account_balances`
Daily balance snapshots. One row per account per day. `UNIQUE(account_id, snapped_at)` prevents duplicates.

### `transactions`
| Column | Type | Notes |
|---|---|---|
| transaction_id | TEXT UNIQUE | Plaid transaction ID |
| account_id | INTEGER FK | |
| amount | REAL | Positive = debit, negative = credit |
| date | TEXT | YYYY-MM-DD |
| name | TEXT | Transaction description |
| merchant_name | TEXT | Cleaned merchant name |
| category | TEXT | Plaid category |
| pending | INTEGER | 1 if pending |

### `net_worth_snapshots`
Daily net worth snapshots broken down by category: `liquid`, `invested`, `crypto`, `real_estate`, `vehicles`, `liabilities`, `other_assets`, `total`. `UNIQUE(snapped_at)` — one snapshot per day.

### `manual_entries`
| Column | Type | Notes |
|---|---|---|
| name | TEXT | e.g. "Primary Home" |
| category | TEXT | home_value, vehicle, credit_score, investment, liability, other |
| value | REAL | |
| notes | TEXT | |
| entered_at | DATE | |

---

## API Reference

All endpoints except `/api/auth/login`, `/api/auth/verify-2fa`, and `/api/health` require `Authorization: Bearer <jwt>`.

### Auth — `/api/auth`
| Method | Path | Description |
|---|---|---|
| POST | `/login` | Username + password → JWT or `{requires_2fa: true}` |
| POST | `/verify-2fa` | Username + TOTP code → JWT |
| GET | `/me` | Returns `{username, two_fa_enabled}` |
| POST | `/change-password` | Change own password |
| GET | `/users` | List all users (admin) |
| POST | `/users` | Create user |
| DELETE | `/users/{username}` | Delete user |
| POST | `/2fa/setup` | Begin TOTP enrollment → returns QR code SVG |
| POST | `/2fa/confirm` | Confirm TOTP enrollment with code |
| DELETE | `/2fa` | Disable 2FA |
| GET | `/security-log?lines=500` | Last N lines of audit log |

### Plaid — `/api/plaid`
| Method | Path | Description |
|---|---|---|
| POST | `/link-token` | Create Plaid Link token |
| POST | `/exchange` | Exchange public token → store encrypted access token |
| POST | `/sync` | Trigger manual sync (rate limited: 5/5min) |
| GET | `/items` | List connected institutions |
| DELETE | `/items/{item_id}` | Disconnect institution |

### Accounts — `/api/accounts`
| Method | Path | Description |
|---|---|---|
| GET | `/` | All active accounts with latest balances |
| GET | `/{id}/balances?days=90` | Balance history for one account |
| GET | `/{id}/transactions?limit=50&offset=0` | Transactions for one account |
| GET | `/transactions/recent?limit=50` | Recent transactions across all accounts |
| PATCH | `/{id}/rename` | Set display name |

### Net Worth — `/api/net-worth`
| Method | Path | Description |
|---|---|---|
| GET | `/latest` | Most recent net worth snapshot |
| GET | `/history?days=365` | Historical snapshots |

### Manual Entries — `/api/manual`
| Method | Path | Description |
|---|---|---|
| GET | `/` | All manual entries |
| POST | `/` | Add entry |
| DELETE | `/{id}` | Delete entry |

### Sage — `/api/sage`
| Method | Path | Description |
|---|---|---|
| POST | `/chat` | Send message, get response + updated history. Rate limited 60/hr. |
| POST | `/speak` | Text → streaming MP3 via OpenAI TTS (ballad voice). One sentence per call for parallel fetch in frontend. |

### PDF — `/api/pdf`
| Method | Path | Description |
|---|---|---|
| POST | `/ingest` | Upload PDF → parse with pdfplumber + Haiku → return entries |
| POST | `/save` | Save confirmed parsed entries as manual entries |

---

## Sage AI Advisor

Sage is a conversational financial advisor built on Claude Haiku with a tool-use loop. He has access to:

### Tools
| Tool | What it does |
|---|---|
| `get_net_worth` | Latest net worth snapshot with full breakdown |
| `get_net_worth_history` | Historical net worth trend |
| `get_accounts` | All accounts with current balances |
| `get_transactions` | Recent transactions (up to 200) |
| `get_manual_entries` | Manually entered assets (home, car, credit score) |
| `get_notes` | Read persistent notes file (`data/sage_notes.md`) |
| `update_notes` | Write to persistent notes — Sage's long-term memory |
| `web_search` | Tavily AI search — live prices, tax rules, rates, news |
| `fetch_page` | Fetch and read a specific web page (up to 8,000 chars) |

### Voice Modes
- **Off** — text only
- **Browser (free)** — Web Speech API, prefers male voice if available
- **AI voice (OpenAI)** — `tts-1` model, `ballad` voice (deep, natural male)

### Hey Sage (Always-On)
- Continuous Web Speech API with wake word detection (`hey sage`, `ok sage`, `hi sage`)
- Two-tone activation sound on wake
- 1.8-second silence timer auto-sends command
- Green pulsing indicator when awake and listening
- Stop button (⏹) appears in header while Sage is speaking

### Session Persistence
Chat history and conversation context are stored in `sessionStorage` — survive panel close/reopen and page navigation. The ↺ button clears both UI history and sessionStorage. Sage's long-term memory (goals, preferences, user context) lives in `data/sage_notes.md` and persists across browser sessions.

### Rate Limiting
- 60 Sage messages per hour per user (in-memory sliding window)
- 5 manual syncs per 5 minutes per user

---

## Security

- **JWT auth** — all API endpoints protected; 401 auto-logs out frontend
- **bcrypt** — passwords hashed with bcrypt (cost factor 12)
- **Fernet encryption** — Plaid access tokens encrypted at rest in SQLite
- **TOTP 2FA** — Google Authenticator / Authy; pending secret pattern prevents partial enrollment
- **Security headers** — HSTS, X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy set on every response
- **Audit log** — every login attempt (with IP + user agent), 2FA event, sync, Sage query, and HTTP 4xx/5xx logged to `data/security.log` with timestamps
- **Rate limiting** — Sage chat and sync endpoints rate limited to prevent abuse and runaway API costs
- **Input validation** — Pydantic models validate all request bodies; manual entry categories validated against allowlist

---

## Local Development Setup

### Prerequisites
- Python 3.12+
- Node.js 18+
- Git

### 1. Clone and create virtual environment

```bash
git clone <your-repo-url>
cd vaultic
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Install frontend dependencies

```bash
cd ui
npm install
cd ..
```

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your values (see [Environment Variables](#environment-variables) below).

Generate required secrets:

```bash
# bcrypt password hash
python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"

# JWT secret (64-char hex)
python -c "import secrets; print(secrets.token_hex(32))"

# Fernet encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 5. Start the backend

```bash
python -m uvicorn api.main:app --reload --port 8000
```

`load_dotenv()` is called at startup — `.env` is loaded automatically regardless of how you launch the server.

### 6. Start the frontend

```bash
cd ui
npm run dev
```

Vite runs on port 5173 and proxies `/api/*` to `localhost:8000` (configured in `vite.config.js`).

Open `http://localhost:5173` in your browser.

### 7. Connect accounts (Plaid sandbox)

In Plaid sandbox mode, use test credentials:
- Username: `user_good`
- Password: `pass_good`
- Any institution

Click **Connect Account** on the Dashboard, complete the Plaid Link flow, then click **↻ Sync**.

---

## Environment Variables

All variables go in `.env` at the project root. `load_dotenv()` reads this file at server startup.

```env
# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_USERNAME=your_username
AUTH_PASSWORD_HASH=<bcrypt hash>
# Generate: python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"

JWT_SECRET=<64-char hex string>
# Generate: python -c "import secrets; print(secrets.token_hex(32))"

JWT_EXPIRE_HOURS=24

# ── Encryption (Plaid access tokens at rest) ──────────────────────────────────
ENCRYPTION_KEY=<fernet key>
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ── Plaid ─────────────────────────────────────────────────────────────────────
PLAID_CLIENT_ID=your_plaid_client_id
PLAID_SECRET=your_plaid_secret
PLAID_ENV=sandbox        # sandbox | production

# ── AI (Sage) ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...

# ── Voice (optional) ──────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-proj-...
# Powers Sage's lifelike voice (tts-1, ballad). Falls back to browser TTS if not set.
# Requires billing credits at platform.openai.com/billing

# ── Web Search (Sage internet access) ─────────────────────────────────────────
TAVILY_API_KEY=tvly-...
# Free tier: 1,000 searches/month. Sign up at app.tavily.com
# Gives Sage live access to: stock prices, tax rules, mortgage rates, news, etc.
```

---

## Deployment

The app deploys to an Oracle Cloud A1 instance (Ubuntu, always-free tier). The frontend is built by Vite and served as static files; the backend runs as a systemd service behind Nginx.

### Server setup (one-time)

```bash
# On the Oracle Cloud instance
sudo apt update && sudo apt install nginx python3.12 python3.12-venv nodejs npm git -y

# Clone repo
git clone <your-repo-url> ~/vaultic
cd ~/vaultic
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build frontend
cd ui && npm ci && npm run build && cd ..

# Create .env with production values
cp .env.example .env
nano .env   # fill in all values; use PLAID_ENV=production when ready
```

### systemd service (`/etc/systemd/system/vaultic-api.service`)

```ini
[Unit]
Description=Vaultic API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/vaultic
ExecStart=/home/ubuntu/vaultic/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable vaultic-api
sudo systemctl start vaultic-api
```

### Nginx config (`/etc/nginx/sites-available/vaultic`)

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate /etc/letsencrypt/live/your.domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your.domain.com/privkey.pem;

    # Serve Vite build
    root /home/ubuntu/vaultic/ui/dist;
    index index.html;

    # API proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

### GitHub Actions Secrets required

| Secret | Value |
|---|---|
| `SERVER_HOST` | Oracle Cloud public IP or domain |
| `SSH_PRIVATE_KEY` | Private key for `ubuntu` user |

---

## CI/CD Pipeline

On every push to `main`:

1. **Test job** — runs `pytest` against in-memory SQLite on Ubuntu (Python 3.12)
2. **Deploy job** — SSH into Oracle Cloud, `git pull`, `npm ci && npm run build`, `systemctl restart vaultic-api`

Deploy only runs if tests pass.

---

## Running Tests

```bash
# From project root, with venv active
pytest

# Verbose output
pytest -v

# Specific file
pytest tests/test_auth.py -v
```

Tests use an in-memory SQLite database — no `.env` required, no external services called.

---

## Test Coverage

### What is covered

**Backend — 80 tests total across:**
- `test_auth.py` — Login, JWT, 401 handling, `/me`, `/health`
- `test_accounts.py` — Accounts, net worth, manual entries
- `test_2fa.py` — TOTP setup, confirm, verify on login, disable
- `test_users.py` — Create, delete, change password, admin endpoints
- `test_sage.py` — Chat endpoint, tool dispatch, rate limiting (429)
- `test_pdf.py` — PDF ingestion parsing, save to manual entries
- `test_rate_limit.py` — Sliding window rate limit behavior

**Playwright E2E — 18 tests across:**
- `tests/e2e/auth.spec.js` — Login, wrong password, 2FA step, logout
- `tests/e2e/dashboard.spec.js` — Net worth display, accounts, manual entries, navigation
- `tests/e2e/sage.spec.js` — Sage button, chat panel, response, session persistence, Hey Sage toggle

All E2E tests use mocked API routes — no live backend required.

### What is NOT covered (gaps)

**Backend:**
- ⬜ Plaid link token, token exchange, sync (requires Plaid SDK mock)
- ⬜ OpenAI TTS endpoint (requires OpenAI mock)
- ⬜ Security log endpoint

**Frontend:**
- ⬜ No component unit tests (no Vitest/Jest setup)

---

## Costs

All costs are for personal use (single user, ~15 connected accounts).

### Monthly Operating Costs

| Service | Purpose | Cost |
|---|---|---|
| **Oracle Cloud A1** | Hosting (2 OCPU, 12GB RAM) | **Free** (always-free tier) |
| **Plaid** | Account data (bank, investment, mortgage) | **~$5–15/mo** (pay-as-you-go, ~$0.30–$1/item/mo) |
| **Anthropic (Haiku)** | Sage AI chat | **~$1–3/mo** (input: $0.80/1M tokens, output: $4/1M tokens) |
| **OpenAI TTS** | Sage voice (ballad) | **~$0.50–2/mo** (tts-1: $15/1M characters) |
| **Tavily** | Sage web search | **Free** (1,000 searches/month free tier) |
| **Domain + SSL** | Custom domain (optional) | **~$1/mo** (Let's Encrypt SSL is free) |
| **Total** | | **~$7–20/month** |

### Cost Notes

- **Plaid sandbox is free** — real account data requires Plaid Production access. Apply at `dashboard.plaid.com` as an individual developer with "Personal Finance Management" use case. No business registration required.
- **Plaid pricing** is per connected item (institution), not per account. All Chase accounts count as one item (~$0.30–1/mo).
- **Anthropic Haiku** is the cheapest Claude model. Even 50 conversations/day would cost ~$3/month.
- **OpenAI TTS** requires billing credits at `platform.openai.com/billing`. Browser TTS fallback is always free.
- **Tavily** free tier resets monthly. At normal personal use (occasional Sage web searches), 1,000/month is more than enough.

### Comparison to Alternatives

| Service | Monthly Cost |
|---|---|
| Monarch Money | $15/mo |
| Copilot | $13/mo |
| YNAB | $15/mo |
| **Vaultic (self-hosted)** | **~$7–20/mo** (and you own your data) |

---

## Accounts Supported

| Institution | Type | Integration |
|---|---|---|
| Chase | Checking, Savings, Money Market, Credit Card | Plaid ✅ |
| Vanguard | 401k | Plaid ✅ |
| Voya | 401k | Plaid ✅ |
| Insperity | 401k | Plaid ✅ |
| Robinhood | Brokerage | Plaid ✅ |
| Rocket Mortgage | Mortgage | Plaid ✅ |
| Optum / HealthEquity | HSA | Plaid ✅ |
| Coinbase | Crypto | Planned (official API) |
| River | Bitcoin | Planned (official API) |
| Parker Financial / NFS (Investor360) | IRAs, college fund | PDF Import ✅ |
| Home value | Asset | Manual entry ✅ |
| Car value | Asset | Manual entry ✅ |
| Credit score | Metric | Manual entry ✅ |

### Parker Financial / NFS Note

Parker Financial (Elkhorn, NE) uses Investor360 by Advisor360° as its client portal. The actual custodian is **National Financial Services (NFS)** — Fidelity's institutional arm. **Plaid does not support NFS** (Fidelity blocked all third-party aggregators for institutional accounts). Investor360 only exports PDFs, not CSV. The solution is the built-in **PDF Import** feature: download your monthly PDF from Investor360, drag it into Vaultic, and Claude AI extracts the account values automatically.

---

## Roadmap

- [ ] **Budget module** — zero-based budgeting to replace EveryDollar; Plaid transactions auto-categorized into budget lines; Sage learns merchant → category mappings over time
- [ ] **Coinbase integration** — official Coinbase API for live crypto balances
- [ ] **River integration** — official River API for Bitcoin balance
- [ ] **Plaid Production** — apply for production access, connect real accounts
- [ ] **Tax module** — W-4 multi-job wizard, quarterly estimated tax calculator (1040-ES), capital gains tracker, withholding tracker
- [x] **Test suite** — 80 backend unit tests + 18 Playwright E2E tests (completed)
- [ ] **Mobile PWA** — installable on iPhone/Android home screen
