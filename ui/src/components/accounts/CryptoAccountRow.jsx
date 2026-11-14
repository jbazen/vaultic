import { useState } from "react";
import { renameAccount, updateAccountNotes } from "../../api.js";
import { fmt, fmtCrypto, fmtPrice, fmtDate } from "../../utils/format.js";
import AccountMetaLine from "./AccountMetaLine.jsx";

// ── Crypto Account Row ─────────────────────────────────────────────────────────
// Expandable row for Coinbase holdings. Click the row to show/hide native balance,
// unit price, and USD value. Same expand pattern as investment accounts.
export default function CryptoAccountRow({ account, onRenamed }) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(account.display_name || account.name);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameAccount(account.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  function handleRowClick(e) {
    if (e.target.closest("button, input, a")) return;
    setExpanded(ex => !ex);
  }

  const label = account.display_name || account.name;
  const currency = (account.subtype || "").toUpperCase();

  return (
    <div className="account-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
      {/* Header — click to expand */}
      <div onClick={handleRowClick}
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}>
        <div className="account-info">
          {editing ? (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input className="form-input" style={{ width: 180, padding: "5px 8px", fontSize: 14 }}
                value={draft} onChange={e => setDraft(e.target.value)} autoFocus
                onKeyDown={e => { if (e.key === "Enter") handleSave(); if (e.key === "Escape") { setEditing(false); setDraft(label); }}} />
              <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={handleSave} disabled={saving}>{saving ? "…" : "Save"}</button>
              <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => { setEditing(false); setDraft(label); }}>Cancel</button>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--text2)", fontSize: 11, display: "inline-block",
                transition: "transform 0.15s", transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}>▶</span>
              <div className="account-name">{label}</div>
              <button onClick={() => setEditing(true)} title="Rename"
                style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }} aria-label="Rename">✎</button>
            </div>
          )}
          <AccountMetaLine
            type="crypto"
            notes={account.notes}
            onSaveNotes={async v => { await updateAccountNotes(account.id, v); onRenamed(); }}
            date={account.snapped_at ? fmtDate(account.snapped_at) : null}
          />
        </div>
        <div className="account-balance">{fmt(account.current)}</div>
      </div>
      {/* Expandable detail: native balance, unit price, USD value */}
      {expanded && (
        <div style={{ display: "flex", gap: 24, fontSize: 13, color: "var(--text2)", marginTop: 10, paddingLeft: 20, flexWrap: "wrap" }}>
          <div>
            <span>Holdings: </span>
            <span style={{ color: "var(--text)", fontWeight: 600, fontFamily: "monospace" }}>
              {fmtCrypto(account.native_balance)} {currency}
            </span>
          </div>
          <div>
            <span>Price: </span>
            <span style={{ color: "var(--text)", fontWeight: 600 }}>{fmtPrice(account.unit_price)}/{currency}</span>
          </div>
          <div>
            <span>Value: </span>
            <span style={{ color: "var(--accent)", fontWeight: 600 }}>{fmtPrice(account.current)}</span>
          </div>
        </div>
      )}
    </div>
  );
}
