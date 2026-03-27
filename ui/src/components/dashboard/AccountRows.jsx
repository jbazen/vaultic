import { useState } from "react";
import { renameAccount, renameManualEntry, updateAccountNotes } from "../../api.js";
import EditableNotes from "../EditableNotes.jsx";
import { isRetirementAccount } from "../../utils/accounts.js";
import { fmt, fmtDate } from "../../utils/format.js";

// ── Stat card (used in the net worth category grid) ──────────────────────────

export function StatCard({ label, value, color, icon, negative }) {
  return (
    <div className="category-card">
      <div className="label">{icon} {label}</div>
      <div className="value" style={{ color, fontSize: 18 }}>
        {negative ? `-${fmt(value)}` : fmt(value)}
      </div>
    </div>
  );
}

// ── Crypto account row ───────────────────────────────────────────────────────

export function CryptoAccountRow({ account, onRenamed }) {
  const [editing, setEditing] = useState(false);
  const ticker = (account.subtype || "").toUpperCase();
  const [draft, setDraft] = useState(account.display_name || ticker || account.name);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameAccount(account.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  const label = account.display_name || ticker || account.name;

  return (
    <div className="account-row">
      <div className="account-info">
        {editing ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input className="form-input" style={{ width: 180, padding: "4px 8px", fontSize: 13 }}
              value={draft} onChange={e => setDraft(e.target.value)} autoFocus
              onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} />
            <button className="btn btn-primary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={save} disabled={saving}>{saving ? "…" : "Save"}</button>
            <button className="btn btn-secondary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setEditing(false)} aria-label="Cancel editing">✕</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div className="account-name">{label}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }} aria-label="Rename">✎</button>
          </div>
        )}
        <div className="account-meta">
          <span className="badge badge-crypto">crypto</span>
          {ticker && <span style={{ marginLeft: 6, color: "var(--text2)" }}>{ticker.toLowerCase()}</span>}
          <span style={{ marginLeft: 6 }}>
            <EditableNotes notes={account.notes} onSave={async (v) => { await updateAccountNotes(account.id, v); onRenamed(); }} />
          </span>
        </div>
      </div>
      <div className="account-balance">{fmt(account.current)}</div>
    </div>
  );
}

// ── Manual account row (PDF-imported entries) ────────────────────────────────
// badge/badgeClass default to "invested" but auto-upgrade to "retirement" for entries
// whose names contain 401k/IRA/Roth/pension keywords. negative=true renders value in red.

export function ManualAccountRow({ entry, onRenamed, badge, badgeClass, negative = false }) {
  // Auto-detect retirement accounts unless caller explicitly provided a badge
  const isRetirement = badge === undefined && entry.category === "invested" && isRetirementAccount(null, entry.name);
  badge     = badge     ?? (isRetirement ? "retirement" : "invested");
  badgeClass = badgeClass ?? (isRetirement ? "badge-retirement" : "badge-investment");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.name);
  const [saving, setSaving] = useState(false);

  async function save() {
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
            <input className="form-input" style={{ width: 240, padding: "4px 8px", fontSize: 13 }}
              value={draft} onChange={e => setDraft(e.target.value)} autoFocus
              onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} />
            <button className="btn btn-primary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={save} disabled={saving}>{saving ? "…" : "Save"}</button>
            <button className="btn btn-secondary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setEditing(false)} aria-label="Cancel editing">✕</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <div className="account-name">{entry.name}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }} aria-label="Rename">✎</button>
            {entry.entered_at && <span style={{ fontSize: 11, color: "var(--text2)" }}>· Imported {fmtDate(entry.entered_at)}</span>}
          </div>
        )}
        <div className="account-meta">
          <span className={`badge ${badgeClass}`}>{badge}</span>
          <span style={{ marginLeft: 6 }}>
            <EditableNotes notes={entry.notes} onSave={async (v) => { await renameManualEntry(entry.id, entry.name, v); onRenamed(); }} />
          </span>
        </div>
      </div>
      <div className={`account-balance ${negative ? "liability" : ""}`}>
        {negative ? `-${fmt(entry.value)}` : fmt(entry.value)}
      </div>
    </div>
  );
}

// ── Plaid account row ────────────────────────────────────────────────────────

export function AccountRow({ account, onRenamed }) {
  if (account.type === "crypto") return <CryptoAccountRow account={account} onRenamed={onRenamed} />;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(account.display_name || account.name);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameAccount(account.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  const mask = account.mask ? ` (...${account.mask})` : "";
  const label = account.display_name || account.name;
  const isLiab = account.type === "credit" || account.type === "loan";

  return (
    <div className="account-row">
      <div className="account-info">
        {editing ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input className="form-input" style={{ width: 180, padding: "4px 8px", fontSize: 13 }}
              value={draft} onChange={e => setDraft(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
              autoFocus />
            <span style={{ color: "var(--text2)", fontSize: 12 }}>{mask}</span>
            <button className="btn btn-primary" style={{ padding: "3px 10px", fontSize: 12 }}
              onClick={save} disabled={saving}>{saving ? "…" : "Save"}</button>
            <button className="btn btn-secondary" style={{ padding: "3px 10px", fontSize: 12 }}
              onClick={() => setEditing(false)} aria-label="Cancel editing">✕</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div className="account-name">{label}{mask}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }} aria-label="Rename">✎</button>
          </div>
        )}
        <div className="account-meta">
          {(() => {
            const retire = account.type === "investment" && isRetirementAccount(account.subtype, account.name);
            return <span className={`badge ${retire ? "badge-retirement" : `badge-${account.type}`}`}>{retire ? "retirement" : account.type}</span>;
          })()}
          {account.subtype && <span style={{ marginLeft: 6, color: "var(--text2)" }}>{account.subtype}</span>}
          <span style={{ marginLeft: 6 }}>
            <EditableNotes notes={account.notes} onSave={async (v) => { await updateAccountNotes(account.id, v); onRenamed(); }} />
          </span>
        </div>
      </div>
      <div className={`account-balance ${isLiab ? "liability" : ""}`}>
        {isLiab ? `-${fmt(account.current)}` : fmt(account.current)}
      </div>
    </div>
  );
}
