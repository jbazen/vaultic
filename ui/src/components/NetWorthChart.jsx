import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from "recharts";

function formatCurrency(v) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`;
  return `$${v?.toFixed(0) ?? 0}`;
}

function formatDate(d) {
  if (!d) return "";
  const dt = new Date(d + "T00:00:00");
  return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export default function NetWorthChart({ data }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ color: "var(--text2)", textAlign: "center", padding: "40px 0", fontSize: "14px" }}>
        No history yet — sync your accounts to start tracking.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
        <defs>
          <linearGradient id="nwGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#4f8ef7" stopOpacity={0.25} />
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
          width={60}
        />
        <Tooltip
          formatter={(v) => [formatCurrency(v), "Net Worth"]}
          labelFormatter={formatDate}
          contentStyle={{
            background: "#171b26",
            border: "1px solid #2a2f3e",
            borderRadius: "8px",
            color: "#e8eaf0",
          }}
        />
        <Area
          type="monotone"
          dataKey="total"
          stroke="#4f8ef7"
          strokeWidth={2}
          fill="url(#nwGrad)"
          dot={false}
          activeDot={{ r: 4 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
