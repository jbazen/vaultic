/**
 * Budget.jsx — Zero-based monthly budget page.
 *
 * Layout in Vaultic's dark theme:
 *   • Left column  — collapsible budget groups with inline editing
 *   • Right panel  — sticky Summary (donut chart) or Transactions panel
 *
 * Key interactions:
 *   • Month picker  — click the month label to open a year/month grid
 *   • Column toggle — each expense group's last column header (Remaining ▾)
 *                     toggles between "Remaining" (blue/red) and "Spent" (green)
 *   • Inline edit   — double-click any item name; single-click the planned amount
 *   • Group edit    — click the group title to rename or delete
 *   • Transactions  — "New" tab shows unassigned (with auto-suggested category
 *                     badges from budget_auto_rules); "Tracked" shows assigned
 */

import { useState, useEffect, useRef, useCallback } from "react";
import {
  getBudget,
  createBudgetGroup, updateBudgetGroup, deleteBudgetGroup,
  createBudgetItem, updateBudgetItem, deleteBudgetItem, setBudgetAmount,
  getUnassignedTransactions, getAssignedTransactions,
  assignTransaction, unassignTransaction, autoAssignFromHistory,
  getItemDetail,
} from "../api.js";

// ── Color palette — one color per expense group (income is always green) ──────
const PALETTE = [
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

function getGroupColor(index) {
  return PALETTE[index % PALETTE.length];
}

// ── Formatters ────────────────────────────────────────────────────────────────
function fmt(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(Math.abs(v));
}

function monthLabel(m) {
  const [y, mo] = m.split("-");
  return new Date(parseInt(y), parseInt(mo) - 1, 1)
    .toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

function prevMonth(m) {
  const [y, mo] = m.split("-").map(Number);
  const d = new Date(y, mo - 2, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function nextMonth(m) {
  const [y, mo] = m.split("-").map(Number);
  const d = new Date(y, mo, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ── Month picker dropdown ─────────────────────────────────────────────────────
function MonthPicker({ month, onChange, onClose }) {
  const [pickerYear, setPickerYear] = useState(parseInt(month.split("-")[0]));
  const ref = useRef(null);

  // Close on outside click
  useEffect(() => {
    function handle(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [onClose]);

  return (
    <div ref={ref} style={{
      position: "absolute", top: "calc(100% + 8px)", left: "50%",
      transform: "translateX(-50%)", zIndex: 300,
      background: "var(--bg2)", border: "1px solid var(--border)",
      borderRadius: 12, padding: 16, boxShadow: "0 12px 32px rgba(0,0,0,0.5)",
      minWidth: 260,
    }}>
      {/* Year navigation */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <button onClick={() => setPickerYear(y => y - 1)}
          style={{ background: "none", border: "none", color: "var(--text)", cursor: "pointer", fontSize: 20, padding: "0 8px", lineHeight: 1 }}>
          ‹
        </button>
        <span style={{ fontWeight: 700, fontSize: 15, color: "var(--text)" }}>{pickerYear}</span>
        <button onClick={() => setPickerYear(y => y + 1)}
          style={{ background: "none", border: "none", color: "var(--text)", cursor: "pointer", fontSize: 20, padding: "0 8px", lineHeight: 1 }}>
          ›
        </button>
      </div>

      {/* Month grid — 4 columns × 3 rows */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
        {MONTH_NAMES.map((name, i) => {
          const m = `${pickerYear}-${String(i + 1).padStart(2, "0")}`;
          const selected = m === month;
          return (
            <button key={name} onClick={() => { onChange(m); onClose(); }}
              style={{
                padding: "8px 0", borderRadius: 6, fontSize: 13,
                fontWeight: selected ? 700 : 400,
                background: selected ? "var(--accent)" : "transparent",
                color: selected ? "#fff" : "var(--text)",
                border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                cursor: "pointer",
              }}>
              {name}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── SVG donut chart ───────────────────────────────────────────────────────────
function DonutChart({ groups, mode, summary }) {
  const size = 200;
  const cx = 100, cy = 100;
  const r = 72;
  const strokeW = 32;
  const circ = 2 * Math.PI * r;

  // Only expense groups contribute to the donut segments
  const expGroups = groups.filter(g => g.type !== "income");

  // Pick the value for each segment based on the current summary mode
  function segVal(g) {
    if (mode === "spent") return g.total_spent;
    if (mode === "remaining") return Math.max(0, g.total_planned - g.total_spent);
    return g.total_planned; // "planned" (default)
  }

  const total = expGroups.reduce((sum, g) => sum + segVal(g), 0);

  // Build arc segments — each one continues where the last left off
  let cumLen = 0;
  const segments = expGroups.map((g, i) => {
    const val = segVal(g);
    const len = total > 0 ? (val / total) * circ : 0;
    const seg = { g, len, offset: cumLen, color: getGroupColor(i) };
    cumLen += len;
    return seg;
  });

  // Center label depends on mode
  let centerLabel, centerValue;
  if (mode === "spent") {
    centerLabel = "SPENT";
    centerValue = summary.total_expense_spent;
  } else if (mode === "remaining") {
    centerLabel = "LEFT";
    centerValue = summary.total_income_planned - summary.total_expense_spent;
  } else {
    centerLabel = "INCOME";
    centerValue = summary.total_income_planned;
  }

  return (
    <div style={{ position: "relative", width: size, height: size, margin: "0 auto" }}>
      {/* Rotate -90° so the first segment starts at 12 o'clock */}
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        {/* Gray background track */}
        <circle cx={cx} cy={cy} r={r} fill="none"
          stroke="var(--bg3)" strokeWidth={strokeW} />
        {/* Colored segments */}
        {segments.map((seg, i) =>
          seg.len > 0 && (
            <circle key={i} cx={cx} cy={cy} r={r}
              fill="none" stroke={seg.color} strokeWidth={strokeW}
              strokeDasharray={`${seg.len} ${circ}`}
              strokeDashoffset={-seg.offset}
            />
          )
        )}
      </svg>

      {/* Center text overlay — not rotated */}
      <div style={{
        position: "absolute", top: "50%", left: "50%",
        transform: "translate(-50%, -50%)",
        textAlign: "center", pointerEvents: "none",
      }}>
        <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text2)", letterSpacing: "1px" }}>
          {centerLabel}
        </div>
        <div style={{ fontSize: 18, fontWeight: 800, color: "var(--text)", lineHeight: 1.2, marginTop: 2 }}>
          {fmt(centerValue)}
        </div>
      </div>
    </div>
  );
}

// ── Summary panel (right side) ────────────────────────────────────────────────
function SummaryPanel({ groups, summary }) {
  // mode controls what the donut center + highlighted column show
  const [mode, setMode] = useState("planned");

  const expGroups = groups.filter(g => g.type !== "income");
  const incGroups = groups.filter(g => g.type === "income");

  // Map expense groups to their palette index (same as GroupSection)
  let expIdx = 0;
  const groupColors = {};
  groups.forEach(g => {
    if (g.type !== "income") groupColors[g.id] = expIdx++;
  });

  function modeBtn(m, label) {
    const active = mode === m;
    return (
      <button onClick={() => setMode(m)} style={{
        flex: 1, padding: "5px 0", fontSize: 10, fontWeight: active ? 700 : 500,
        background: "none", border: "none",
        borderBottom: `2px solid ${active ? "var(--accent)" : "transparent"}`,
        color: active ? "var(--accent)" : "var(--text2)",
        cursor: "pointer", letterSpacing: "0.8px", textTransform: "uppercase",
      }}>
        {label}
      </button>
    );
  }

  return (
    <div>
      {/* Mode selector */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: 16 }}>
        {modeBtn("planned", "Planned")}
        {modeBtn("spent", "Spent")}
        {modeBtn("remaining", "Left")}
      </div>

      {/* Donut chart */}
      <DonutChart groups={groups} mode={mode} summary={summary} />

      {/* Group breakdown table */}
      <div style={{ marginTop: 16 }}>
        {/* Table header */}
        <div style={{
          display: "grid", gridTemplateColumns: "1fr 72px 72px 72px",
          gap: 4, padding: "4px 0 6px",
          borderBottom: "1px solid var(--border)",
        }}>
          {["", "Planned", "Spent", "Left"].map((h, i) => (
            <div key={i} style={{
              fontSize: 9, fontWeight: 700, color: i > 0 && mode === ["", "planned", "spent", "remaining"][i] ? "var(--accent)" : "var(--text2)",
              textAlign: i === 0 ? "left" : "right",
              textTransform: "uppercase", letterSpacing: "0.6px",
            }}>{h}</div>
          ))}
        </div>

        {/* Expense groups */}
        {expGroups.map(g => {
          const remaining = g.total_planned - g.total_spent;
          const ci = groupColors[g.id] ?? 0;
          return (
            <div key={g.id} style={{
              display: "grid", gridTemplateColumns: "1fr 72px 72px 72px",
              gap: 4, padding: "5px 0",
              borderBottom: "1px solid var(--border)",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: getGroupColor(ci), flexShrink: 0 }} />
                <span style={{ fontSize: 11, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {g.name}
                </span>
              </div>
              <div style={{ fontSize: 11, textAlign: "right", color: "var(--text2)" }}>
                {g.total_planned > 0 ? fmt(g.total_planned) : "—"}
              </div>
              <div style={{ fontSize: 11, textAlign: "right", color: g.total_spent > 0 ? "var(--text)" : "var(--text2)" }}>
                {g.total_spent > 0 ? fmt(g.total_spent) : "—"}
              </div>
              <div style={{ fontSize: 11, textAlign: "right", fontWeight: 600,
                color: remaining < 0 ? "var(--red)" : remaining > 0 ? "#3b82f6" : "var(--text2)" }}>
                {g.total_planned > 0 ? fmt(Math.abs(remaining)) : "—"}
              </div>
            </div>
          );
        })}

        {/* Income groups */}
        {incGroups.map(g => (
          <div key={g.id} style={{
            display: "grid", gridTemplateColumns: "1fr 72px 72px 72px",
            gap: 4, padding: "5px 0",
            borderBottom: "1px solid var(--border)",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", flexShrink: 0 }} />
              <span style={{ fontSize: 11, color: "var(--text)" }}>{g.name}</span>
            </div>
            <div style={{ fontSize: 11, textAlign: "right", color: "var(--text2)" }}>
              {fmt(g.total_planned)}
            </div>
            <div style={{ fontSize: 11, textAlign: "right", color: g.total_spent > 0 ? "#22c55e" : "var(--text2)" }}>
              {g.total_spent > 0 ? fmt(g.total_spent) : "—"}
            </div>
            <div style={{ fontSize: 11, textAlign: "right", color: "var(--text2)" }}>—</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Transactions panel (right side) ───────────────────────────────────────────
function TransactionsPanel({ month, allGroups, onBudgetUpdate }) {
  const [tab, setTab] = useState("new"); // new | tracked
  const [unassigned, setUnassigned] = useState([]);
  const [assigned, setAssigned] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [autoAssigning, setAutoAssigning] = useState(false);
  const [autoResult, setAutoResult] = useState(null); // {assigned, skipped} after run

  async function loadAll() {
    setLoading(true);
    await Promise.all([
      getUnassignedTransactions(month).then(setUnassigned),
      getAssignedTransactions(month).then(setAssigned),
    ]);
    setLoading(false);
  }

  useEffect(() => { loadAll(); setAutoResult(null); }, [month]);

  async function handleAssign(txnId, itemId) {
    if (!itemId) return;
    await assignTransaction(txnId, parseInt(itemId));
    // Reload both the transactions panel and the budget groups (spent/remaining totals)
    await loadAll();
    onBudgetUpdate?.();
  }

  async function handleUnassign(txnId) {
    await unassignTransaction(txnId);
    await loadAll();
    onBudgetUpdate?.();
  }

  async function handleAutoAssign() {
    setAutoAssigning(true);
    setAutoResult(null);
    try {
      const result = await autoAssignFromHistory(month);
      setAutoResult(result);
      if (result.assigned > 0) {
        await loadAll();
        onBudgetUpdate?.();
      }
    } finally {
      setAutoAssigning(false);
    }
  }

  const txns = (tab === "new" ? unassigned : assigned).filter(t => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (t.merchant_name || t.name || "").toLowerCase().includes(q);
  });

  function tabBtn(t, label, count) {
    const active = tab === t;
    return (
      <button onClick={() => setTab(t)} style={{
        padding: "8px 10px", fontSize: 12, fontWeight: active ? 700 : 400,
        background: "none", border: "none",
        borderBottom: `2px solid ${active ? "var(--accent)" : "transparent"}`,
        color: active ? "var(--accent)" : "var(--text2)",
        cursor: "pointer",
      }}>
        {label}{count > 0 ? ` (${count})` : ""}
      </button>
    );
  }

  return (
    <div>
      {/* Sub-tabs */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: 10 }}>
        {tabBtn("new", "New", unassigned.length)}
        {tabBtn("tracked", "Tracked", assigned.length)}
      </div>

      {/* Search */}
      <input value={search} onChange={e => setSearch(e.target.value)}
        placeholder="Search…"
        style={{
          width: "100%", boxSizing: "border-box",
          background: "var(--bg3)", border: "1px solid var(--border)",
          borderRadius: 6, color: "var(--text)", fontSize: 12,
          padding: "6px 10px", marginBottom: 8,
        }}
      />

      {/* Auto-assign button — only shown on New tab when there are unassigned transactions */}
      {tab === "new" && unassigned.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <button onClick={handleAutoAssign} disabled={autoAssigning}
            style={{
              width: "100%", padding: "6px 0", borderRadius: 6, fontSize: 11, fontWeight: 600,
              background: "rgba(59,130,246,0.12)", border: "1px solid rgba(59,130,246,0.3)",
              color: "#3b82f6", cursor: autoAssigning ? "default" : "pointer", opacity: autoAssigning ? 0.6 : 1,
            }}>
            {autoAssigning ? "Matching…" : "⚡ Auto-assign from budget history"}
          </button>
          {autoResult && (
            <div style={{ fontSize: 11, color: "var(--text2)", textAlign: "center", marginTop: 4 }}>
              {autoResult.assigned} assigned, {autoResult.skipped} skipped (ambiguous amount)
            </div>
          )}
        </div>
      )}

      {loading && (
        <div style={{ color: "var(--text2)", fontSize: 12, textAlign: "center", padding: "16px 0" }}>
          Loading…
        </div>
      )}

      {!loading && txns.length === 0 && (
        <div style={{ color: "var(--text2)", fontSize: 12, textAlign: "center", padding: "20px 0" }}>
          {tab === "new" ? "✓ All transactions assigned" : "No tracked transactions this month"}
        </div>
      )}

      {/* Transaction rows */}
      {txns.map(t => {
        const merchant = t.merchant_name || t.name || "Unknown";
        const isDebit = t.amount >= 0; // Plaid: positive = outflow/expense
        return (
          <div key={t.transaction_id} style={{ padding: "9px 0", borderBottom: "1px solid var(--border)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)",
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {merchant}
                </div>
                <div style={{ fontSize: 10, color: "var(--text2)", marginTop: 1 }}>{t.date}</div>

                {/* One-click category suggestion badge (New tab only) */}
                {tab === "new" && t.suggested_item_name && (
                  <button onClick={() => handleAssign(t.transaction_id, t.suggested_item_id)}
                    title={`Assign to ${t.suggested_item_name}`}
                    style={{
                      marginTop: 4, padding: "2px 8px", borderRadius: 12, fontSize: 10,
                      background: "rgba(59,130,246,0.15)", border: "1px solid rgba(59,130,246,0.3)",
                      color: "#3b82f6", cursor: "pointer", fontWeight: 700,
                    }}>
                    + {t.suggested_item_name}
                  </button>
                )}

                {/* Assigned item label (Tracked tab) */}
                {tab === "tracked" && t.item_name && (
                  <div style={{ marginTop: 4, fontSize: 10, color: "var(--accent)", fontWeight: 600 }}>
                    {t.group_name} › {t.item_name}
                  </div>
                )}
              </div>

              <div style={{ textAlign: "right", flexShrink: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: isDebit ? "var(--red)" : "var(--green)" }}>
                  {isDebit ? "-" : "+"}{fmt(Math.abs(t.amount))}
                </div>
                {tab === "tracked" && (
                  <button onClick={() => handleUnassign(t.transaction_id)}
                    style={{ fontSize: 10, color: "var(--text2)", background: "none",
                      border: "none", cursor: "pointer", marginTop: 2, padding: 0 }}>
                    unassign
                  </button>
                )}
              </div>
            </div>

            {/* Assign dropdown (New tab) */}
            {tab === "new" && (
              <select key={t.transaction_id} defaultValue=""
                onChange={e => handleAssign(t.transaction_id, e.target.value)}
                style={{
                  marginTop: 6, width: "100%",
                  background: "var(--bg3)", border: "1px solid var(--border)",
                  borderRadius: 6, color: "var(--text)", fontSize: 11, padding: "4px 8px",
                }}>
                <option value="" disabled>Assign to budget item…</option>
                {allGroups.map(g => {
                  // Mirror the auto-hide logic: only show items active this month.
                  // Items with $0 planned and $0 spent are hidden from the budget
                  // view and shouldn't clutter the assignment dropdown either.
                  const activeItems = g.items.filter(i => i.planned > 0 || i.spent > 0);
                  if (activeItems.length === 0) return null;
                  return (
                    <optgroup key={g.id} label={g.name}>
                      {activeItems.map(i => (
                        <option key={i.id} value={i.id}>{i.name}</option>
                      ))}
                    </optgroup>
                  );
                })}
              </select>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Item detail modal ─────────────────────────────────────────────────────────
// Shows when the user clicks a budget item row. Displays planned/spent/remaining,
// a mini bar chart of the last few months, and all transactions for this month.
function ItemDetailModal({ itemId, itemName, month, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getItemDetail(itemId, month)
      .then(setDetail)
      .finally(() => setLoading(false));
  }, [itemId, month]);

  // Close on Escape key
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isOver = detail && detail.remaining < 0;
  const remainColor = isOver ? "var(--red)" : "#3b82f6";

  // Mini bar chart: renders up to 4 monthly history bars
  function MiniBarChart({ history }) {
    if (!history || history.length === 0) return null;
    const maxVal = Math.max(...history.map(h => h.spent), 1);
    // Short month name for labels
    function shortMonth(m) {
      const [y, mo] = m.split("-");
      return new Date(parseInt(y), parseInt(mo) - 1, 1)
        .toLocaleDateString("en-US", { month: "short" });
    }
    return (
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end", height: 60, marginBottom: 16 }}>
        {history.map(h => {
          const pct = Math.max((h.spent / maxVal) * 100, 4);
          const isCurrent = h.month === month;
          return (
            <div key={h.month} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
              <div style={{ fontSize: 9, color: "var(--text2)", fontWeight: 600 }}>
                {fmt(h.spent)}
              </div>
              <div style={{
                width: "100%", height: `${pct}%`,
                background: isCurrent ? "var(--accent)" : "var(--bg3)",
                borderRadius: 3, minHeight: 4,
                border: isCurrent ? "none" : "1px solid var(--border)",
              }} />
              <div style={{ fontSize: 9, color: isCurrent ? "var(--accent)" : "var(--text2)", fontWeight: isCurrent ? 700 : 400 }}>
                {shortMonth(h.month)}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  return (
    // Backdrop
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 500,
      background: "rgba(0,0,0,0.6)",
      display: "flex", alignItems: "flex-start", justifyContent: "flex-end",
      padding: "20px 20px 0 0",
    }}>
      {/* Panel — stop clicks from closing when clicking inside */}
      <div onClick={e => e.stopPropagation()} style={{
        width: 360, maxHeight: "calc(100vh - 40px)", overflowY: "auto",
        background: "var(--bg2)", borderRadius: 12,
        border: "1px solid var(--border)",
        boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
        display: "flex", flexDirection: "column",
      }}>
        {/* Header */}
        <div style={{ padding: "16px 16px 0", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 4 }}>
              {detail?.group_name ?? ""}
            </div>
            <div style={{ fontSize: 17, fontWeight: 700, color: "var(--text)", lineHeight: 1.2 }}>
              {itemName}
            </div>
          </div>
          <button onClick={onClose} style={{
            background: "none", border: "none", color: "var(--text2)",
            fontSize: 18, cursor: "pointer", padding: "0 0 0 8px", lineHeight: 1,
          }}>✕</button>
        </div>

        {loading ? (
          <div style={{ padding: "40px 0", textAlign: "center", color: "var(--text2)", fontSize: 13 }}>
            Loading…
          </div>
        ) : !detail ? (
          <div style={{ padding: "40px 0", textAlign: "center", color: "var(--text2)", fontSize: 13 }}>
            Failed to load
          </div>
        ) : (
          <div style={{ padding: 16 }}>
            {/* Mini bar chart — spending trend across recent months */}
            <MiniBarChart history={detail.monthly_history} />

            {/* Planned / Spent / Remaining stats */}
            <div style={{
              display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
              gap: 8, marginBottom: 16,
              padding: 12, borderRadius: 8, background: "var(--bg3)",
            }}>
              {[
                { label: "Planned", value: detail.planned, color: "var(--text)" },
                { label: "Spent",   value: detail.spent,   color: detail.spent > detail.planned ? "var(--red)" : "var(--text)" },
                { label: "Left",    value: Math.abs(detail.remaining), color: remainColor },
              ].map(({ label, value, color }) => (
                <div key={label} style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 4 }}>
                    {label}
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 700, color }}>
                    {isOver && label === "Left" ? "-" : ""}{fmt(value)}
                  </div>
                </div>
              ))}
            </div>

            {/* Activity this month */}
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 8 }}>
              Activity This Month
            </div>

            {detail.transactions.length === 0 ? (
              <div style={{ color: "var(--text2)", fontSize: 12, padding: "12px 0" }}>
                No transactions assigned yet
              </div>
            ) : (
              <div style={{ borderRadius: 8, overflow: "hidden", border: "1px solid var(--border)" }}>
                {detail.transactions.map((t, i) => (
                  <div key={t.transaction_id} style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "10px 12px",
                    borderBottom: i < detail.transactions.length - 1 ? "1px solid var(--border)" : "none",
                    background: i % 2 === 0 ? "var(--bg3)" : "transparent",
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)",
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {t.merchant}
                      </div>
                      <div style={{ fontSize: 10, color: "var(--text2)", marginTop: 1 }}>{t.date}</div>
                    </div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "var(--red)", flexShrink: 0 }}>
                      -{fmt(t.amount)}
                    </div>
                  </div>
                ))}
                {/* Planned row at bottom — mirrors the reference app */}
                {detail.planned > 0 && (
                  <div style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "10px 12px",
                    borderTop: "1px solid var(--border)",
                    background: "rgba(34,197,94,0.06)",
                  }}>
                    <div style={{ fontSize: 12, color: "var(--text2)" }}>Planned this month</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#22c55e" }}>
                      +{fmt(detail.planned)}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Available summary at bottom */}
            <div style={{ marginTop: 12, textAlign: "right", fontSize: 12, color: "var(--text2)" }}>
              <span style={{ fontWeight: 700, color: remainColor }}>
                {isOver ? "-" : ""}{fmt(Math.abs(detail.remaining))}
              </span>
              {" "}available
            </div>
          </div>
        )}
      </div>

      {/* Item detail modal — rendered at page root so it's never clipped */}
      {activeItem && (
        <ItemDetailModal
          itemId={activeItem.id}
          itemName={activeItem.name}
          month={month}
          onClose={() => setActiveItem(null)}
        />
      )}
    </div>
  );
}

// ── Inline amount editor ──────────────────────────────────────────────────────
function AmountCell({ value, onSave }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const ref = useRef(null);

  function start(e) {
    e.stopPropagation();
    setDraft(value > 0 ? String(value) : "");
    setEditing(true);
    setTimeout(() => ref.current?.select(), 0);
  }

  async function commit() {
    const v = parseFloat(draft);
    setEditing(false);
    if (!isNaN(v) && v !== value) await onSave(v);
  }

  if (editing) {
    return (
      <input ref={ref} type="number" min="0" step="any" value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={e => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
        onClick={e => e.stopPropagation()}
        style={{
          width: "100%", textAlign: "right", background: "var(--bg3)",
          border: "1px solid var(--accent)", borderRadius: 4,
          color: "var(--text)", fontSize: 13, padding: "2px 6px",
        }}
      />
    );
  }

  return (
    <span onClick={start} title="Click to edit planned amount"
      style={{
        cursor: "pointer", fontSize: 13, display: "block", textAlign: "right",
        color: value > 0 ? "var(--text)" : "var(--text2)",
        borderBottom: "1px dashed var(--border)",
      }}>
      {value > 0 ? fmt(value) : "$0.00"}
    </span>
  );
}

// ── Budget item row ───────────────────────────────────────────────────────────
function ItemRow({ item, month, groupType, showSpent, onUpdate, onOpenItem }) {
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(item.name);
  const isIncome = groupType === "income";
  const remaining = item.planned - item.spent;

  async function saveName() {
    const trimmed = nameDraft.trim();
    if (!trimmed || trimmed === item.name) { setEditingName(false); return; }
    await updateBudgetItem(item.id, trimmed);
    setEditingName(false);
    onUpdate();
  }

  // Remaining column: blue = money left, red = over budget
  // Spent column: green when there's spending, gray otherwise
  function valueCell() {
    if (isIncome) {
      // Income group shows received amount (what's come in so far)
      return (
        <span style={{ fontSize: 13, color: item.spent > 0 ? "#22c55e" : "var(--text2)" }}>
          {item.spent > 0 ? fmt(item.spent) : "—"}
        </span>
      );
    }
    if (showSpent) {
      return (
        <span style={{ fontSize: 13, fontWeight: 600, color: item.spent > 0 ? "#22c55e" : "var(--text2)" }}>
          {item.spent > 0 ? fmt(item.spent) : "—"}
        </span>
      );
    }
    // Remaining view (default)
    if (item.planned === 0) {
      return <span style={{ fontSize: 13, color: "var(--text2)" }}>—</span>;
    }
    return (
      <span style={{
        fontSize: 13, fontWeight: 600,
        color: remaining < 0 ? "var(--red)" : "#3b82f6",
      }}>
        {remaining < 0 ? `-${fmt(Math.abs(remaining))}` : fmt(remaining)}
      </span>
    );
  }

  return (
    <div style={{
      display: "grid", gridTemplateColumns: "1fr 110px 90px 28px",
      gap: 8, alignItems: "center",
      padding: "9px 16px",
      borderBottom: "1px solid var(--border)",
    }}>
      {/* Item name — double-click to edit */}
      <div>
        {editingName ? (
          <input value={nameDraft} onChange={e => setNameDraft(e.target.value)}
            onBlur={saveName}
            onKeyDown={e => { if (e.key === "Enter") saveName(); if (e.key === "Escape") setEditingName(false); }}
            autoFocus
            style={{
              width: "100%", background: "var(--bg3)",
              border: "1px solid var(--accent)", borderRadius: 4,
              color: "var(--text)", fontSize: 13, padding: "2px 6px",
            }}
          />
        ) : (
          <span
            onClick={() => onOpenItem?.(item)}
            onDoubleClick={e => { e.stopPropagation(); setEditingName(true); }}
            style={{ fontSize: 13, color: "var(--text)", cursor: "pointer" }}
            title="Click to view detail · Double-click to rename">
            {item.name}
          </span>
        )}
      </div>

      {/* Planned — click to edit */}
      <AmountCell value={item.planned}
        onSave={v => setBudgetAmount(item.id, month, v).then(onUpdate)} />

      {/* Remaining / Spent / Received */}
      <div style={{ textAlign: "right" }}>{valueCell()}</div>

      {/* Delete */}
      <button
        onClick={() => { if (window.confirm(`Delete "${item.name}"?`)) deleteBudgetItem(item.id).then(onUpdate); }}
        title="Delete item"
        style={{
          background: "none", border: "none", color: "var(--text2)",
          cursor: "pointer", fontSize: 13, padding: 0, opacity: 0.5,
          lineHeight: 1,
        }}
        onMouseEnter={e => e.currentTarget.style.opacity = 1}
        onMouseLeave={e => e.currentTarget.style.opacity = 0.5}>
        ✕
      </button>
    </div>
  );
}

// ── Group totals row (shown at bottom of expanded group) ──────────────────────
function GroupTotalsRow({ group, showSpent }) {
  const isIncome = group.type === "income";
  const remaining = group.total_planned - group.total_spent;

  return (
    <div style={{
      display: "grid", gridTemplateColumns: "1fr 110px 90px 28px",
      gap: 8, alignItems: "center",
      padding: "8px 16px",
      background: "var(--bg3)",
      borderTop: "1px solid var(--border)",
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        Total
      </div>
      <div style={{ textAlign: "right", fontSize: 13, fontWeight: 700, color: "var(--text)" }}>
        {fmt(group.total_planned)}
      </div>
      <div style={{ textAlign: "right", fontSize: 13, fontWeight: 700 }}>
        {isIncome ? (
          <span style={{ color: group.total_spent > 0 ? "#22c55e" : "var(--text2)" }}>
            {group.total_spent > 0 ? fmt(group.total_spent) : "—"}
          </span>
        ) : showSpent ? (
          <span style={{ color: group.total_spent > 0 ? "#22c55e" : "var(--text2)" }}>
            {group.total_spent > 0 ? fmt(group.total_spent) : "—"}
          </span>
        ) : (
          <span style={{ color: remaining < 0 ? "var(--red)" : "#3b82f6" }}>
            {remaining < 0 ? `-${fmt(Math.abs(remaining))}` : fmt(remaining)}
          </span>
        )}
      </div>
      <div />
    </div>
  );
}

// ── Budget group section ──────────────────────────────────────────────────────
function GroupSection({ group, month, colorIndex, onUpdate, onOpenItem }) {
  const [collapsed, setCollapsed] = useState(false);
  const [showSpent, setShowSpent] = useState(false);  // toggles Remaining ↔ Spent column
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(group.name);
  const [addingItem, setAddingItem] = useState(false);
  const [newItemName, setNewItemName] = useState("");

  const isIncome = group.type === "income";
  const dotColor = isIncome ? "#22c55e" : getGroupColor(colorIndex);
  const remaining = group.total_planned - group.total_spent;

  async function saveName() {
    const trimmed = nameDraft.trim();
    if (!trimmed || trimmed === group.name) { setEditingName(false); return; }
    await updateBudgetGroup(group.id, { name: trimmed });
    setEditingName(false);
    onUpdate();
  }

  async function handleDelete() {
    if (!window.confirm(`Delete group "${group.name}" and all its items? This cannot be undone.`)) return;
    await deleteBudgetGroup(group.id);
    onUpdate();
  }

  async function addItem() {
    if (!newItemName.trim()) return;
    await createBudgetItem(group.id, newItemName.trim());
    setNewItemName("");
    setAddingItem(false);
    onUpdate();
  }

  return (
    <div style={{
      marginBottom: 6, borderRadius: 8, overflow: "hidden",
      border: "1px solid var(--border)", background: "var(--bg2)",
    }}>
      {/* ── Group header ── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10, padding: "11px 16px",
        cursor: "pointer", userSelect: "none",
      }}
        onClick={() => { if (!editingName) setCollapsed(c => !c); }}
      >
        {/* Collapse chevron */}
        <span style={{ color: "var(--text2)", fontSize: 10, width: 10, flexShrink: 0 }}>
          {collapsed ? "▶" : "▼"}
        </span>

        {/* Color dot */}
        <div style={{ width: 10, height: 10, borderRadius: "50%", background: dotColor, flexShrink: 0 }} />

        {/* Group name or inline editor */}
        {editingName ? (
          <div style={{ flex: 1, display: "flex", gap: 8, alignItems: "center" }}
            onClick={e => e.stopPropagation()}>
            <input value={nameDraft} onChange={e => setNameDraft(e.target.value)}
              onBlur={saveName}
              onKeyDown={e => { if (e.key === "Enter") saveName(); if (e.key === "Escape") { setEditingName(false); setNameDraft(group.name); } }}
              autoFocus
              style={{
                flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
                borderRadius: 4, color: "var(--text)", fontSize: 14, fontWeight: 700,
                padding: "3px 8px",
              }}
            />
            <button onClick={handleDelete}
              style={{
                background: "var(--red)", border: "none", borderRadius: 6,
                color: "#fff", fontSize: 11, fontWeight: 600, padding: "4px 10px", cursor: "pointer",
              }}>
              Delete Group
            </button>
            <button onClick={e => { e.stopPropagation(); setEditingName(false); setNameDraft(group.name); }}
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 14, padding: 0 }}>
              ✕
            </button>
          </div>
        ) : (
          <span style={{ flex: 1, fontWeight: 700, fontSize: 14, color: "var(--text)" }}
            onClick={e => { e.stopPropagation(); setEditingName(true); setCollapsed(false); }}
            title="Click to rename">
            {group.name}
          </span>
        )}

        {/* Group totals summary in header (hidden while editing name) */}
        {!editingName && (
          <div style={{ fontSize: 11, color: "var(--text2)", flexShrink: 0, textAlign: "right" }}>
            <span style={{ fontWeight: 600, color: "var(--text)" }}>{fmt(group.total_spent)}</span>
            {" "}<span style={{ color: "var(--text2)" }}>of</span>{" "}
            <span>{fmt(group.total_planned)}</span>
          </div>
        )}
      </div>

      {/* ── Expanded content ── */}
      {!collapsed && (
        <>
          {/* Column headers */}
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 110px 90px 28px",
            gap: 8, padding: "5px 16px",
            background: "var(--bg3)", borderTop: "1px solid var(--border)",
          }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Item
            </div>
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "right" }}>
              Planned
            </div>
            {isIncome ? (
              <div style={{ fontSize: 10, fontWeight: 600, color: "#22c55e", textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "right" }}>
                Received
              </div>
            ) : (
              /* Clickable header toggles between Remaining and Spent */
              <button onClick={() => setShowSpent(s => !s)}
                style={{
                  background: "none", border: "none", cursor: "pointer", padding: 0,
                  fontSize: 10, fontWeight: 700, letterSpacing: "0.5px", textTransform: "uppercase",
                  color: showSpent ? "#22c55e" : "#3b82f6",
                  textAlign: "right", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 3,
                }}
                title="Click to toggle Remaining / Spent">
                {showSpent ? "Spent" : "Remaining"} ▾
              </button>
            )}
            <div />
          </div>

          {/* Item rows — auto-hide items with $0 planned AND $0 spent for this month.
               Items from prior months that have no activity this month simply don't
               appear; they'll show again in any month where they have data. */}
          {group.items
            .filter(i => i.planned > 0 || i.spent > 0)
            .map(item => (
              <ItemRow key={item.id} item={item} month={month}
                groupType={group.type} showSpent={showSpent}
                onUpdate={onUpdate} onOpenItem={onOpenItem} />
            ))}

          {/* Group totals row */}
          {group.items.length > 0 && (
            <GroupTotalsRow group={group} showSpent={showSpent} />
          )}

          {/* Add item link */}
          <div style={{ padding: "8px 16px" }}>
            {addingItem ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input value={newItemName} onChange={e => setNewItemName(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") addItem(); if (e.key === "Escape") setAddingItem(false); }}
                  autoFocus
                  placeholder={isIncome ? "Income source name…" : "Budget item name…"}
                  style={{
                    flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
                    borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "5px 10px",
                  }}
                />
                <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={addItem}>Add</button>
                <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => setAddingItem(false)}>Cancel</button>
              </div>
            ) : (
              <button onClick={() => setAddingItem(true)}
                style={{
                  background: "none", border: "none", cursor: "pointer", padding: 0,
                  fontSize: 12, fontWeight: 600,
                  color: isIncome ? "#22c55e" : "var(--accent)",
                }}>
                + {isIncome ? "Add Income" : "Add Item"}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Main Budget page ──────────────────────────────────────────────────────────
export default function Budget() {
  const [month, setMonth] = useState(currentMonth);
  const [budget, setBudget] = useState(null);
  const [loading, setLoading] = useState(true);
  const [rightTab, setRightTab] = useState("summary"); // summary | transactions
  const [showMonthPicker, setShowMonthPicker] = useState(false);
  const [addingGroup, setAddingGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupType, setNewGroupType] = useState("expense");
  const [activeItem, setActiveItem] = useState(null); // item clicked for detail modal

  const load = useCallback(async () => {
    setLoading(true);
    try { setBudget(await getBudget(month)); }
    catch { setBudget(null); }
    finally { setLoading(false); }
  }, [month]);

  // Silent refresh — updates budget data in place without showing a loading
  // spinner or resetting scroll position. Used after transaction assignment
  // so spent/remaining totals update smoothly without the page jumping to top.
  const silentLoad = useCallback(async () => {
    try { setBudget(await getBudget(month)); }
    catch { /* ignore — stale data is fine for a background refresh */ }
  }, [month]);

  useEffect(() => { load(); }, [load]);

  async function handleAddGroup() {
    if (!newGroupName.trim()) return;
    await createBudgetGroup(newGroupName.trim(), newGroupType);
    setNewGroupName("");
    setAddingGroup(false);
    await load();
  }

  const summary = budget?.summary;
  const groups = budget?.groups ?? [];
  const hasGroups = groups.length > 0;  // true if any groups exist at all (for empty state)

  // Zero-based status
  const remainingToBudget = summary?.remaining_to_budget ?? 0;
  const fullyBudgeted = Math.abs(remainingToBudget) < 0.01;

  // Auto-hide groups that have no items with planned > 0 or spent > 0 this month.
  // Groups whose every item is $0/$0 for the viewed month are not shown — they'll
  // reappear in any month where they have data. Deleted groups never come from the
  // API (is_deleted=1 filtered server-side), so no extra check needed here.
  const visibleGroups = groups.filter(
    g => g.total_planned > 0 || g.total_spent > 0
  );

  // Assign a stable color index to each expense group (income stays green)
  let expIdx = 0;
  const groupColorIdx = {};
  visibleGroups.forEach(g => { if (g.type !== "income") groupColorIdx[g.id] = expIdx++; });

  return (
    <div>
      {/* ── Month navigator ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 16, marginBottom: 10 }}>
        <button className="btn btn-secondary" style={{ padding: "6px 16px", fontSize: 18, lineHeight: 1 }}
          onClick={() => setMonth(prevMonth)}>
          ‹
        </button>

        <div style={{ position: "relative" }}>
          <button onClick={() => setShowMonthPicker(p => !p)}
            style={{
              background: "none", border: "none", color: "var(--text)",
              fontSize: 22, fontWeight: 800, cursor: "pointer", padding: "4px 8px",
              display: "flex", alignItems: "center", gap: 6,
            }}>
            {monthLabel(month)}
            <span style={{ fontSize: 13, color: "var(--text2)" }}>▾</span>
          </button>
          {showMonthPicker && (
            <MonthPicker month={month} onChange={m => { setMonth(m); }}
              onClose={() => setShowMonthPicker(false)} />
          )}
        </div>

        <button className="btn btn-secondary" style={{ padding: "6px 16px", fontSize: 18, lineHeight: 1 }}
          onClick={() => setMonth(nextMonth)}>
          ›
        </button>
      </div>

      {/* ── Zero-based budget status banner ── */}
      {summary && (
        <div style={{ textAlign: "center", marginBottom: 20 }}>
          {fullyBudgeted ? (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 16px", borderRadius: 20, fontSize: 12, fontWeight: 600,
              background: "rgba(34,197,94,0.12)", color: "#22c55e",
            }}>
              ✓ Zero-based budget achieved
            </span>
          ) : remainingToBudget > 0 ? (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 16px", borderRadius: 20, fontSize: 12, fontWeight: 600,
              background: "rgba(245,158,11,0.12)", color: "#f59e0b",
            }}>
              {fmt(remainingToBudget)} left to budget
            </span>
          ) : (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 16px", borderRadius: 20, fontSize: 12, fontWeight: 600,
              background: "rgba(239,68,68,0.12)", color: "var(--red)",
            }}>
              {fmt(Math.abs(remainingToBudget))} over-budgeted
            </span>
          )}
        </div>
      )}

      {loading && (
        <div style={{ color: "var(--text2)", padding: "60px 0", textAlign: "center", fontSize: 14 }}>
          Loading…
        </div>
      )}

      {/* ── Empty state ── */}
      {!loading && !hasGroups && (
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{ fontSize: 40, marginBottom: 16 }}>📋</div>
          <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>No budget categories yet</div>
          <div style={{ color: "var(--text2)", fontSize: 14, marginBottom: 24 }}>
            Add your first group to get started with zero-based budgeting.
          </div>
          <button className="btn btn-primary" onClick={() => setAddingGroup(true)}>
            + Add First Group
          </button>
        </div>
      )}

      {/* ── Main two-column layout ── */}
      {!loading && hasGroups && (
        <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>

          {/* LEFT — budget groups list (only groups with activity this month) */}
          <div style={{ flex: 1, minWidth: 0 }}>
            {visibleGroups.map(g => (
              <GroupSection key={g.id} group={g} month={month}
                colorIndex={groupColorIdx[g.id] ?? 0}
                onUpdate={load}
                onOpenItem={setActiveItem} />
            ))}

            {/* Add group */}
            <div style={{ marginTop: 8 }}>
              {addingGroup ? (
                <div style={{
                  display: "flex", gap: 10, alignItems: "center",
                  padding: "12px 16px", background: "var(--bg2)",
                  border: "1px solid var(--border)", borderRadius: 8,
                }}>
                  <input value={newGroupName} onChange={e => setNewGroupName(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") handleAddGroup(); if (e.key === "Escape") setAddingGroup(false); }}
                    autoFocus placeholder="Group name…"
                    style={{
                      flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
                      borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "6px 12px",
                    }}
                  />
                  <select value={newGroupType} onChange={e => setNewGroupType(e.target.value)}
                    className="form-select" style={{ fontSize: 13, width: 110 }}>
                    <option value="expense">Expense</option>
                    <option value="income">Income</option>
                  </select>
                  <button className="btn btn-primary" style={{ padding: "6px 16px" }} onClick={handleAddGroup}>Add</button>
                  <button className="btn btn-secondary" style={{ padding: "6px 16px" }} onClick={() => setAddingGroup(false)}>Cancel</button>
                </div>
              ) : (
                <button onClick={() => setAddingGroup(true)}
                  style={{
                    width: "100%", padding: "10px", textAlign: "center",
                    background: "none", border: "1px dashed var(--border)",
                    borderRadius: 8, color: "var(--text2)", fontSize: 13,
                    fontWeight: 600, cursor: "pointer",
                  }}>
                  + Add Group
                </button>
              )}
            </div>
          </div>

          {/* RIGHT — Summary / Transactions sticky panel */}
          <div style={{
            width: 340, flexShrink: 0,
            position: "sticky", top: 20,
            maxHeight: "calc(100vh - 100px)", overflowY: "auto",
          }}>
            <div className="card" style={{ padding: 16 }}>
              {/* Panel tab bar */}
              <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: 14 }}>
                {[
                  { key: "summary", label: "Summary" },
                  { key: "transactions", label: "Transactions" },
                ].map(({ key, label }) => (
                  <button key={key} onClick={() => setRightTab(key)}
                    style={{
                      flex: 1, padding: "8px 0", fontSize: 13,
                      fontWeight: rightTab === key ? 700 : 400,
                      background: "none", border: "none",
                      borderBottom: `2px solid ${rightTab === key ? "var(--accent)" : "transparent"}`,
                      color: rightTab === key ? "var(--text)" : "var(--text2)",
                      cursor: "pointer",
                    }}>
                    {label}
                  </button>
                ))}
              </div>

              {rightTab === "summary" ? (
                <SummaryPanel groups={groups} summary={summary} />
              ) : (
                <TransactionsPanel month={month} allGroups={groups} onBudgetUpdate={silentLoad} />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
