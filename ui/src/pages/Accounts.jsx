import { useState, useEffect } from "react";
import { getAccounts, getPlaidItems, removePlaidItem, renameAccount, syncCoinbase } from "../api.js";
import PlaidLink from "../components/PlaidLink.jsx";

function fmt(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(v));
}

function typeBadge(type) {
  return <span className={`badge badge-${type}`}>{type}</span>;
}

function isLiability(type) {
  return type === "credit" || type === "loan";
}

function fmtCrypto(v, decimals = 8) {
  if (v == null) return "—";
  // Trim trailing zeros but keep at least 4 decimal places
  const s = Number(v).toFixed(decimals);
  return s.replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
}

function fmtPrice(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(v);
}

function CryptoAccountRow({ account, onRenamed }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(account.display_name || account.name);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameAccount(account.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  const label = account.display_name || account.name;
  const currency = (account.subtype || "").toUpperCase();

  return (
    <div className="account-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
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
              <div className="account-name">{label}</div>
              <button onClick={() => setEditing(true)} title="Rename"
                style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }}>✎</button>
            </div>
          )}
          <div className="account-meta">{typeBadge(account.type)}</div>
        </div>
        <div className="account-balance">{fmt(account.current)}</div>
      </div>
      {/* Full crypto detail row */}
      <div style={{ display: "flex", gap: 24, fontSize: 13, color: "var(--text2)", paddingLeft: 4, flexWrap: "wrap" }}>
        <div>
          <span style={{ color: "var(--text2)" }}>Holdings: </span>
          <span style={{ color: "var(--text)", fontWeight: 600, fontFamily: "monospace" }}>
            {fmtCrypto(account.native_balance)} {currency}
          </span>
        </div>
        <div>
          <span style={{ color: "var(--text2)" }}>Price: </span>
          <span style={{ color: "var(--text)", fontWeight: 600 }}>{fmtPrice(account.unit_price)}/{currency}</span>
        </div>
        <div>
          <span style={{ color: "var(--text2)" }}>Value: </span>
          <span style={{ color: "var(--accent)", fontWeight: 600 }}>{fmtPrice(account.current)}</span>
        </div>
        {account.snapped_at && (
          <div>
            <span style={{ color: "var(--text2)" }}>Updated: </span>
            <span>{new Date(account.snapped_at).toLocaleDateString()}</span>
          </div>
        )}
        {account.coinbase_uuid && (
          <div>
            <span style={{ color: "var(--text2)" }}>UUID: </span>
            <span style={{ fontFamily: "monospace", fontSize: 11 }}>{account.coinbase_uuid}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function AccountRow({ account, onRenamed }) {
  if (account.type === "crypto") return <CryptoAccountRow account={account} onRenamed={onRenamed} />;

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
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const [coinbaseStatus, setCoinbaseStatus] = useState(null);

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

  async function handleCoinbaseSync() {
    setCoinbaseSyncing(true);
    setCoinbaseStatus(null);
    try {
      const result = await syncCoinbase();
      if (result.skipped) {
        setCoinbaseStatus({ ok: false, msg: "Coinbase API keys not configured in .env" });
      } else {
        setCoinbaseStatus({ ok: true, msg: `Synced ${result.synced} holdings` });
        await load();
      }
    } catch (err) {
      setCoinbaseStatus({ ok: false, msg: err.message });
    } finally {
      setCoinbaseSyncing(false);
    }
  }

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
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
            <button
              className="btn btn-secondary"
              onClick={handleCoinbaseSync}
              disabled={coinbaseSyncing}
              style={{ fontSize: 13 }}
            >
              {coinbaseSyncing ? "Syncing…" : "⟳ Sync Coinbase"}
            </button>
            {coinbaseStatus && (
              <span style={{ fontSize: 12, color: coinbaseStatus.ok ? "var(--accent)" : "#f87171" }}>
                {coinbaseStatus.msg}
              </span>
            )}
          </div>
          <PlaidLink onSuccess={load} />
        </div>
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
