import { useState, useEffect } from "react";
import { getAccounts, getPlaidItems, removePlaidItem, renameAccount, syncCoinbase, getManualEntries } from "../api.js";
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

const ASSET_CLASS_COLORS = {
  equities: "#4f8ef7", fixed_income: "#a78bfa",
  cash: "#34d399", alternatives: "#fbbf24", other: "#8b92a8",
};
const ASSET_CLASS_LABELS = {
  equities: "Equities", fixed_income: "Fixed Income",
  cash: "Cash", alternatives: "Alternatives", other: "Other",
};

function fmtPct(v) {
  if (v == null) return "—";
  return `${Number(v).toFixed(2)}%`;
}
function fmtNum(v, decimals = 4) {
  if (v == null) return "—";
  return Number(v).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: decimals });
}

function AllocationBar({ allocation, total }) {
  if (!total) return null;
  const entries = Object.entries(allocation).sort((a, b) => b[1] - a[1]);
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", marginBottom: 8 }}>
        {entries.map(([cls, val]) => (
          <div key={cls} style={{
            width: `${(val / total * 100).toFixed(1)}%`,
            background: ASSET_CLASS_COLORS[cls] || "#8b92a8",
          }} />
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px" }}>
        {entries.map(([cls, val]) => (
          <div key={cls} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: ASSET_CLASS_COLORS[cls] || "#8b92a8" }} />
            <span style={{ color: "var(--text2)" }}>{ASSET_CLASS_LABELS[cls] || cls}</span>
            <span style={{ fontWeight: 600, color: "var(--text)" }}>{(val / total * 100).toFixed(1)}%</span>
            <span style={{ color: "var(--text2)" }}>{fmt(val)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ManualInvestmentCard({ entry, onDelete }) {
  const [expanded, setExpanded] = useState(false);
  const holdings = entry.holdings || [];
  const holdingsTotal = holdings.reduce((s, h) => s + (h.value || 0), 0);
  const allocation = holdings.reduce((acc, h) => {
    const cls = h.asset_class || "other";
    acc[cls] = (acc[cls] || 0) + (h.value || 0);
    return acc;
  }, {});
  const hasHoldings = holdings.length > 0;

  return (
    <div style={{ borderBottom: "1px solid var(--border)", paddingBottom: 12, marginBottom: 12 }}>
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontWeight: 600, fontSize: 15, color: "var(--text)" }}>{entry.name}</span>
            <span className="badge badge-investment" style={{ fontSize: 11 }}>invested</span>
          </div>
          {entry.notes && (
            <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 2 }}>{entry.notes}</div>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ fontWeight: 700, fontSize: 16, color: "var(--text)" }}>{fmt(entry.value)}</div>
          <div style={{ display: "flex", gap: 6 }}>
            {(hasHoldings || entry.activity_summary) && (
              <button
                onClick={() => setExpanded(e => !e)}
                style={{
                  background: "none", border: "1px solid var(--border)",
                  color: "var(--text2)", borderRadius: 6, padding: "3px 10px",
                  cursor: "pointer", fontSize: 12,
                }}
              >
                {expanded ? "▲ Hide" : "▼ Details"}
              </button>
            )}
            {onDelete && (
              <button
                onClick={() => onDelete(entry.id)}
                style={{
                  background: "none", border: "1px solid var(--border)",
                  color: "#f87171", borderRadius: 6, padding: "3px 8px",
                  cursor: "pointer", fontSize: 12,
                }}
                title="Delete entry"
              >✕</button>
            )}
          </div>
        </div>
      </div>

      {/* Activity Summary */}
      {entry.activity_summary && (
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          gap: 10, marginTop: 12, padding: "12px 14px",
          background: "var(--bg)", borderRadius: 8, border: "1px solid var(--border)",
        }}>
          {[
            { label: "Beginning Balance", value: entry.activity_summary.beginning_balance, date: entry.activity_summary.beginning_date },
            { label: "Additions / Withdrawals", value: entry.activity_summary.additions_withdrawals },
            { label: "Net Change", value: entry.activity_summary.net_change, signed: true },
            { label: "Ending Balance", value: entry.activity_summary.ending_balance, date: entry.activity_summary.ending_date },
          ].map(({ label, value, date, signed }) => value != null ? (
            <div key={label}>
              <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 2 }}>{label}{date ? ` (${date})` : ""}</div>
              <div style={{ fontWeight: 700, fontSize: 14, color: signed ? (value >= 0 ? "#34d399" : "#f87171") : "var(--text)" }}>
                {signed && value > 0 ? "+" : ""}{fmt(value)}
              </div>
            </div>
          ) : null)}
          {entry.activity_summary.twr_pct != null && (
            <div>
              <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 2 }}>Time-Weighted Return</div>
              <div style={{ fontWeight: 700, fontSize: 14, color: entry.activity_summary.twr_pct >= 0 ? "#34d399" : "#f87171" }}>
                {entry.activity_summary.twr_pct > 0 ? "+" : ""}{entry.activity_summary.twr_pct.toFixed(2)}%
              </div>
            </div>
          )}
        </div>
      )}

      {/* Expanded detail */}
      {expanded && (hasHoldings || entry.activity_summary) && (
        <div style={{ marginTop: 14 }}>
          {/* Asset allocation */}
          {holdingsTotal > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 8 }}>
                Asset Allocation
              </div>
              <AllocationBar allocation={allocation} total={holdingsTotal} />
            </div>
          )}

          {/* Holdings table */}
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 8 }}>
            Holdings
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  {["Security", "Class", "Ticker", "Shares", "Price", "Value ($)", "Pct. Assets (%)", "Principal ($)", "Gain/Loss ($)", "Gain/Loss (%)"].map(h => (
                    <th key={h} style={{ padding: "6px 10px 8px", textAlign: h === "Security" || h === "Class" ? "left" : "right", color: "var(--text2)", fontWeight: 600, whiteSpace: "nowrap", fontSize: 12 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {holdings.map((h, i) => {
                  const glPos = h.gain_loss_dollars > 0;
                  const glNeg = h.gain_loss_dollars < 0;
                  return (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ padding: "8px 10px", color: "var(--text)", maxWidth: 200, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={h.name}>{h.name}</td>
                      <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>
                        {h.asset_class && (
                          <span style={{ background: ASSET_CLASS_COLORS[h.asset_class] + "22", color: ASSET_CLASS_COLORS[h.asset_class], borderRadius: 4, padding: "2px 6px", fontSize: 11, fontWeight: 600 }}>
                            {ASSET_CLASS_LABELS[h.asset_class] || h.asset_class}
                          </span>
                        )}
                      </td>
                      <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace", color: "var(--text2)" }}>{h.ticker || "—"}</td>
                      <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{fmtNum(h.shares, 4)}</td>
                      <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{h.price != null ? fmt(h.price) : "—"}</td>
                      <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: "var(--text)" }}>{h.value != null ? fmt(h.value) : "—"}</td>
                      <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{fmtPct(h.pct_assets)}</td>
                      <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{h.principal != null ? fmt(h.principal) : "—"}</td>
                      <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: glPos ? "#34d399" : glNeg ? "#f87171" : "var(--text)" }}>
                        {h.gain_loss_dollars != null ? (glPos ? "+" : "") + fmt(h.gain_loss_dollars) : "—"}
                      </td>
                      <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: glPos ? "#34d399" : glNeg ? "#f87171" : "var(--text)" }}>
                        {h.gain_loss_pct != null ? (h.gain_loss_pct > 0 ? "+" : "") + fmtPct(h.gain_loss_pct) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr style={{ borderTop: "2px solid var(--border)" }}>
                  <td colSpan={5} style={{ padding: "8px 10px", fontWeight: 700, color: "var(--text)" }}>Total</td>
                  <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 700, color: "var(--text)" }}>{fmt(holdingsTotal || entry.value)}</td>
                  <td colSpan={4} />
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const [coinbaseStatus, setCoinbaseStatus] = useState(null);
  const [manualEntries, setManualEntries] = useState([]);

  async function load() {
    try {
      const [accts, its, manual] = await Promise.all([getAccounts(), getPlaidItems(), getManualEntries()]);
      setAccounts(accts);
      setItems(its);
      setManualEntries(manual);
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

      {/* ── Manual Investment Accounts (PDF imported) ── */}
      {manualEntries.filter(e => e.category === "invested").length > 0 && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 16 }}>
            Investment Accounts (PDF Imported)
          </div>
          {manualEntries.filter(e => e.category === "invested").map(entry => (
            <ManualInvestmentCard key={entry.id} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}
