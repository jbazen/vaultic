import { useState } from "react";
import { renameAccount, getAccountHoldings, getAccountInvestmentTransactions, updateAccountNotes } from "../../api.js";
import { fmt, fmtDate } from "../../utils/format.js";
import { isRetirementAccount } from "../../utils/accounts.js";
import { tabBtn } from "./accountsUtils.jsx";
import AccountMetaLine from "./AccountMetaLine.jsx";
import AccountBalanceChart from "./AccountBalanceChart.jsx";
import PlaidHoldingsTable from "./PlaidHoldingsTable.jsx";
import InvestmentTransactionsTable from "./InvestmentTransactionsTable.jsx";

// ── Plaid Investment Card ──────────────────────────────────────────────────────
// Expandable row for Plaid investment/retirement accounts. Uses the same bordered
// box style as all other rows. Click header (not buttons) to expand/collapse.
export default function PlaidInvestmentCard({ account, onRenamed }) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState("holdings");
  const [holdings, setHoldings] = useState(null);
  const [transactions, setTransactions] = useState(null);
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
      try { const data = await getAccountHoldings(account.id); setHoldings(data); }
      finally { setLoading(false); }
    }
  }

  function handleRowClick(e) {
    if (e.target.closest("button, input, a")) return;
    handleToggle();
  }

  async function handleTabChange(tab) {
    setActiveTab(tab);
    if (tab === "transactions" && transactions === null) {
      setLoading(true);
      try { const data = await getAccountInvestmentTransactions(account.id, 100, 0); setTransactions(data); }
      finally { setLoading(false); }
    }
  }

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameAccount(account.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  return (
    <div className="account-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
      {/* Header — click to expand */}
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
          <AccountMetaLine
            type={account.type}
            subtype={account.subtype}
            notes={account.notes}
            onSaveNotes={async v => { await updateAccountNotes(account.id, v); onRenamed(); }}
            date={account.snapped_at ? fmtDate(account.snapped_at) : null}
            badgeType={account.type === "investment" && isRetirementAccount(account.subtype, account.name) ? "retirement" : undefined}
            badgeLabel={account.type === "investment" && isRetirementAccount(account.subtype, account.name) ? "retirement" : undefined}
          />
        </div>
        <div className="account-balance">{fmt(account.current)}</div>
      </div>

      {/* Expanded panel */}
      {expanded && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <button style={tabBtn(activeTab === "holdings")} onClick={() => handleTabChange("holdings")}>Holdings</button>
            <button style={tabBtn(activeTab === "transactions")} onClick={() => handleTabChange("transactions")}>Transactions</button>
            <button style={tabBtn(activeTab === "performance")} onClick={() => handleTabChange("performance")}>Performance</button>
          </div>
          {activeTab === "performance" ? (
            <AccountBalanceChart accountId={account.id} />
          ) : loading ? (
            <div style={{ color: "var(--text2)", fontSize: 13, padding: "8px 0" }}>Loading…</div>
          ) : activeTab === "holdings" ? (
            <PlaidHoldingsTable holdings={holdings?.holdings} totalValue={holdings?.total_value} />
          ) : (
            <InvestmentTransactionsTable transactions={transactions} />
          )}
        </div>
      )}
    </div>
  );
}
