import { useState, useEffect } from "react";
import { i360Holdings, i360Performance, i360ActivitySummary,
         i360AssetAllocation, i360BalanceHistory, i360Status } from "../../api.js";
import { fmt, fmtDate } from "../../utils/format.js";
import I360ActivitySummary from "../accounts/I360ActivitySummary.jsx";
import I360PerformanceTable from "../accounts/I360PerformanceTable.jsx";
import AssetCategoryDonut from "./AssetCategoryDonut.jsx";
import PortfolioBalanceHistoryChart from "./PortfolioBalanceHistoryChart.jsx";
import TopHoldingsTable from "./TopHoldingsTable.jsx";

// ── Main Section ──────────────────────────────────────────────────────────────

export default function I360SummarySection() {
  const [status, setStatus] = useState(null);
  const [activity, setActivity] = useState(null);
  const [allocation, setAllocation] = useState(null);
  const [balanceHistory, setBalanceHistory] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [holdings, setHoldings] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      i360Status().catch(() => null),
      i360ActivitySummary().catch(() => null),
      i360AssetAllocation().catch(() => null),
      i360BalanceHistory().catch(() => null),
      i360Performance().catch(() => null),
      i360Holdings().catch(() => null),
    ]).then(([st, act, alloc, hist, perf, hold]) => {
      setStatus(st);
      setActivity(act);
      setAllocation(alloc);
      setBalanceHistory(hist);
      setPerformance(perf);
      setHoldings(hold);
    }).finally(() => setLoading(false));
  }, []);

  if (loading) return null;
  if (!status?.configured) return null;

  // Flatten all holdings across accounts for top 10
  const allHoldings = holdings?.accounts?.flatMap(a => a.holdings) || [];

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div className="card-title" style={{ margin: 0 }}>Parker Financial — Portfolio Summary</div>
        <div style={{ fontSize: 12, color: "var(--text2)" }}>
          {holdings?.totals?.value > 0 && (
            <span style={{ fontWeight: 700, color: "var(--text)", fontSize: 18, marginRight: 12 }}>
              {fmt(holdings.totals.value)}
            </span>
          )}
          {status.last_sync && <span>Synced {fmtDate(status.last_sync)}</span>}
          {status.stale && <span style={{ color: "var(--orange)", marginLeft: 8 }}>Stale</span>}
        </div>
      </div>

      {/* Row 1: Account Summary + Asset Category donut */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 20 }}>
        <div style={{ flex: "1 1 380px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Account Summary</div>
          <I360ActivitySummary data={activity} />
        </div>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Asset Category</div>
          <AssetCategoryDonut data={allocation} />
        </div>
      </div>

      {/* Row 2: Balance History + Top 10 Holdings */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 20 }}>
        <div style={{ flex: "1 1 400px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Balance History</div>
          <PortfolioBalanceHistoryChart data={balanceHistory} />
        </div>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Top 10 Holdings</div>
          <TopHoldingsTable holdings={allHoldings} />
        </div>
      </div>

      {/* Row 3: Portfolio Performance (full width) */}
      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Portfolio Performance</div>
        <I360PerformanceTable data={performance} />
      </div>
    </div>
  );
}
