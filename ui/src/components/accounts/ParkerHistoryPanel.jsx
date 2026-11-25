import { useState, useEffect } from "react";
import { i360Performance, i360ActivitySummary } from "../../api.js";
import { fmtDate } from "../../utils/format.js";
import I360ActivitySummary from "./I360ActivitySummary.jsx";
import I360PerformanceTable from "./I360PerformanceTable.jsx";

// ── Parker History (#181) ─────────────────────────────────────────────────────
// The last account summary and performance Parker (Investor360) gave the
// account this one moved from. They end at Parker's last sync.
export default function ParkerHistoryPanel({ accountId, asOf }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      i360ActivitySummary(accountId).catch(() => null),
      i360Performance(accountId).catch(() => null),
    ]).then(([activity, performance]) => { if (alive) setData({ activity, performance }); });
    return () => { alive = false; };
  }, [accountId]);

  if (!data) return <div style={{ color: "var(--text2)", fontSize: 13, padding: "8px 0" }}>Loading…</div>;

  return (
    <div>
      {asOf && (
        <div style={{ color: "var(--text2)", fontSize: 12, marginBottom: 10 }}>
          From Parker, as of {fmtDate(asOf)}.
        </div>
      )}
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Account Summary</div>
      <I360ActivitySummary data={data.activity} />
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", margin: "16px 0 8px" }}>Performance</div>
      <I360PerformanceTable data={data.performance} />
    </div>
  );
}
