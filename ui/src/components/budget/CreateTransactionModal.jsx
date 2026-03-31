/**
 * CreateTransactionModal — Modal for manually creating a new transaction.
 *
 * Fields: amount, date, merchant/description, expense/income toggle,
 * budget item dropdown (grouped by budget groups), check #, notes.
 * Modeled after EditExpenseModal for consistent look and feel.
 */
import { useState, useEffect } from "react";
import { createManualTransaction } from "../../api.js";

export default function CreateTransactionModal({ month, allGroups, onClose, onSaved }) {
  const [amount, setAmount]           = useState("");
  const [txnDate, setTxnDate]         = useState("");
  const [merchant, setMerchant]       = useState("");
  const [txnType, setTxnType]         = useState("expense");
  const [itemId, setItemId]           = useState("");
  const [checkNumber, setCheckNumber] = useState("");
  const [notes, setNotes]             = useState("");
  const [saving, setSaving]           = useState(false);
  const [error, setError]             = useState(null);
  const [windowWidth, setWindowWidth] = useState(window.innerWidth);

  // Default date to current month's first day or today (whichever is in-month)
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    const todayMonth = today.slice(0, 7);
    setTxnDate(todayMonth === month ? today : `${month}-01`);
  }, [month]);

  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Close on Escape
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isIncome    = txnType === "income";
  const amountColor = isIncome ? "var(--green)" : "var(--red)";
  const accentBg    = isIncome ? "var(--green)" : "var(--accent)";
  const parsedAmt   = parseFloat(amount) || 0;
  const canSave     = parsedAmt > 0 && merchant.trim().length > 0 && txnDate;

  async function handleSave() {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      await createManualTransaction({
        amount: parsedAmt,
        date: txnDate,
        merchant_name: merchant.trim(),
        is_income: isIncome,
        item_id: itemId ? parseInt(itemId) : null,
        check_number: checkNumber || null,
        notes: notes || null,
      });
      onSaved?.();
      onClose();
    } catch (e) {
      setError(e.message || "Failed to create transaction");
    } finally {
      setSaving(false);
    }
  }

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
          padding: windowWidth <= 480 ? "16px 16px 20px" : "20px 48px 24px 36px",
          boxSizing: "border-box",
        }}
      >
        {/* Header: title + Expense/Income toggle + close */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text)", flex: 1 }}>
            {isIncome ? "New Income" : "New Expense"}
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
          >&#x2715;</button>
        </div>

        {error && (
          <div style={{ padding: "8px 0", textAlign: "center", color: "var(--red)", fontSize: 13 }}>
            {error}
          </div>
        )}

        {/* Editable dollar amount */}
        <div style={{ textAlign: "center", marginBottom: 18 }}>
          <div style={{ display: "inline-flex", alignItems: "baseline", gap: 2 }}>
            <span style={{ fontSize: 28, fontWeight: 800, color: amountColor }}>$</span>
            <input
              type="text"
              inputMode="decimal"
              value={amount}
              onChange={e => setAmount(e.target.value.replace(/[^0-9.]/g, ""))}
              onBlur={e => {
                const n = parseFloat(e.target.value);
                if (!isNaN(n) && n > 0) setAmount(n.toFixed(2));
              }}
              placeholder="0.00"
              autoFocus
              style={{
                fontSize: 32, fontWeight: 800, color: amountColor,
                background: "transparent", border: "none",
                borderBottom: `2px solid ${amountColor}`,
                outline: "none",
                width: Math.max(80, Math.min((amount.length || 4) * 22 + 10, 300)) + "px",
                textAlign: "center", padding: "0 4px",
                MozAppearance: "textfield", appearance: "textfield",
              }}
            />
          </div>
        </div>

        {/* Date + Merchant row */}
        <div style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "stretch" }}>
          <input
            type="date"
            value={txnDate}
            onChange={e => setTxnDate(e.target.value)}
            style={{
              flexShrink: 0, background: "var(--bg3)",
              border: "1px solid var(--border)", borderRadius: 6,
              color: "var(--text)", fontSize: 14, fontWeight: 600,
              padding: "8px 10px",
            }}
          />
          <input
            type="text"
            value={merchant}
            onChange={e => setMerchant(e.target.value)}
            placeholder="Merchant / Description"
            style={{
              flex: 1, background: "var(--bg3)",
              border: "1px solid var(--border)", borderRadius: 6,
              color: "var(--text)", fontSize: 16, fontWeight: 700,
              padding: "8px 10px",
            }}
          />
        </div>

        {/* Budget item assignment dropdown */}
        <div style={{ marginBottom: 14 }}>
          <label style={{
            fontSize: 11, fontWeight: 700, color: "var(--text2)",
            textTransform: "uppercase", letterSpacing: "0.6px",
            display: "block", marginBottom: 6,
          }}>
            Budget Assignment
          </label>
          <select
            value={itemId}
            onChange={e => setItemId(e.target.value)}
            style={{
              width: "100%", background: "var(--bg3)",
              border: "1px solid var(--border)", borderRadius: 6,
              color: "var(--text)", fontSize: 13, padding: "8px 10px",
            }}
          >
            <option value="">Unassigned (goes to New queue)</option>
            {allGroups.map(g => (
              <optgroup key={g.id} label={g.name}>
                {(g.items || []).map(item => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>

        {/* Check # */}
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

        {/* Notes */}
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
            placeholder="Add a note..."
            rows={3}
            style={{
              width: "100%", boxSizing: "border-box",
              background: "var(--bg3)", border: "1px solid var(--border)",
              borderRadius: 6, color: "var(--text)", fontSize: 13,
              padding: "7px 10px", resize: "vertical", fontFamily: "inherit",
            }}
          />
        </div>

        {/* Action buttons */}
        <div style={{
          display: "flex", justifyContent: "flex-end", alignItems: "center",
          borderTop: "1px solid var(--border)", paddingTop: 16, gap: 8,
        }}>
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
            {saving ? "Creating..." : (isIncome ? "Add Income" : "Add Expense")}
          </button>
        </div>
      </div>
    </div>
  );
}
