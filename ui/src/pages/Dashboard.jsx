import { useState, useEffect, useRef } from "react";
import {
  getNetWorthLatest, getNetWorthHistory, triggerSync,
  getAccounts, getManualEntries,
  getPlaidItems, removePlaidItem, syncCoinbase, getPortfolioPerformance,
  getMarketRates, i360SyncLog, reorderInstitutions, getInstitutionOrder,
} from "../api.js";

// Pseudo-keys for manual sections so they can live in the same drag/drop
// ordering table as real Plaid/I360 institutions.
const SEC_INVESTED       = "__sec_investment_imported__";
const SEC_INVESTED_OTHER = "__sec_investment_other__";
const SEC_LIQUID         = "__sec_hsa_cash__";
const SEC_LIABILITIES    = "__sec_liabilities__";
const SEC_OTHER_ASSETS   = "__sec_other_assets__";
import NetWorthChart from "../components/NetWorthChart.jsx";
import PlaidLink from "../components/PlaidLink.jsx";
import AllocationBar from "../components/AllocationBar.jsx";
import AllocationPieChart from "../components/dashboard/AllocationPieChart.jsx";
import PortfolioPerformanceCard from "../components/dashboard/PortfolioPerformanceCard.jsx";
import { StatCard, AccountRow, ManualAccountRow } from "../components/dashboard/AccountRows.jsx";
import { isRetirementAccount } from "../utils/accounts.js";
import { fmt, fmtSigned, fmtDate } from "../utils/format.js";
import CalendarSection from "../components/calendar/CalendarSection.jsx";
import NewsFeedPanel from "../components/dashboard/NewsFeedPanel.jsx";
import I360SyncModal from "../components/dashboard/I360SyncModal.jsx";
import InsperitySyncModal from "../components/dashboard/InsperitySyncModal.jsx";
import I360SummarySection from "../components/dashboard/I360SummarySection.jsx";
import MarketSummaryCard from "../components/dashboard/MarketSummaryCard.jsx";

// ── Category config ───────────────────────────────────────────────────────────

const CATS = [
  { key: "liquid",       label: "Liquid",       color: "var(--green)", icon: "💵" },
  { key: "invested",     label: "Invested",     color: "var(--accent)", icon: "📈" },
  { key: "real_estate",  label: "Real Estate",  color: "var(--purple)", icon: "🏠" },
  { key: "vehicles",     label: "Vehicles",     color: "var(--yellow)", icon: "🚗" },
  { key: "crypto",       label: "Crypto",       color: "var(--orange)", icon: "₿" },
  { key: "other_assets", label: "Other Assets", color: "var(--teal)", icon: "📦" },
  { key: "liabilities",  label: "Liabilities",  color: "var(--red)", icon: "💳" },
];

const MANUAL_CAT_LABELS = {
  home_value:        { label: "Home Value",    cat: "real_estate", color: "var(--purple)" },
  car_value:         { label: "Car Value",     cat: "vehicles",    color: "var(--yellow)" },
  credit_score:      { label: "Credit Score",  cat: null,          color: "var(--green)" },
  other_asset:       { label: "Other Asset",   cat: "other_asset", color: "var(--teal)" },
  other_liability:   { label: "Liability",     cat: "liabilities", color: "var(--red)" },
  invested:          { label: "Invested",      cat: "invested",    color: "var(--accent)" },
  liquid:            { label: "Liquid",        cat: "liquid",      color: "var(--green)" },
};

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [nw, setNw] = useState(null);
  const [history, setHistory] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [manualEntries, setManualEntries] = useState([]);
  const [plaidItems, setPlaidItems] = useState([]);
  const [portfolioPerf, setPortfolioPerf] = useState([]);
  const [marketRates, setMarketRates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const [i360Open, setI360Open] = useState(false);
  const [insperityOpen, setInsperityOpen] = useState(false);
  const [i360LastSync, setI360LastSync] = useState(null);
  const [savedOrder, setSavedOrder] = useState([]);
  const mountedRef = useRef(true);
  // Drag-and-drop refs (avoid setState during dragstart — see CLAUDE.md)
  const dragInstRef = useRef(null);

  async function load() {
    setLoadError(false);
    try {
      const [nwData, hist, accts, manual, items, perf, rates, i360Log, order] = await Promise.all([
        getNetWorthLatest(),
        getNetWorthHistory(1825),
        getAccounts(),
        getManualEntries(),
        getPlaidItems(),
        getPortfolioPerformance(1825),
        getMarketRates().catch(() => ({ rates: [] })),
        i360SyncLog(1).catch(() => []),
        getInstitutionOrder("dashboard").catch(() => ({ names: [] })),
      ]);
      if (!mountedRef.current) return;
      setNw(nwData);
      setHistory(hist);
      setAccounts(accts);
      setManualEntries(manual);
      setPlaidItems(items);
      setPortfolioPerf(perf);
      setMarketRates(rates?.rates ?? []);
      setI360LastSync(i360Log?.[0]?.synced_at || null);
      setSavedOrder(order?.names || []);
    } catch (e) {
      if (!mountedRef.current) return;
      setLoadError(true);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    mountedRef.current = true;   // Reset on remount (React 18 StrictMode re-runs effects)
    load();
    return () => { mountedRef.current = false; };
  }, []);

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

  // Build the unified block list — institutions AND manual sections, all
  // draggable in one shared order. Each block has a stable string key that
  // is stored in institution_display_order on reorder.
  const blocks = [];
  for (const [inst, accts] of Object.entries(grouped)) {
    blocks.push({ key: inst, kind: "institution", accts });
  }
  if (manualInvested.length > 0)      blocks.push({ key: SEC_INVESTED,       kind: "sec_invested" });
  if (manualInvestedOther.length > 0) blocks.push({ key: SEC_INVESTED_OTHER, kind: "sec_invested_other" });
  if (manualLiquid.length > 0)        blocks.push({ key: SEC_LIQUID,         kind: "sec_liquid" });
  if (liabilities.length > 0)         blocks.push({ key: SEC_LIABILITIES,    kind: "sec_liabilities" });
  if (otherAssets.length > 0)         blocks.push({ key: SEC_OTHER_ASSETS,   kind: "sec_other_assets" });

  // Sort by saved order (positions from getInstitutionOrder). Blocks not in
  // the saved order fall to the end with a sensible default: institutions
  // first (I360 priority), then manual sections in the natural array order.
  const orderMap = new Map(savedOrder.map((n, i) => [n, i]));
  const defaultIdx = new Map(blocks.map((b, i) => [b.key, i]));
  const orderedBlocks = [...blocks].sort((a, b) => {
    const oA = orderMap.get(a.key);
    const oB = orderMap.get(b.key);
    if (oA != null && oB != null) return oA - oB;
    if (oA != null) return -1;
    if (oB != null) return 1;
    // Both unsaved — institutions before sections, then I360 priority within
    if (a.kind === "institution" && b.kind !== "institution") return -1;
    if (a.kind !== "institution" && b.kind === "institution") return 1;
    if (a.kind === "institution" && b.kind === "institution") {
      const i360A = a.accts.some(x => x.source === "investor360") ? 0 : 1;
      const i360B = b.accts.some(x => x.source === "investor360") ? 0 : 1;
      if (i360A !== i360B) return i360A - i360B;
      return a.key.localeCompare(b.key);
    }
    return defaultIdx.get(a.key) - defaultIdx.get(b.key);
  });

  function handleInstDragStart(e, key) {
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
      await reorderInstitutions("dashboard", keys);
      await load();
    } catch (_) { /* non-fatal — leave order as-is */ }
  }

  function handleInstDragEnd() {
    dragInstRef.current = null;
    document.body.classList.remove("inst-drag-active");
  }

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

  if (loadError && !nw) return (
    <div className="card" style={{ textAlign: "center", padding: "48px 24px", margin: "40px auto", maxWidth: 400 }}>
      <div style={{ fontSize: 14, color: "var(--text2)", marginBottom: 16 }}>
        Could not load dashboard data.
      </div>
      <button className="btn btn-primary" onClick={() => { setLoading(true); load(); }}>
        Retry
      </button>
    </div>
  );

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
          <button className="btn btn-secondary" onClick={() => setI360Open(true)}>
            ⟳ Parker
          </button>
          <button className="btn btn-secondary" onClick={() => setInsperityOpen(true)}>
            ⟳ Insperity
          </button>
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
                <span style={{ fontSize: 22, fontWeight: 700, color: "var(--purple)" }}>{fmt(homeValue.value)}</span>
              </div>
            )}
            {carValue && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>🚗 Car</div>
                <span style={{ fontSize: 22, fontWeight: 700, color: "var(--yellow)" }}>{fmt(carValue.value)}</span>
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

      {/* ── Portfolio Performance + Calendar (side by side, equal height) ── */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "stretch", marginBottom: 20 }}>
        {portfolioPerf.length >= 2 && (
          <div style={{ flex: "1 1 400px", minWidth: 0, display: "flex" }}>
            <PortfolioPerformanceCard data={portfolioPerf} />
          </div>
        )}
        <div style={{ flex: "1 1 340px", minWidth: 0, display: "flex" }}>
          <CalendarSection />
        </div>
      </div>

      {/* ── Market Summary ── */}
      <MarketSummaryCard />

      {/* ── News Feed ── */}
      <NewsFeedPanel />

      {/* ── Parker Financial Portfolio Summary ── */}
      <I360SummarySection />

      {/* ── Accounts: 2-column grid; every block (institution + manual section) draggable ── */}
      <div className="account-grid">
        {accounts.length === 0 && blocks.length === 0 ? (
          <div className="card" style={{ margin: 0, gridColumn: "1 / -1", textAlign: "center", padding: "24px 0", color: "var(--text2)" }}>
            No accounts connected. Click <strong>+ Connect Account</strong> above.
          </div>
        ) : (
          orderedBlocks.map(block => {
            // ── Header content varies by block kind ──
            let titleText = "";
            let rightButton = null;
            let bodyContent = null;

            if (block.kind === "institution") {
              const item = plaidItems.find(i => i.institution_name === block.key);
              const isI360 = block.accts.some(x => x.source === "investor360");
              const syncedAt = item?.last_synced_at || (isI360 ? i360LastSync : null);
              titleText = block.key;
              if (item) {
                rightButton = (
                  <button className="btn btn-danger" style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={() => handleRemoveItem(item.item_id)}>
                    Disconnect
                  </button>
                );
              }
              bodyContent = (
                <>
                  {syncedAt && (
                    <div style={{ fontSize: 11, color: "var(--text2)", marginTop: -6, marginBottom: 8 }}>
                      synced {fmtDate(syncedAt)}
                    </div>
                  )}
                  <div className="account-list">
                    {block.accts.map(a => <AccountRow key={a.id} account={a} onRenamed={load} />)}
                  </div>
                </>
              );
            } else if (block.kind === "sec_invested") {
              titleText = "Investment Accounts (Imported)";
              bodyContent = (
                <>
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
                </>
              );
            } else if (block.kind === "sec_invested_other") {
              titleText = "Other Investment Accounts";
              bodyContent = (
                <div className="account-list">
                  {manualInvestedOther.map(e => <ManualAccountRow key={e.id} entry={e} onRenamed={load} />)}
                </div>
              );
            } else if (block.kind === "sec_liquid") {
              titleText = "HSA / Cash Accounts (Imported)";
              bodyContent = (
                <div className="account-list">
                  {manualLiquid.map(e => (
                    <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                      badge="liquid" badgeClass="badge-depository" />
                  ))}
                </div>
              );
            } else if (block.kind === "sec_liabilities") {
              titleText = "Liabilities";
              bodyContent = (
                <div className="account-list">
                  {liabilities.map(e => (
                    <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                      badge="liability" badgeClass="badge-credit" negative />
                  ))}
                </div>
              );
            } else if (block.kind === "sec_other_assets") {
              titleText = "Other Assets";
              bodyContent = (
                <div className="account-list">
                  {otherAssets.map(e => (
                    <ManualAccountRow key={e.id} entry={e} onRenamed={load}
                      badge="asset" badgeClass="badge-depository" />
                  ))}
                </div>
              );
            }

            return (
              <div
                className="card inst-drop-target"
                key={block.key}
                style={{ margin: 0 }}
                onDragOver={handleInstDragOver}
                onDrop={(e) => handleInstDrop(e, block.key)}
              >
                <div
                  draggable
                  onDragStart={(e) => handleInstDragStart(e, block.key)}
                  onDragEnd={handleInstDragEnd}
                  title="Drag to reorder"
                  style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    marginBottom: 12, cursor: "grab",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ color: "var(--text2)", fontSize: 14, userSelect: "none" }}>⋮⋮</span>
                    <span style={{ fontWeight: 700, fontSize: 15 }}>{titleText}</span>
                  </div>
                  {rightButton}
                </div>
                {bodyContent}
              </div>
            );
          })
        )}
      </div>

      <I360SyncModal open={i360Open} onClose={() => setI360Open(false)} onSynced={load} />
      <InsperitySyncModal open={insperityOpen} onClose={() => setInsperityOpen(false)} onSynced={load} />
    </div>
  );
}
