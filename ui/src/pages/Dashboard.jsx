import { useState, useEffect } from "react";
import {
  getNetWorthLatest, getNetWorthHistory, triggerSync,
  getAccounts, getManualEntries, getRecentTransactions,
  getPlaidItems, removePlaidItem, renameAccount, renameManualEntry,
  updateAccountNotes, syncCoinbase, getPortfolioPerformance,
  getMarketRates,
} from "../api.js";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, AreaChart, Area, XAxis, YAxis } from "recharts";
import NetWorthChart from "../components/NetWorthChart.jsx";
import PlaidLink from "../components/PlaidLink.jsx";
import EditableNotes from "../components/EditableNotes.jsx";
import AllocationBar, { ASSET_CLASS_COLORS, ASSET_CLASS_LABELS } from "../components/AllocationBar.jsx";
import { isRetirementAccount } from "../utils/accounts.js";
import { fmt, fmtSigned, fmtDate, fmtPrice, fmtCrypto } from "../utils/format.js";

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

// ── Asset Allocation Pie Chart ─────────────────────────────────────────────────

const PIE_CATS = {
  checking:          { label: "Checking",          color: "#34d399" },
  savings:           { label: "Savings",           color: "#4f8ef7" },
  retirement:        { label: "Retirement",        color: "#a78bfa" },
  other_investments: { label: "Other Investments", color: "#22d3ee" },
  crypto:            { label: "Crypto",            color: "#fb923c" },
  liquid_cash:       { label: "Liquid Cash",       color: "#86efac" },
  real_estate:       { label: "Real Estate",       color: "#f59e0b" },
  other_assets:      { label: "Other Assets",      color: "#f87171" },
};

// Compute dollar totals per category from live account + manual entry data.
// Liabilities (credit/loan) are excluded — this chart shows where assets are, not net worth.
function computeAllocation(accounts, manualEntries) {
  const out = Object.fromEntries(Object.keys(PIE_CATS).map(k => [k, 0]));

  for (const a of accounts) {
    const val = a.current || 0;
    if (val <= 0) continue; // skip credit cards and negative balances
    if (a.type === "crypto") {
      out.crypto += val;
    } else if (a.type === "depository") {
      if (a.subtype === "checking") out.checking += val;
      else out.savings += val; // savings, money market, cd, hsa (Plaid-connected)
    } else if (a.type === "investment") {
      if (isRetirementAccount(a.subtype, a.name)) out.retirement += val;
      else out.other_investments += val;
    }
  }

  for (const e of manualEntries) {
    if (e.exclude_from_net_worth) continue;
    const val = e.value || 0;
    if (val <= 0) continue;
    if (e.category === "invested") {
      // Route retirement accounts (401k, IRA, Roth, etc.) separately from other investments
      if (isRetirementAccount(null, e.name)) out.retirement += val;
      else out.other_investments += val;
    } else if (e.category === "liquid")                                  out.liquid_cash += val;
    else if (e.category === "crypto")                                    out.crypto += val;
    else if (e.category === "home_value" || e.category === "real_estate") out.real_estate += val;
    else if (["car_value", "vehicles", "other_asset"].includes(e.category)) out.other_assets += val;
  }

  return out;
}

// Tooltip rendered by recharts on hover: shows category, dollar amount, % of view total.
function PieTooltipContent({ active, payload, total }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const pct = total > 0 ? ((d.value / total) * 100).toFixed(1) : "0.0";
  return (
    <div style={{
      background: "#171b26", border: "1px solid #2a2f3e",
      borderRadius: 8, padding: "10px 14px", pointerEvents: "none",
    }}>
      <div style={{ fontWeight: 700, color: d.color, marginBottom: 4 }}>{d.name}</div>
      <div style={{ color: "#e8eaf0", fontSize: 14, fontWeight: 600 }}>{fmt(d.value)}</div>
      <div style={{ color: "#8b92a8", fontSize: 12, marginTop: 2 }}>{pct}% of portfolio</div>
    </div>
  );
}

function AllocationPieChart({ accounts, manualEntries }) {
  const [view, setView] = useState("all");

  const alloc = computeAllocation(accounts, manualEntries);

  const ALL_KEYS        = Object.keys(PIE_CATS);
  const FINANCIAL_KEYS  = ["checking", "savings", "retirement", "other_investments", "crypto", "liquid_cash"];
  const activeKeys      = view === "all" ? ALL_KEYS : FINANCIAL_KEYS;

  const data = activeKeys
    .map(k => ({ key: k, name: PIE_CATS[k].label, value: alloc[k], color: PIE_CATS[k].color }))
    .filter(d => d.value > 0);

  const total = data.reduce((s, d) => s + d.value, 0);

  if (total === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, textAlign: "center", padding: "40px 0" }}>No asset data yet.</div>;
  }

  // Selector tab style (reused from other tab bars in the app)
  const tab = active => ({
    background: active ? "var(--accent)" : "var(--bg3)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "4px 12px",
    fontSize: 12, fontWeight: active ? 700 : 400,
    cursor: "pointer",
  });

  return (
    <div>
      {/* View selector */}
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <button style={tab(view === "all")} onClick={() => setView("all")}>All Assets</button>
        <button style={tab(view === "financial")} onClick={() => setView("financial")}>Financial Only</button>
      </div>

      {/* Pie + legend side by side */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: "0 0 200px" }}>
          <ResponsiveContainer width={200} height={200}>
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                cx="50%" cy="50%"
                outerRadius={90} innerRadius={50}
                paddingAngle={2}
                strokeWidth={0}
              >
                {data.map(d => <Cell key={d.key} fill={d.color} />)}
              </Pie>
              <Tooltip content={(props) => <PieTooltipContent {...props} total={total} />} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Legend */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 5, minWidth: 140 }}>
          {data.map(d => {
            const pct = ((d.value / total) * 100).toFixed(1);
            return (
              <div key={d.key} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}>
                <div style={{ width: 10, height: 10, borderRadius: 3, background: d.color, flexShrink: 0 }} />
                <span style={{ color: "var(--text2)", flex: 1 }}>{d.name}</span>
                <span style={{ color: "var(--text)", fontWeight: 600, fontFamily: "monospace" }}>{pct}%</span>
              </div>
            );
          })}
          <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid var(--border)", fontSize: 12 }}>
            <span style={{ color: "var(--text2)" }}>Total Assets: </span>
            <span style={{ fontWeight: 700, color: "var(--text)" }}>{fmt(total)}</span>
          </div>
        </div>
      </div>
    </div>
  );
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

function CryptoAccountRow({ account, onRenamed }) {
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
          <span style={{ marginLeft: 6 }}>
            <EditableNotes notes={account.notes} onSave={async (v) => { await updateAccountNotes(account.id, v); onRenamed(); }} />
          </span>
        </div>
      </div>
      <div className="account-balance">{fmt(account.current)}</div>
    </div>
  );
}

// Editable row for PDF-imported manual entries — same layout as Plaid account rows.
// badge/badgeClass default to "invested" but auto-upgrade to "retirement" for entries
// whose names contain 401k/IRA/Roth/pension keywords. negative=true renders value in red.
function ManualAccountRow({ entry, onRenamed, badge, badgeClass, negative = false }) {
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
            <button className="btn btn-secondary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setEditing(false)}>✕</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <div className="account-name">{entry.name}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }}>✎</button>
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

// ── Portfolio Performance Card ─────────────────────────────────────────────────
// Shows total Plaid investment+retirement account value over time.
// Only investment-type accounts (Vanguard, Voya, etc.) — not banking or crypto.
function PortfolioPerformanceCard({ data }) {
  const [days, setDays] = useState(365);

  // Filter to the selected period client-side (data is already fetched for full range)
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  const filtered = data.filter(d => new Date(d.snapped_at + "T12:00:00") >= cutoff);

  const periods = [
    { label: "90D",  days: 90 },
    { label: "1Y",   days: 365 },
    { label: "3Y",   days: 1095 },
    { label: "5Y",   days: 1825 },
  ];

  const tab = active => ({
    background: active ? "var(--accent)" : "var(--bg3)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "4px 12px",
    fontSize: 12, fontWeight: active ? 700 : 400,
    cursor: "pointer",
  });

  if (!filtered || filtered.length < 2) return null;

  const first = filtered[0]?.total_value ?? 0;
  const last  = filtered[filtered.length - 1]?.total_value ?? 0;
  const returnDollar = last - first;
  const returnPct    = first > 0 ? ((last - first) / first) * 100 : 0;
  const isPositive   = returnDollar >= 0;

  function fmtCompact(v) {
    if (v == null) return "—";
    const abs = Math.abs(v);
    if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
    return `$${v.toFixed(0)}`;
  }

  function fmtAxisDate(s) {
    if (!s) return "";
    const d = new Date(s + "T12:00:00");
    if (days <= 90) return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
  }

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    return (
      <div style={{ background: "#171b26", border: "1px solid #2a2f3e", borderRadius: 8, padding: "10px 14px" }}>
        <div style={{ color: "var(--text2)", fontSize: 11, marginBottom: 4 }}>{fmtAxisDate(label)}</div>
        <div style={{ color: "var(--text)", fontWeight: 700, fontSize: 14 }}>{fmt(payload[0].value)}</div>
      </div>
    );
  };

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <div>
          <div className="card-title" style={{ marginBottom: 4 }}>Portfolio Performance</div>
          <div style={{ fontSize: 11, color: "var(--text2)" }}>Investment &amp; retirement accounts · excl. banking &amp; crypto</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: isPositive ? "var(--green)" : "var(--red)" }}>
            {isPositive ? "+" : ""}{fmt(returnDollar)}
          </div>
          <div style={{ fontSize: 12, color: "var(--text2)" }}>
            {isPositive ? "+" : ""}{returnPct.toFixed(2)}% over period
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
        {periods.map(p => (
          <button key={p.days} style={tab(days === p.days)} onClick={() => setDays(p.days)}>{p.label}</button>
        ))}
      </div>

      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={filtered} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="portfolioGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#a78bfa" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#a78bfa" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="snapped_at"
            tickFormatter={fmtAxisDate}
            tick={{ fontSize: 10, fill: "var(--text2)" }}
            axisLine={false} tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={fmtCompact}
            tick={{ fontSize: 10, fill: "var(--text2)" }}
            axisLine={false} tickLine={false}
            width={60}
          />
          <Tooltip content={<CustomTooltip />} />
          <Area
            type="monotone"
            dataKey="total_value"
            stroke="#a78bfa"
            strokeWidth={2}
            fill="url(#portfolioGrad)"
          />
        </AreaChart>
      </ResponsiveContainer>
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
  const [portfolioPerf, setPortfolioPerf] = useState([]);
  const [marketRates, setMarketRates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);

  async function load() {
    try {
      const [nwData, hist, accts, manual, txns, items, perf, rates] = await Promise.all([
        getNetWorthLatest(),
        getNetWorthHistory(1825),
        getAccounts(),
        getManualEntries(),
        getRecentTransactions(20),
        getPlaidItems(),
        getPortfolioPerformance(1825),
        getMarketRates().catch(() => ({ rates: [] })),
      ]);
      setNw(nwData);
      setHistory(hist);
      setAccounts(accts);
      setManualEntries(manual);
      setTransactions(txns);
      setPlaidItems(items);
      setPortfolioPerf(perf);
      setMarketRates(rates?.rates ?? []);
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
  // Excluded entries (e.g. "Overall Portfolio") sort first, then alphabetical
  const sortManual = arr => [...arr].sort((a, b) =>
    (b.exclude_from_net_worth - a.exclude_from_net_worth) || a.name.localeCompare(b.name)
  );
  const manualInvested = sortManual(manualEntries.filter(e => e.category === "invested" && (e.account_number || e.exclude_from_net_worth)));
  const manualInvestedOther = manualEntries.filter(e => e.category === "invested" && !e.account_number && !e.exclude_from_net_worth);
  const manualLiquid = sortManual(manualEntries.filter(e => e.category === "liquid"));

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
                Investable Net Worth
              </div>
              <div style={{ fontSize: 28, fontWeight: 700, color: "var(--accent)" }}>
                {fmt(nw.investable)}
              </div>
              <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 2 }}>
                net worth excl. home &amp; car
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

        {/* Credit score, home value, car value + market rates — compact inline row */}
        {(creditScore || homeValue || carValue || marketRates.length > 0) && (
          <div style={{ display: "flex", gap: 28, flexWrap: "wrap", alignItems: "flex-start", paddingTop: 14, marginTop: 10, borderTop: "1px solid var(--border)" }}>
            {creditScore && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>Credit Score</div>
                <span style={{ fontSize: 22, fontWeight: 800, color: creditScore.value >= 750 ? "var(--green)" : creditScore.value >= 670 ? "var(--yellow)" : "var(--red)" }}>
                  {creditScore.value}
                </span>
                <span style={{ fontSize: 12, color: "var(--text2)", marginLeft: 6 }}>
                  {creditScore.value >= 800 ? "Exceptional" : creditScore.value >= 750 ? "Very Good" : creditScore.value >= 700 ? "Good" : creditScore.value >= 670 ? "Fair" : "Needs Work"}
                </span>
              </div>
            )}
            {homeValue && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>🏠 Home</div>
                <span style={{ fontSize: 22, fontWeight: 700, color: "#a78bfa" }}>{fmt(homeValue.value)}</span>
              </div>
            )}
            {carValue && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>🚗 Car</div>
                <span style={{ fontSize: 22, fontWeight: 700, color: "#fbbf24" }}>{fmt(carValue.value)}</span>
              </div>
            )}
            {/* Market rates — separated by a vertical rule when other items are present */}
            {marketRates.length > 0 && (
              <>
                {(creditScore || homeValue || carValue) && (
                  <div style={{ width: 1, background: "var(--border)", alignSelf: "stretch", margin: "0 4px" }} />
                )}
                {marketRates.map(r => (
                  <div key={r.label}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>{r.label}</div>
                    <span style={{ fontSize: 22, fontWeight: 700, color: "#22d3ee" }}>{r.value.toFixed(2)}%</span>
                  </div>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      {/* ── Net Worth Chart + Asset Allocation (side by side) ── */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "flex-start", marginBottom: 20 }}>
        {history.length > 0 && (
          <div className="card" style={{ flex: "2 1 400px", margin: 0 }}>
            <div className="card-title">Net Worth History</div>
            <NetWorthChart data={history} />
          </div>
        )}
        <div className="card" style={{ flex: "1 1 280px", margin: 0 }}>
          <div className="card-title">Asset Allocation</div>
          <AllocationPieChart accounts={accounts} manualEntries={manualEntries} />
        </div>
      </div>

      {/* ── Portfolio Performance ── */}
      {portfolioPerf.length >= 2 && (
        <PortfolioPerformanceCard data={portfolioPerf} />
      )}

      {/* ── Accounts: 2-column grid, each institution/section its own card ── */}
      <div className="account-grid">
        {/* One card per Plaid/Coinbase institution */}
        {accounts.length === 0 ? (
          <div className="card" style={{ margin: 0, gridColumn: "1 / -1", textAlign: "center", padding: "24px 0", color: "var(--text2)" }}>
            No accounts connected. Click <strong>+ Connect Account</strong> above.
          </div>
        ) : (
          Object.entries(grouped).map(([institution, accts]) => {
            const item = plaidItems.find(i => i.institution_name === institution);
            return (
              <div className="card" key={institution} style={{ margin: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                  <div>
                    <span style={{ fontWeight: 700, fontSize: 15 }}>{institution}</span>
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

        {/* Investment Accounts (PDF imported) */}
        {manualInvested.length > 0 && (
          <div className="card" style={{ margin: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>Investment Accounts (Imported)</div>
            {allHoldingsTotal > 0 && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 8 }}>
                  Portfolio Allocation
                </div>
                <AllocationBar allocation={allocationByClass} total={allHoldingsTotal} />
              </div>
            )}
            <div className="account-list">
              {manualInvested.map(e => <ManualAccountRow key={e.id} entry={e} onRenamed={load} />)}
            </div>
          </div>
        )}

        {/* Other Investment Accounts (manual, no account number — e.g. Insperity) */}
        {manualInvestedOther.length > 0 && (
          <div className="card" style={{ margin: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>Other Investment Accounts</div>
            <div className="account-list">
              {manualInvestedOther.map(e => <ManualAccountRow key={e.id} entry={e} onRenamed={load} />)}
            </div>
          </div>
        )}

        {/* HSA / Cash Accounts */}
        {manualLiquid.length > 0 && (
          <div className="card" style={{ margin: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>HSA / Cash Accounts (Imported)</div>
            <div className="account-list">
              {manualLiquid.map(e => (
                <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                  badge="liquid" badgeClass="badge-depository" />
              ))}
            </div>
          </div>
        )}

        {/* Liabilities (Mortgage, Loans) */}
        {liabilities.length > 0 && (
          <div className="card" style={{ margin: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>Liabilities</div>
            <div className="account-list">
              {liabilities.map(e => (
                <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                  badge="liability" badgeClass="badge-credit" negative />
              ))}
            </div>
          </div>
        )}

        {/* Other Assets */}
        {otherAssets.length > 0 && (
          <div className="card" style={{ margin: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>Other Assets</div>
            <div className="account-list">
              {otherAssets.map(e => (
                <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                  badge="asset" badgeClass="badge-depository" />
              ))}
            </div>
          </div>
        )}
      </div>

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
