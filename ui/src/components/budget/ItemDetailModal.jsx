import { useState, useEffect } from "react";
import { getItemDetail, updateBudgetItem } from "../../api.js";
import { fmt } from "../../utils/format.js";
import EditExpenseModal from "./EditExpenseModal.jsx";

export default function ItemDetailModal({ itemId, itemName, month, allGroups, onClose, onUpdate }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(itemName);
  const [editTxn, setEditTxn] = useState(null);

  useEffect(() => {
    setLoading(true);
    getItemDetail(itemId, month)
      .then(setDetail)
      .catch(() => {})   // detail stays null → shows "Failed to load" rather than crashing
      .finally(() => setLoading(false));
  }, [itemId, month]);

  async function saveName() {
    const trimmed = nameDraft.trim();
    if (!trimmed || trimmed === (detail?.name ?? itemName)) { setEditingName(false); return; }
    await updateBudgetItem(itemId, trimmed);
    setEditingName(false);
    onUpdate?.();
    // Refresh detail so the modal reflects the new name
    getItemDetail(itemId, month).then(setDetail);
  }

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
      <div role="dialog" aria-modal="true" onClick={e => e.stopPropagation()} style={{
        width: 360, maxHeight: "calc(100vh - 40px)", overflowY: "auto",
        background: "var(--bg2)", borderRadius: 12,
        border: "1px solid var(--border)",
        boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
        display: "flex", flexDirection: "column",
      }}>
        {/* Header */}
        <div style={{ padding: "16px 16px 0", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 4 }}>
              {detail?.group_name ?? ""}
            </div>
            {editingName ? (
              <input value={nameDraft} onChange={e => setNameDraft(e.target.value)}
                onBlur={saveName}
                onKeyDown={e => { if (e.key === "Enter") saveName(); if (e.key === "Escape") setEditingName(false); }}
                autoFocus
                style={{
                  width: "100%", background: "var(--bg3)",
                  border: "1px solid var(--accent)", borderRadius: 4,
                  color: "var(--text)", fontSize: 16, fontWeight: 700, padding: "3px 8px",
                }}
              />
            ) : (
              <div onClick={() => { setNameDraft(detail?.name ?? itemName); setEditingName(true); }}
                style={{ fontSize: 17, fontWeight: 700, color: "var(--text)", lineHeight: 1.2, cursor: "text" }}
                title="Click to rename">
                {detail?.name ?? itemName}
              </div>
            )}
          </div>
          <button onClick={onClose} aria-label="Close" style={{
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
                  <div
                    key={t.transaction_id}
                    onClick={() => setEditTxn(t.transaction_id)}
                    style={{
                      display: "flex", justifyContent: "space-between", alignItems: "center",
                      padding: "10px 12px",
                      borderBottom: i < detail.transactions.length - 1 ? "1px solid var(--border)" : "none",
                      background: i % 2 === 0 ? "var(--bg3)" : "transparent",
                      cursor: "pointer",
                    }}
                    title="Click to edit or split this transaction"
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)",
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {t.merchant}
                      </div>
                      <div style={{ fontSize: 10, color: "var(--text2)", marginTop: 1 }}>{t.date}</div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", flexShrink: 0, gap: 2 }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--red)" }}>
                        -{fmt(t.amount)}
                      </div>
                      {t.is_split && (
                        <div style={{
                          fontSize: 9, fontWeight: 700, color: "var(--accent)",
                          background: "color-mix(in srgb, var(--accent) 15%, transparent)",
                          borderRadius: 3, padding: "1px 4px", letterSpacing: "0.4px",
                        }}>SPLIT</div>
                      )}
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

        {/* Edit Expense modal — nested inside ItemDetailModal so it inherits context */}
        {editTxn && (
          <EditExpenseModal
            txnId={editTxn}
            allGroups={allGroups || []}
            onClose={() => setEditTxn(null)}
            onSaved={() => {
              setEditTxn(null);
              // Reload item detail to reflect the updated assignment
              getItemDetail(itemId, month).then(setDetail);
              onUpdate?.();
            }}
          />
        )}
    </div>
  );
}
