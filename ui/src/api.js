// ── Token storage ─────────────────────────────────────────────────────────────
// Access tokens and refresh tokens are both stored in localStorage.
// localStorage survives browser close/reopen, so the user stays signed in
// across sessions until expiry or logout.

const getToken        = () => localStorage.getItem("vaultic_token");
const setToken        = (t) => localStorage.setItem("vaultic_token", t);
const clearToken      = () => localStorage.removeItem("vaultic_token");

const getRefreshToken  = () => localStorage.getItem("vaultic_refresh_token");
const setRefreshToken  = (t) => localStorage.setItem("vaultic_refresh_token", t);
const clearRefreshToken = () => localStorage.removeItem("vaultic_refresh_token");

// ── JWT expiry helper ─────────────────────────────────────────────────────────

/** Decode the JWT expiry claim (no signature verification — timing only). */
function getTokenExpiry(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return (payload.exp || 0) * 1000; // exp is seconds → ms
  } catch {
    return 0;
  }
}

// ── Silent refresh ────────────────────────────────────────────────────────────

// Guard: prevents concurrent refresh calls if multiple requests fire simultaneously
let _refreshing = null;

/**
 * If the access token is expired or within 2 minutes of expiry, and a refresh
 * token is available, silently swap in a fresh access token before the request.
 * Uses a shared promise (_refreshing) so concurrent calls don't double-refresh.
 */
async function ensureFreshToken() {
  const token        = getToken();
  const refreshToken = getRefreshToken();
  if (!refreshToken) return; // Web session — no silent refresh

  // If no access token at all, or it expires within 2 minutes, refresh now
  const TWO_MIN = 2 * 60 * 1000;
  const needsRefresh = !token || (getTokenExpiry(token) - Date.now() < TWO_MIN);
  if (!needsRefresh) return;

  if (_refreshing) {
    await _refreshing; // Another call already refreshing — wait for it
    return;
  }

  _refreshing = (async () => {
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (res.ok) {
        const data = await res.json();
        setToken(data.token);
        setRefreshToken(data.refresh_token);
      } else {
        // Refresh token is invalid/expired — force full logout
        clearToken();
        clearRefreshToken();
        window.dispatchEvent(new Event("auth:logout"));
      }
    } catch {
      // Network error — don't force logout; the subsequent request will handle 401
    }
  })();

  await _refreshing;
  _refreshing = null;
}

// ── Core fetch wrapper ────────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  // Silently refresh the access token if it's expired and we have a refresh token
  await ensureFreshToken();

  const token = getToken();
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  // Timeout: abort after 30s (or caller-supplied value). Without this, fetch
  // hangs indefinitely on slow mobile networks, piling up pending requests that
  // block React from processing navigation events.
  const timeout = options.timeout || 30000;
  const controller = new AbortController();
  if (options.signal) {
    // If caller supplied their own signal (e.g. page-unmount abort), forward it
    options.signal.addEventListener("abort", () => controller.abort());
  }
  const timer = setTimeout(() => controller.abort(), timeout);

  let res;
  try {
    res = await fetch(path, { ...options, headers, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 401) {
    clearToken();
    clearRefreshToken();
    window.dispatchEvent(new Event("auth:logout"));
    throw new Error("Session expired");
  }

  if (!res.ok) {
    const detail = await res.json().then(d => d.detail).catch(() => res.statusText);
    throw new Error(detail || `Request failed (${res.status})`);
  }

  return res;
}

// ── Auth functions ────────────────────────────────────────────────────────────

/**
 * Log in with username + password.
 * rememberMe=true issues a refresh token (mobile "keep me signed in").
 * rememberMe=false issues a 30-day web access token only.
 */
export async function login(username, password, rememberMe = false) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, remember_me: rememberMe }),
  });
  if (!res.ok) throw new Error("Invalid credentials");
  const data = await res.json();
  if (data.requires_2fa) return { requires_2fa: true, pending_token: data.pending_token };
  setToken(data.token);
  if (data.refresh_token) setRefreshToken(data.refresh_token);
  return { requires_2fa: false };
}

/**
 * Complete 2FA verification.
 * rememberMe must match what was passed at the login step.
 */
export async function verify2FA(pendingToken, code, rememberMe = false) {
  const res = await fetch("/api/auth/verify-2fa", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pending_token: pendingToken, code, remember_me: rememberMe }),
  });
  if (!res.ok) throw new Error("Invalid or expired code");
  const data = await res.json();
  setToken(data.token);
  if (data.refresh_token) setRefreshToken(data.refresh_token);
}

export async function logout() {
  const refreshToken = getRefreshToken();
  try {
    // Send refresh token so the server revokes it immediately — prevents the
    // 90-day window from surviving a logout on mobile.
    await apiFetch("/api/auth/logout", {
      method: "POST",
      body: refreshToken ? JSON.stringify({ refresh_token: refreshToken }) : undefined,
    });
  } catch {
    // If server is unreachable, still clear locally
  }
  clearToken();
  clearRefreshToken();
  window.dispatchEvent(new Event("auth:logout"));
}

/** Revoke all refresh tokens for the current user (signs out all mobile devices). */
export async function revokeAllSessions() {
  const res = await apiFetch("/api/auth/revoke-all-sessions", { method: "POST" });
  return res.json();
}

/**
 * True if the user has a valid (or refreshable) session.
 * - If access token is present and not expired → true
 * - If access token is expired but refresh token exists → true (ensureFreshToken
 *   will swap it in transparently on the next API call)
 * - Otherwise → false (show login screen)
 */
export function isAuthed() {
  const token = getToken();
  if (token && getTokenExpiry(token) > Date.now()) return true;
  return !!getRefreshToken(); // mobile: still authenticated, will refresh on next request
}

// --- Net worth ---
export async function getNetWorthLatest() {
  const res = await apiFetch("/api/net-worth/latest");
  return res.json();
}

export async function getNetWorthHistory(days = 365) {
  const res = await apiFetch(`/api/net-worth/history?days=${days}`);
  return res.json();
}

// --- Accounts ---
export async function getAccounts() {
  const res = await apiFetch("/api/accounts");
  return res.json();
}

export async function getBalanceHistory(accountId, days = 365) {
  const res = await apiFetch(`/api/accounts/${accountId}/balances?days=${days}`);
  return res.json();
}

export async function getPortfolioPerformance(days = 365) {
  const res = await apiFetch(`/api/accounts/portfolio/performance?days=${days}`);
  return res.json();
}

export async function getTransactions(accountId, limit = 50, offset = 0) {
  const res = await apiFetch(`/api/accounts/${accountId}/transactions?limit=${limit}&offset=${offset}`);
  return res.json();
}

export async function getRecentTransactions(limit = 50) {
  const res = await apiFetch(`/api/accounts/transactions/recent?limit=${limit}`);
  return res.json();
}

// --- Plaid ---
export async function createLinkToken() {
  const res = await apiFetch("/api/plaid/link-token", { method: "POST" });
  return res.json();
}

export async function exchangeToken(public_token, institution_id, institution_name) {
  const res = await apiFetch("/api/plaid/exchange", {
    method: "POST",
    body: JSON.stringify({ public_token, institution_id, institution_name }),
  });
  return res.json();
}

export async function triggerSync() {
  const res = await apiFetch("/api/plaid/sync", { method: "POST" });
  return res.json();
}

export async function getPlaidItems() {
  const res = await apiFetch("/api/plaid/items");
  return res.json();
}

export async function removePlaidItem(itemId) {
  const res = await apiFetch(`/api/plaid/items/${itemId}`, { method: "DELETE" });
  return res.json();
}

// --- Sage ---
export async function sageChat(message, history = [], attachments = []) {
  const res = await apiFetch("/api/sage/chat", {
    method: "POST",
    body: JSON.stringify({ message, history, attachments }),
  });
  return res.json();
}

export async function sageProcessFile(file) {
  const token = getToken();
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/sage/process-file", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (res.status === 401) { clearToken(); window.dispatchEvent(new Event("auth:logout")); throw new Error("Session expired"); }
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "File processing failed"); }
  return res.json();
}

export async function sageTranscribe(audioBlob) {
  const token = getToken();
  const form = new FormData();
  form.append("file", audioBlob, "audio.webm");
  const res = await fetch("/api/sage/transcribe", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (res.status === 401) { clearToken(); window.dispatchEvent(new Event("auth:logout")); throw new Error("Session expired"); }
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Transcription failed"); }
  return res.json(); // { text: "..." }
}

export async function sageSpeak(text) {
  const res = await apiFetch("/api/sage/speak", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  // The endpoint streams an MP3 — collect the full blob then create an object URL
  // so the browser can play it via new Audio(url). The caller is responsible for
  // calling URL.revokeObjectURL(url) after playback to release the memory.
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// --- Account rename ---
export async function renameAccount(accountId, displayName) {
  const res = await apiFetch(`/api/accounts/${accountId}/rename`, {
    method: "PATCH",
    body: JSON.stringify({ display_name: displayName }),
  });
  return res.json();
}

// --- Account notes ---
// Saves a user-written description for any Plaid or Coinbase account.
// Empty string clears the note (stored as NULL server-side).
export async function updateAccountNotes(accountId, notes) {
  const res = await apiFetch(`/api/accounts/${accountId}/notes`, {
    method: "PATCH",
    body: JSON.stringify({ notes }),
  });
  return res.json();
}

// --- Manual entry rename ---
// Updates the display name of a PDF-imported manual entry.
// Optionally updates notes too — only pass notes when you want to change it;
// omitting notes preserves whatever is already stored.
export async function renameManualEntry(entryId, name, notes) {
  const body = { name };
  if (notes !== undefined) body.notes = notes;
  const res = await apiFetch(`/api/manual/${entryId}/rename`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  return res.json();
}

// --- User management ---
export async function getUsers() {
  const res = await apiFetch("/api/auth/users");
  return res.json();
}

export async function createUser(username, password) {
  const res = await apiFetch("/api/auth/users", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  return res.json();
}

export async function deleteUser(username) {
  const res = await apiFetch(`/api/auth/users/${username}`, { method: "DELETE" });
  return res.json();
}

export async function changePassword(current_password, new_password) {
  const res = await apiFetch("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });
  return res.json();
}

// --- 2FA (TOTP) ---
export async function totpSetup() {
  // Returns SVG string of QR code
  const res = await apiFetch("/api/auth/2fa/setup", { method: "POST" });
  return res.text(); // SVG markup
}

export async function totpConfirm(code) {
  const res = await apiFetch("/api/auth/2fa/confirm", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
  return res.json();
}

export async function disable2FA(password) {
  const res = await apiFetch("/api/auth/2fa/disable", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  return res.json();
}

export async function getMe() {
  const res = await apiFetch("/api/auth/me");
  return res.json();
}

export async function getSecurityLog(lines = 500) {
  const res = await apiFetch(`/api/auth/security-log?lines=${lines}`);
  return res.json();
}

// --- PDF ingestion ---
export async function ingestPDF(file) {
  const token = getToken();
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/pdf/ingest", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (res.status === 401) { clearToken(); window.dispatchEvent(new Event("auth:logout")); throw new Error("Session expired"); }
  if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "PDF parse failed"); }
  return res.json();
}

export async function saveParsedPDF(entries) {
  const res = await apiFetch("/api/pdf/save", {
    method: "POST",
    body: JSON.stringify({ entries }),
  });
  return res.json();
}

// --- Crypto ---
export async function syncCoinbase() {
  const res = await apiFetch("/api/crypto/sync", { method: "POST" });
  return res.json();
}

// Fetch trade fills from Coinbase and store locally (idempotent)
export async function syncCryptoTrades() {
  const res = await apiFetch("/api/crypto/sync-trades", { method: "POST" });
  return res.json();
}

// List stored crypto trades with optional date filter
export async function getCryptoTrades(startDate, endDate, limit = 200) {
  let url = `/api/crypto/trades?limit=${limit}`;
  if (startDate) url += `&start_date=${startDate}`;
  if (endDate) url += `&end_date=${endDate}`;
  const res = await apiFetch(url);
  return res.json();
}

// Recompute FIFO cost basis lots and capital gains from stored trades
export async function calculateCryptoGains() {
  const res = await apiFetch("/api/crypto/calculate-gains", { method: "POST" });
  return res.json();
}

// Get realized crypto gains/losses for a tax year with short/long term breakdown
export async function getCryptoGains(year) {
  const res = await apiFetch(`/api/crypto/gains/${year}`);
  return res.json();
}

// List all FIFO cost basis lots, optionally filtered by currency
export async function getCryptoLots(currency) {
  let url = "/api/crypto/lots";
  if (currency) url += `?currency=${encodeURIComponent(currency)}`;
  const res = await apiFetch(url);
  return res.json();
}

// --- Plaid investment data ---
// Fetches current holdings snapshot for one investment account, joined with security
// metadata (name, ticker, type) and computed gain/loss fields.
export async function getAccountHoldings(accountId) {
  const res = await apiFetch(`/api/accounts/${accountId}/holdings`);
  return res.json(); // { holdings: [...], total_value: N }
}

// Fetches buy/sell/dividend/transfer history for one investment account.
// Paginated: default 100 rows, use offset for next pages.
export async function getAccountInvestmentTransactions(accountId, limit = 100, offset = 0) {
  const res = await apiFetch(`/api/accounts/${accountId}/investment-transactions?limit=${limit}&offset=${offset}`);
  return res.json();
}

// Fetches daily price/value history for a single security within an account.
// Used for plotting an individual holding's performance over time.
export async function getHoldingsHistory(accountId, securityId, days = 90) {
  const res = await apiFetch(`/api/accounts/${accountId}/holdings/history?security_id=${encodeURIComponent(securityId)}&days=${days}`);
  return res.json();
}

// --- Manual entries ---
export async function getManualEntries() {
  const res = await apiFetch("/api/manual");
  return res.json();
}

export async function addManualEntry(entry) {
  const res = await apiFetch("/api/manual", {
    method: "POST",
    body: JSON.stringify(entry),
  });
  return res.json();
}

export async function deleteManualEntry(id) {
  const res = await apiFetch(`/api/manual/${id}`, { method: "DELETE" });
  return res.json();
}

// Fetches balance history for a PDF-imported manual entry from manual_entry_snapshots.
// Returns [{snapped_at, current}] in ascending date order — same shape as
// getBalanceHistory() so BalanceChart renders both Plaid and PDF accounts identically.
export async function getManualEntryHistory(entryId, days = 1825) {
  const res = await apiFetch(`/api/manual/${entryId}/history?days=${days}`);
  return res.json();
}

// ── Budget ────────────────────────────────────────────────────────────────────
// month format: "YYYY-MM" (e.g. "2026-03")

export async function getBudget(month) {
  const res = await apiFetch(`/api/budget/${month}`);
  return res.json();
}
// Bulk-import historical budget transaction CSVs. Accepts multiple files in one
// request — creates groups/items, loads history, and seeds auto-categorization rules.
export async function importBudgetCSV(files) {
  const token = getToken();
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const res = await fetch("/api/budget/import/csv", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (res.status === 401) { clearToken(); window.dispatchEvent(new Event("auth:logout")); throw new Error("Session expired"); }
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Import failed"); }
  return res.json();
}

// Import a single month from external budget app API JSON (copy from DevTools Network tab).
export async function importBudgetJSON(jsonData) {
  const res = await apiFetch("/api/budget/import/json", {
    method: "POST",
    body: JSON.stringify(jsonData),
  });
  return res.json();
}

export async function seedBudgetTemplate() {
  const res = await apiFetch("/api/budget/template", { method: "POST" });
  return res.json();
}
export async function createBudgetGroup(name, type) {
  const res = await apiFetch("/api/budget/groups", { method: "POST", body: JSON.stringify({ name, type }) });
  return res.json();
}
export async function updateBudgetGroup(id, data) {
  const res = await apiFetch(`/api/budget/groups/${id}`, { method: "PATCH", body: JSON.stringify(data) });
  return res.json();
}
export async function deleteBudgetGroup(id) {
  const res = await apiFetch(`/api/budget/groups/${id}`, { method: "DELETE" });
  return res.json();
}
export async function createBudgetItem(groupId, name) {
  const res = await apiFetch(`/api/budget/groups/${groupId}/items`, { method: "POST", body: JSON.stringify({ name }) });
  return res.json();
}
export async function updateBudgetItem(id, name) {
  const res = await apiFetch(`/api/budget/items/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
  return res.json();
}
export async function deleteBudgetItem(id) {
  const res = await apiFetch(`/api/budget/items/${id}`, { method: "DELETE" });
  return res.json();
}
export async function setBudgetAmount(itemId, month, planned) {
  const res = await apiFetch(`/api/budget/items/${itemId}/amount`, {
    method: "PUT", body: JSON.stringify({ month, planned }),
  });
  return res.json();
}
export async function getUnassignedTransactions(month) {
  const res = await apiFetch(`/api/budget/unassigned/${month}`);
  return res.json();
}
// All unassigned transactions across recent months — used by the Review Queue.
export async function getAllUnassignedTransactions() {
  const res = await apiFetch("/api/budget/unassigned");
  return res.json();
}
export async function getAssignedTransactions(month) {
  const res = await apiFetch(`/api/budget/assigned/${month}`);
  return res.json();
}
export async function assignTransaction(transactionId, itemId) {
  const res = await apiFetch("/api/budget/assign", { method: "POST", body: JSON.stringify({ transaction_id: transactionId, item_id: itemId }) });
  return res.json();
}
export async function unassignTransaction(transactionId) {
  const res = await apiFetch(`/api/budget/assign/${encodeURIComponent(transactionId)}`, { method: "DELETE" });
  return res.json();
}
export async function autoAssignFromHistory(month) {
  const res = await apiFetch(`/api/budget/auto-assign/${month}`, { method: "POST" });
  return res.json();
}
export async function unassignAll(month) {
  const res = await apiFetch(`/api/budget/assign-all/${month}`, { method: "DELETE" });
  return res.json();
}
export async function autoAssignDebug(month) {
  const res = await apiFetch(`/api/budget/auto-assign/${month}/debug`);
  return res.json();
}
/** Fetch transactions Sage auto-categorized during sync that need approval. */
export async function getPendingReviewTransactions(month) {
  const res = await apiFetch(`/api/budget/pending-review/${month}`);
  return res.json();
}

/** Fetch ALL pending_review transactions across every month — used by the
 *  mobile Review Queue page (/review) so nothing is missed. */
export async function getAllPendingReview() {
  const res = await apiFetch("/api/budget/pending-review");
  return res.json();
}

/** Approve or correct a Sage-suggested assignment.
 *  itemId = same as suggestion → approve; different → correct + learn. */
export async function approveTransaction(transactionId, itemId) {
  const res = await apiFetch("/api/budget/assign/approve", {
    method: "POST",
    body: JSON.stringify({ transaction_id: transactionId, item_id: itemId }),
  });
  return res.json();
}

export async function getItemDetail(itemId, month) {
  const res = await apiFetch(`/api/budget/items/${itemId}/detail?month=${month}`);
  return res.json();
}
export async function reorderGroups(ids) {
  const res = await apiFetch("/api/budget/groups/reorder", {
    method: "PATCH", body: JSON.stringify({ ids }),
  });
  return res.json();
}
export async function reorderItems(ids) {
  const res = await apiFetch("/api/budget/items/reorder", {
    method: "PATCH", body: JSON.stringify({ ids }),
  });
  return res.json();
}

// ── Fund Financials ───────────────────────────────────────────────────────────
export async function getFunds() {
  const res = await apiFetch("/api/funds");
  return res.json();
}
export async function createFund(data) {
  const res = await apiFetch("/api/funds", { method: "POST", body: JSON.stringify(data) });
  return res.json();
}
export async function updateFund(id, data) {
  const res = await apiFetch(`/api/funds/${id}`, { method: "PATCH", body: JSON.stringify(data) });
  return res.json();
}
export async function deleteFund(id) {
  const res = await apiFetch(`/api/funds/${id}`, { method: "DELETE" });
  return res.json();
}
export async function getFundTransactions(id) {
  const res = await apiFetch(`/api/funds/${id}/transactions`);
  return res.json();
}
export async function addFundTransaction(id, data) {
  const res = await apiFetch(`/api/funds/${id}/transactions`, { method: "POST", body: JSON.stringify(data) });
  return res.json();
}
export async function deleteFundTransaction(id) {
  const res = await apiFetch(`/api/funds/transactions/${id}`, { method: "DELETE" });
  return res.json();
}
export async function getSheetFundFinancials(limit = 6) {
  const res = await apiFetch(`/api/sheet/fund-financials?limit=${limit}`);
  return res.json();
}

// Toggles whether a manual entry is excluded from the net worth total.
// Useful when a PDF import creates both an "Overall Portfolio" summary entry
// and individual per-account entries — exclude the summary to avoid double-counting.
export async function toggleExcludeFromNetWorth(id) {
  const res = await apiFetch(`/api/manual/${id}/exclude`, { method: "PATCH" });
  return res.json(); // { exclude_from_net_worth: 0 | 1 }
}

// Soft-delete a transaction from the budget (excluded from all queues and spending totals)
export async function budgetDeleteTransaction(transactionId) {
  const res = await apiFetch(`/api/budget/transactions/${transactionId}`, { method: "DELETE" });
  return res.json();
}

// Restore a soft-deleted transaction back to the unassigned queue
export async function budgetRestoreTransaction(transactionId) {
  const res = await apiFetch(`/api/budget/transactions/${transactionId}/restore`, { method: "POST" });
  return res.json();
}

// Get soft-deleted transactions for a month (no arg = current month)
export async function getDeletedTransactions(month) {
  const m = month || new Date().toISOString().slice(0, 7);
  const res = await apiFetch(`/api/budget/deleted/${m}`);
  return res.json();
}

// ── Edit Expense / transaction split API ──────────────────────────────────────

/** Fetch full transaction details + current assignment/splits for the edit modal. */
export async function getTransaction(transactionId) {
  const res = await apiFetch(`/api/budget/transactions/${encodeURIComponent(transactionId)}`);
  return res.json();
}

/** Save assignment or split for a transaction.
 *  splits: [{item_id, amount}, ...] — amounts must sum to the transaction total.
 *  meta: optional {check_number, notes} stored in transaction_metadata.
 */
export async function saveTransactionSplits(transactionId, splits, meta = {}) {
  const res = await apiFetch(
    `/api/budget/transactions/${encodeURIComponent(transactionId)}/splits`,
    { method: "PUT", body: JSON.stringify({ splits, ...meta }) }
  );
  return res.json();
}

// ── Web Push / PWA ────────────────────────────────────────────────────────────

/** Fetch the server's VAPID public key (no auth required). */
export async function getPushVapidKey() {
  const res = await fetch("/api/push/vapid-public-key");
  if (!res.ok) throw new Error("Push not configured on server");
  const data = await res.json();
  return data.publicKey;
}

/**
 * Convert a base64url string to a Uint8Array.
 * Required to pass the VAPID public key as applicationServerKey to
 * PushManager.subscribe(), which expects a BufferSource, not a string.
 */
export function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64  = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw     = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

/**
 * Subscribe this browser to Web Push and register the subscription with
 * the Vaultic server. Returns the PushSubscription object on success.
 *
 * Flow:
 *   1. Fetch VAPID public key from server
 *   2. Request notification permission from the user
 *   3. Call PushManager.subscribe() with the VAPID key
 *   4. POST the subscription to /api/push/subscribe
 */
export async function subscribePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    throw new Error("Push notifications are not supported in this browser");
  }

  const vapidKey = await getPushVapidKey();
  const reg      = await navigator.serviceWorker.ready;

  const subscription = await reg.pushManager.subscribe({
    userVisibleOnly:      true,
    applicationServerKey: urlBase64ToUint8Array(vapidKey),
  });

  const json = subscription.toJSON();
  const res  = await apiFetch("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: json.endpoint,
      p256dh:   json.keys.p256dh,
      auth:     json.keys.auth,
    }),
  });
  const data = await res.json();

  // Store the device_token so the Review page can silently re-authenticate
  // after the normal JWT expires, without requiring a manual login.
  if (data.device_token) {
    localStorage.setItem("vaultic_device_token", data.device_token);
  }

  return subscription;
}

/** Unsubscribe from Web Push and notify the server to deactivate the record. */
export async function unsubscribePush() {
  if (!("serviceWorker" in navigator)) return;

  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return;

  const endpoint = sub.endpoint;
  await sub.unsubscribe();

  await apiFetch("/api/push/unsubscribe", {
    method: "POST",
    body: JSON.stringify({ endpoint }),
  });
}

/** Return the current PushSubscription, or null if not subscribed. */
export async function getPushSubscription() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

/** Fire a test push notification to all active subscriptions. */
export async function sendTestPush() {
  const res = await apiFetch("/api/push/test", { method: "POST" });
  return res.json();
}

// ─── Tax ──────────────────────────────────────────────────────────────────────
export async function getTaxReturns() {
  const res = await apiFetch("/api/tax/returns");
  return res.json();
}

export async function getTaxReturn(year) {
  const res = await apiFetch(`/api/tax/returns/${year}`);
  return res.json();
}

export async function getTaxSummary() {
  const res = await apiFetch("/api/tax/summary");
  return res.json();
}

export async function uploadTaxPdf(file) {
  const token = localStorage.getItem("vaultic_token");
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/tax/parse-pdf", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  if (!res.ok) throw new Error(`Upload failed (${res.status})`);
  return res.json();
}

// ── Document Vault ────────────────────────────────────────────────────────────
export async function getVaultYears() {
  const res = await apiFetch("/api/vault/years");
  return res.json();
}

export async function getVaultDocuments(year) {
  const res = await apiFetch(`/api/vault/documents/${year}`);
  return res.json();
}

export async function getVaultChecklist(year) {
  const res = await apiFetch(`/api/vault/checklist/${year}`);
  return res.json();
}

export async function getDeductionTracker(year) {
  const res = await apiFetch(`/api/vault/deductions/${year}`);
  return res.json();
}

export async function uploadToVault(file, year, category, issuer, description, autoRename = false) {
  const token = localStorage.getItem("vaultic_token");
  const formData = new FormData();
  formData.append("file", file);
  formData.append("year", year || 0);
  formData.append("category", category || "other");
  if (issuer) formData.append("issuer", issuer);
  if (description) formData.append("description", description);
  formData.append("auto_parse", autoRename ? "true" : "false");
  const res = await fetch("/api/vault/upload", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  if (!res.ok) throw new Error(`Upload failed (${res.status})`);
  return res.json();
}

export async function downloadVaultDoc(id, filename) {
  const token = localStorage.getItem("vaultic_token");
  const res = await fetch(`/api/vault/download/${id}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "document.pdf";
  a.click();
  URL.revokeObjectURL(url);
}

export async function deleteVaultDoc(id) {
  const res = await apiFetch(`/api/vault/documents/${id}`, { method: "DELETE" });
  return res.json();
}

export async function uploadTaxDoc(file) {
  const token = localStorage.getItem("vaultic_token");
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/tax/docs/upload", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  if (!res.ok) throw new Error(`Upload failed (${res.status})`);
  return res.json();
}

export async function getTaxDocs(year) {
  const res = await apiFetch(`/api/tax/docs/${year}`);
  return res.json();
}

export async function deleteTaxDoc(id) {
  const res = await apiFetch(`/api/tax/docs/${id}`, { method: "DELETE" });
  return res.json();
}

export async function getEstimatedPayments(year, otherIncome = 0) {
  const res = await apiFetch(`/api/tax/estimated-payments/${year}?other_income=${otherIncome}`);
  return res.json();
}

export async function getDraftReturn(year) {
  const res = await apiFetch(`/api/tax/draft/${year}`);
  return res.json();
}

export async function getTaxProjection(year) {
  const res = await apiFetch(`/api/tax/projection/${year}`);
  return res.json();
}

export async function getTaxChecklist(year) {
  const res = await apiFetch(`/api/tax/checklist/${year}`);
  return res.json();
}

export async function getW4s() {
  const res = await apiFetch("/api/tax/w4s");
  return res.json();
}

export async function getW4WizardPrefill() {
  const res = await apiFetch("/api/tax/w4-wizard/prefill");
  return res.json();
}

export async function runW4Wizard(input) {
  const res = await apiFetch("/api/tax/w4-wizard", { method: "POST", body: JSON.stringify(input) });
  return res.json();
}

export async function uploadW4(file) {
  const token = localStorage.getItem("vaultic_token");
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/tax/upload-w4", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  if (!res.ok) throw new Error(`Upload failed (${res.status})`);
  return res.json();
}

export async function getPaystubs() {
  const res = await apiFetch("/api/paystubs");
  return res.json();
}

export async function getPaystubsYtd() {
  const res = await apiFetch("/api/paystubs/ytd");
  return res.json();
}

export async function uploadPaystub(file) {
  const token = localStorage.getItem("vaultic_token");
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/paystubs/upload", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  if (!res.ok) throw new Error(`Upload failed (${res.status})`);
  return res.json();
}

// ── Market Rates ──────────────────────────────────────────────────────────────
export async function getMarketRates() {
  const res = await apiFetch("/api/market/rates");
  return res.json(); // { rates: [{label, value, source}], cached }
}

/**
 * Exchange the stored device_token for a fresh JWT.
 *
 * Called by the Review page when no valid JWT is present (e.g. after the
 * 24-hour token expires). Returns true if a new JWT was successfully issued
 * and stored, false if the device token is missing or invalid.
 */
export async function deviceAuth() {
  const deviceToken = localStorage.getItem("vaultic_device_token");
  if (!deviceToken) return false;

  try {
    const res = await fetch("/api/push/device-auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_token: deviceToken }),
    });
    if (!res.ok) return false;
    const { token } = await res.json();
    setToken(token);
    return true;
  } catch {
    return false;
  }
}


// ── Financial Calendar ─────────────────────────────────────────────────────────
export async function getUpcomingEvents(days = 14) {
  const res = await apiFetch(`/api/calendar/upcoming?days=${days}`);
  return res.json();
}
export async function seedCalendarEvents() {
  const res = await apiFetch("/api/calendar/seed", { method: "POST" });
  return res.json();
}
export async function getCalendarEvents(fromDate, toDate) {
  const params = fromDate && toDate ? `?from_date=${fromDate}&to_date=${toDate}` : "";
  const res = await apiFetch(`/api/calendar${params}`);
  return res.json();
}
export async function createCalendarEvent(data) {
  const res = await apiFetch("/api/calendar", { method: "POST", body: JSON.stringify(data) });
  return res.json();
}
export async function updateCalendarEvent(id, data) {
  const res = await apiFetch(`/api/calendar/${id}`, { method: "PATCH", body: JSON.stringify(data) });
  return res.json();
}
export async function deleteCalendarEvent(id) {
  const res = await apiFetch(`/api/calendar/${id}`, { method: "DELETE" });
  return res.json();
}
