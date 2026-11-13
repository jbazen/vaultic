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

// ── Block list (institutions + manual sections) ────────────────────────────────

// Splits manual entries into the collections each manual section renders.
export function splitManualEntries(manualEntries) {
  const investedImported = manualEntries
    .filter(e => e.category === "invested" && (e.account_number || e.exclude_from_net_worth))
    .sort((a, b) => (b.exclude_from_net_worth - a.exclude_from_net_worth) || a.name.localeCompare(b.name));
  return {
    investedImported,
    investedOther:    manualEntries.filter(e => e.category === "invested" && !e.account_number && !e.exclude_from_net_worth),
    liquidEntries:    manualEntries.filter(e => e.category === "liquid"),
    propertyEntries:  manualEntries.filter(e => ["home_value", "car_value", "real_estate", "vehicles"].includes(e.category)),
    liabilityEntries: manualEntries.filter(e => e.category === "other_liability"),
  };
}

// Unified block list: one block per institution plus one per non-empty manual section.
export function buildAccountBlocks(accounts, manual) {
  const grouped = accounts.reduce((acc, a) => {
    const key = a.institution_name || "Manual";
    if (!acc[key]) acc[key] = [];
    acc[key].push(a);
    return acc;
  }, {});
  const blocks = [];
  for (const [inst, accts] of Object.entries(grouped)) {
    blocks.push({ key: inst, kind: "institution", accts });
  }
  if (manual.investedImported.length > 0) blocks.push({ key: SEC_INVESTED,       kind: "sec_invested" });
  if (manual.investedOther.length > 0)    blocks.push({ key: SEC_INVESTED_OTHER, kind: "sec_invested_other" });
  if (manual.liquidEntries.length > 0)    blocks.push({ key: SEC_LIQUID,         kind: "sec_liquid" });
  if (manual.propertyEntries.length > 0)  blocks.push({ key: SEC_PROPERTY,       kind: "sec_property" });
  if (manual.liabilityEntries.length > 0) blocks.push({ key: SEC_LIABILITIES,    kind: "sec_liabilities" });
  return blocks;
}

// Sort blocks by the saved order. Blocks missing from it fall to the end:
// institutions first (A-Z), then manual sections in their natural order.
export function sortBlocks(blocks, savedOrder) {
  const orderMap = new Map(savedOrder.map((n, i) => [n, i]));
  const defaultIdx = new Map(blocks.map((b, i) => [b.key, i]));
  return [...blocks].sort((a, b) => {
    const oA = orderMap.get(a.key);
    const oB = orderMap.get(b.key);
    if (oA != null && oB != null) return oA - oB;
    if (oA != null) return -1;
    if (oB != null) return 1;
    if (a.kind === "institution" && b.kind !== "institution") return -1;
    if (a.kind !== "institution" && b.kind === "institution") return 1;
    if (a.kind === "institution" && b.kind === "institution") return a.key.localeCompare(b.key);
    return defaultIdx.get(a.key) - defaultIdx.get(b.key);
  });
}
