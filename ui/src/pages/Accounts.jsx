import { useState, useEffect, useRef } from "react";
import {
  getAccounts, getPlaidItems, removePlaidItem, renameAccount, syncCoinbase, getManualEntries,
  getAccountHoldings, getAccountInvestmentTransactions, toggleExcludeFromNetWorth,
  deleteManualEntry, updateAccountNotes, renameManualEntry,
  getManualEntryHistory, reorderInstitutions, getInstitutionOrder,
} from "../api.js";

import PlaidLink from "../components/PlaidLink.jsx";
import PlaidReconnect from "../components/PlaidReconnect.jsx";
import EditableNotes from "../components/EditableNotes.jsx";
import AccountNumberMeta from "../components/AccountNumberMeta.jsx";
import AllocationBar, { ASSET_CLASS_COLORS, ASSET_CLASS_LABELS } from "../components/AllocationBar.jsx";
import I360AccountCard from "../components/accounts/I360AccountCard.jsx";
import InsperityAccountCard from "../components/accounts/InsperityAccountCard.jsx";
import VoyaAccountCard from "../components/accounts/VoyaAccountCard.jsx";
import { isRetirementAccount } from "../utils/accounts.js";
import AccountMetaLine from "../components/accounts/AccountMetaLine.jsx";
import PlaidHoldingsTable from "../components/accounts/PlaidHoldingsTable.jsx";
import InvestmentTransactionsTable from "../components/accounts/InvestmentTransactionsTable.jsx";
import AccountBalanceChart from "../components/accounts/AccountBalanceChart.jsx";
import {
  SEC_INVESTED, SEC_INVESTED_OTHER, SEC_LIQUID, SEC_PROPERTY, SEC_LIABILITIES,
  isSyncStale, isLiability, isPlaidInvestment, tabBtn,
} from "../components/accounts/accountsUtils.jsx";
import { fmt, fmtCrypto, fmtPrice, fmtPercent as fmtPct, fmtNum, fmtDate } from "../utils/format.js";

// ── Crypto Account Row ─────────────────────────────────────────────────────────
// Expandable row for Coinbase holdings. Click the row to show/hide native balance,
// unit price, and USD value. Same expand pattern as investment accounts.
function CryptoAccountRow({ account, onRenamed }) {
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

// ── Standard Account Row ───────────────────────────────────────────────────────
// Checking, savings, credit cards, loans. All use the same bordered box style.
// Shows available balance for depository accounts and credit limit for credit accounts.
function AccountRow({ account, onRenamed }) {
  if (account.type === "crypto") return <CryptoAccountRow account={account} onRenamed={onRenamed} />;
  if (account.source === "investor360") return <I360AccountCard account={account} onRenamed={onRenamed} />;
  if (isPlaidInvestment(account)) return <PlaidInvestmentCard account={account} onRenamed={onRenamed} />;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(account.display_name || account.name);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameAccount(account.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") { setEditing(false); setDraft(account.display_name || account.name); }
  }

  const mask = account.mask ? ` (...${account.mask})` : "";
  const label = account.display_name || account.name;
  const isLiab = isLiability(account.type);

  // Extra inline labels on the meta line
  const extra = (
    <>
      {account.type === "depository" && account.available != null && account.available !== account.current && (
        <span style={{ color: "var(--text2)", fontSize: 12 }}>Avail: {fmt(account.available)}</span>
      )}
      {account.type === "credit" && account.limit_amount != null && (
        <span style={{ color: "var(--text2)", fontSize: 12 }}>Limit: {fmt(account.limit_amount)}</span>
      )}
    </>
  );

  return (
    <div className="account-row">
      <div className="account-info">
        {editing ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input className="form-input" style={{ width: 200, padding: "5px 8px", fontSize: 14 }}
              value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={handleKeyDown} autoFocus />
            <span style={{ color: "var(--text2)", fontSize: 13 }}>{mask}</span>
            <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={handleSave} disabled={saving}>{saving ? "…" : "Save"}</button>
            <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => { setEditing(false); setDraft(label); }}>Cancel</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
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
          extra={extra}
          badgeType={account.type === "investment" && isRetirementAccount(account.subtype, account.name) ? "retirement" : undefined}
          badgeLabel={account.type === "investment" && isRetirementAccount(account.subtype, account.name) ? "retirement" : undefined}
        />
      </div>
      <div className={`account-balance ${isLiab ? "liability" : ""}`}>
        {isLiab ? `-${fmt(account.current)}` : fmt(account.current)}
      </div>
    </div>
  );
}


// ── Manual Investment Card ─────────────────────────────────────────────────────
// PDF-imported investment account row. Uses the same bordered box style as all other
// rows. Click the row (not buttons) to expand/collapse holdings and activity summary.
function ManualInvestmentCard({ entry, onDelete, onToggleExclude, onRenamed }) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState("holdings");
  const [excluded, setExcluded] = useState(!!entry.exclude_from_net_worth);
  const [toggling, setToggling] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(entry.name);
  const [nameSaving, setNameSaving] = useState(false);
  // Full balance history (all snapshots, max 5 years) for the Performance tab.
  // Loaded once when the Performance tab is first opened; BalanceChart filters
  // client-side by the selected period (30D/90D/1Y/3Y) without re-fetching.
  const [perfHistory, setPerfHistory] = useState(null);
  const [perfLoading, setPerfLoading] = useState(false);

  async function handleTabChange(tab) {
    setActiveTab(tab);
    if (tab === "performance" && perfHistory === null && !perfLoading) {
      setPerfLoading(true);
      try {
        const data = await getManualEntryHistory(entry.id, 1825);
        setPerfHistory(data);
      } catch {
        setPerfHistory([]);
      } finally {
        setPerfLoading(false);
      }
    }
  }

  async function handleNameSave() {
    if (!nameDraft.trim()) return;
    setNameSaving(true);
    try { await renameManualEntry(entry.id, nameDraft.trim()); onRenamed(); setEditingName(false); }
    finally { setNameSaving(false); }
  }
  const holdings = entry.holdings || [];
  const holdingsTotal = holdings.reduce((s, h) => s + (h.value || 0), 0);
  const allocation = holdings.reduce((acc, h) => {
    const cls = h.asset_class || "other";
    acc[cls] = (acc[cls] || 0) + (h.value || 0);
    return acc;
  }, {});
  // Always expandable — every investment account shows Holdings/Transactions/Performance tabs
  const hasDetails = true;

  async function handleToggleExclude() {
    setToggling(true);
    try {
      const res = await toggleExcludeFromNetWorth(entry.id);
      setExcluded(!!res.exclude_from_net_worth);
      if (onToggleExclude) onToggleExclude();
    } finally {
      setToggling(false);
    }
  }

  // Click the header area (not buttons) to toggle details
  function handleRowClick(e) {
    if (!hasDetails) return;
    if (e.target.closest("button, input, a")) return;
    setExpanded(ex => !ex);
  }

  return (
    <div className="account-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
      {/* Header — click to expand */}
      <div onClick={handleRowClick}
        style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start",
          cursor: hasDetails ? "pointer" : "default" }}>
        <div className="account-info" style={{ flex: 1 }}>
          {editingName ? (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input className="form-input" style={{ width: 240, padding: "5px 8px", fontSize: 14 }}
                value={nameDraft} onChange={e => setNameDraft(e.target.value)} autoFocus
                onKeyDown={e => { if (e.key === "Enter") handleNameSave(); if (e.key === "Escape") { setEditingName(false); setNameDraft(entry.name); }}} />
              <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={handleNameSave} disabled={nameSaving}>{nameSaving ? "…" : "Save"}</button>
              <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => { setEditingName(false); setNameDraft(entry.name); }}>Cancel</button>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {hasDetails && (
                <span style={{ color: "var(--text2)", fontSize: 11, display: "inline-block",
                  transition: "transform 0.15s", transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}>▶</span>
              )}
              <div className="account-name" style={{ color: excluded ? "var(--text2)" : "var(--text)" }}>
                {entry.name}
              </div>
              <button onClick={() => setEditingName(true)} title="Rename"
                style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }} aria-label="Rename">✎</button>
            </div>
          )}
          {/* Badge + notes + imported date all on one meta line */}
          <div className="account-meta" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0 8px", marginLeft: hasDetails ? 18 : 0 }}>
            <span className={`badge ${isRetirementAccount(null, entry.name) ? "badge-retirement" : "badge-investment"}`}>
              {isRetirementAccount(null, entry.name) ? "retirement" : "invested"}
            </span>
            <AccountNumberMeta accountNumber={entry.account_number} />
            {excluded && (
              <span style={{ fontSize: 11, color: "#f59e0b", background: "#f59e0b22", borderRadius: 4, padding: "2px 6px", fontWeight: 600 }}>
                excluded
              </span>
            )}
            <EditableNotes
              notes={entry.notes}
              onSave={async v => { await renameManualEntry(entry.id, entry.name, v); onRenamed(); }}
              inline
            />
            {entry.entered_at && (
              <span style={{ color: "var(--text2)", opacity: 0.6 }}>· Imported {fmtDate(entry.entered_at)}</span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 16, color: excluded ? "var(--text2)" : "var(--text)" }}>
            {fmt(entry.value)}
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <button onClick={handleToggleExclude} disabled={toggling}
              title={excluded ? "Include in net worth" : "Exclude from net worth"}
              style={{ background: "none", border: "1px solid var(--border)",
                color: excluded ? "var(--green)" : "#f59e0b", borderRadius: 6, padding: "3px 8px",
                cursor: "pointer", fontSize: 12 }}>
              {toggling ? "…" : excluded ? "+ Include" : "⊘ Exclude"}
            </button>
            {onDelete && (
              <button onClick={() => onDelete(entry.id)}
                style={{ background: "none", border: "1px solid var(--border)",
                  color: "var(--red)", borderRadius: 6, padding: "3px 8px",
                  cursor: "pointer", fontSize: 12 }} title="Delete" aria-label="Delete">✕</button>
            )}
          </div>
        </div>
      </div>

      {/* Expanded panel — same tab structure as Plaid investment accounts */}
      {expanded && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <button style={tabBtn(activeTab === "holdings")} onClick={() => handleTabChange("holdings")}>Holdings</button>
            <button style={tabBtn(activeTab === "transactions")} onClick={() => handleTabChange("transactions")}>Transactions</button>
            <button style={tabBtn(activeTab === "performance")} onClick={() => handleTabChange("performance")}>Performance</button>
          </div>

          {/* Holdings tab — asset allocation bar + holdings table */}
          {activeTab === "holdings" && (
            <div>
              {holdingsTotal > 0 && <AllocationBar allocation={allocation} total={holdingsTotal} style="accounts" />}
              {holdings.length > 0 ? (
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid var(--border)" }}>
                        {["Security", "Class", "Ticker", "Shares", "Price", "Value ($)", "% Assets", "Principal ($)", "Gain/Loss ($)", "Gain/Loss (%)"].map(h => (
                          <th key={h} scope="col" style={{ padding: "6px 10px 8px", textAlign: h === "Security" || h === "Class" ? "left" : "right",
                            color: "var(--text2)", fontWeight: 600, whiteSpace: "nowrap", fontSize: 12 }}>{h}</th>
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
                              {h.asset_class ? (
                                <span style={{ background: `color-mix(in srgb, ${ASSET_CLASS_COLORS[h.asset_class] || "var(--text2)"} 13%, transparent)`, color: ASSET_CLASS_COLORS[h.asset_class] || "var(--text2)", borderRadius: 4, padding: "2px 6px", fontSize: 11, fontWeight: 600 }}>
                                  {ASSET_CLASS_LABELS[h.asset_class] || h.asset_class}
                                </span>
                              ) : "—"}
                            </td>
                            <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace", color: "var(--text2)" }}>{h.ticker || "—"}</td>
                            <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{fmtNum(h.shares, 4)}</td>
                            <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{h.price != null ? fmt(h.price) : "—"}</td>
                            <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: "var(--text)" }}>{h.value != null ? fmt(h.value) : "—"}</td>
                            <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{fmtPct(h.pct_assets)}</td>
                            <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{h.principal != null ? fmt(h.principal) : "—"}</td>
                            <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: glPos ? "var(--green)" : glNeg ? "var(--red)" : "var(--text)" }}>
                              {h.gain_loss_dollars != null ? (glPos ? "+" : "") + fmt(h.gain_loss_dollars) : "—"}
                            </td>
                            <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: glPos ? "var(--green)" : glNeg ? "var(--red)" : "var(--text)" }}>
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
              ) : (
                <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>
                  No holdings detail — not included in the imported PDF.
                </div>
              )}
            </div>
          )}

          {/* Transactions tab — activity summary from PDF, or empty state */}
          {activeTab === "transactions" && (
            entry.activity_summary ? (
              <div style={{
                display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
                gap: 10, padding: "12px 14px",
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
                    <div style={{ fontWeight: 700, fontSize: 14, color: signed ? (value >= 0 ? "var(--green)" : "var(--red)") : "var(--text)" }}>
                      {signed && value > 0 ? "+" : ""}{fmt(value)}
                    </div>
                  </div>
                ) : null)}
                {entry.activity_summary.twr_pct != null && (
                  <div>
                    <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 2 }}>Time-Weighted Return</div>
                    <div style={{ fontWeight: 700, fontSize: 14, color: entry.activity_summary.twr_pct >= 0 ? "var(--green)" : "var(--red)" }}>
                      {entry.activity_summary.twr_pct > 0 ? "+" : ""}{entry.activity_summary.twr_pct.toFixed(2)}%
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>
                No transaction history — this account is imported via PDF snapshot, not live-synced.
                Reimport an updated PDF to see period activity.
              </div>
            )
          )}

          {/* Performance tab — balance chart built from manual_entry_snapshots history.
              Every PDF import writes a snapshot row, so uploading monthly PDFs (or
              historical PDFs from past months) builds up the same time-series chart
              used for Plaid-connected accounts. */}
          {activeTab === "performance" && (
            perfLoading
              ? <div style={{ color: "var(--text2)", fontSize: 13, padding: "20px 0" }}>Loading…</div>
              : <AccountBalanceChart data={perfHistory ?? []} />
          )}
        </div>
      )}
    </div>
  );
}

// ── Plaid Investment Card ──────────────────────────────────────────────────────
// Expandable row for Plaid investment/retirement accounts. Uses the same bordered
// box style as all other rows. Click header (not buttons) to expand/collapse.
function PlaidInvestmentCard({ account, onRenamed }) {
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

// ── Manual Simple Row ──────────────────────────────────────────────────────────
// HSA, property, vehicles, and liabilities. Editable name (✎) and description.
function ManualSimpleRow({ entry, badge, badgeClass, negative, onDelete, onRenamed }) {
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

// ── Main Accounts Page ─────────────────────────────────────────────────────────

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const [coinbaseStatus, setCoinbaseStatus] = useState(null);
  const [manualEntries, setManualEntries] = useState([]);
  const [savedOrder, setSavedOrder] = useState([]);
  // Drag-and-drop ref (avoid setState during dragstart — see CLAUDE.md)
  const dragInstRef = useRef(null);

  async function load() {
    setLoadError(false);
    try {
      const [accts, its, manual, order] = await Promise.all([
        getAccounts(),
        getPlaidItems(),
        getManualEntries(),
        getInstitutionOrder("accounts").catch(() => ({ names: [] })),
      ]);
      setAccounts(accts);
      setItems(its);
      setManualEntries(manual);
      setSavedOrder(order?.names || []);
    } catch {
      setLoadError(true);
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
    setRemoveError("");
    try {
      await removePlaidItem(itemId);
      await load();
    } catch (e) {
      setRemoveError(e.message || "Failed to disconnect. Please try again.");
    }
  }

  const grouped = accounts.reduce((acc, a) => {
    const key = a.institution_name || "Manual";
    if (!acc[key]) acc[key] = [];
    acc[key].push(a);
    return acc;
  }, {});

  // ── Filtered manual entry collections (computed once) ──
  const investedImported = manualEntries
    .filter(e => e.category === "invested" && (e.account_number || e.exclude_from_net_worth))
    .sort((a, b) => (b.exclude_from_net_worth - a.exclude_from_net_worth) || a.name.localeCompare(b.name));
  const investedOther    = manualEntries.filter(e => e.category === "invested" && !e.account_number && !e.exclude_from_net_worth);
  const liquidEntries    = manualEntries.filter(e => e.category === "liquid");
  const propertyEntries  = manualEntries.filter(e => ["home_value", "car_value", "real_estate", "vehicles"].includes(e.category));
  const liabilityEntries = manualEntries.filter(e => e.category === "other_liability");

  // ── Build unified block list (institutions + manual sections, all draggable) ──
  const blocks = [];
  for (const [inst, accts] of Object.entries(grouped)) {
    blocks.push({ key: inst, kind: "institution", accts });
  }
  if (investedImported.length > 0) blocks.push({ key: SEC_INVESTED,       kind: "sec_invested" });
  if (investedOther.length > 0)    blocks.push({ key: SEC_INVESTED_OTHER, kind: "sec_invested_other" });
  if (liquidEntries.length > 0)    blocks.push({ key: SEC_LIQUID,         kind: "sec_liquid" });
  if (propertyEntries.length > 0)  blocks.push({ key: SEC_PROPERTY,       kind: "sec_property" });
  if (liabilityEntries.length > 0) blocks.push({ key: SEC_LIABILITIES,    kind: "sec_liabilities" });

  const orderMap = new Map(savedOrder.map((n, i) => [n, i]));
  const defaultIdx = new Map(blocks.map((b, i) => [b.key, i]));
  const orderedBlocks = [...blocks].sort((a, b) => {
    const oA = orderMap.get(a.key);
    const oB = orderMap.get(b.key);
    if (oA != null && oB != null) return oA - oB;
    if (oA != null) return -1;
    if (oB != null) return 1;
    if (a.kind === "institution" && b.kind !== "institution") return -1;
    if (a.kind !== "institution" && b.kind === "institution") return 1;
    if (a.kind === "institution" && b.kind === "institution") return a.key.localeCompare(b.key);
    return defaultIdx.get(a.key) - defaultIdx.get(b.key);
  });

  function handleInstDragStart(e, key) {
    // Never call setState during dragstart — React re-renders mutate the DOM,
    // which the browser interprets as the drag source disappearing and cancels
    // the operation. Use a ref + a body class instead (see CLAUDE.md).
    dragInstRef.current = key;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", key);
    document.body.classList.add("inst-drag-active");
  }

  function handleInstDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }

  async function handleInstDrop(e, targetKey) {
    e.preventDefault();
    const sourceKey = dragInstRef.current;
    dragInstRef.current = null;
    document.body.classList.remove("inst-drag-active");
    if (!sourceKey || sourceKey === targetKey) return;

    const keys = orderedBlocks.map(b => b.key);
    const fromIdx = keys.indexOf(sourceKey);
    const toIdx = keys.indexOf(targetKey);
    if (fromIdx < 0 || toIdx < 0) return;
    keys.splice(fromIdx, 1);
    keys.splice(toIdx, 0, sourceKey);

    try {
      await reorderInstitutions("accounts", keys);
      await load();
    } catch (_) { /* non-fatal */ }
  }

  function handleInstDragEnd() {
    dragInstRef.current = null;
    document.body.classList.remove("inst-drag-active");
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2>Accounts</h2>
          <p>All connected institutions and balances</p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
            <button className="btn btn-secondary" onClick={handleCoinbaseSync} disabled={coinbaseSyncing} style={{ fontSize: 13 }}>
              {coinbaseSyncing ? "Syncing…" : "⟳ Sync Coinbase"}
            </button>
            {coinbaseStatus && (
              <span style={{ fontSize: 12, color: coinbaseStatus.ok ? "var(--accent)" : "var(--red)" }}>
                {coinbaseStatus.msg}
              </span>
            )}
          </div>
          <PlaidLink onSuccess={load} />
        </div>
      </div>

      {removeError && (
        <div className="status-banner warn" role="alert" style={{ marginBottom: 16 }}>{removeError}</div>
      )}

      {loading ? (
        <div style={{ color: "var(--text2)" }}>Loading…</div>
      ) : loadError && accounts.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{ fontSize: 14, color: "var(--text2)", marginBottom: 16 }}>
            Could not load accounts.
          </div>
          <button className="btn btn-primary" onClick={() => { setLoading(true); load(); }}>
            Retry
          </button>
        </div>
      ) : accounts.length === 0 ? (
        <div className="card empty-state">
          <p>No accounts connected yet.</p>
          <p style={{ fontSize: "13px" }}>Click "Connect Account" to link your first institution via Plaid.</p>
        </div>
      ) : (
        orderedBlocks.map(block => {
          let titleText = "";
          let subtitleText = null;
          let subtitleWarn = false;
          let rightButton = null;
          let bodyContent = null;

          if (block.kind === "institution") {
            const item = items.find(i => i.institution_name === block.key);
            titleText = block.key;
            // Real disconnect detection (issue #73): the "Reconnect" banner is
            // driven by Plaid's actual error_code, not by sync staleness. A merely
            // slow/late sync no longer falsely reads as "login expired"; it shows a
            // softer "hasn't synced recently" note instead.
            const loginExpired = item?.error_code === "ITEM_LOGIN_REQUIRED";
            const stale = item ? isSyncStale(item.last_synced_at) : false;
            if (loginExpired) {
              subtitleText = "⚠ Login expired — click Reconnect to resume syncing";
              subtitleWarn = true;
            } else if (item?.last_synced_at) {
              subtitleText = stale
                ? `Synced ${fmtDate(item.last_synced_at)} — hasn't synced recently`
                : `Synced ${fmtDate(item.last_synced_at)}`;
              subtitleWarn = false;
            }
            if (item) {
              rightButton = (
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <PlaidReconnect itemId={item.item_id} onSuccess={load} />
                  <button className="btn btn-danger" style={{ fontSize: 12, padding: "5px 12px" }}
                    onClick={() => handleRemove(item.item_id)}>
                    Disconnect
                  </button>
                </div>
              );
            }
            bodyContent = (
              <div className="account-list">
                {block.accts.map(a => <AccountRow key={a.id} account={a} onRenamed={load} />)}
              </div>
            );
          } else if (block.kind === "sec_invested") {
            titleText = "Investment Accounts (PDF Imported)";
            subtitleText = "Click any row to expand holdings. Use \"⊘ Exclude\" on consolidated portfolio summaries to avoid double-counting in net worth.";
            bodyContent = (
              <div className="account-list">
                {investedImported.map(entry => entry.account_number === "105001401K" ? (
                  <InsperityAccountCard key={entry.id} entry={entry}
                    onDelete={async (id) => { await deleteManualEntry(id); await load(); }}
                    onToggleExclude={load} onRenamed={load} />
                ) : entry.account_number === "861956" ? (
                  <VoyaAccountCard key={entry.id} entry={entry} />
                ) : (
                  <ManualInvestmentCard key={entry.id} entry={entry}
                    onDelete={async (id) => { await deleteManualEntry(id); await load(); }}
                    onToggleExclude={load} onRenamed={load} />
                ))}
              </div>
            );
          } else if (block.kind === "sec_invested_other") {
            titleText = "Other Investment Accounts";
            bodyContent = (
              <div className="account-list">
                {investedOther.map(entry => (
                  <ManualInvestmentCard key={entry.id} entry={entry}
                    onDelete={async (id) => { await deleteManualEntry(id); await load(); }}
                    onToggleExclude={load} onRenamed={load} />
                ))}
              </div>
            );
          } else if (block.kind === "sec_liquid") {
            titleText = "HSA / Cash Accounts";
            bodyContent = (
              <div className="account-list">
                {liquidEntries.map(entry => (
                  <ManualSimpleRow key={entry.id} entry={entry} badge="liquid" badgeClass="badge-depository"
                    onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                    onRenamed={load} />
                ))}
              </div>
            );
          } else if (block.kind === "sec_property") {
            titleText = "Property & Vehicles";
            bodyContent = (
              <div className="account-list">
                {propertyEntries.map(entry => (
                  <ManualSimpleRow key={entry.id} entry={entry}
                    badge={{ home_value: "Home", car_value: "Vehicle", real_estate: "Real Estate", vehicles: "Vehicle" }[entry.category]}
                    badgeClass="badge-investment"
                    onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                    onRenamed={load} />
                ))}
              </div>
            );
          } else if (block.kind === "sec_liabilities") {
            titleText = "Liabilities";
            bodyContent = (
              <div className="account-list">
                {liabilityEntries.map(entry => (
                  <ManualSimpleRow key={entry.id} entry={entry} badge="Liability" badgeClass="badge-credit" negative
                    onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                    onRenamed={load} />
                ))}
              </div>
            );
          }

          return (
            <div
              className="card inst-drop-target"
              key={block.key}
              onDragOver={handleInstDragOver}
              onDrop={(e) => handleInstDrop(e, block.key)}
            >
              <div
                draggable
                onDragStart={(e) => handleInstDragStart(e, block.key)}
                onDragEnd={handleInstDragEnd}
                title="Drag to reorder"
                style={{
                  display: "flex", justifyContent: "space-between", alignItems: "flex-start",
                  marginBottom: 16, cursor: "grab",
                }}
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                  <span style={{ color: "var(--text2)", fontSize: 16, userSelect: "none", marginTop: 1 }}>⋮⋮</span>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 16 }}>{titleText}</div>
                    {subtitleText && (
                      <div style={{ fontSize: 12, color: subtitleWarn ? "var(--red)" : "var(--text2)", marginTop: 2 }}>{subtitleText}</div>
                    )}
                  </div>
                </div>
                {rightButton}
              </div>
              {bodyContent}
            </div>
          );
        })
      )}
    </div>
  );
}
