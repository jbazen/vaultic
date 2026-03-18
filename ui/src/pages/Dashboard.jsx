import { useState, useEffect } from "react";
import {
  getNetWorthLatest, getNetWorthHistory, triggerSync,
  getAccounts, getManualEntries, getRecentTransactions,
  getPlaidItems, removePlaidItem, renameAccount, renameManualEntry, syncCoinbase,
} from "../api.js";
import NetWorthChart from "../components/NetWorthChart.jsx";
import PlaidLink from "../components/PlaidLink.jsx";

// ── Formatters ────────────────────────────────────────────────────────────────

function fmt(v, opts = {}) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2, ...opts
  }).format(Math.abs(v));
}

function fmtSigned(v) {
  if (v == null) return "—";
  const n = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(v));
  return v < 0 ? `-${n}` : n;
}

function fmtDate(s) {
  if (!s) return "";
  // Handles both "YYYY-MM-DD" and full datetime strings like "2026-03-18 07:46:43"
  const dateOnly = s.length > 10 ? s.substring(0, 10) : s;
  return new Date(dateOnly + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// ── Category config ───────────────────────────────────────────────────────────

const CATS = [
  { key: "liquid",       label: "Liquid",       color: "#34d399", icon: "💵" },
  { key: "invested",     label: "Invested",     color: "#4f8ef7", icon: "📈" },
  { key: "real_estate",  label: "Real Estate",  color: "#a78bfa", icon: "🏠" },
  { key: "vehicles",     label: "Vehicles",     color: "#fbbf24", icon: "🚗" },
  { key: "crypto",       label: "Crypto",       color: "#fb923c", icon: "₿" },
  { key: "other_assets", label: "Other Assets", color: "#6ee7b7", icon: "📦" },
  { key: "liabilities",  label: "Liabilities",  color: "#f87171", icon: "💳" },
];

const MANUAL_CAT_LABELS = {
  home_value:        { label: "Home Value",    cat: "real_estate", color: "#a78bfa" },
  car_value:         { label: "Car Value",     cat: "vehicles",    color: "#fbbf24" },
  credit_score:      { label: "Credit Score",  cat: null,          color: "#34d399" },
  other_asset:       { label: "Other Asset",   cat: "other_asset", color: "#6ee7b7" },
  other_liability:   { label: "Liability",     cat: "liabilities", color: "#f87171" },
  invested:          { label: "Invested",      cat: "invested",    color: "#4f8ef7" },
  liquid:            { label: "Liquid",        cat: "liquid",      color: "#34d399" },
};

// ── Sub-components ────────────────────────────────────────────────────────────

function fmtPrice(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(v);
}

function fmtCrypto(v, decimals = 8) {
  if (v == null) return "—";
  return Number(v).toFixed(decimals).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
}

function StatCard({ label, value, color, icon, negative }) {
  return (
    <div className="category-card">
      <div className="label">{icon} {label}</div>
      <div className="value" style={{ color, fontSize: 18 }}>
        {negative ? `-${fmt(value)}` : fmt(value)}
      </div>
    </div>
  );
}

const ASSET_CLASS_COLORS = {
  equities:     "#4f8ef7",
  fixed_income: "#a78bfa",
  cash:         "#34d399",
  alternatives: "#fbbf24",
  other:        "#8b92a8",
};
const ASSET_CLASS_LABELS = {
  equities: "Equities", fixed_income: "Fixed Income",
  cash: "Cash", alternatives: "Alternatives", other: "Other",
};

function AllocationBar({ allocation, total }) {
  if (!total) return null;
  const entries = Object.entries(allocation).sort((a, b) => b[1] - a[1]);
  return (
    <div>
      <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", marginBottom: 8 }}>
        {entries.map(([cls, val]) => (
          <div key={cls} style={{
            width: `${(val / total * 100).toFixed(1)}%`,
            background: ASSET_CLASS_COLORS[cls] || "#8b92a8",
            transition: "width 0.3s",
          }} />
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
        {entries.map(([cls, val]) => (
          <div key={cls} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: ASSET_CLASS_COLORS[cls] || "#8b92a8" }} />
            <span style={{ color: "var(--text2)" }}>{ASSET_CLASS_LABELS[cls] || cls}</span>
            <span style={{ color: "var(--text)", fontWeight: 600 }}>{(val / total * 100).toFixed(1)}%</span>
            <span style={{ color: "var(--text2)" }}>{fmt(val)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CryptoAccountRow({ account, onRenamed }) {
  const [editing, setEditing] = useState(false);
  const ticker = (account.subtype || "").toUpperCase();
  // Default display: just the ticker ("AVAX"). If user has renamed, show their name.
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
            <button className="btn btn-secondary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setEditing(false)}>✕</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div className="account-name">{label}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }}>✎</button>
          </div>
        )}
        <div className="account-meta">
          <span className="badge badge-crypto">crypto</span>
          {ticker && <span style={{ marginLeft: 6, color: "var(--text2)" }}>{ticker.toLowerCase()}</span>}
        </div>
      </div>
      <div className="account-balance">{fmt(account.current)}</div>
    </div>
  );
}

// Editable row for PDF-imported manual entries — same layout as Plaid account rows.
// badge/badgeClass control the colored type pill; negative=true renders value in red.
function ManualAccountRow({ entry, onRenamed, badge = "invested", badgeClass = "badge-investment", negative = false }) {
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
            <button className="btn btn-secondary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setEditing(false)}>✕</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div className="account-name">{entry.name}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }}>✎</button>
          </div>
        )}
        <div className="account-meta">
          <span className={`badge ${badgeClass}`}>{badge}</span>
          {entry.notes && <span style={{ marginLeft: 6, color: "var(--text2)", fontSize: 12 }}>{entry.notes}</span>}
        </div>
        {entry.entered_at && (
          <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 2 }}>
            Imported {fmtDate(entry.entered_at)}
          </div>
        )}
      </div>
      <div className={`account-balance ${negative ? "liability" : ""}`}>
        {negative ? `-${fmt(entry.value)}` : fmt(entry.value)}
      </div>
    </div>
  );
}

function AccountRow({ account, onRenamed }) {
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
              onClick={() => setEditing(false)}>✕</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div className="account-name">{label}{mask}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }}>✎</button>
          </div>
        )}
        <div className="account-meta">
          <span className={`badge badge-${account.type}`}>{account.type}</span>
          {account.subtype && <span style={{ marginLeft: 6, color: "var(--text2)" }}>{account.subtype}</span>}
        </div>
      </div>
      <div className={`account-balance ${isLiab ? "liability" : ""}`}>
        {isLiab ? `-${fmt(account.current)}` : fmt(account.current)}
      </div>
    </div>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [nw, setNw] = useState(null);
  const [history, setHistory] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [manualEntries, setManualEntries] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const [plaidItems, setPlaidItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);

  async function load() {
    try {
      const [nwData, hist, accts, manual, txns, items] = await Promise.all([
        getNetWorthLatest(),
        getNetWorthHistory(1825),
        getAccounts(),
        getManualEntries(),
        getRecentTransactions(20),
        getPlaidItems(),
      ]);
      setNw(nwData);
      setHistory(hist);
      setAccounts(accts);
      setManualEntries(manual);
      setTransactions(txns);
      setPlaidItems(items);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleSync() {
    setSyncing(true);
    try { await triggerSync(); await load(); }
    finally { setSyncing(false); }
  }

  async function handleCoinbaseSync() {
    setCoinbaseSyncing(true);
    try { await syncCoinbase(); await load(); }
    finally { setCoinbaseSyncing(false); }
  }

  async function handleRemoveItem(itemId) {
    if (!confirm("Disconnect this institution? Account history will be preserved.")) return;
    await removePlaidItem(itemId);
    await load();
  }

  // Group accounts by institution
  const grouped = accounts.reduce((acc, a) => {
    const k = a.institution_name || "Other";
    if (!acc[k]) acc[k] = [];
    acc[k].push(a);
    return acc;
  }, {});

  // Latest manual entry per category
  const latestManual = manualEntries.reduce((acc, e) => {
    if (!acc[e.category] || e.entered_at > acc[e.category].entered_at) acc[e.category] = e;
    return acc;
  }, {});

  const creditScore = latestManual["credit_score"];
  const homeValue = latestManual["home_value"];
  const carValue = latestManual["car_value"];
  const otherAssets = manualEntries.filter(e => e.category === "other_asset");
  const liabilities = manualEntries.filter(e => e.category === "other_liability");
  const manualInvested = manualEntries.filter(e => e.category === "invested");
  const manualLiquid = manualEntries.filter(e => e.category === "liquid");

  // Consolidated allocation across all manual investment holdings
  const allHoldings = manualInvested.flatMap(e => e.holdings || []);
  const allHoldingsTotal = allHoldings.reduce((s, h) => s + (h.value || 0), 0);
  const allocationByClass = allHoldings.reduce((acc, h) => {
    const cls = h.asset_class || "other";
    acc[cls] = (acc[cls] || 0) + (h.value || 0);
    return acc;
  }, {});

  const total = nw?.total ?? null;

  if (loading) return <div style={{ color: "var(--text2)", padding: 40, textAlign: "center" }}>Loading…</div>;

  return (
    <div>
      {/* ── Page header ── */}
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2>Dashboard</h2>
          <p>Your complete financial picture</p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <PlaidLink onSuccess={load} />
          <button className="btn btn-secondary" onClick={handleCoinbaseSync} disabled={coinbaseSyncing}>
            {coinbaseSyncing ? "Syncing…" : "⟳ Coinbase"}
          </button>
          <button className="btn btn-secondary" onClick={handleSync} disabled={syncing}>
            {syncing ? "Syncing…" : "↻ Sync All"}
          </button>
        </div>
      </div>

      {/* ── Net Worth Hero ── */}
      <div className="card">
        <div style={{ display: "flex", gap: 40, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 4 }}>
          <div>
            <div className="card-title" style={{ marginBottom: 4 }}>Net Worth</div>
            <div className={`nw-total ${total > 0 ? "positive" : ""}`} style={{ marginBottom: 0 }}>
              {total != null ? fmtSigned(total) : "—"}
            </div>
          </div>
          {nw?.investable != null && (
            <div style={{ paddingBottom: 4 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 4 }}>
                Investable Assets
              </div>
              <div style={{ fontSize: 28, fontWeight: 700, color: "var(--accent)" }}>
                {fmt(nw.investable)}
              </div>
              <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 2 }}>
                excl. real estate &amp; vehicles
              </div>
            </div>
          )}
        </div>
        {nw?.snapped_at && (
          <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 6 }}>
            Last updated {fmtDate(nw.snapped_at)}
          </div>
        )}
        <div className="category-grid">
          {CATS.map(({ key, label, color, icon }) => {
            const val = nw?.[key] ?? 0;
            if (!val) return null;
            return (
              <StatCard key={key} label={label} value={val} color={color} icon={icon}
                negative={key === "liabilities"} />
            );
          })}
        </div>
      </div>

      {/* ── Net Worth Chart ── */}
      {history.length > 0 && (
        <div className="card">
          <div className="card-title">Net Worth History</div>
          <NetWorthChart data={history} />
        </div>
      )}

      {/* ── Credit Score + Key Assets ── */}
      {(creditScore || homeValue || carValue) && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12, marginBottom: 20 }}>
          {creditScore && (
            <div className="card" style={{ margin: 0, textAlign: "center" }}>
              <div className="card-title">Credit Score</div>
              <div style={{ fontSize: 48, fontWeight: 800, color: creditScore.value >= 750 ? "var(--green)" : creditScore.value >= 670 ? "var(--yellow)" : "var(--red)" }}>
                {creditScore.value}
              </div>
              <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 4 }}>
                {creditScore.value >= 800 ? "Exceptional" : creditScore.value >= 750 ? "Very Good" : creditScore.value >= 700 ? "Good" : creditScore.value >= 670 ? "Fair" : "Needs Work"}
              </div>
            </div>
          )}
          {homeValue && (
            <div className="card" style={{ margin: 0 }}>
              <div className="card-title">🏠 Home Value</div>
              <div style={{ fontSize: 28, fontWeight: 700, color: "#a78bfa" }}>{fmt(homeValue.value)}</div>
              {homeValue.notes && <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 4 }}>{homeValue.notes}</div>}
            </div>
          )}
          {carValue && (
            <div className="card" style={{ margin: 0 }}>
              <div className="card-title">🚗 Car Value</div>
              <div style={{ fontSize: 28, fontWeight: 700, color: "#fbbf24" }}>{fmt(carValue.value)}</div>
              {carValue.notes && <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 4 }}>{carValue.notes}</div>}
            </div>
          )}
        </div>
      )}

      {/* ── Connected Accounts ── */}
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 16 }}>Connected Accounts</div>
        </div>
        {accounts.length === 0 ? (
          <div style={{ textAlign: "center", padding: "24px 0", color: "var(--text2)" }}>
            No accounts connected. Click <strong>+ Connect Account</strong> above.
          </div>
        ) : (
          Object.entries(grouped).map(([institution, accts]) => {
            const item = plaidItems.find(i => i.institution_name === institution);
            return (
              <div key={institution} style={{ marginBottom: 20 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                  <div>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>{institution}</span>
                    {item?.last_synced_at && (
                      <span style={{ fontSize: 11, color: "var(--text2)", marginLeft: 10 }}>
                        synced {fmtDate(item.last_synced_at)}
                      </span>
                    )}
                  </div>
                  {item && (
                    <button className="btn btn-danger" style={{ fontSize: 11, padding: "3px 10px" }}
                      onClick={() => handleRemoveItem(item.item_id)}>
                      Disconnect
                    </button>
                  )}
                </div>
                <div className="account-list">
                  {accts.map(a => <AccountRow key={a.id} account={a} onRenamed={load} />)}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* ── Manual Investment Accounts (PDF imported) ── */}
      {manualInvested.length > 0 && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 14 }}>
            Investment Accounts (Imported)
          </div>
          {allHoldingsTotal > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 8 }}>
                Portfolio Allocation
              </div>
              <AllocationBar allocation={allocationByClass} total={allHoldingsTotal} />
            </div>
          )}
          <div className="account-list">
            {manualInvested.map(e => (
              <ManualAccountRow key={e.id} entry={e} onRenamed={load} />
            ))}
          </div>
        </div>
      )}

      {/* ── HSA / Liquid (PDF imported) ── */}
      {manualLiquid.length > 0 && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 14 }}>HSA / Cash Accounts (Imported)</div>
          <div className="account-list">
            {manualLiquid.map(e => (
              <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                badge="liquid" badgeClass="badge-depository" />
            ))}
          </div>
        </div>
      )}

      {/* ── Other Assets & Liabilities ── */}
      {(otherAssets.length > 0 || liabilities.length > 0) && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 14 }}>Other Assets & Liabilities</div>
          <div className="account-list">
            {otherAssets.map(e => (
              <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                badge="asset" badgeClass="badge-depository" />
            ))}
            {liabilities.map(e => (
              <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                badge="liability" badgeClass="badge-credit" negative />
            ))}
          </div>
        </div>
      )}

      {/* ── Recent Transactions ── */}
      {transactions.length > 0 && (
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
            <div style={{ fontWeight: 700, fontSize: 16 }}>Recent Transactions</div>
            <a href="/transactions" style={{ fontSize: 13, color: "var(--accent)", textDecoration: "none" }}>
              View all →
            </a>
          </div>
          {transactions.map((t, i) => (
            <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "9px 0", borderBottom: i < transactions.length - 1 ? "1px solid var(--border)" : "none" }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, color: "var(--text)", fontWeight: 500 }}>
                  {t.merchant_name || t.name}
                </div>
                <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 2 }}>
                  {t.account_name} · {fmtDate(t.date)}
                  {t.pending ? " · pending" : ""}
                </div>
              </div>
              <div style={{ fontSize: 14, fontWeight: 600,
                color: t.amount < 0 ? "var(--green)" : "var(--text)" }}>
                {t.amount < 0 ? "+" : "-"}{fmt(Math.abs(t.amount))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
