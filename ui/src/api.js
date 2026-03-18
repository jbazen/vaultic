const getToken = () => localStorage.getItem("vaultic_token");
const setToken = (t) => localStorage.setItem("vaultic_token", t);
const clearToken = () => localStorage.removeItem("vaultic_token");

async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("auth:logout"));
    throw new Error("Session expired");
  }

  return res;
}

export async function login(username, password) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error("Invalid credentials");
  const data = await res.json();
  if (data.requires_2fa) return { requires_2fa: true, username: data.username };
  setToken(data.token);
  return { requires_2fa: false };
}

export async function verify2FA(username, code) {
  const res = await fetch("/api/auth/verify-2fa", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, code }),
  });
  if (!res.ok) throw new Error("Invalid or expired code");
  const { token } = await res.json();
  setToken(token);
}

export function logout() {
  clearToken();
  window.dispatchEvent(new Event("auth:logout"));
}

export function isAuthed() {
  return !!getToken();
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
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Sage error (${res.status})`);
  }
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
  if (!res.ok) {
    // We must explicitly read the JSON body to extract the `detail` field from
    // FastAPI's HTTPException response. Simply doing `throw new Error(res.statusText)`
    // would give a generic "Internal Server Error" with no useful context.
    // The `.catch(() => ({}))` guard handles edge cases where the error response
    // body is not valid JSON (e.g. network-level errors or proxy errors that return
    // plain text), falling back to a generic message instead of a secondary throw.
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "TTS unavailable");
  }
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
  if (!res.ok) throw new Error("Setup failed");
  return res.text(); // SVG markup
}

export async function totpConfirm(code) {
  const res = await apiFetch("/api/auth/2fa/confirm", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
  if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Invalid code"); }
  return res.json();
}

export async function disable2FA() {
  const res = await apiFetch("/api/auth/2fa", { method: "DELETE" });
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
  if (!res.ok) throw new Error("Save failed");
  return res.json();
}

// --- Crypto ---
export async function syncCoinbase() {
  const res = await apiFetch("/api/crypto/sync", { method: "POST" });
  if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Sync failed"); }
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

// Toggles whether a manual entry is excluded from the net worth total.
// Useful when a PDF import creates both an "Overall Portfolio" summary entry
// and individual per-account entries — exclude the summary to avoid double-counting.
export async function toggleExcludeFromNetWorth(id) {
  const res = await apiFetch(`/api/manual/${id}/exclude`, { method: "PATCH" });
  return res.json(); // { exclude_from_net_worth: 0 | 1 }
}
