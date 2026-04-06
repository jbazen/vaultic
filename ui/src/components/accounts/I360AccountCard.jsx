import { useState } from "react";
import { renameAccount, updateAccountNotes, getBalanceHistory } from "../../api.js";
import { i360AccountHoldings, i360Performance, i360ActivitySummary, i360AssetAllocation } from "../../api.js";
import { isRetirementAccount } from "../../utils/accounts.js";
import { fmt, fmtDate } from "../../utils/format.js";
import EditableNotes from "../EditableNotes.jsx";
import I360HoldingsTable from "./I360HoldingsTable.jsx";
import I360ActivitySummary from "./I360ActivitySummary.jsx";
import I360PerformanceTable from "./I360PerformanceTable.jsx";
import I360AssetAllocation from "./I360AssetAllocation.jsx";

function tabBtn(active) {
  return {
    background: active ? "var(--accent)" : "var(--bg)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "4px 14px",
    fontSize: 12, fontWeight: active ? 700 : 400,
    cursor: "pointer",
  };
}

function MetaTag({ label }) {
  return (
    <span style={{
      fontSize: 11, padding: "2px 8px", borderRadius: 4,
      background: "var(--bg3)", color: "var(--text2)", whiteSpace: "nowrap",
    }}>{label}</span>
  );
}

export default function I360AccountCard({ account, onRenamed, BalanceChart }) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState("holdings");
  const [holdings, setHoldings] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [activity, setActivity] = useState(null);
  const [allocation, setAllocation] = useState(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(account.display_name || account.name);
  const [saving, setSaving] = useState(false);

  const label = account.display_name || account.name;
  const mask = account.mask ? ` (...${account.mask})` : "";

  async function handleToggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && holdings === null) {
      setLoading(true);
      try {
        const data = await i360AccountHoldings(account.id);
        setHoldings(data);
      } finally { setLoading(false); }
    }
  }

  function handleRowClick(e) {
    if (e.target.closest("button, input, a")) return;
    handleToggle();
  }

  async function handleTabChange(tab) {
    setActiveTab(tab);
    if (tab === "performance" && performance === null) {
      setLoading(true);
      try { const data = await i360Performance(); setPerformance(data); }
      finally { setLoading(false); }
    }
    if (tab === "activity" && activity === null) {
      setLoading(true);
      try { const data = await i360ActivitySummary(); setActivity(data); }
      finally { setLoading(false); }
    }
    if (tab === "allocation" && allocation === null) {
      setLoading(true);
      try { const data = await i360AssetAllocation(); setAllocation(data); }
      finally { setLoading(false); }
    }
  }

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameAccount(account.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  const isRetirement = isRetirementAccount(account.subtype, account.name);
  const badgeType = isRetirement ? "retirement" : account.type;
  const badgeLabel = isRetirement ? "retirement" : account.type;

  return (
    <div className="account-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
      {/* Header */}
      <div onClick={handleRowClick}
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}>
        <div className="account-info">
          {editing ? (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input className="form-input" style={{ width: 200, padding: "5px 8px", fontSize: 14 }}
                value={draft} onChange={e => setDraft(e.target.value)} autoFocus
                onKeyDown={e => { if (e.key === "Enter") handleSave(); if (e.key === "Escape") { setEditing(false); setDraft(label); }}} />
              <span style={{ color: "var(--text2)", fontSize: 13 }}>{mask}</span>
              <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={handleSave} disabled={saving}>{saving ? "…" : "Save"}</button>
              <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => { setEditing(false); setDraft(label); }}>Cancel</button>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--text2)", fontSize: 11, display: "inline-block",
                transition: "transform 0.15s", transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}>▶</span>
              <div className="account-name">{label}{mask}</div>
              <button onClick={() => setEditing(true)} title="Rename"
                style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }} aria-label="Rename">✎</button>
            </div>
          )}
          <div className="account-meta" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0 8px" }}>
            <span className={`badge badge-${badgeType}`}>{badgeLabel}</span>
            {account.subtype && <span style={{ color: "var(--text2)" }}>{account.subtype}</span>}
            {account.registration_type && <MetaTag label={account.registration_type} />}
            {account.open_date && <MetaTag label={`Opened ${fmtDate(account.open_date)}`} />}
            {account.investment_objective && <MetaTag label={account.investment_objective} />}
            <EditableNotes notes={account.notes}
              onSave={async v => { await updateAccountNotes(account.id, v); onRenamed(); }} inline />
            {account.snapped_at && <span style={{ color: "var(--text2)", opacity: 0.6 }}>· {fmtDate(account.snapped_at)}</span>}
          </div>
        </div>
        <div className="account-balance">{fmt(account.current)}</div>
      </div>

      {/* Expanded panel */}
      {expanded && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
            {["holdings", "activity", "performance", "allocation", "history"].map(tab => (
              <button key={tab} style={tabBtn(activeTab === tab)}
                onClick={() => handleTabChange(tab)}>
                {{ holdings: "Holdings", activity: "Activity", performance: "Performance",
                   allocation: "Allocation", history: "Balance History" }[tab]}
              </button>
            ))}
          </div>
          {activeTab === "history" ? (
            <BalanceChart accountId={account.id} />
          ) : loading ? (
            <div style={{ color: "var(--text2)", fontSize: 13, padding: "8px 0" }}>Loading…</div>
          ) : activeTab === "holdings" ? (
            <I360HoldingsTable holdings={holdings?.holdings} totals={holdings?.totals} />
          ) : activeTab === "activity" ? (
            <I360ActivitySummary data={activity} />
          ) : activeTab === "performance" ? (
            <I360PerformanceTable data={performance} />
          ) : activeTab === "allocation" ? (
            <I360AssetAllocation data={allocation} />
          ) : null}
        </div>
      )}
    </div>
  );
}
