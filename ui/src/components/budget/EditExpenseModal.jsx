import { useState, useEffect } from "react";
import { getTransaction, saveTransactionSplits, unassignTransaction } from "../../api.js";
import { fmt } from "../../utils/format.js";

export default function EditExpenseModal({ txnId, allGroups, onClose, onSaved }) {
  const [txn, setTxn]             = useState(null);
  const [splits, setSplits]       = useState([]);
  const [saving, setSaving]       = useState(false);
  const [error, setError]         = useState(null);
  // Editable transaction fields
  const [editDate, setEditDate]       = useState("");
  const [editMerchant, setEditMerchant] = useState("");
  const [editAmount, setEditAmount]   = useState("");
  const [checkNumber, setCheckNumber] = useState("");
  const [notes, setNotes]             = useState("");
  // Track window width for responsive padding
  const [windowWidth, setWindowWidth] = useState(window.innerWidth);
  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  // Expense vs Income toggle — seeded from the raw Plaid amount sign on load
  const [txnType, setTxnType] = useState("expense"); // "expense" | "income"

  // Load transaction and pre-populate all editable fields
  useEffect(() => {
    if (!txnId) return;
    setTxn(null); setError(null);
    getTransaction(txnId).then(data => {
      setTxn(data);
      setEditDate(data.date);
      setEditMerchant(data.merchant);
      setEditAmount(data.amount.toFixed(2));
      setCheckNumber(data.check_number || "");
      setNotes(data.notes || "");
      setTxnType(data.is_income ? "income" : "expense");
      setSplits(
        data.splits.length > 0
          ? data.splits.map(s => ({ item_id: s.item_id, amount: String(s.amount.toFixed(2)) }))
          : [{ item_id: null, amount: data.amount.toFixed(2) }]
      );
    }).catch(() => setError("Failed to load transaction"));
  }, [txnId]);

  // Close on Escape key
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Add a split row for the selected item, pre-filling the remaining unallocated amount
  function addSplit(itemId) {
    const effective = parseFloat(editAmount) || 0;
    setSplits(prev => {
      const allocated = prev.reduce((s, x) => s + (parseFloat(x.amount) || 0), 0);
      const remainder = Math.max(0, effective - allocated);
      return [...prev, { item_id: parseInt(itemId), amount: remainder.toFixed(2) }];
    });
  }

  function removeSplit(idx) {
    setSplits(prev => prev.filter((_, i) => i !== idx));
  }

  function updateSplit(idx, field, value) {
    setSplits(prev => prev.map((s, i) => i === idx ? { ...s, [field]: value } : s));
  }

  const effectiveAmount = parseFloat(editAmount) || 0;
  const splitTotal      = splits.reduce((sum, s) => sum + (parseFloat(s.amount) || 0), 0);
  const totalMatch      = effectiveAmount > 0 && Math.abs(splitTotal - effectiveAmount) < 0.02;
  const amountValid     = effectiveAmount > 0;
  const canSave = txn && amountValid && splits.length > 0
    && splits.every(s => s.item_id != null && parseFloat(s.amount) > 0)
    && totalMatch;

  async function handleSave() {
    if (!canSave) return;
    setSaving(true); setError(null);
    try {
      await saveTransactionSplits(
        txnId,
        splits.map(s => ({
          item_id: parseInt(s.item_id),
          amount:  parseFloat(parseFloat(s.amount).toFixed(2)),
        })),
        { check_number: checkNumber || null, notes: notes || null }
      );
      onSaved?.();
      onClose();
    } catch (e) {
      setError(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    await unassignTransaction(txnId);
    onSaved?.();
    onClose();
  }

  // "CREDIT CARD *1941" — built from account subtype + mask
  function accountLabel(txnData) {
    if (!txnData) return "";
    const subtype = (txnData.account_subtype || "").replace(/_/g, " ").toUpperCase();
    const mask    = txnData.account_mask ? ` *${txnData.account_mask}` : "";
    return (subtype || txnData.account_name || "ACCOUNT") + mask;
  }

  const isIncome    = txnType === "income";
  const amountColor = isIncome ? "var(--green)" : "var(--red)";
  const accentBg    = isIncome ? "var(--green)" : "var(--accent)";

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 600,
        background: "rgba(0,0,0,0.75)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={e => e.stopPropagation()}
        style={{
          width: 700, maxWidth: "95vw", maxHeight: "90vh",
          overflowY: "auto", overflowX: "hidden",
          background: "var(--bg2)", borderRadius: 12,
          border: "1px solid var(--border)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.7)",
          // Reduce horizontal padding on narrow screens so content isn't cut off
          padding: windowWidth <= 480 ? "16px 16px 20px" : "20px 48px 24px 36px",
          boxSizing: "border-box",
        }}
      >
        {/* ── Header: title + Expense/Income toggle + close ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text)", flex: 1 }}>
            {isIncome ? "Edit Income" : "Edit Expense"}
          </div>

          {/* Expense / Income pill toggle */}
          <div style={{
            display: "flex", alignItems: "center", gap: 2,
            background: "var(--bg3)", borderRadius: 20, padding: "3px 4px",
          }}>
            {["expense", "income"].map(t => (
              <button
                key={t}
                onClick={() => setTxnType(t)}
                style={{
                  padding: "4px 12px", borderRadius: 16, border: "none",
                  cursor: "pointer", fontSize: 12, fontWeight: 600,
                  background: txnType === t ? (t === "income" ? "var(--green)" : "var(--accent)") : "transparent",
                  color: txnType === t ? "#fff" : "var(--text2)",
                  transition: "all 0.15s",
                }}
              >
                {t === "expense" ? "Expense" : "Income"}
              </button>
            ))}
          </div>

          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background: "none", border: "none", color: "var(--text2)", fontSize: 18, cursor: "pointer", lineHeight: 1, marginLeft: 4 }}
          >✕</button>
        </div>

        {/* Loading / error states */}
        {!txn && !error && (
          <div style={{ padding: "32px 0", textAlign: "center", color: "var(--text2)", fontSize: 13 }}>
            Loading…
          </div>
        )}
        {error && (
          <div style={{ padding: "16px 0", textAlign: "center", color: "var(--red)", fontSize: 13 }}>
            {error}
          </div>
        )}

        {txn && (
          <>
            {/* ── Editable dollar amount — large, centered, colored ── */}
            <div style={{ textAlign: "center", marginBottom: 18 }}>
              <div style={{ display: "inline-flex", alignItems: "baseline", gap: 2 }}>
                <span style={{ fontSize: 28, fontWeight: 800, color: amountColor }}>$</span>
                <input
                  type="text"
                  inputMode="decimal"
                  value={editAmount}
                  onChange={e => setEditAmount(e.target.value.replace(/[^0-9.]/g, ""))}
                  onBlur={e => {
                    const n = parseFloat(e.target.value);
                    if (!isNaN(n) && n > 0) setEditAmount(n.toFixed(2));
                  }}
                  style={{
                    fontSize: 32, fontWeight: 800, color: amountColor,
                    background: "transparent", border: "none",
                    borderBottom: `2px solid ${amountColor}`,
                    outline: "none",
                    width: Math.max(80, Math.min((editAmount.length || 4) * 22 + 10, 300)) + "px",
                    textAlign: "center", padding: "0 4px",
                    MozAppearance: "textfield", appearance: "textfield",
                  }}
                />
              </div>
              {!amountValid && editAmount !== "" && (
                <div style={{ color: "var(--red)", fontSize: 11, marginTop: 4 }}>
                  Enter a valid amount greater than $0
                </div>
              )}
            </div>

            {/* ── Editable date + merchant row ── */}
            <div style={{ display: "flex", gap: 10, marginBottom: 8, alignItems: "stretch" }}>
              <input
                type="date"
                value={editDate}
                onChange={e => setEditDate(e.target.value)}
                style={{
                  flexShrink: 0, background: "var(--bg3)",
                  border: "1px solid var(--border)", borderRadius: 6,
                  color: "var(--text)", fontSize: 14, fontWeight: 600,
                  padding: "8px 10px",
                }}
              />
              <input
                type="text"
                value={editMerchant}
                onChange={e => setEditMerchant(e.target.value)}
                placeholder="Merchant"
                style={{
                  flex: 1, background: "var(--bg3)",
                  border: "1px solid var(--border)", borderRadius: 6,
                  color: "var(--text)", fontSize: 16, fontWeight: 700,
                  padding: "8px 10px",
                }}
              />
            </div>

            {/* ── Raw Plaid transaction description (e.g. "EBAY O*21-14309-16607") ── */}
            {txn.description && (
              <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 6, letterSpacing: "0.2px" }}>
                {txn.description}
              </div>
            )}

            {/* ── Account label (e.g. "CREDIT CARD *1941") — large, badge-style ── */}
            <div style={{ marginBottom: 20 }}>
              <span style={{
                display: "inline-block",
                fontSize: 14, fontWeight: 600, color: "var(--text2)",
                background: "var(--bg3)", border: "1px solid var(--border)",
                borderRadius: 6, padding: "5px 12px", letterSpacing: "0.5px",
              }}>
                {accountLabel(txn)}
              </span>
            </div>

            {/* ── Split assignment rows ── */}
            <div style={{ marginBottom: 4 }}>
              <div style={{
                fontSize: 11, fontWeight: 700, color: "var(--text2)",
                textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 10,
              }}>
                Budget Assignment
              </div>

              {splits.map((split, idx) => (
                <div key={idx} style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
                  {/* Red − remove button — only when more than one split */}
                  {splits.length > 1 ? (
                    <button
                      onClick={() => removeSplit(idx)}
                      title="Remove this split"
                      style={{
                        width: 22, height: 22, borderRadius: "50%",
                        background: "var(--red)", border: "none", color: "#fff",
                        cursor: "pointer", fontSize: 16, lineHeight: 1, flexShrink: 0,
                        display: "flex", alignItems: "center", justifyContent: "center",
                      }}
                    aria-label="Remove this split">−</button>
                  ) : (
                    <div style={{ width: 22, flexShrink: 0 }} />
                  )}

                  {/* Budget item dropdown — allGroups already has only active (non-deleted) items.
                      minWidth:0 lets the select compress on narrow mobile screens so the
                      amount input to its right doesn't get clipped. */}
                  <select
                    value={split.item_id ?? ""}
                    onChange={e => updateSplit(idx, "item_id", e.target.value ? parseInt(e.target.value) : null)}
                    style={{
                      flex: 1, minWidth: 0, background: "var(--bg3)",
                      border: `1px solid ${split.item_id ? "var(--border)" : "var(--accent)"}`,
                      borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "7px 8px",
                    }}
                  >
                    <option value="">Select budget item…</option>
                    {allGroups.map(g => (
                      <optgroup key={g.id} label={g.name}>
                        {(g.items || []).map(item => (
                          <option key={item.id} value={item.id}>{item.name}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>

                  {/* Split amount — text input, never shrinks so value is always visible */}
                  <input
                    type="text"
                    inputMode="decimal"
                    value={split.amount}
                    onChange={e => updateSplit(idx, "amount", e.target.value.replace(/[^0-9.]/g, ""))}
                    onBlur={e => {
                      const n = parseFloat(e.target.value);
                      if (!isNaN(n)) updateSplit(idx, "amount", n.toFixed(2));
                    }}
                    style={{
                      width: 96, flexShrink: 0, background: "var(--bg3)",
                      border: "1px solid var(--border)",
                      borderRadius: 6, color: "var(--text)", fontSize: 13,
                      padding: "7px 8px", textAlign: "right",
                    }}
                  />
                </div>
              ))}
            </div>

            {/* ── Add a Split dropdown — only shows active items not yet in the split ── */}
            {(() => {
              const usedIds = new Set(splits.map(s => s.item_id).filter(Boolean));
              const hasMore = allGroups.some(g => (g.items || []).some(item => !usedIds.has(item.id)));
              if (!hasMore) return null;
              return (
                <select
                  value=""
                  onChange={e => { if (e.target.value) addSplit(e.target.value); }}
                  style={{
                    background: "var(--bg3)", border: "1px solid var(--border)",
                    borderRadius: 6, color: "var(--accent)", fontSize: 13,
                    padding: "6px 10px", cursor: "pointer", marginBottom: 8,
                    fontWeight: 600,
                  }}
                >
                  <option value="">+ Add a Split…</option>
                  {allGroups.map(g => (
                    <optgroup key={g.id} label={g.name}>
                      {(g.items || []).filter(item => !usedIds.has(item.id)).map(item => (
                        <option key={item.id} value={item.id}>{item.name}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              );
            })()}

            {/* Split total balance indicator — only visible when multiple splits */}
            {splits.length > 1 && (
              <div style={{
                fontSize: 12, marginBottom: 12, textAlign: "right",
                color: totalMatch ? "var(--text2)" : "var(--red)",
              }}>
                Split total: {fmt(splitTotal)} / {fmt(effectiveAmount)}
                {!totalMatch && " — must equal transaction amount"}
              </div>
            )}

            {/* ── Check # ── */}
            <div style={{ marginBottom: 10 }}>
              <label style={{
                fontSize: 11, fontWeight: 700, color: "var(--text2)",
                textTransform: "uppercase", letterSpacing: "0.5px",
                display: "block", marginBottom: 4,
              }}>
                Check #
              </label>
              <input
                type="text"
                value={checkNumber}
                onChange={e => setCheckNumber(e.target.value)}
                placeholder="Optional check number"
                style={{
                  width: "100%", boxSizing: "border-box",
                  background: "var(--bg3)", border: "1px solid var(--border)",
                  borderRadius: 6, color: "var(--text)", fontSize: 13,
                  padding: "7px 10px",
                }}
              />
            </div>

            {/* ── Notes ── */}
            <div style={{ marginBottom: 18 }}>
              <label style={{
                fontSize: 11, fontWeight: 700, color: "var(--text2)",
                textTransform: "uppercase", letterSpacing: "0.5px",
                display: "block", marginBottom: 4,
              }}>
                Notes
              </label>
              <textarea
                value={notes}
                onChange={e => setNotes(e.target.value)}
                placeholder="Add a note…"
                rows={3}
                style={{
                  width: "100%", boxSizing: "border-box",
                  background: "var(--bg3)", border: "1px solid var(--border)",
                  borderRadius: 6, color: "var(--text)", fontSize: 13,
                  padding: "7px 10px", resize: "vertical", fontFamily: "inherit",
                }}
              />
            </div>

            {/* ── Action buttons ── */}
            <div style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              borderTop: "1px solid var(--border)", paddingTop: 16, gap: 8,
            }}>
              <button
                onClick={handleDelete}
                style={{
                  background: "none", border: "none", color: "var(--red)",
                  cursor: "pointer", fontSize: 13, fontWeight: 600, flexShrink: 0,
                }}
              >
                Delete Transaction
              </button>

              <div style={{ display: "flex", gap: 8 }}>
                <button
                  onClick={onClose}
                  style={{
                    padding: "8px 16px", borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: "none", color: "var(--text)",
                    cursor: "pointer", fontSize: 13,
                  }}
                >Cancel</button>

                <button
                  onClick={canSave && !saving ? handleSave : undefined}
                  style={{
                    padding: "8px 18px", borderRadius: 6, border: "none",
                    background: canSave && !saving ? accentBg : "var(--bg3)",
                    color: canSave && !saving ? "#fff" : "var(--text2)",
                    cursor: canSave && !saving ? "pointer" : "not-allowed",
                    fontSize: 13, fontWeight: 600,
                    opacity: canSave && !saving ? 1 : 0.45,
                    pointerEvents: saving ? "none" : "auto",
                  }}
                >
                  {saving ? "Saving…" : (isIncome ? "Track Income" : "Track Expense")}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
