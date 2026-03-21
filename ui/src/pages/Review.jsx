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
import { getAllPendingReview, approveTransaction, getBudget, isAuthed, deviceAuth } from "../api.js";

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(v) {
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(Math.abs(v ?? 0));
}

function fmtDate(s) {
  if (!s) return "";
  return new Date(s.substring(0, 10) + "T12:00:00")
    .toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** Map 0–100 confidence to a label and color. */
function confidenceInfo(conf) {
  if (conf == null) return { label: "—",    color: "var(--text2)" };
  if (conf >= 90)   return { label: "High", color: "var(--green)" };
  if (conf >= 70)   return { label: "Good", color: "#f59e0b" };
  return              { label: "Low",  color: "var(--red)" };
}


// ── Category picker ───────────────────────────────────────────────────────────

/**
 * Full-screen category picker shown when the user taps "Reassign".
 * Fetches all active budget groups/items and renders them as a flat scrollable
 * list grouped by category name.
 */
function CategoryPicker({ currentItemId, onSelect, onCancel }) {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const month = new Date().toISOString().slice(0, 7);
    getBudget(month)
      .then(data => setGroups(data.groups ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 200,
      background: "var(--bg)", display: "flex", flexDirection: "column",
    }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "16px 16px 12px", borderBottom: "1px solid var(--border)",
        background: "var(--bg2)",
      }}>
        <button onClick={onCancel}
          style={{ background: "none", border: "none", color: "var(--accent)",
            fontSize: 16, cursor: "pointer", padding: 0 }}>
          ← Back
        </button>
        <span style={{ fontWeight: 700, fontSize: 16, flex: 1 }}>Reassign to…</span>
      </div>

      {/* Items list */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {loading ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text2)" }}>
            Loading…
          </div>
        ) : (
          groups.map(g => (
            <div key={g.id}>
              {/* Group header */}
              <div style={{
                padding: "10px 16px 6px",
                fontSize: 11, fontWeight: 700, color: "var(--text2)",
                textTransform: "uppercase", letterSpacing: "0.8px",
                background: "var(--bg3)",
                borderBottom: "1px solid var(--border)",
              }}>
                {g.name}
              </div>
              {/* Items */}
              {g.items.map(item => (
                <button key={item.id}
                  onClick={() => onSelect(item.id, item.name, g.name)}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    width: "100%", padding: "14px 16px",
                    background: item.id === currentItemId ? "var(--bg3)" : "transparent",
                    border: "none", borderBottom: "1px solid var(--border)",
                    color: "var(--text)", fontSize: 15, cursor: "pointer",
                    textAlign: "left",
                  }}>
                  <span>{item.name}</span>
                  {item.id === currentItemId && (
                    <span style={{ color: "var(--accent)", fontSize: 13 }}>✓ current</span>
                  )}
                </button>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}


// ── Transaction card ──────────────────────────────────────────────────────────

/**
 * One full-width card representing a single pending transaction.
 * Shows merchant, amount, date, account, and suggested category.
 * Approve collapses the card with a green flash; Reassign opens the picker.
 */
function TxnCard({ txn, onApprove, onReassign, busy }) {
  const conf  = confidenceInfo(txn.confidence);
  const label = txn.merchant_name || txn.name || "Unknown";
  const acct  = txn.account_name
    ? `${txn.account_name}${txn.account_mask ? ` *${txn.account_mask}` : ""}`
    : null;

  return (
    <div style={{
      background: "var(--bg2)",
      borderBottom: "3px solid var(--border)",
      padding: "18px 16px 14px",
    }}>
      {/* Row 1: merchant + amount */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 6 }}>
        <span style={{
          flex: 1, fontWeight: 800, fontSize: 20, color: "var(--text)",
          lineHeight: 1.2, wordBreak: "break-word",
        }}>
          {label}
        </span>
        <span style={{
          fontWeight: 800, fontSize: 22, color: "var(--text)",
          flexShrink: 0, paddingTop: 1,
        }}>
          {fmt(txn.amount)}
        </span>
      </div>

      {/* Row 2: date + account */}
      <div style={{
        fontSize: 13, color: "var(--text2)", marginBottom: 12,
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

      {/* Row 3: suggested category + confidence */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8, marginBottom: 14,
        padding: "8px 12px", background: "var(--bg3)", borderRadius: 8,
      }}>
        <span style={{ fontSize: 12, color: "var(--text2)" }}>Suggested</span>
        <span style={{
          flex: 1, fontWeight: 700, fontSize: 14, color: "var(--text)",
        }}>
          {txn.suggested_group_name} › {txn.suggested_item_name}
        </span>
        <span style={{
          fontSize: 11, fontWeight: 700, color: conf.color,
          background: "var(--bg)", borderRadius: 4, padding: "2px 7px",
        }}>
          {conf.label}
        </span>
      </div>

      {/* Row 4: action buttons */}
      <div style={{ display: "flex", gap: 10 }}>
        <button
          onClick={() => onApprove(txn.transaction_id, txn.suggested_item_id)}
          disabled={busy}
          style={{
            flex: 2, padding: "13px 0",
            background: "var(--green)", border: "none", borderRadius: 10,
            color: "#fff", fontWeight: 800, fontSize: 16, cursor: busy ? "default" : "pointer",
            opacity: busy ? 0.6 : 1,
          }}>
          ✓ Approve
        </button>
        <button
          onClick={() => onReassign(txn)}
          disabled={busy}
          style={{
            flex: 1, padding: "13px 0",
            background: "var(--bg3)", border: "1px solid var(--border)",
            borderRadius: 10, color: "var(--text)", fontWeight: 700,
            fontSize: 15, cursor: busy ? "default" : "pointer",
            opacity: busy ? 0.6 : 1,
          }}>
          Reassign
        </button>
      </div>
    </div>
  );
}


// ── Main Review page ──────────────────────────────────────────────────────────

export default function Review() {
  const navigate = useNavigate();
  const [txns,        setTxns]        = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [authFailed,  setAuthFailed]  = useState(false);   // true if device token missing/invalid
  const [busyIds,     setBusyIds]     = useState(new Set());
  const [doneIds,     setDoneIds]     = useState(new Set());   // approved — fade out
  const [reassigning, setReassigning] = useState(null);        // txn being reassigned
  const [bulkBusy,    setBulkBusy]    = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await getAllPendingReview();
      setTxns(data);
    } catch {
      // If API returns 401 the apiFetch handler fires auth:logout and throws —
      // treat as auth failure rather than empty queue
      setAuthFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // If the normal JWT is missing or expired, try to silently re-authenticate
    // using the device_token stored in localStorage when push was subscribed.
    // This lets the Review page work after a notification tap without the user
    // ever needing to open the full app and log in manually.
    async function init() {
      if (!isAuthed()) {
        const ok = await deviceAuth();
        if (!ok) {
          setAuthFailed(true);
          setLoading(false);
          return;
        }
      }
      load();
    }
    init();
  }, [load]);

  // Approve a single transaction (or reassign to a different item)
  async function approve(transactionId, itemId) {
    setBusyIds(prev => new Set([...prev, transactionId]));
    try {
      await approveTransaction(transactionId, itemId);
      setDoneIds(prev => new Set([...prev, transactionId]));
      // Remove from list after brief green flash
      setTimeout(() => {
        setTxns(prev => prev.filter(t => t.transaction_id !== transactionId));
        setDoneIds(prev => { const s = new Set(prev); s.delete(transactionId); return s; });
      }, 400);
    } catch {}
    finally {
      setBusyIds(prev => { const s = new Set(prev); s.delete(transactionId); return s; });
    }
  }

  // Bulk-approve all transactions with confidence ≥ 85
  async function approveAllHighConf() {
    const highConf = txns.filter(t => (t.confidence ?? 0) >= 85);
    if (!highConf.length) return;
    setBulkBusy(true);
    for (const t of highConf) {
      await approve(t.transaction_id, t.suggested_item_id);
    }
    setBulkBusy(false);
  }

  // Pending (not yet animated away) transactions
  const visible = txns.filter(t => !doneIds.has(t.transaction_id));
  const highConfCount = visible.filter(t => (t.confidence ?? 0) >= 85).length;

  // ── Reassign picker handler ──────────────────────────────────────────────
  function handleReassignSelect(itemId, itemName, groupName) {
    if (!reassigning) return;
    const txn = reassigning;
    setReassigning(null);
    approve(txn.transaction_id, itemId);
  }

  // ── Render ───────────────────────────────────────────────────────────────

  // Device token missing or rejected — user needs to log in once from the full app
  if (authFailed) {
    return (
      <div style={{
        position: "fixed", inset: 0, zIndex: 50,
        background: "var(--bg)", display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        gap: 12, padding: 32, textAlign: "center",
      }}>
        <div style={{ fontSize: 48 }}>🔒</div>
        <div style={{ fontWeight: 800, fontSize: 20, color: "var(--text)" }}>
          Sign in required
        </div>
        <div style={{ fontSize: 14, color: "var(--text2)", maxWidth: 280 }}>
          Open Vaultic, sign in, then go to Settings → Push Notifications
          and tap <strong>Disable</strong> then <strong>Enable</strong> to link this device.
        </div>
      </div>
    );
  }

  if (reassigning) {
    return (
      <CategoryPicker
        currentItemId={reassigning.suggested_item_id}
        onSelect={handleReassignSelect}
        onCancel={() => setReassigning(null)}
      />
    );
  }

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 50,
      background: "var(--bg)",
      display: "flex", flexDirection: "column",
      overflowY: "auto",
    }}>
      {/* ── Header ── */}
      <div style={{
        position: "sticky", top: 0, zIndex: 10,
        background: "var(--bg2)", borderBottom: "1px solid var(--border)",
        padding: "14px 16px 12px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={() => navigate("/")}
            style={{
              background: "none", border: "none", color: "var(--accent)",
              fontSize: 20, cursor: "pointer", padding: "0 4px", lineHeight: 1,
            }}
            title="Back to Vaultic"
          >
            ←
          </button>
          <span style={{ fontWeight: 800, fontSize: 20, flex: 1 }}>
            Review Queue
          </span>
          {visible.length > 0 && (
            <span style={{
              background: "var(--accent)", color: "#fff",
              borderRadius: 12, padding: "2px 10px",
              fontSize: 13, fontWeight: 700,
            }}>
              {visible.length}
            </span>
          )}
          <button
            onClick={load}
            style={{
              background: "none", border: "none", color: "var(--text2)",
              fontSize: 18, cursor: "pointer", padding: "0 4px",
            }}
            title="Refresh">
            ↻
          </button>
        </div>

        {/* Bulk approve button — only shown when there are high-confidence items */}
        {highConfCount > 1 && (
          <button
            onClick={approveAllHighConf}
            disabled={bulkBusy}
            style={{
              marginTop: 10, width: "100%", padding: "11px 0",
              background: "var(--bg3)", border: "1px solid var(--green)",
              borderRadius: 8, color: "var(--green)",
              fontWeight: 700, fontSize: 14, cursor: bulkBusy ? "default" : "pointer",
              opacity: bulkBusy ? 0.6 : 1,
            }}>
            {bulkBusy
              ? "Approving…"
              : `✓ Approve all ${highConfCount} high-confidence`}
          </button>
        )}
      </div>

      {/* ── Body ── */}
      {loading ? (
        <div style={{
          flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
          color: "var(--text2)", fontSize: 15,
        }}>
          Loading…
        </div>
      ) : visible.length === 0 ? (
        /* All clear state */
        <div style={{
          flex: 1, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 12, padding: 32, textAlign: "center",
        }}>
          <div style={{ fontSize: 56 }}>✓</div>
          <div style={{ fontWeight: 800, fontSize: 22, color: "var(--text)" }}>
            All clear!
          </div>
          <div style={{ fontSize: 14, color: "var(--text2)", maxWidth: 260 }}>
            No transactions waiting for review. You'll get a notification when
            new ones arrive after the next sync.
          </div>
        </div>
      ) : (
        /* Transaction cards */
        <div style={{ flex: 1 }}>
          {visible.map(txn => (
            <div
              key={txn.transaction_id}
              style={{
                transition: "opacity 0.3s, transform 0.3s",
                opacity: doneIds.has(txn.transaction_id) ? 0 : 1,
                transform: doneIds.has(txn.transaction_id) ? "translateX(60px)" : "none",
              }}>
              <TxnCard
                txn={txn}
                onApprove={approve}
                onReassign={setReassigning}
                busy={busyIds.has(txn.transaction_id)}
              />
            </div>
          ))}
          {/* Bottom padding so last card isn't flush against safe area */}
          <div style={{ height: 32 }} />
        </div>
      )}
    </div>
  );
}
