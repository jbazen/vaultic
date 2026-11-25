import { useState, useEffect } from "react";
import { getLinkedSummary, i360Performance, i360ActivitySummary } from "../../api.js";
import { fmt, fmtDate } from "../../utils/format.js";
import I360ActivitySummary from "../accounts/I360ActivitySummary.jsx";
import I360PerformanceTable from "../accounts/I360PerformanceTable.jsx";
import AssetCategoryDonut from "./AssetCategoryDonut.jsx";
import PortfolioBalanceHistoryChart from "./PortfolioBalanceHistoryChart.jsx";
import TopHoldingsTable from "./TopHoldingsTable.jsx";

// ── Linked Portfolio Summary (#181) ───────────────────────────────────────────
// The Parker section, rebuilt for the accounts Parker moved to (LPL, through
// Plaid). Allocation and top holdings come from the current Plaid holdings; the
// balance history is Parker's, then the new accounts'. Parker's own account
// summary and performance end at its last sync and are labeled that way.
export default function LinkedPortfolioSection() {
  const [groups, setGroups] = useState([]);
  const [parker, setParker] = useState(null);

  useEffect(() => {
    let alive = true;
    getLinkedSummary()
      .then(async data => {
        if (!alive) return;
        const list = data?.groups || [];
        setGroups(list);
        if (list.some(g => g.parker_only)) {
          const [activity, performance] = await Promise.all([
            i360ActivitySummary().catch(() => null),
            i360Performance().catch(() => null),
          ]);
          if (alive) setParker({ activity, performance });
        }
      })
      .catch(() => { if (alive) setGroups([]); });
    return () => { alive = false; };
  }, []);

  return groups.map(g => (
    <div className="card" key={g.institution} data-testid="linked-portfolio">
      <div className="flex-between" style={{ marginBottom: 16, flexWrap: "wrap", gap: 8 }}>
        <div className="card-title" style={{ margin: 0 }}>{g.institution} — Portfolio Summary</div>
        <div style={{ fontWeight: 700, color: "var(--text)", fontSize: 18 }}>{fmt(g.total_value)}</div>
      </div>

      <div className="flex-wrap" style={{ display: "flex", gap: 20, marginBottom: 20 }}>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Asset Category</div>
          <AssetCategoryDonut data={g.categories} />
        </div>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Top 10 Holdings</div>
          <TopHoldingsTable holdings={g.top_holdings} />
        </div>
      </div>

      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>
          Balance History
          {g.predecessor_as_of && (
            <span style={{ fontWeight: 400, color: "var(--text2)", marginLeft: 8 }}>
              {g.predecessor_institution} through {fmtDate(g.predecessor_as_of)}, then {g.institution}
            </span>
          )}
        </div>
        <PortfolioBalanceHistoryChart data={g.balance_history} />
      </div>

      {g.parker_only && parker && (
        <div className="flex-wrap" style={{ display: "flex", gap: 20 }}>
          <div style={{ flex: "1 1 380px", minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>
              Account Summary <span style={{ fontWeight: 400, color: "var(--text2)" }}>
                (Parker, as of {fmtDate(g.predecessor_as_of)})</span>
            </div>
            <I360ActivitySummary data={parker.activity} />
          </div>
          <div style={{ flex: "1 1 380px", minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>
              Performance <span style={{ fontWeight: 400, color: "var(--text2)" }}>
                (Parker, as of {fmtDate(g.predecessor_as_of)})</span>
            </div>
            <I360PerformanceTable data={parker.performance} />
          </div>
        </div>
      )}
    </div>
  ));
}
