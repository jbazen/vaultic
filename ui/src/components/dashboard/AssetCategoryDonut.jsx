import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import { fmt } from "../../utils/format.js";

// ── Asset Category colors (match Investor360 palette) ─────────────────────────

const CATEGORY_COLORS = [
  "#4f8ef7", "#34d399", "#fb923c", "#a78bfa", "#22d3ee",
  "#f59e0b", "#f87171", "#86efac", "#e879f9", "#facc15",
];

// ── Donut Pie Tooltip ─────────────────────────────────────────────────────────

function PieTooltipContent({ active, payload, total }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const pct = total > 0 ? ((d.value / total) * 100).toFixed(1) : "0.0";
  return (
    <div style={{ background: "var(--bg2)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "10px 14px", pointerEvents: "none" }}>
      <div style={{ fontWeight: 700, color: d.color, marginBottom: 4 }}>{d.name}</div>
      <div style={{ color: "var(--text)", fontSize: 14, fontWeight: 600 }}>{fmt(d.value)}</div>
      <div style={{ color: "var(--text2)", fontSize: 12, marginTop: 2 }}>{pct}%</div>
    </div>
  );
}

// ── Asset Category Donut ──────────────────────────────────────────────────────

export default function AssetCategoryDonut({ data }) {
  if (!Array.isArray(data) || data.length === 0) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No allocation data.</div>;

  const items = data
    .map((d, i) => ({ name: d.asset_name, value: d.market_value, color: CATEGORY_COLORS[i % CATEGORY_COLORS.length] }))
    .filter(d => d.value > 0)
    .sort((a, b) => b.value - a.value);
  const total = items.reduce((s, d) => s + d.value, 0);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
      <div style={{ flex: "0 0 200px" }}>
        <ResponsiveContainer width={200} height={200}>
          <PieChart>
            <Pie data={items} dataKey="value" cx="50%" cy="50%"
              outerRadius={90} innerRadius={50} paddingAngle={2} strokeWidth={0}>
              {items.map((d, i) => <Cell key={i} fill={d.color} />)}
            </Pie>
            <Tooltip content={(props) => <PieTooltipContent {...props} total={total} />} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 5, minWidth: 140 }}>
        {items.map((d, i) => {
          const pct = ((d.value / total) * 100).toFixed(1);
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}>
              <div style={{ width: 10, height: 10, borderRadius: 3, background: d.color, flexShrink: 0 }} />
              <span style={{ color: "var(--text2)", flex: 1 }}>{d.name}</span>
              <span style={{ color: "var(--text)", fontWeight: 600, fontFamily: "monospace" }}>{pct}%</span>
            </div>
          );
        })}
        <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid var(--border)", fontSize: 12 }}>
          <span style={{ color: "var(--text2)" }}>Total: </span>
          <span style={{ fontWeight: 700, color: "var(--text)" }}>{fmt(total)}</span>
        </div>
      </div>
    </div>
  );
}
