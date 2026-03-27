// ── Shared account helpers ────────────────────────────────────────────────────

// Detect retirement accounts from Plaid subtype or account/entry name.
// Uses regex instead of an exact-match Set because Plaid returns many variants:
// "401k", "roth 401k", "403b", "ira", "roth", "traditional ira", "simple ira", "sep ira", etc.
// Name fallback handles PDF-imported manual entries (always category "invested"):
// "Insperity 401k Plan", "Roth IRA", "IRA Rollover", "Traditional IRA", etc.
export function isRetirementAccount(subtype, name) {
  return /401|403b|roth|\bira\b|sep\s*ira|simple\s*ira|pension/i.test(subtype || "") ||
         /401[\s(]?k|403b|roth|\bira\b|rollover\s*ira|pension/i.test(name || "");
}
