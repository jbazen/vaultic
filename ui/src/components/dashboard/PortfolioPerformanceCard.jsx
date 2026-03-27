import { useState } from "react";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from "recharts";
import { fmt } from "../../utils/format.js";

export default function PortfolioPerformanceCard({ data }) {
  const [days, setDays] = useState(365);

  // Filter to the selected period client-side (data is already fetched for full range)
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  const filtered = data.filter(d => new Date(d.snapped_at + "T12:00:00") >= cutoff);

  const periods = [
    { label: "90D",  days: 90 },
    { label: "1Y",   days: 365 },
    { label: "3Y",   days: 1095 },
    { label: "5Y",   days: 1825 },
  ];

  const tab = active => ({
    background: active ? "var(--accent)" : "var(--bg3)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "4px 12px",
    fontSize: 12, fontWeight: active ? 700 : 400,
    cursor: "pointer",
  });

  if (!filtered || filtered.length < 2) return null;

  const first = filtered[0]?.total_value ?? 0;
  const last  = filtered[filtered.length - 1]?.total_value ?? 0;
  const returnDollar = last - first;
  const returnPct    = first > 0 ? ((last - first) / first) * 100 : 0;
  const isPositive   = returnDollar >= 0;

  function fmtCompact(v) {
    if (v == null) return "—";
    const abs = Math.abs(v);
    if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
    return `$${v.toFixed(0)}`;
  }

  function fmtAxisDate(s) {
    if (!s) return "";
    const d = new Date(s + "T12:00:00");
    if (days <= 90) return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
  }

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    return (
      <div style={{ background: "#171b26", border: "1px solid #2a2f3e", borderRadius: 8, padding: "10px 14px" }}>
        <div style={{ color: "var(--text2)", fontSize: 11, marginBottom: 4 }}>{fmtAxisDate(label)}</div>
        <div style={{ color: "var(--text)", fontWeight: 700, fontSize: 14 }}>{fmt(payload[0].value)}</div>
      </div>
    );
  };

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <div>
          <div className="card-title" style={{ marginBottom: 4 }}>Portfolio Performance</div>
          <div style={{ fontSize: 11, color: "var(--text2)" }}>Investment &amp; retirement accounts · excl. banking &amp; crypto</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: isPositive ? "var(--green)" : "var(--red)" }}>
            {isPositive ? "+" : ""}{fmt(returnDollar)}
          </div>
          <div style={{ fontSize: 12, color: "var(--text2)" }}>
            {isPositive ? "+" : ""}{returnPct.toFixed(2)}% over period
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
        {periods.map(p => (
          <button key={p.days} style={tab(days === p.days)} onClick={() => setDays(p.days)}>{p.label}</button>
        ))}
      </div>

      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={filtered} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="portfolioGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#a78bfa" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#a78bfa" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="snapped_at"
            tickFormatter={fmtAxisDate}
            tick={{ fontSize: 10, fill: "var(--text2)" }}
            axisLine={false} tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={fmtCompact}
            tick={{ fontSize: 10, fill: "var(--text2)" }}
            axisLine={false} tickLine={false}
            width={60}
          />
          <Tooltip content={<CustomTooltip />} />
          <Area
            type="monotone"
            dataKey="total_value"
            stroke="#a78bfa"
            strokeWidth={2}
            fill="url(#portfolioGrad)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
