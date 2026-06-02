/**
 * BalanceHistoryChart — balance-over-time area chart for a manual-entry account.
 *
 * Used by the Insperity and Voya account cards' Performance tab. Fetches the
 * full snapshot history once (getManualEntryHistory) and filters client-side by
 * the selected period. Same visual as the generic ManualInvestmentCard chart.
 *
 * Props:
 *   entryId — manual_entries.id (history is read from manual_entry_snapshots)
 */
import { useState, useEffect } from "react";
import {
  AreaChart, Area, XAxis, YAxis, ResponsiveContainer, Tooltip as RechartsTooltip,
} from "recharts";
import { getManualEntryHistory } from "../../api.js";
import { fmt } from "../../utils/format.js";

const PERIODS = [
  { label: "30D", days: 30 },
  { label: "90D", days: 90 },
  { label: "1Y", days: 365 },
  { label: "3Y", days: 1095 },
  { label: "All", days: 3650 },
];

function fmtCompact(n) {
  if (n == null) return "";
  const a = Math.abs(n);
  if (a >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(n / 1e3).toFixed(0)}k`;
  return `$${n.toFixed(0)}`;
}
function fmtAxisDate(s) {
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function periodBtn(active) {
  return {
    background: active ? "var(--accent)" : "var(--bg3)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "3px 10px",
    fontSize: 11, fontWeight: active ? 700 : 400, cursor: "pointer",
  };
}

export default function BalanceHistoryChart({ entryId }) {
  const [days, setDays] = useState(365);
  const [all, setAll] = useState(null);

  useEffect(() => {
    let active = true;
    getManualEntryHistory(entryId, 3650)
      .then(d => { if (active) setAll(Array.isArray(d) ? d : []); })
      .catch(() => { if (active) setAll([]); });
    return () => { active = false; };
  }, [entryId]);

  if (all === null) return <div style={{ color: "var(--text2)", fontSize: 13, padding: "16px 0" }}>Loading chart…</div>;

  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  const history = all.filter(r => r.snapped_at >= cutoffStr);

  if (history.length === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, padding: "16px 0" }}>No balance history yet for this period.</div>;
  }

  const first = history[0]?.current ?? 0;
  const last = history[history.length - 1]?.current ?? 0;
  const returnDollar = last - first;
  const returnPct = first > 0 ? ((last - first) / first) * 100 : 0;
  const isPositive = returnDollar >= 0;

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    return (
      <div style={{ background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 14px" }}>
        <div style={{ color: "var(--text2)", fontSize: 11, marginBottom: 4 }}>{fmtAxisDate(label)}</div>
        <div style={{ color: "var(--text)", fontWeight: 700, fontSize: 14 }}>{fmt(payload[0].value)}</div>
      </div>
    );
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", gap: 5 }}>
          {PERIODS.map(p => (
            <button key={p.days} style={periodBtn(days === p.days)} onClick={() => setDays(p.days)}>{p.label}</button>
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
      <ResponsiveContainer width="100%" height={170}>
        <AreaChart data={history} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={`grad-bh-${entryId}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.3} />
              <stop offset="95%" stopColor="var(--accent)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="snapped_at" tickFormatter={fmtAxisDate}
            tick={{ fontSize: 10, fill: "var(--text2)" }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tickFormatter={fmtCompact} tick={{ fontSize: 10, fill: "var(--text2)" }}
            axisLine={false} tickLine={false} width={55} domain={["auto", "auto"]} />
          <RechartsTooltip content={<CustomTooltip />} />
          <Area type="monotone" dataKey="current" stroke="var(--accent)" strokeWidth={2}
            fill={`url(#grad-bh-${entryId})`} dot={history.length === 1 ? { r: 5, fill: "var(--accent)" } : false} activeDot={{ r: 4 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
