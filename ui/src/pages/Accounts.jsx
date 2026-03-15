import { useState, useEffect } from "react";
import { getAccounts, getPlaidItems, removePlaidItem, renameAccount } from "../api.js";
import PlaidLink from "../components/PlaidLink.jsx";

function fmt(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Math.abs(v));
}

function typeBadge(type) {
  return <span className={`badge badge-${type}`}>{type}</span>;
}

function isLiability(type) {
  return type === "credit" || type === "loan";
}

function AccountRow({ account, onRenamed }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(account.display_name || account.name);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    try {
      await renameAccount(account.id, draft.trim());
      onRenamed();
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") { setEditing(false); setDraft(account.display_name || account.name); }
  }

  // Always show "LABEL (...MASK)" — user edits label, mask is locked
  const mask = account.mask ? ` (...${account.mask})` : "";
  const label = account.display_name || account.name;

  return (
    <div className="account-row">
      <div className="account-info">
        {editing ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              className="form-input"
              style={{ width: 200, padding: "5px 8px", fontSize: 14 }}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              autoFocus
            />
            <span style={{ color: "var(--text2)", fontSize: 13 }}>{mask}</span>
            <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={handleSave} disabled={saving}>
              {saving ? "…" : "Save"}
            </button>
            <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => { setEditing(false); setDraft(label); }}>
              Cancel
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div className="account-name">{label}{mask}</div>
            <button
              onClick={() => setEditing(true)}
              title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }}
            >
              ✎
            </button>
          </div>
        )}
        <div className="account-meta">
          {typeBadge(account.type)}
          {account.subtype && <span style={{ marginLeft: 6, color: "var(--text2)" }}>{account.subtype}</span>}
        </div>
      </div>
      <div className={`account-balance ${isLiability(account.type) ? "liability" : ""}`}>
        {isLiability(account.type) ? `-${fmt(account.current)}` : fmt(account.current)}
      </div>
    </div>
  );
}

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const [accts, its] = await Promise.all([getAccounts(), getPlaidItems()]);
      setAccounts(accts);
      setItems(its);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleRemove(itemId) {
    if (!confirm("Disconnect this institution? Account history will be preserved.")) return;
    await removePlaidItem(itemId);
    await load();
  }

  const grouped = accounts.reduce((acc, a) => {
    const key = a.institution_name || "Manual";
    if (!acc[key]) acc[key] = [];
    acc[key].push(a);
    return acc;
  }, {});

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2>Accounts</h2>
          <p>All connected institutions and balances</p>
        </div>
        <PlaidLink onSuccess={load} />
      </div>

      {loading ? (
        <div style={{ color: "var(--text2)" }}>Loading…</div>
      ) : accounts.length === 0 ? (
        <div className="card empty-state">
          <p>No accounts connected yet.</p>
          <p style={{ fontSize: "13px" }}>Click "Connect Account" to link your first institution via Plaid.</p>
        </div>
      ) : (
        Object.entries(grouped).map(([institution, accts]) => {
          const item = items.find(i => i.institution_name === institution);
          return (
            <div className="card" key={institution}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 16 }}>{institution}</div>
                  {item?.last_synced_at && (
                    <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 2 }}>
                      Synced {new Date(item.last_synced_at).toLocaleDateString()}
                    </div>
                  )}
                </div>
                {item && (
                  <button
                    className="btn btn-danger"
                    style={{ fontSize: 12, padding: "5px 12px" }}
                    onClick={() => handleRemove(item.item_id)}
                  >
                    Disconnect
                  </button>
                )}
              </div>
              <div className="account-list">
                {accts.map(a => (
                  <AccountRow key={a.id} account={a} onRenamed={load} />
                ))}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
