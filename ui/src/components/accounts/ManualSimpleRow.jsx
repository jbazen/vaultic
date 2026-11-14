import { useState } from "react";
import { renameManualEntry } from "../../api.js";
import { fmt, fmtDate } from "../../utils/format.js";
import EditableNotes from "../EditableNotes.jsx";
import AccountNumberMeta from "../AccountNumberMeta.jsx";

// ── Manual Simple Row ──────────────────────────────────────────────────────────
// HSA, property, vehicles, and liabilities. Editable name (✎) and description.
export default function ManualSimpleRow({ entry, badge, badgeClass, negative, onDelete, onRenamed }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.name);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameManualEntry(entry.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  return (
    <div className="account-row">
      <div className="account-info">
        {editing ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input className="form-input" style={{ width: 220, padding: "5px 8px", fontSize: 14 }}
              value={draft} onChange={e => setDraft(e.target.value)} autoFocus
              onKeyDown={e => { if (e.key === "Enter") handleSave(); if (e.key === "Escape") { setEditing(false); setDraft(entry.name); }}} />
            <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={handleSave} disabled={saving}>{saving ? "…" : "Save"}</button>
            <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => { setEditing(false); setDraft(entry.name); }}>Cancel</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div className="account-name">{entry.name}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }} aria-label="Rename">✎</button>
          </div>
        )}
        <div className="account-meta" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0 8px" }}>
          <span className={`badge ${badgeClass}`} style={{ fontSize: 11 }}>{badge}</span>
          <AccountNumberMeta accountNumber={entry.account_number} />
          <EditableNotes
            notes={entry.notes}
            onSave={async v => { await renameManualEntry(entry.id, entry.name, v); onRenamed(); }}
            inline
          />
          {entry.entered_at && (
            <span style={{ color: "var(--text2)", opacity: 0.6 }}>· {fmtDate(entry.entered_at)}</span>
          )}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <div className={`account-balance ${negative ? "liability" : ""}`}>
          {negative ? `-${fmt(entry.value)}` : fmt(entry.value)}
        </div>
        <button onClick={onDelete}
          style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 14 }} title="Delete" aria-label="Delete">✕</button>
      </div>
    </div>
  );
}
