/**
 * Shared formatting helpers used across Vaultic pages.
 *
 * Canonical implementations — every page imports from here instead of
 * defining its own copy.
 */

/**
 * Format a number as USD currency (absolute value).
 * @param {number|null} v - value to format
 * @param {object} opts - Intl.NumberFormat overrides (e.g. { maximumFractionDigits: 0 })
 * @returns {string}
 */
export function fmt(v, opts = {}) {
  if (v == null) return "\u2014";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    ...opts,
  }).format(Math.abs(v));
}

/**
 * Format a number as signed USD currency (preserves negative sign).
 * @param {number|null} v
 * @returns {string}
 */
export function fmtSigned(v) {
  if (v == null) return "\u2014";
  const n = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(v));
  return v < 0 ? `-${n}` : n;
}

/**
 * Format an amount in the currency Plaid reported it in (#141), absolute
 * value like fmt. The ISO code wins, then the unofficial code (crypto such
 * as BTC or GBX pence, shown as "0.5 BTC"); USD when neither is set.
 * @param {number|null} v
 * @param {string|null} iso - iso_currency_code
 * @param {string|null} unofficial - unofficial_currency_code
 * @returns {string}
 */
export function fmtMoney(v, iso, unofficial) {
  if (v == null) return "—";
  if (!iso && unofficial) return `${fmtNum(Math.abs(v), 8)} ${unofficial}`;
  if (!iso || iso === "USD") return fmt(v);
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: iso }).format(Math.abs(v));
  } catch {
    return `${fmtNum(Math.abs(v), 2)} ${iso}`;
  }
}

/**
 * Format a date string ("YYYY-MM-DD" or full datetime) for display.
 * @param {string} s
 * @param {object} opts - toLocaleDateString options override
 * @returns {string}
 */
export function fmtDate(s, opts) {
  if (!s) return "";
  const dateOnly = s.length > 10 ? s.substring(0, 10) : s;
  return new Date(dateOnly + "T12:00:00").toLocaleDateString(
    "en-US",
    opts || { month: "short", day: "numeric", year: "numeric" },
  );
}

/**
 * Format a crypto quantity — trims trailing zeros.
 * @param {number|string|null} v
 * @param {number} decimals - max decimal places (default 8)
 * @returns {string}
 */
export function fmtCrypto(v, decimals = 8) {
  if (v == null) return "\u2014";
  return Number(v)
    .toFixed(decimals)
    .replace(/(\.\d*?[1-9])0+$/, "$1")
    .replace(/\.0+$/, "");
}

/**
 * Format a number as compact USD (e.g. "$1.23M", "$45K", "$99").
 * @param {number|null} v
 * @returns {string}
 */
export function fmtCompact(v) {
  if (v == null) return "\u2014";
  const abs = Math.abs(v);
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

/**
 * Format a percentage value (e.g. "12.34%").
 * @param {number|null} v
 * @param {number} decimals - decimal places (default 2)
 * @returns {string}
 */
export function fmtPercent(v, decimals = 2) {
  if (v == null) return "\u2014";
  return `${Number(v).toFixed(decimals)}%`;
}

/**
 * Format a USD price (allows negative values, unlike fmt which uses abs).
 * @param {number|null} v
 * @returns {string}
 */
export function fmtPrice(v) {
  if (v == null) return "\u2014";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(v);
}

/**
 * Format a number with locale grouping.
 * @param {number|null} v
 * @param {number} decimals - max decimal places (default 4)
 * @returns {string}
 */
export function fmtNum(v, decimals = 4) {
  if (v == null) return "\u2014";
  return Number(v).toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

/**
 * Format a date for chart X-axis labels.
 * Short form (month + day) for <=90 days, otherwise month + 2-digit year.
 * @param {string} s - "YYYY-MM-DD"
 * @param {number} days - range width in days
 * @returns {string}
 */
export function fmtAxisDate(s, days) {
  if (!s) return "";
  const d = new Date(s + "T12:00:00");
  if (days <= 90)
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
}
