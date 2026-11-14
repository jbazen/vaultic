import { useState, useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import {
  getNetWorthLatest, getNetWorthHistory, triggerSync,
  getAccounts, getManualEntries,
  getPlaidItems, syncCoinbase, getPortfolioPerformance,
  getMarketRates, i360SyncLog, getInstitutionOrder,
} from "../api.js";

import NetWorthChart from "../components/NetWorthChart.jsx";
import PlaidLink from "../components/PlaidLink.jsx";
import AllocationPieChart from "../components/dashboard/AllocationPieChart.jsx";
import PortfolioPerformanceCard from "../components/dashboard/PortfolioPerformanceCard.jsx";
import AccountsSection from "../components/accounts/AccountsSection.jsx";
import NetWorthHero from "../components/dashboard/NetWorthHero.jsx";
import NewsFeedPanel from "../components/dashboard/NewsFeedPanel.jsx";
import I360SyncModal from "../components/dashboard/I360SyncModal.jsx";
import InsperitySyncModal from "../components/dashboard/InsperitySyncModal.jsx";
import I360SummarySection from "../components/dashboard/I360SummarySection.jsx";
import MarketSummaryCard from "../components/dashboard/MarketSummaryCard.jsx";

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
  const [syncMsg, setSyncMsg] = useState("");
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const [coinbaseStatus, setCoinbaseStatus] = useState(null);
  const [i360Open, setI360Open] = useState(false);
  const [insperityOpen, setInsperityOpen] = useState(false);
  const [i360LastSync, setI360LastSync] = useState(null);
  const [savedOrder, setSavedOrder] = useState([]);
  const mountedRef = useRef(true);
  const { hash } = useLocation();

  // Full reload: first load, Sync All, Coinbase and the Parker / Insperity syncs.
  async function loadAll() {
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
    } catch {
      if (!mountedRef.current) return;
      setLoadError(true);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }

  // Light reload after an account edit (rename, notes, exclude, delete, reorder,
  // connect, disconnect). Skips the 5-year history and the market / news calls,
  // and never sets `loading`, so the page doesn't flash or lose its scroll position.
  async function loadAccounts() {
    try {
      const [nwData, accts, manual, items, order] = await Promise.all([
        getNetWorthLatest(),
        getAccounts(),
        getManualEntries(),
        getPlaidItems(),
        getInstitutionOrder("dashboard").catch(() => ({ names: [] })),
      ]);
      if (!mountedRef.current) return;
      setNw(nwData);
      setAccounts(accts);
      setManualEntries(manual);
      setPlaidItems(items);
      setSavedOrder(order?.names || []);
    } catch { /* non-fatal — the list keeps showing what it has */ }
  }

  useEffect(() => {
    mountedRef.current = true;   // Reset on remount (React 18 StrictMode re-runs effects)
    loadAll();
    return () => { mountedRef.current = false; };
  }, []);

  // /#accounts (the /accounts redirect and push-notification links) lands on the
  // account list. It only exists once the first load has finished. The cards above
  // it (market, news, Parker) load after that and push it down, so keep it in view
  // while the page grows, for a few seconds or until the user scrolls or taps.
  useEffect(() => {
    if (loading || hash !== "#accounts") return;
    const el = document.getElementById("accounts");
    if (!el) return;
    const keepInView = () => el.scrollIntoView();
    const userEvents = ["wheel", "touchstart", "keydown", "mousedown"];
    const observer = new ResizeObserver(keepInView);   // also fires once on observe()
    observer.observe(el.parentElement);
    const timer = setTimeout(stop, 5000);
    userEvents.forEach(ev => window.addEventListener(ev, stop, { passive: true }));
    function stop() {
      observer.disconnect();
      clearTimeout(timer);
      userEvents.forEach(ev => window.removeEventListener(ev, stop));
    }
    return stop;
  }, [loading, hash]);

  async function handleSync() {
    setSyncing(true);
    setSyncMsg("");
    try {
      // refresh=true forces Plaid to pull fresh transactions from the bank now.
      await triggerSync();
      await loadAll();
      // That pull is async on Plaid's side (issue #72) — new transactions land
      // seconds later. Tell the user, then poll once more (refresh=false, no extra
      // charge) to surface them without a manual re-sync.
      if (mountedRef.current) setSyncMsg("Refresh requested — new transactions will appear shortly…");
      setTimeout(async () => {
        try { await triggerSync(false); await loadAll(); } catch { /* non-fatal — also runs 4×/day */ }
        if (mountedRef.current) setSyncMsg("");
      }, 12000);
    }
    finally { if (mountedRef.current) setSyncing(false); }
  }

  async function handleCoinbaseSync() {
    setCoinbaseSyncing(true);
    setCoinbaseStatus(null);
    try {
      const result = await syncCoinbase();
      if (result.skipped) {
        setCoinbaseStatus({ ok: false, msg: "Coinbase API keys not configured in .env" });
      } else {
        setCoinbaseStatus({ ok: true, msg: `Synced ${result.synced} holdings` });
        await loadAll();
      }
    } catch (err) {
      setCoinbaseStatus({ ok: false, msg: err.message });
    } finally {
      setCoinbaseSyncing(false);
    }
  }

  if (loading) return <div style={{ color: "var(--text2)", padding: 40, textAlign: "center" }}>Loading…</div>;

  if (loadError && !nw) return (
    <div className="card" style={{ textAlign: "center", padding: "48px 24px", margin: "40px auto", maxWidth: 400 }}>
      <div style={{ fontSize: 14, color: "var(--text2)", marginBottom: 16 }}>
        Could not load dashboard data.
      </div>
      <button className="btn btn-primary" onClick={() => { setLoading(true); loadAll(); }}>
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
          <PlaidLink onSuccess={loadAccounts} />
          <button className="btn btn-secondary" onClick={() => setI360Open(true)}>
            ⟳ Parker
          </button>
          <button className="btn btn-secondary" onClick={() => setInsperityOpen(true)}>
            ⟳ Insperity
          </button>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
            <button className="btn btn-secondary" onClick={handleCoinbaseSync} disabled={coinbaseSyncing}>
              {coinbaseSyncing ? "Syncing…" : "⟳ Coinbase"}
            </button>
            {coinbaseStatus && (
              <span style={{ fontSize: 12, color: coinbaseStatus.ok ? "var(--accent)" : "var(--red)" }}>
                {coinbaseStatus.msg}
              </span>
            )}
          </div>
          <button className="btn btn-secondary" onClick={handleSync} disabled={syncing}>
            {syncing ? "Syncing…" : "↻ Sync All"}
          </button>
        </div>
      </div>

      {syncMsg && (
        <div style={{ fontSize: 13, color: "var(--accent)", marginBottom: 12 }}>{syncMsg}</div>
      )}

      <NetWorthHero nw={nw} manualEntries={manualEntries} marketRates={marketRates} />

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
        <div style={{ display: "flex", marginBottom: 20 }}>
          <PortfolioPerformanceCard data={portfolioPerf} />
        </div>
      )}

      {/* ── Market Summary ── */}
      <MarketSummaryCard />

      {/* ── News Feed ── */}
      <NewsFeedPanel />

      {/* ── Parker Financial Portfolio Summary ── */}
      <I360SummarySection />

      {/* ── Accounts: full-width list; every block (institution + manual section) draggable ── */}
      <AccountsSection accounts={accounts} manualEntries={manualEntries} plaidItems={plaidItems}
        savedOrder={savedOrder} i360LastSync={i360LastSync} onChanged={loadAccounts} />

      <I360SyncModal open={i360Open} onClose={() => setI360Open(false)} onSynced={loadAll} />
      <InsperitySyncModal open={insperityOpen} onClose={() => setInsperityOpen(false)} onSynced={loadAll} />
    </div>
  );
}
