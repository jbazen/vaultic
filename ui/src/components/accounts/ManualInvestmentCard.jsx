import { useState } from "react";
import { toggleExcludeFromNetWorth, renameManualEntry, getManualEntryHistory } from "../../api.js";
import { fmt, fmtNum, fmtDate, fmtPercent as fmtPct } from "../../utils/format.js";
import { isRetirementAccount } from "../../utils/accounts.js";
import EditableNotes from "../EditableNotes.jsx";
import AccountNumberMeta from "../AccountNumberMeta.jsx";
import AllocationBar, { ASSET_CLASS_COLORS, ASSET_CLASS_LABELS } from "../AllocationBar.jsx";
import { tabBtn } from "./accountsUtils.jsx";
import AccountBalanceChart from "./AccountBalanceChart.jsx";

// ── Manual Investment Card ─────────────────────────────────────────────────────
// PDF-imported investment account row. Uses the same bordered box style as all other
// rows. Click the row (not buttons) to expand/collapse holdings and activity summary.
export default function ManualInvestmentCard({ entry, onDelete, onToggleExclude, onRenamed }) {
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
