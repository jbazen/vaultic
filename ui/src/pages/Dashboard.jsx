import { useState, useEffect, useRef } from "react";
import {
  getNetWorthLatest, getNetWorthHistory, triggerSync,
  getAccounts, getManualEntries, getRecentTransactions,
  getPlaidItems, removePlaidItem, syncCoinbase, getPortfolioPerformance,
  getMarketRates,
} from "../api.js";
import NetWorthChart from "../components/NetWorthChart.jsx";
import PlaidLink from "../components/PlaidLink.jsx";
import AllocationBar from "../components/AllocationBar.jsx";
import AllocationPieChart from "../components/dashboard/AllocationPieChart.jsx";
import PortfolioPerformanceCard from "../components/dashboard/PortfolioPerformanceCard.jsx";
import { StatCard, AccountRow, ManualAccountRow } from "../components/dashboard/AccountRows.jsx";
import { isRetirementAccount } from "../utils/accounts.js";
import { fmt, fmtSigned, fmtDate } from "../utils/format.js";
import CalendarSection from "../components/calendar/CalendarSection.jsx";

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
  const [transactions, setTransactions] = useState([]);
  const [plaidItems, setPlaidItems] = useState([]);
  const [portfolioPerf, setPortfolioPerf] = useState([]);
  const [marketRates, setMarketRates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const mountedRef = useRef(true);

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
      if (!mountedRef.current) return;
      setNw(nwData);
      setHistory(hist);
      setAccounts(accts);
      setManualEntries(manual);
      setTransactions(txns);
      setPlaidItems(items);
      setPortfolioPerf(perf);
      setMarketRates(rates?.rates ?? []);
    } catch (e) {
      if (!mountedRef.current) return;
      console.error(e);
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

      {/* ── Financial Calendar ── */}
      <CalendarSection />
    </div>
  );
}
