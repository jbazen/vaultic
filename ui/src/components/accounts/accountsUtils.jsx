// Pseudo-keys for manual sections so they can be ordered alongside institutions
// in the shared institution_display_order table.
export const SEC_INVESTED       = "__sec_investment_imported__";
export const SEC_INVESTED_OTHER = "__sec_investment_other__";
export const SEC_LIQUID         = "__sec_hsa_cash__";
export const SEC_PROPERTY       = "__sec_property_vehicles__";
export const SEC_LIABILITIES    = "__sec_liabilities__";

// A Plaid item that hasn't synced in over 2 days has almost certainly dropped
// its login (the cron runs 4×/day) — surface it so the user can reconnect.
const STALE_SYNC_MS = 2 * 24 * 60 * 60 * 1000;
export function isSyncStale(lastSyncedAt) {
  if (!lastSyncedAt) return true;
  const t = new Date(String(lastSyncedAt).replace(" ", "T")).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t > STALE_SYNC_MS;
}

export function isLiability(type) {
  return type === "credit" || type === "loan";
}

// ── Plaid Investment Detection ─────────────────────────────────────────────────
export function isPlaidInvestment(account) {
  if (account.is_manual) return false;
  return account.type === "investment" ||
    ["401k", "ira", "roth", "pension", "brokerage"].includes(account.subtype);
}

export function tabBtn(active) {
  return {
    background: active ? "var(--accent)" : "var(--bg)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "4px 14px",
    fontSize: 12, fontWeight: active ? 700 : 400,
    cursor: "pointer",
  };
}
