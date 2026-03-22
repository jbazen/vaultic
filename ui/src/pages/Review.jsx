/**
 * Review.jsx — Mobile-first transaction review queue.
 *
 * Opened when the user taps a push notification or navigates to /review.
 * Shows every pending_review transaction as a large full-width card so the
 * user can approve or reassign each one with a single tap.
 *
 * Design goals:
 *   • Cards fill the full phone width — easy to tap on mobile
 *   • Merchant name and amount are the visual anchors (large, bold)
 *   • Suggested category shown with confidence badge
 *   • One-tap approve; reassign opens an inline category picker
 *   • "Approve All High-Confidence" bulk action at top
 *   • "All clear" state when queue is empty
 */

import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { getAllPendingReview, getAllUnassignedTransactions, approveTransaction, assignTransaction, saveTransactionSplits, getBudget, isAuthed, deviceAuth } from "../api.js";

// ── Helpers ───────────────────────────────────────────────────────────────────


function fmtDate(s) {
  if (!s) return "";
  return new Date(s.substring(0, 10) + "T12:00:00")
    .toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** Map 0–100 Sage confidence score to a label and color.
 *  Only used for pending_review items — unassigned items have no Sage score.
 *  null / 0 = Sage had no real basis → red
 *  1–69     = Low confidence         → orange
 *  70–89    = Good confidence        → yellow
 *  ≥90      = High confidence        → green
 */
function confidenceInfo(conf) {
  if (conf == null || conf === 0) return { label: conf === 0 ? "0%" : "—", color: "#ef4444" };  // red
  if (conf >= 90) return { label: `${conf}%`, color: "#34d399" };   // green
  if (conf >= 70) return { label: `${conf}%`, color: "#f59e0b" };   // yellow
  return            { label: `${conf}%`, color: "#f97316" };         // orange
}

/** Format amount with sign and color: red "-$x" for debits, green "+$x" for credits.
 *  Plaid convention: positive = money out (expense), negative = money in (income). */
function fmtAmount(v) {
  const abs = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(Math.abs(v ?? 0));
  const isDebit = (v ?? 0) >= 0;
  return { text: (isDebit ? "-" : "+") + abs, color: isDebit ? "#f87171" : "#34d399" };
}


// ── Category picker ───────────────────────────────────────────────────────────

/**
 * Full-screen category picker shown when the user taps "Reassign".
 * Fetches all active budget groups/items and renders them as a flat scrollable
 * list grouped by category name.
 */
function CategoryPicker({ currentItemId, onSelect, onCancel }) {
  const [groups,  setGroups]  = useState([]);
  const [loading, setLoading] = useState(true);
  const [search,  setSearch]  = useState("");

  useEffect(() => {
    const month = new Date().toISOString().slice(0, 7);
    getBudget(month)
      .then(data => setGroups(data.groups ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const q = search.toLowerCase().trim();

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 200,
      background: "var(--bg, #0f1117)", display: "flex", flexDirection: "column",
    }}>
      {/* Header */}
      <div style={{
        padding: "16px 16px 10px", borderBottom: "1px solid var(--border, #2a2f45)",
        background: "var(--bg2, #171b27)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
          <button onClick={onCancel}
            style={{ background: "none", border: "none", color: "var(--accent, #4f8ef7)",
              fontSize: 16, cursor: "pointer", padding: 0 }}>
            ← Back
          </button>
          <span style={{ fontWeight: 700, fontSize: 16, flex: 1 }}>Reassign to…</span>
        </div>
        {/* Search box — triggers keyboard on mobile */}
        <input
          type="search"
          placeholder="Search categories…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            width: "100%", boxSizing: "border-box",
            padding: "10px 12px", borderRadius: 8,
            background: "var(--bg3, #1e2336)", border: "1px solid var(--border, #2a2f45)",
            color: "var(--text, #e8eaf0)", fontSize: 15, outline: "none",
          }}
        />
      </div>

      {/* Items list */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {loading ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text2, #8b90a7)" }}>
            Loading…
          </div>
        ) : (
          groups
            .map(g => ({
              ...g,
              items: g.items
                // Active budget items only (+ always include current suggestion)
                .filter(item => item.planned > 0 || item.spent > 0 || item.id === currentItemId)
                // Fuzzy search: match item name or group name
                .filter(item => !q || item.name.toLowerCase().includes(q) || g.name.toLowerCase().includes(q)),
            }))
            .filter(g => g.items.length > 0)
            .map(g => (
            <div key={g.id}>
              {/* Group header */}
              <div style={{
                padding: "10px 16px 6px",
                fontSize: 11, fontWeight: 700, color: "var(--text2, #8b90a7)",
                textTransform: "uppercase", letterSpacing: "0.8px",
                background: "var(--bg3, #1e2336)",
                borderBottom: "1px solid var(--border, #2a2f45)",
              }}>
                {g.name}
              </div>
              {/* Items — show name on left, remaining balance on right */}
              {g.items.map(item => {
                const rem = item.remaining ?? 0;
                const remColor = rem >= 0 ? "#34d399" : "#f87171";
                const remText  = rem >= 0
                  ? `$${rem.toFixed(2)} left`
                  : `-$${Math.abs(rem).toFixed(2)} over`;
                return (
                  <button key={item.id}
                    onClick={() => onSelect(item.id, item.name, g.name)}
                    style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      width: "100%", padding: "14px 16px",
                      background: item.id === currentItemId ? "var(--bg3, #1e2336)" : "transparent",
                      border: "none", borderBottom: "1px solid var(--border, #2a2f45)",
                      color: "var(--text, #e8eaf0)", fontSize: 15, cursor: "pointer",
                      textAlign: "left", gap: 8,
                    }}>
                    <span style={{ flex: 1 }}>{item.name}</span>
                    <span style={{ fontSize: 13, fontWeight: 600, color: remColor, flexShrink: 0 }}>
                      {remText}
                    </span>
                    {item.id === currentItemId && (
                      <span style={{ color: "var(--accent, #4f8ef7)", fontSize: 13, flexShrink: 0 }}>✓</span>
                    )}
                  </button>
                );
              })}
            </div>
          ))
        )}
      </div>
    </div>
  );
}


// ── Split modal ───────────────────────────────────────────────────────────────

/**
 * Full-screen split modal for the Review Queue.
 * Lets the user divide a single transaction across multiple budget items.
 * Mirrors the split UX from EditExpenseModal but designed for the mobile-first
 * Review page layout.
 *
 * Props:
 *   txn       — transaction object (needs transaction_id, amount, merchant_name)
 *   onSave    — called with no args after a successful save; parent removes txn from list
 *   onCancel  — called to dismiss without saving
 */
function ReviewSplitModal({ txn, onSave, onCancel }) {
  const totalAbs = Math.abs(txn.amount ?? 0);           // absolute value — positive = expense
  const [splits,  setSplits]  = useState([              // start with two empty rows
    { item_id: "", amount: "" },
    { item_id: "", amount: "" },
  ]);
  const [saving,  setSaving]  = useState(false);
  const [error,   setError]   = useState(null);
  const [loadingBudget, setLoadingBudget] = useState(true);

  // Flat list of active items across all groups for the <select> options
  const [allItems, setAllItems] = useState([]);

  useEffect(() => {
    const month = new Date().toISOString().slice(0, 7);
    getBudget(month)
      .then(data => {
        const g = data.groups ?? [];
        // Flatten to { id, name, groupName, remaining } — active items only
        const flat = [];
        for (const grp of g) {
          for (const item of grp.items) {
            if (item.planned > 0 || item.spent > 0) {
              flat.push({
                id: item.id,
                name: item.name,
                groupName: grp.name,
                remaining: item.remaining ?? 0,
              });
            }
          }
        }
        setAllItems(flat);
      })
      .catch(() => {})
      .finally(() => setLoadingBudget(false));
  }, []);

  // Sum of amounts entered so far
  const allocatedSum = splits.reduce((s, r) => s + (parseFloat(r.amount) || 0), 0);
  const remaining    = totalAbs - allocatedSum;

  function setRow(idx, field, value) {
    setSplits(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r));
  }

  function addRow() {
    setSplits(prev => [...prev, { item_id: "", amount: "" }]);
  }

  function removeRow(idx) {
    setSplits(prev => prev.filter((_, i) => i !== idx));
  }

  async function handleSave() {
    setError(null);
    const valid = splits.filter(r => r.item_id && parseFloat(r.amount) > 0);
    if (valid.length < 2) { setError("Add at least two split rows."); return; }
    const total = valid.reduce((s, r) => s + parseFloat(r.amount), 0);
    if (Math.abs(total - totalAbs) > 0.015) {
      setError(`Split total $${total.toFixed(2)} must equal transaction $${totalAbs.toFixed(2)}.`);
      return;
    }
    setSaving(true);
    try {
      await saveTransactionSplits(
        txn.transaction_id,
        valid.map(r => ({ item_id: parseInt(r.item_id), amount: parseFloat(r.amount) })),
        {}
      );
      onSave();
    } catch (e) {
      setError("Save failed. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  const label = txn.merchant_name || txn.name || "Transaction";
  const amt   = fmtAmount(txn.amount);

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 300,
      background: "var(--bg, #0f1117)", display: "flex", flexDirection: "column",
    }}>
      {/* Header */}
      <div style={{
        padding: "16px 16px 12px", borderBottom: "1px solid var(--border, #2a2f45)",
        background: "var(--bg2, #171b27)",
        position: "sticky", top: 0, zIndex: 10,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 4 }}>
          <button onClick={onCancel}
            style={{ background: "none", border: "none", color: "var(--accent, #4f8ef7)",
              fontSize: 16, cursor: "pointer", padding: 0 }}>
            ← Cancel
          </button>
          <span style={{ fontWeight: 700, fontSize: 16, flex: 1 }}>Split Transaction</span>
        </div>
        {/* Merchant + amount summary */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginTop: 6 }}>
          <span style={{ fontWeight: 700, fontSize: 17, color: "var(--text, #e8eaf0)" }}>{label}</span>
          <span style={{ fontWeight: 800, fontSize: 19, color: amt.color }}>{amt.text}</span>
        </div>
        {/* Remaining to allocate */}
        <div style={{
          marginTop: 6, fontSize: 13,
          color: Math.abs(remaining) < 0.01 ? "#34d399"
               : remaining < 0             ? "#f87171"
               : "#f59e0b",
          fontWeight: 700,
        }}>
          {Math.abs(remaining) < 0.01
            ? "✓ Fully allocated"
            : remaining > 0
              ? `$${remaining.toFixed(2)} left to allocate`
              : `-$${Math.abs(remaining).toFixed(2)} over-allocated`}
        </div>
      </div>

      {/* Split rows */}
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px" }}>
        {loadingBudget ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text2, #8b90a7)" }}>Loading…</div>
        ) : (
          <>
            {splits.map((row, idx) => {
              const selItem = allItems.find(it => it.id === parseInt(row.item_id));
              return (
                <div key={idx} style={{
                  background: "var(--bg2, #171b27)",
                  borderRadius: 10, padding: "14px 14px 12px",
                  marginBottom: 10,
                  border: "1px solid var(--border, #2a2f45)",
                }}>
                  {/* Category select */}
                  <div style={{ marginBottom: 8, fontSize: 12, color: "var(--text2, #8b90a7)", fontWeight: 700 }}>
                    CATEGORY
                  </div>
                  <select
                    value={row.item_id}
                    onChange={e => setRow(idx, "item_id", e.target.value)}
                    style={{
                      width: "100%", padding: "11px 10px",
                      background: "var(--bg3, #1e2336)",
                      border: "1px solid var(--border, #2a2f45)",
                      borderRadius: 8, color: "var(--text, #e8eaf0)",
                      fontSize: 15, marginBottom: 10,
                    }}>
                    <option value="">— Pick a category —</option>
                    {allItems.map(item => {
                      const rem = item.remaining;
                      const remStr = rem >= 0
                        ? `$${rem.toFixed(2)} left`
                        : `-$${Math.abs(rem).toFixed(2)} over`;
                      return (
                        <option key={item.id} value={item.id}>
                          {item.groupName} › {item.name} ({remStr})
                        </option>
                      );
                    })}
                  </select>

                  {/* Amount input + remove button */}
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <div style={{ fontSize: 12, color: "var(--text2, #8b90a7)", fontWeight: 700, marginBottom: 0, flexShrink: 0 }}>
                      $
                    </div>
                    <input
                      type="number"
                      inputMode="decimal"
                      min="0"
                      step="0.01"
                      placeholder="0.00"
                      value={row.amount}
                      onChange={e => setRow(idx, "amount", e.target.value)}
                      style={{
                        flex: 1, padding: "11px 10px",
                        background: "var(--bg3, #1e2336)",
                        border: "1px solid var(--border, #2a2f45)",
                        borderRadius: 8, color: "var(--text, #e8eaf0)",
                        fontSize: 16,
                      }}
                    />
                    {splits.length > 2 && (
                      <button
                        onClick={() => removeRow(idx)}
                        style={{
                          background: "none", border: "none",
                          color: "#f87171", fontSize: 20, cursor: "pointer",
                          padding: "0 4px", flexShrink: 0,
                        }}>
                        ✕
                      </button>
                    )}
                  </div>
                </div>
              );
            })}

            {/* Add another split */}
            <button
              onClick={addRow}
              style={{
                width: "100%", padding: "12px 0",
                background: "none", border: "1px dashed var(--border, #2a2f45)",
                borderRadius: 10, color: "var(--text2, #8b90a7)",
                fontSize: 14, cursor: "pointer", marginBottom: 16,
              }}>
              + Add another split
            </button>

            {error && (
              <div style={{ color: "#f87171", fontSize: 13, marginBottom: 12 }}>{error}</div>
            )}

            {/* Save button */}
            <button
              onClick={handleSave}
              disabled={saving}
              style={{
                width: "100%", padding: "15px 0",
                background: "#34d399", border: "none", borderRadius: 10,
                color: "#fff", fontWeight: 800, fontSize: 16,
                cursor: saving ? "default" : "pointer", opacity: saving ? 0.6 : 1,
              }}>
              {saving ? "Saving…" : "Save Split"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}


// ── Transaction card ──────────────────────────────────────────────────────────

/**
 * One full-width card for a transaction that needs action.
 *
 * mode="pending"  — Sage already suggested a category with a confidence score.
 *                   Shows colored % badge.  Buttons: "✓ Approve" / "Reassign".
 * mode="new"      — Unassigned this month; no Sage score.
 *                   If an auto-rule suggestion exists: shows "From history" row
 *                   with no badge, buttons "✓ Assign" / "Pick other".
 *                   If no suggestion: no suggestion row, just "Choose Category →".
 */
function TxnCard({ txn, onApprove, onReassign, onSplit, busy, mode = "pending" }) {
  const label  = txn.merchant_name || txn.name || "Unknown";
  const acct   = txn.account_name
    ? `${txn.account_name}${txn.account_mask ? ` *${txn.account_mask}` : ""}`
    : null;
  const amount  = fmtAmount(txn.amount);
  const hasSuggestion = !!txn.suggested_item_id;
  // Confidence badge only exists for pending_review items (always have a score)
  const conf = mode === "pending" && txn.confidence != null
    ? confidenceInfo(txn.confidence)
    : null;

  return (
    <div style={{
      background: "var(--bg2, #171b27)",
      marginBottom: 8,          // visible gap between cards — dark bg shows through
      padding: "18px 16px 16px",
    }}>
      {/* Row 1: merchant + colored amount */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 4 }}>
        <span style={{
          flex: 1, fontWeight: 800, fontSize: 20, color: "var(--text, #e8eaf0)",
          lineHeight: 1.2, wordBreak: "break-word",
        }}>
          {label}
        </span>
        <span style={{
          fontWeight: 800, fontSize: 22, color: amount.color,
          flexShrink: 0, paddingTop: 1,
        }}>
          {amount.text}
        </span>
      </div>

      {/* Row 2: date + account */}
      <div style={{
        fontSize: 13, color: "var(--text2, #8b90a7)", marginBottom: 14,
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <span>{fmtDate(txn.date)}</span>
        {acct && (
          <>
            <span style={{ opacity: 0.4 }}>·</span>
            <span>{acct}</span>
          </>
        )}
      </div>

      {/* Row 3: category suggestion row
          - pending: "Suggested" label + colored confidence % badge (always present)
          - new with auto-rule: "From history" label + grey N/A badge (no Sage score)
          - new with no suggestion: omitted entirely — just show the assign button */}
      {hasSuggestion && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, marginBottom: 14,
          padding: "11px 14px", background: "var(--bg3, #1e2336)", borderRadius: 8,
        }}>
          <span style={{ fontSize: 12, color: "var(--text2, #8b90a7)", flexShrink: 0 }}>
            {mode === "pending" ? "Suggested" : "From history"}
          </span>
          <span style={{
            flex: 1, fontWeight: 700, fontSize: 16, color: "var(--text, #e8eaf0)",
          }}>
            {txn.suggested_group_name} › {txn.suggested_item_name}
          </span>
          {/* Confidence badge: solid colored background so it pops visually.
              pending = green/yellow/orange/red fill based on score
              new     = grey fill (auto-rule match, no Sage score) */}
          <span style={{
            fontSize: 13, fontWeight: 800,
            background: conf ? conf.color : "#4b5563",
            color: conf && conf.color === "#f59e0b" ? "#000" : "#fff",
            borderRadius: 6, padding: "4px 11px", flexShrink: 0,
          }}>
            {conf ? conf.label : "N/A"}
          </span>
        </div>
      )}

      {/* Row 4: action buttons
          Primary (green) = approve/assign the suggestion
          Secondary (indigo) = open picker to choose a different category
          Teal = open split modal to divide across multiple categories */}
      <div style={{ display: "flex", gap: 8 }}>
        {hasSuggestion ? (
          <>
            <button
              onClick={() => onApprove(txn.transaction_id, txn.suggested_item_id)}
              disabled={busy}
              style={{
                flex: 2, padding: "14px 0",
                background: "#34d399", border: "none", borderRadius: 10,
                color: "#fff", fontWeight: 800, fontSize: 16,
                cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1,
              }}>
              {mode === "pending" ? "✓ Approve" : "✓ Assign"}
            </button>
            <button
              onClick={() => onReassign(txn)}
              disabled={busy}
              style={{
                flex: 1, padding: "14px 0",
                background: "#4f46e5", border: "none",
                borderRadius: 10, color: "#fff", fontWeight: 700,
                fontSize: 14, cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1,
              }}>
              {mode === "pending" ? "Reassign" : "Pick other"}
            </button>
          </>
        ) : (
          /* No Sage suggestion at all — only option is to open the picker */
          <button
            onClick={() => onReassign(txn)}
            disabled={busy}
            style={{
              flex: 1, padding: "14px 0",
              background: "#4f46e5", border: "none",
              borderRadius: 10, color: "#fff", fontWeight: 700,
              fontSize: 15, cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1,
            }}>
            Choose Category →
          </button>
        )}
        {/* Split button — teal, always visible */}
        <button
          onClick={() => onSplit(txn)}
          disabled={busy}
          style={{
            padding: "14px 14px",
            background: "#0d9488", border: "none",
            borderRadius: 10, color: "#fff", fontWeight: 700,
            fontSize: 14, cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1,
            flexShrink: 0,
          }}
          title="Split this transaction across multiple categories">
          Split
        </button>
      </div>
    </div>
  );
}


// ── Main Review page ──────────────────────────────────────────────────────────

export default function Review() {
  const navigate = useNavigate();
  // pending = Sage auto-assigned, awaiting user approval
  const [txns,        setTxns]        = useState([]);
  // newTxns = unassigned this month — no Sage match yet
  const [newTxns,     setNewTxns]     = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [authFailed,  setAuthFailed]  = useState(false);
  const [busyIds,     setBusyIds]     = useState(new Set());
  const [doneIds,     setDoneIds]     = useState(new Set());     // pending: fading out
  const [newDoneIds,  setNewDoneIds]  = useState(new Set());     // new: fading out
  // reassigning carries the txn + which list it came from so the right API is called
  const [reassigning,  setReassigning]  = useState(null);        // { txn, mode: "pending"|"new" }
  // splittingTxn carries the txn + which list it came from for the split modal
  const [splittingTxn, setSplittingTxn] = useState(null);        // { txn, mode: "pending"|"new" }
  const [bulkBusy,     setBulkBusy]     = useState(false);

  const load = useCallback(async () => {
    try {
      // Fetch both lists in parallel
      const [pending, unassigned] = await Promise.all([
        getAllPendingReview(),
        getAllUnassignedTransactions(),
      ]);
      setTxns(Array.isArray(pending) ? pending : []);
      setNewTxns(Array.isArray(unassigned) ? unassigned : []);
    } catch (err) {
      console.error("[Review] load failed:", err);
      setAuthFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    async function init() {
      const hasDeviceToken = !!localStorage.getItem("vaultic_device_token");
      if (hasDeviceToken) {
        const ok = await deviceAuth();
        if (!ok) { setAuthFailed(true); setLoading(false); return; }
      } else if (!isAuthed()) {
        setAuthFailed(true); setLoading(false); return;
      }
      load();
    }
    init();
  }, [load]);

  // Approve a pending_review transaction (POST /assign/approve)
  async function approve(transactionId, itemId) {
    setBusyIds(prev => new Set([...prev, transactionId]));
    try {
      await approveTransaction(transactionId, itemId);
      setDoneIds(prev => new Set([...prev, transactionId]));
      setTimeout(() => {
        setTxns(prev => prev.filter(t => t.transaction_id !== transactionId));
        setDoneIds(prev => { const s = new Set(prev); s.delete(transactionId); return s; });
      }, 400);
    } catch {}
    finally { setBusyIds(prev => { const s = new Set(prev); s.delete(transactionId); return s; }); }
  }

  // Assign an unassigned transaction for the first time (POST /assign)
  async function assign(transactionId, itemId) {
    setBusyIds(prev => new Set([...prev, transactionId]));
    try {
      await assignTransaction(transactionId, itemId);
      setNewDoneIds(prev => new Set([...prev, transactionId]));
      setTimeout(() => {
        setNewTxns(prev => prev.filter(t => t.transaction_id !== transactionId));
        setNewDoneIds(prev => { const s = new Set(prev); s.delete(transactionId); return s; });
      }, 400);
    } catch {}
    finally { setBusyIds(prev => { const s = new Set(prev); s.delete(transactionId); return s; }); }
  }

  // Bulk-approve all pending_review with confidence ≥ 85
  async function approveAllHighConf() {
    const highConf = (Array.isArray(txns) ? txns : []).filter(t => t != null && (t.confidence ?? 0) >= 85);
    if (!highConf.length) return;
    setBulkBusy(true);
    for (const t of highConf) await approve(t.transaction_id, t.suggested_item_id);
    setBulkBusy(false);
  }

  // Visible (not yet faded out) items for each list
  const safeTxns   = Array.isArray(txns)    ? txns    : [];
  const safeNewTxns = Array.isArray(newTxns) ? newTxns : [];
  const visible    = safeTxns.filter(t   => t != null && (doneIds    instanceof Set ? !doneIds.has(t.transaction_id)    : true));
  const visibleNew = safeNewTxns.filter(t => t != null && (newDoneIds instanceof Set ? !newDoneIds.has(t.transaction_id) : true));
  const highConfCount = visible.filter(t => (t.confidence ?? 0) >= 85).length;
  const totalCount = visible.length + visibleNew.length;

  // ── Reassign / assign picker handler ─────────────────────────────────────
  // Calls the right API depending on which list the transaction came from.
  function handleReassignSelect(itemId) {
    if (!reassigning) return;
    const { txn, mode } = reassigning;
    setReassigning(null);
    if (mode === "new") assign(txn.transaction_id, itemId);
    else                approve(txn.transaction_id, itemId);
  }

  // ── Split save handler ────────────────────────────────────────────────────
  // After saving splits, remove the transaction from whichever list it was in.
  function handleSplitSave() {
    if (!splittingTxn) return;
    const { txn, mode } = splittingTxn;
    setSplittingTxn(null);
    // Animate out then remove from the correct list
    if (mode === "new") {
      setNewDoneIds(prev => new Set([...prev, txn.transaction_id]));
      setTimeout(() => {
        setNewTxns(prev => prev.filter(t => t.transaction_id !== txn.transaction_id));
        setNewDoneIds(prev => { const s = new Set(prev); s.delete(txn.transaction_id); return s; });
      }, 400);
    } else {
      setDoneIds(prev => new Set([...prev, txn.transaction_id]));
      setTimeout(() => {
        setTxns(prev => prev.filter(t => t.transaction_id !== txn.transaction_id));
        setDoneIds(prev => { const s = new Set(prev); s.delete(txn.transaction_id); return s; });
      }, 400);
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────

  // Device token missing or rejected — user needs to log in once from the full app
  if (authFailed) {
    return (
      <div style={{
        position: "fixed", inset: 0, zIndex: 50,
        background: "var(--bg, #0f1117)", display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        gap: 12, padding: 32, textAlign: "center",
      }}>
        <div style={{ fontSize: 48 }}>🔒</div>
        <div style={{ fontWeight: 800, fontSize: 20, color: "var(--text, #e8eaf0)" }}>
          Sign in required
        </div>
        <div style={{ fontSize: 14, color: "var(--text2, #8b90a7)", maxWidth: 280 }}>
          Open Vaultic, sign in, then go to Settings → Push Notifications
          and tap <strong>Disable</strong> then <strong>Enable</strong> to link this device.
        </div>
      </div>
    );
  }

  if (reassigning) {
    return (
      <CategoryPicker
        currentItemId={reassigning.txn.suggested_item_id}
        onSelect={handleReassignSelect}
        onCancel={() => setReassigning(null)}
      />
    );
  }

  if (splittingTxn) {
    return (
      <ReviewSplitModal
        txn={splittingTxn.txn}
        onSave={handleSplitSave}
        onCancel={() => setSplittingTxn(null)}
      />
    );
  }

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 50,
      background: "var(--bg, #0f1117)",
      display: "flex", flexDirection: "column",
      overflowY: "auto",
    }}>
      {/* ── Header ── */}
      <div style={{
        position: "sticky", top: 0, zIndex: 10,
        background: "var(--bg2, #171b27)", borderBottom: "1px solid var(--border, #2a2f45)",
        padding: "14px 16px 12px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={() => navigate("/")}
            style={{
              background: "none", border: "none", color: "var(--accent, #4f8ef7)",
              fontSize: 20, cursor: "pointer", padding: "0 4px", lineHeight: 1,
            }}
            title="Back to Vaultic"
          >
            ←
          </button>
          <span style={{ fontWeight: 800, fontSize: 20, flex: 1 }}>
            Review Queue
          </span>
          {totalCount > 0 && (
            <span style={{
              background: "var(--accent, #4f8ef7)", color: "#fff",
              borderRadius: 12, padding: "2px 10px",
              fontSize: 13, fontWeight: 700,
            }}>
              {totalCount}
            </span>
          )}
          <button
            onClick={load}
            style={{
              background: "none", border: "none", color: "var(--text2, #8b90a7)",
              fontSize: 18, cursor: "pointer", padding: "0 4px",
            }}
            title="Refresh">
            ↻
          </button>
        </div>

        {/* Bulk approve — only for pending_review items with high confidence */}
        {highConfCount > 0 && (
          <button
            onClick={approveAllHighConf}
            disabled={bulkBusy}
            style={{
              marginTop: 10, width: "100%", padding: "11px 0",
              background: "var(--bg3, #1e2336)", border: "1px solid var(--green, #34d399)",
              borderRadius: 8, color: "var(--green, #34d399)",
              fontWeight: 700, fontSize: 14, cursor: bulkBusy ? "default" : "pointer",
              opacity: bulkBusy ? 0.6 : 1,
            }}>
            {bulkBusy ? "Approving…" : `✓ Approve all ${highConfCount} high-confidence`}
          </button>
        )}
      </div>

      {/* ── Body ── */}
      {loading ? (
        <div style={{
          flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
          color: "var(--text2, #8b90a7)", fontSize: 15,
        }}>
          Loading…
        </div>
      ) : totalCount === 0 ? (
        /* All clear — nothing pending AND nothing unassigned */
        <div style={{
          flex: 1, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 12, padding: 32, textAlign: "center",
        }}>
          <div style={{ fontSize: 56 }}>✓</div>
          <div style={{ fontWeight: 800, fontSize: 22, color: "var(--text, #e8eaf0)" }}>
            All clear!
          </div>
          <div style={{ fontSize: 14, color: "var(--text2, #8b90a7)", maxWidth: 260 }}>
            No transactions waiting for review. You'll get a notification when
            new ones arrive after the next sync.
          </div>
        </div>
      ) : (
        <div style={{ flex: 1 }}>

          {/* ── Section 1: Pending Approval (Sage auto-assigned) ── */}
          {visible.length > 0 && (
            <>
              <div style={{
                padding: "10px 16px 10px",
                fontSize: 12, fontWeight: 800, color: "#818cf8",
                textTransform: "uppercase", letterSpacing: "1px",
                background: "var(--bg3, #1e2336)",
                borderBottom: "1px solid var(--border, #2a2f45)",
                borderLeft: "4px solid #6366f1",
              }}>
                Pending Approval · {visible.length}
              </div>
              {visible.map(txn => (
                <div key={txn.transaction_id} style={{
                  transition: "opacity 0.3s, transform 0.3s",
                  opacity: doneIds.has(txn.transaction_id) ? 0 : 1,
                  transform: doneIds.has(txn.transaction_id) ? "translateX(60px)" : "none",
                }}>
                  <TxnCard
                    txn={txn}
                    mode="pending"
                    onApprove={approve}
                    onReassign={txn => setReassigning({ txn, mode: "pending" })}
                    onSplit={txn => setSplittingTxn({ txn, mode: "pending" })}
                    busy={busyIds.has(txn.transaction_id)}
                  />
                </div>
              ))}
            </>
          )}

          {/* ── Section 2: New — Unassigned this month ── */}
          {visibleNew.length > 0 && (
            <>
              <div style={{
                padding: "10px 16px 10px",
                fontSize: 12, fontWeight: 800, color: "#fbbf24",
                textTransform: "uppercase", letterSpacing: "1px",
                background: "var(--bg3, #1e2336)",
                borderBottom: "1px solid var(--border, #2a2f45)",
                borderLeft: "4px solid #f59e0b",
                marginTop: visible.length > 0 ? 8 : 0,
              }}>
                New — Unassigned · {visibleNew.length}
              </div>
              {visibleNew.map(txn => (
                <div key={txn.transaction_id} style={{
                  transition: "opacity 0.3s, transform 0.3s",
                  opacity: newDoneIds.has(txn.transaction_id) ? 0 : 1,
                  transform: newDoneIds.has(txn.transaction_id) ? "translateX(60px)" : "none",
                }}>
                  <TxnCard
                    txn={txn}
                    mode="new"
                    onApprove={assign}
                    onReassign={txn => setReassigning({ txn, mode: "new" })}
                    onSplit={txn => setSplittingTxn({ txn, mode: "new" })}
                    busy={busyIds.has(txn.transaction_id)}
                  />
                </div>
              ))}
            </>
          )}

          <div style={{ height: 32 }} />
        </div>
      )}
    </div>
  );
}
