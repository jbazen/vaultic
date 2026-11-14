import { useState, useEffect } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip as RechartsTooltip, ResponsiveContainer } from "recharts";
import { getBalanceHistory } from "../../api.js";
import { fmt, fmtCompact, fmtAxisDate } from "../../utils/format.js";

// ── Balance History Chart ──────────────────────────────────────────────────────
// Renders account balance over time for investment/retirement accounts.
// Two modes:
//   accountId  — fetches from /api/accounts/{id}/balances (Plaid-connected accounts)
//   data       — pre-loaded [{snapped_at, current}] array; filters client-side by
//                the selected period (PDF-imported accounts using manual_entry_snapshots)
// Both modes produce identical charts so the UI is consistent across account types.
export default function AccountBalanceChart({ accountId, data: preloadedData }) {
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
