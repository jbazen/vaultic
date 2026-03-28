import { useState, useEffect } from "react";
import {
  getUnassignedTransactions, getAssignedTransactions,
  assignTransaction, unassignTransaction, autoAssignFromHistory, unassignAll,
  getPendingReviewTransactions, approveTransaction,
  budgetDeleteTransaction, budgetRestoreTransaction, getDeletedTransactions,
} from "../../api.js";
import { fmt } from "../../utils/format.js";

export default function TransactionsPanel({ month, allGroups, onBudgetUpdate }) {
  const [tab, setTab] = useState("pending"); // pending | new | tracked | deleted
  const [unassigned, setUnassigned] = useState([]);
  const [assigned, setAssigned] = useState([]);
  const [pending, setPending] = useState([]);   // Sage-suggested, awaiting review
  const [deleted, setDeleted] = useState([]);   // soft-deleted transactions
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [autoAssigning, setAutoAssigning] = useState(false);
  const [autoResult, setAutoResult] = useState(null); // {assigned, skipped} after run

  async function loadAll() {
    setLoading(true);
    await Promise.all([
      getUnassignedTransactions(month).then(setUnassigned),
      getAssignedTransactions(month).then(setAssigned),
      getPendingReviewTransactions(month).then(setPending),
      getDeletedTransactions(month).then(setDeleted),
    ]);
    setLoading(false);
  }

  // Switch to Pending tab automatically when there are items waiting
  useEffect(() => {
    loadAll();
    setAutoResult(null);
  }, [month]);

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

  async function handleUnassignAll() {
    if (!window.confirm(`Unassign all ${assigned.length} tracked transactions for this month?`)) return;
    await unassignAll(month);
    await loadAll();
    onBudgetUpdate?.();
  }

  // Approve Sage's suggestion (or correct it to a different item).
  // Both paths call the same endpoint — different item_id = correction.
  async function handleApprove(txnId, itemId) {
    await approveTransaction(txnId, itemId);
    await loadAll();
    onBudgetUpdate?.();
  }

  // Approve all pending transactions that meet a minimum confidence threshold.
  async function handleApproveAll(minConfidence = 0) {
    const eligible = pending.filter(t => (t.confidence ?? 0) >= minConfidence);
    await Promise.all(
      eligible.map(t => approveTransaction(t.transaction_id, t.suggested_item_id))
    );
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

  async function handleDelete(txnId) {
    await budgetDeleteTransaction(txnId);
    await loadAll();
    onBudgetUpdate?.();
  }

  async function handleRestore(txnId) {
    await budgetRestoreTransaction(txnId);
    await loadAll();
    onBudgetUpdate?.();
  }

  const txns = (tab === "pending" ? pending : tab === "new" ? unassigned : tab === "tracked" ? assigned : deleted).filter(t => {
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
      {/* Sub-tabs — Pending shows Sage suggestions; New = unassigned; Tracked = confirmed; Deleted = soft-deleted */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: 10 }}>
        {tabBtn("pending", "⚡ Pending", pending.length)}
        {tabBtn("new", "New", unassigned.length)}
        {tabBtn("tracked", "Tracked", assigned.length)}
        {tabBtn("deleted", "Deleted", deleted.length)}
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

      {/* Unassign all — only shown on Tracked tab when there are assigned transactions */}
      {tab === "tracked" && assigned.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <button onClick={handleUnassignAll}
            style={{
              width: "100%", padding: "6px 0", borderRadius: 6, fontSize: 11, fontWeight: 600,
              background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
              color: "var(--red)", cursor: "pointer",
            }}>
            ✕ Unassign all ({assigned.length})
          </button>
        </div>
      )}

      {/* Pending tab: bulk approve buttons */}
      {tab === "pending" && pending.length > 0 && (
        <div style={{ marginBottom: 8, display: "flex", gap: 6 }}>
          <button
            onClick={() => handleApproveAll(85)}
            style={{
              flex: 1, padding: "6px 0", borderRadius: 6, fontSize: 11, fontWeight: 600,
              background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.3)",
              color: "#22c55e", cursor: "pointer",
            }}
            title="Approve all suggestions with ≥85% confidence"
          >
            ✓ Approve high-confidence (≥85%)
          </button>
          <button
            onClick={() => handleApproveAll(0)}
            style={{
              padding: "6px 10px", borderRadius: 6, fontSize: 11, fontWeight: 600,
              background: "var(--bg3)", border: "1px solid var(--border)",
              color: "var(--text2)", cursor: "pointer",
            }}
            title="Approve all pending suggestions"
          >
            All
          </button>
        </div>
      )}

      {/* Auto-assign + debug buttons hidden for now — useful for prior months
          but clutters the current-month view. Re-enable if needed for debugging.
      {tab === "new" && unassigned.length > 0 && (
        ...
      )} */}

      {loading && (
        <div style={{ color: "var(--text2)", fontSize: 12, textAlign: "center", padding: "16px 0" }}>
          Loading…
        </div>
      )}

      {!loading && txns.length === 0 && (
        <div style={{ color: "var(--text2)", fontSize: 12, textAlign: "center", padding: "20px 0" }}>
          {tab === "new" ? "✓ All transactions assigned"
           : tab === "deleted" ? "No deleted transactions this month"
           : "No tracked transactions this month"}
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
                <div style={{ fontSize: 10, color: "var(--text2)", marginTop: 1, display: "flex", gap: 6 }}>
                  <span>{t.date}</span>
                  {t.account_name && (
                    <span style={{ color: "var(--text2)", opacity: 0.7 }}>
                      {t.account_name.toUpperCase()}{t.account_mask ? `*${t.account_mask}` : ""}
                    </span>
                  )}
                </div>

                {/* Pending tab: Sage's suggestion with confidence badge */}
                {tab === "pending" && t.suggested_item_name && (
                  <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
                    <span style={{
                      fontSize: 10, fontWeight: 700, color: "var(--accent)",
                      background: "color-mix(in srgb, var(--accent) 15%, transparent)",
                      borderRadius: 4, padding: "1px 6px",
                    }}>
                      {t.suggested_group_name} › {t.suggested_item_name}
                    </span>
                    {t.confidence != null && (
                      <span style={{
                        fontSize: 9, fontWeight: 700,
                        color: t.confidence >= 85 ? "#22c55e" : t.confidence >= 70 ? "#f59e0b" : "var(--text2)",
                        background: t.confidence >= 85
                          ? "rgba(34,197,94,0.12)"
                          : t.confidence >= 70 ? "rgba(245,158,11,0.12)" : "var(--bg3)",
                        borderRadius: 4, padding: "1px 5px",
                      }}>
                        {t.confidence}% confidence
                      </span>
                    )}
                  </div>
                )}

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

              <div style={{ textAlign: "right", flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: isDebit ? "var(--red)" : "var(--green)" }}>
                  {isDebit ? "-" : "+"}{fmt(Math.abs(t.amount))}
                </div>
                {tab === "pending" && (
                  <button
                    onClick={() => handleApprove(t.transaction_id, t.suggested_item_id)}
                    title="Approve Sage's suggestion"
                    style={{
                      marginTop: 4, padding: "3px 10px", borderRadius: 5, fontSize: 10, fontWeight: 700,
                      background: "rgba(34,197,94,0.15)", border: "1px solid rgba(34,197,94,0.35)",
                      color: "#22c55e", cursor: "pointer",
                    }}
                  >✓ Approve</button>
                )}
                {tab === "tracked" && (
                  <button onClick={() => handleUnassign(t.transaction_id)}
                    style={{ fontSize: 10, color: "var(--text2)", background: "none",
                      border: "none", cursor: "pointer", marginTop: 2, padding: 0 }}>
                    unassign
                  </button>
                )}
                {/* Trash icon — delete button for New and Tracked tabs */}
                {(tab === "new" || tab === "tracked") && (
                  <button
                    onClick={() => handleDelete(t.transaction_id)}
                    title="Remove from budget"
                    style={{
                      fontSize: 11, color: "var(--text2)", background: "none",
                      border: "none", cursor: "pointer", padding: 0, opacity: 0.5,
                      lineHeight: 1,
                    }}
                  aria-label="Remove from budget">🗑</button>
                )}
                {/* Restore button for Deleted tab */}
                {tab === "deleted" && (
                  <button
                    onClick={() => handleRestore(t.transaction_id)}
                    title="Restore to unassigned"
                    style={{
                      marginTop: 4, padding: "3px 8px", borderRadius: 5, fontSize: 10, fontWeight: 600,
                      background: "rgba(79,142,247,0.12)", border: "1px solid rgba(79,142,247,0.3)",
                      color: "var(--accent)", cursor: "pointer",
                    }}
                  >↩ Restore</button>
                )}
              </div>
            </div>

            {/* Correct dropdown (Pending tab) — approve with a different item */}
            {tab === "pending" && (
              <select key={"p-" + t.transaction_id} defaultValue=""
                onChange={e => e.target.value && handleApprove(t.transaction_id, parseInt(e.target.value))}
                style={{
                  marginTop: 6, width: "100%",
                  background: "var(--bg3)", border: "1px solid var(--border)",
                  borderRadius: 6, color: "var(--text)", fontSize: 11, padding: "4px 8px",
                }}>
                <option value="" disabled>Correct to different item…</option>
                {allGroups.map(g => {
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
