import { useId } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip as RechartsTooltip, ResponsiveContainer } from "recharts";
import { fmt, fmtDate, fmtCompact } from "../../utils/format.js";

// ── Portfolio Balance History Chart ───────────────────────────────────────────

export default function PortfolioBalanceHistoryChart({ data }) {
  const gradId = `grad-mv-${useId().replace(/:/g, "")}`;
  if (!Array.isArray(data) || data.length === 0) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No balance history.</div>;

  const ChartTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    return (
      <div style={{ background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 14px" }}>
        <div style={{ color: "var(--text2)", fontSize: 11, marginBottom: 4 }}>{fmtDate(label)}</div>
        {payload.map(p => (
          <div key={p.dataKey} style={{ color: p.color, fontSize: 13, fontWeight: 600 }}>
            {p.name}: {fmt(p.value)}
          </div>
        ))}
      </div>
    );
  };

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.3} />
            <stop offset="95%" stopColor="var(--accent)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="balance_date" tickFormatter={s => fmtDate(s)}
          tick={{ fontSize: 10, fill: "var(--text2)" }} axisLine={false} tickLine={false}
          interval="preserveStartEnd" />
        <YAxis tickFormatter={fmtCompact}
          tick={{ fontSize: 10, fill: "var(--text2)" }} axisLine={false} tickLine={false}
          width={55} domain={["auto", "auto"]} />
        <RechartsTooltip content={<ChartTooltip />} />
        <Area type="monotone" dataKey="market_value" name="Market Value"
          stroke="var(--accent)" strokeWidth={2} fill={`url(#${gradId})`} dot={false} />
        <Area type="monotone" dataKey="net_investment" name="Net Investment"
          stroke="var(--green)" strokeWidth={1.5} fill="none" dot={false}
          strokeDasharray="5 3" />
      </AreaChart>
    </ResponsiveContainer>
  );
}
