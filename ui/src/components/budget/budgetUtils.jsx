/**
 * budgetUtils — Shared constants and helpers for the budget module (palette, month math, drag handle).
 */
// ── Shared budget utilities ──────────────────────────────────────────────────

export const PALETTE = [
  "#3b82f6", // blue
  "#a855f7", // purple
  "#f59e0b", // amber
  "#f97316", // orange
  "#14b8a6", // teal
  "#ec4899", // pink
  "#ef4444", // red
  "#64748b", // slate
  "#0ea5e9", // sky
  "#84cc16", // lime
  "#6b7280", // gray
  "#8b5cf6", // violet
];

export function getGroupColor(index) {
  return PALETTE[index % PALETTE.length];
}

export function monthLabel(m) {
  const [y, mo] = m.split("-");
  return new Date(parseInt(y), parseInt(mo) - 1, 1)
    .toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

export function prevMonth(m) {
  const [y, mo] = m.split("-").map(Number);
  const d = new Date(y, mo - 2, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function nextMonth(m) {
  const [y, mo] = m.split("-").map(Number);
  const d = new Date(y, mo, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Format a budget item's remaining amount as a plain dollar string
 * (no parens). Returns "—" when remaining is null/undefined.
 */
function _itemAmountString(item) {
  const rem = item?.remaining;
  if (rem == null) return "—";
  const n = Number(rem);
  return n >= 0 ? `$${n.toFixed(2)}` : `-$${Math.abs(n).toFixed(2)}`;
}

/**
 * Compute the maximum name/amount character widths across a list of items,
 * so every option in a dropdown can be padded to the same width and the
 * amounts visually align into a right-justified column (when the <select>
 * uses a monospace font — add className="budget-item-select").
 */
export function computeBudgetItemColumnWidths(items, { withGroup } = {}) {
  let nameWidth = 0;
  let amountWidth = 0;
  for (const item of items || []) {
    const groupPrefix = withGroup
      ? `${item.groupName || ""} › `
      : "";
    const fullName = groupPrefix + (item.name || "");
    if (fullName.length > nameWidth) nameWidth = fullName.length;
    const amountStr = _itemAmountString(item);
    if (amountStr.length > amountWidth) amountWidth = amountStr.length;
  }
  return { nameWidth, amountWidth };
}

/**
 * Format a budget item as a single-line dropdown option label.
 * Pads name on the right and amount on the left with non-breaking spaces
 * so amounts right-justify into a column. The containing <select> must
 * use a monospace font for the alignment to render correctly — apply
 * className="budget-item-select".
 */
export function formatBudgetItemOption(
  item,
  { withGroup, nameWidth = 0, amountWidth = 0 } = {}
) {
  const groupPrefix = withGroup ? `${withGroup} › ` : "";
  const fullName = groupPrefix + item.name;
  const amountStr = _itemAmountString(item);
  const paddedName = fullName.padEnd(nameWidth, "\u00a0");
  const paddedAmount = amountStr.padStart(amountWidth, "\u00a0");
  // Two non-breaking spaces of gap between name column and amount column
  return `${paddedName}\u00a0\u00a0${paddedAmount}`;
}

// ── Drag handle — 6-dot grip icon shown on hover to the left of names ─────────
export function DragHandle({ onMouseDown }) {
  return (
    <div
      onMouseDown={onMouseDown}
      style={{
        display: "flex", flexDirection: "column", gap: 2,
        padding: "0 4px", cursor: "grab", flexShrink: 0, opacity: 0,
        transition: "opacity 0.15s",
      }}
      className="drag-handle"
      title="Drag to reorder"
    >
      {[0, 1].map(row => (
        <div key={row} style={{ display: "flex", gap: 2 }}>
          {[0, 1, 2].map(col => (
            <div key={col} style={{
              width: 3, height: 3, borderRadius: "50%",
              background: "var(--text2)",
            }} />
          ))}
        </div>
      ))}
    </div>
  );
}
