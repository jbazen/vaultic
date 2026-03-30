import { useState, useEffect } from "react";
import {
  getAccounts, getPlaidItems, removePlaidItem, renameAccount, syncCoinbase, getManualEntries,
  getAccountHoldings, getAccountInvestmentTransactions, toggleExcludeFromNetWorth,
  deleteManualEntry, updateAccountNotes, renameManualEntry, getBalanceHistory,
  getManualEntryHistory,
} from "../api.js";
import PlaidLink from "../components/PlaidLink.jsx";
import EditableNotes from "../components/EditableNotes.jsx";
import AllocationBar, { ASSET_CLASS_COLORS, ASSET_CLASS_LABELS } from "../components/AllocationBar.jsx";
import { isRetirementAccount } from "../utils/accounts.js";
import { AreaChart, Area, XAxis, YAxis, Tooltip as RechartsTooltip, ResponsiveContainer } from "recharts";
import { fmt, fmtCrypto, fmtPrice, fmtPercent as fmtPct, fmtNum, fmtDate, fmtCompact, fmtAxisDate } from "../utils/format.js";

function typeBadge(type) {
  return <span className={`badge badge-${type}`}>{type}</span>;
}

function isLiability(type) {
  return type === "credit" || type === "loan";
}

// ── Shared: Meta line ──────────────────────────────────────────────────────────
// Badge + subtype/extra labels + editable notes + date — all on one line.
// badgeType/badgeLabel override the type-derived badge (e.g. "retirement" for 401k/IRA).
function MetaLine({ type, subtype, notes, onSaveNotes, date, extra, badgeType, badgeLabel }) {
  const bType  = badgeType  || type;
  const bLabel = badgeLabel || type;
  return (
    <div className="account-meta" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0 8px" }}>
      <span className={`badge badge-${bType}`}>{bLabel}</span>
      {subtype && <span style={{ color: "var(--text2)" }}>{subtype}</span>}
      {extra}
      <EditableNotes notes={notes} onSave={onSaveNotes} inline />
      {date && <span style={{ color: "var(--text2)", opacity: 0.6 }}>· {date}</span>}
    </div>
  );
}

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
          <MetaLine
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
        <MetaLine
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
              : <BalanceChart data={perfHistory ?? []} />
          )}
        </div>
      )}
    </div>
  );
}

// ── Plaid Investment Detection ─────────────────────────────────────────────────
function isPlaidInvestment(account) {
  if (account.is_manual) return false;
  return account.type === "investment" ||
    ["401k", "ira", "roth", "pension", "brokerage"].includes(account.subtype);
}

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

// ── Plaid Holdings Table ───────────────────────────────────────────────────────
function HoldingsTable({ holdings, totalValue }) {
  if (!holdings || holdings.length === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>No holdings data available.</div>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {["Security", "Ticker", "Type", "Qty", "Price", "Value", "Cost Basis", "Gain/Loss $", "Gain/Loss %", "% Assets"].map(h => (
              <th key={h} scope="col" style={{ padding: "6px 10px 8px", textAlign: h === "Security" || h === "Type" ? "left" : "right",
                color: "var(--text2)", fontWeight: 600, whiteSpace: "nowrap", fontSize: 12 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {holdings.map((h, i) => {
            const glPos = h.gain_loss_dollars > 0;
            const glNeg = h.gain_loss_dollars < 0;
            const glColor = glPos ? "var(--green)" : glNeg ? "var(--red)" : "var(--text)";
            return (
              <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "8px 10px", color: "var(--text)", maxWidth: 200, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={h.name}>{h.name || "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace", color: "var(--text2)" }}>{h.ticker_symbol || "—"}</td>
                <td style={{ padding: "8px 10px", color: "var(--text2)", fontSize: 12 }}>{h.security_type || "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{fmtNum(h.quantity, 4)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{h.institution_price != null ? fmt(h.institution_price) : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: "var(--text)" }}>{h.institution_value != null ? fmt(h.institution_value) : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{h.cost_basis != null ? fmt(h.cost_basis) : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: glColor }}>
                  {h.gain_loss_dollars != null ? (glPos ? "+" : "") + fmt(h.gain_loss_dollars) : "—"}
                </td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: glColor }}>
                  {h.gain_loss_pct != null ? (h.gain_loss_pct > 0 ? "+" : "") + fmtPct(h.gain_loss_pct) : "—"}
                </td>
                <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{fmtPct(h.pct_assets)}</td>
              </tr>
            );
          })}
        </tbody>
        {totalValue > 0 && (
          <tfoot>
            <tr style={{ borderTop: "2px solid var(--border)" }}>
              <td colSpan={5} style={{ padding: "8px 10px", fontWeight: 700, color: "var(--text)" }}>Total</td>
              <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 700, color: "var(--text)" }}>{fmt(totalValue)}</td>
              <td colSpan={4} />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

// ── Investment Transactions Table ──────────────────────────────────────────────
function InvestmentTransactionsTable({ transactions }) {
  if (!transactions || transactions.length === 0) {
    return (
      <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>
        No investment transactions found. Many 401k and IRA providers don't support
        transaction history via Plaid — holdings and balance history still sync normally.
      </div>
    );
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {["Date", "Type", "Subtype", "Security", "Ticker", "Qty", "Amount", "Fees"].map(h => (
              <th key={h} scope="col" style={{ padding: "6px 10px 8px",
                textAlign: ["Date", "Security", "Type", "Subtype"].includes(h) ? "left" : "right",
                color: "var(--text2)", fontWeight: 600, whiteSpace: "nowrap", fontSize: 12 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {transactions.map((t, i) => (
            <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
              <td style={{ padding: "8px 10px", color: "var(--text2)", whiteSpace: "nowrap" }}>{t.date}</td>
              <td style={{ padding: "8px 10px", color: "var(--text)" }}>{t.type || "—"}</td>
              <td style={{ padding: "8px 10px", color: "var(--text2)", fontSize: 12 }}>{t.subtype || "—"}</td>
              <td style={{ padding: "8px 10px", color: "var(--text)", maxWidth: 180, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={t.security_name}>{t.security_name || t.name || "—"}</td>
              <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace", color: "var(--text2)" }}>{t.ticker || "—"}</td>
              <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{fmtNum(t.quantity, 4)}</td>
              <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: "var(--text)" }}>{t.amount != null ? fmt(t.amount) : "—"}</td>
              <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{t.fees != null ? fmt(t.fees) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Balance History Chart ──────────────────────────────────────────────────────
// Renders account balance over time for investment/retirement accounts.
// Two modes:
//   accountId  — fetches from /api/accounts/{id}/balances (Plaid-connected accounts)
//   data       — pre-loaded [{snapped_at, current}] array; filters client-side by
//                the selected period (PDF-imported accounts using manual_entry_snapshots)
// Both modes produce identical charts so the UI is consistent across account types.
function BalanceChart({ accountId, data: preloadedData }) {
  const [days, setDays] = useState(365);
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(!preloadedData);

  useEffect(() => {
    // When pre-loaded data is provided (PDF-imported), filter it client-side
    // by the selected period rather than making a new network request.
    if (preloadedData) {
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - days);
      const cutoffStr = cutoff.toISOString().slice(0, 10);
      setHistory(preloadedData.filter(r => r.snapped_at >= cutoffStr));
      return;
    }
    // Plaid-connected path: fetch balance snapshots from the server.
    setLoading(true);
    getBalanceHistory(accountId, days)
      .then(data => setHistory(data))
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  }, [accountId, days, preloadedData]);

  const periods = [
    { label: "30D", days: 30 },
    { label: "90D", days: 90 },
    { label: "1Y",  days: 365 },
    { label: "3Y",  days: 1095 },
  ];

  const tab = active => ({
    background: active ? "var(--accent)" : "var(--bg3)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "3px 10px",
    fontSize: 11, fontWeight: active ? 700 : 400,
    cursor: "pointer",
  });

  if (loading) return <div style={{ color: "var(--text2)", fontSize: 13, padding: "20px 0" }}>Loading…</div>;
  if (!history || history.length === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, padding: "20px 0" }}>No history yet — sync this account or import a PDF to start tracking performance.</div>;
  }

  const first = history[0]?.current ?? 0;
  const last  = history[history.length - 1]?.current ?? 0;
  const returnDollar = last - first;
  const returnPct    = first > 0 ? ((last - first) / first) * 100 : 0;
  const isPositive   = returnDollar >= 0;

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    return (
      <div style={{ background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 14px" }}>
        <div style={{ color: "var(--text2)", fontSize: 11, marginBottom: 4 }}>{fmtAxisDate(label, days)}</div>
        <div style={{ color: "var(--text)", fontWeight: 700, fontSize: 14 }}>{fmt(payload[0].value)}</div>
      </div>
    );
  };

  return (
    <div>
      {/* Period selector + return summary */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", gap: 5 }}>
          {periods.map(p => (
            <button key={p.days} style={tab(days === p.days)} onClick={() => setDays(p.days)}>{p.label}</button>
          ))}
        </div>
        <div style={{ fontSize: 13 }}>
          <span style={{ color: isPositive ? "var(--green)" : "var(--red)", fontWeight: 700 }}>
            {isPositive ? "+" : ""}{fmt(returnDollar)}
          </span>
          <span style={{ color: "var(--text2)", marginLeft: 6, fontSize: 12 }}>
            ({isPositive ? "+" : ""}{returnPct.toFixed(2)}%)
          </span>
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={history} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={`grad-${accountId ?? "manual"}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="var(--accent)" stopOpacity={0.3} />
              <stop offset="95%" stopColor="var(--accent)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="snapped_at"
            tickFormatter={s => fmtAxisDate(s, days)}
            tick={{ fontSize: 10, fill: "var(--text2)" }}
            axisLine={false} tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={fmtCompact}
            tick={{ fontSize: 10, fill: "var(--text2)" }}
            axisLine={false} tickLine={false}
            width={55}
            domain={["auto", "auto"]}
          />
          <RechartsTooltip content={<CustomTooltip />} />
          <Area
            type="monotone"
            dataKey="current"
            stroke="var(--accent)"
            strokeWidth={2}
            fill={`url(#grad-${accountId ?? "manual"})`}
            dot={history.length === 1 ? { r: 5, fill: "var(--accent)" } : false}
            activeDot={{ r: 4 }}
          />
        </AreaChart>
      </ResponsiveContainer>
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
          <MetaLine
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
            <BalanceChart accountId={account.id} />
          ) : loading ? (
            <div style={{ color: "var(--text2)", fontSize: 13, padding: "8px 0" }}>Loading…</div>
          ) : activeTab === "holdings" ? (
            <HoldingsTable holdings={holdings?.holdings} totalValue={holdings?.total_value} />
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
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const [coinbaseStatus, setCoinbaseStatus] = useState(null);
  const [manualEntries, setManualEntries] = useState([]);

  async function load() {
    setLoadError(false);
    try {
      const [accts, its, manual] = await Promise.all([getAccounts(), getPlaidItems(), getManualEntries()]);
      setAccounts(accts);
      setItems(its);
      setManualEntries(manual);
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
        Object.entries(grouped).map(([institution, accts]) => {
          const item = items.find(i => i.institution_name === institution);
          return (
            <div className="card" key={institution}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 16 }}>{institution}</div>
                  {item?.last_synced_at && (
                    <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 2 }}>
                      Synced {fmtDate(item.last_synced_at)}
                    </div>
                  )}
                </div>
                {item && (
                  <button className="btn btn-danger" style={{ fontSize: 12, padding: "5px 12px" }}
                    onClick={() => handleRemove(item.item_id)}>
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

      {/* ── PDF-Imported Investment Accounts ── */}
      {manualEntries.filter(e => e.category === "invested" && (e.account_number || e.exclude_from_net_worth)).length > 0 && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 4 }}>
            Investment Accounts (PDF Imported)
          </div>
          <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 16 }}>
            Click any row to expand holdings. Use "⊘ Exclude" on consolidated portfolio summaries to avoid double-counting in net worth.
          </div>
          <div className="account-list">
            {[...manualEntries.filter(e => e.category === "invested" && (e.account_number || e.exclude_from_net_worth))]
              .sort((a, b) => (b.exclude_from_net_worth - a.exclude_from_net_worth) || a.name.localeCompare(b.name))
              .map(entry => (
                <ManualInvestmentCard key={entry.id} entry={entry}
                  onDelete={async (id) => { await deleteManualEntry(id); await load(); }}
                  onToggleExclude={load} onRenamed={load} />
              ))}
          </div>
        </div>
      )}

      {/* ── Manually-entered Investment Accounts (no account number — e.g. Insperity) ── */}
      {manualEntries.filter(e => e.category === "invested" && !e.account_number && !e.exclude_from_net_worth).length > 0 && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 16 }}>Other Investment Accounts</div>
          <div className="account-list">
            {manualEntries.filter(e => e.category === "invested" && !e.account_number && !e.exclude_from_net_worth).map(entry => (
              <ManualInvestmentCard key={entry.id} entry={entry}
                onDelete={async (id) => { await deleteManualEntry(id); await load(); }}
                onToggleExclude={load} onRenamed={load} />
            ))}
          </div>
        </div>
      )}

      {/* ── HSA / Cash Accounts ── */}
      {manualEntries.filter(e => e.category === "liquid").length > 0 && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 16 }}>HSA / Cash Accounts</div>
          <div className="account-list">
            {manualEntries.filter(e => e.category === "liquid").map(entry => (
              <ManualSimpleRow key={entry.id} entry={entry} badge="liquid" badgeClass="badge-depository"
                onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                onRenamed={load} />
            ))}
          </div>
        </div>
      )}

      {/* ── Property & Vehicles ── */}
      {manualEntries.filter(e => ["home_value", "car_value", "real_estate", "vehicles"].includes(e.category)).length > 0 && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 16 }}>Property & Vehicles</div>
          <div className="account-list">
            {manualEntries.filter(e => ["home_value", "car_value", "real_estate", "vehicles"].includes(e.category)).map(entry => (
              <ManualSimpleRow key={entry.id} entry={entry}
                badge={{ home_value: "Home", car_value: "Vehicle", real_estate: "Real Estate", vehicles: "Vehicle" }[entry.category]}
                badgeClass="badge-investment"
                onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                onRenamed={load} />
            ))}
          </div>
        </div>
      )}

      {/* ── Liabilities ── */}
      {manualEntries.filter(e => e.category === "other_liability").length > 0 && (
        <div className="card">
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 16 }}>Liabilities</div>
          <div className="account-list">
            {manualEntries.filter(e => e.category === "other_liability").map(entry => (
              <ManualSimpleRow key={entry.id} entry={entry} badge="Liability" badgeClass="badge-credit" negative
                onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                onRenamed={load} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
