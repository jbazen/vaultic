import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts";

function formatCurrency(v) {
  if (v == null) return "$0";
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(0)}k`;
  return `$${v.toFixed(0)}`;
}

function formatDate(d) {
  if (!d) return "";
  // Monthly data (YYYY-MM) vs daily (YYYY-MM-DD)
  if (d.length === 7) {
    const [y, m] = d.split("-");
    return new Date(+y, +m - 1, 1).toLocaleDateString("en-US", { month: "short", year: "2-digit" });
  }
  const dt = new Date(d + "T00:00:00");
  return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "#171b26", border: "1px solid #2a2f3e",
      borderRadius: 8, padding: "10px 14px", color: "#e8eaf0", fontSize: 13,
    }}>
      <div style={{ marginBottom: 6, color: "#8b92a8", fontSize: 12 }}>{formatDate(label)}</div>
      {payload.map(p => (
        <div key={p.dataKey} style={{ display: "flex", justifyContent: "space-between", gap: 24, marginBottom: 3 }}>
          <span style={{ color: p.color }}>{p.name}</span>
          <span style={{ fontWeight: 600 }}>{formatCurrency(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

export default function NetWorthChart({ data }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ color: "var(--text2)", textAlign: "center", padding: "40px 0", fontSize: 14 }}>
        No history yet — sync your accounts to start tracking.
      </div>
    );
  }

  const hasInvestable = data.some(d => d.investable != null && d.investable !== d.total);

  return (
    <ResponsiveContainer width="100%" height={240}>
      <ComposedChart data={data} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
        <defs>
          <linearGradient id="nwGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#4f8ef7" stopOpacity={0.2} />
            <stop offset="95%" stopColor="#4f8ef7" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3e" />
        <XAxis
          dataKey="snapped_at"
          tickFormatter={formatDate}
          tick={{ fill: "#8b92a8", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tickFormatter={formatCurrency}
          tick={{ fill: "#8b92a8", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={64}
        />
        <Tooltip content={<CustomTooltip />} />
        {hasInvestable && (
          <Legend
            formatter={(v) => <span style={{ fontSize: 12, color: "#8b92a8" }}>{v}</span>}
          />
        )}
        <Area
          type="monotone"
          dataKey="total"
          name="Total Net Worth"
          stroke="#4f8ef7"
          strokeWidth={2}
          fill="url(#nwGrad)"
          dot={false}
          activeDot={{ r: 4 }}
        />
        {hasInvestable && (
          <Line
            type="monotone"
            dataKey="investable"
            name="Investable Assets"
            stroke="#34d399"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            strokeDasharray="5 3"
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
