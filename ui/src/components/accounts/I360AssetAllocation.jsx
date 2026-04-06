import { fmt, fmtPercent as fmtPct } from "../../utils/format.js";

const BAR_COLORS = [
  "var(--accent)", "var(--green)", "var(--blue)", "var(--orange)",
  "var(--purple)", "var(--red)", "var(--cyan)", "var(--text2)",
];

export default function I360AssetAllocation({ data }) {
  if (!data || data.length === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>No asset allocation data available.</div>;
  }

  const total = data.reduce((sum, d) => sum + (d.market_value || 0), 0);

  return (
    <div>
      {data.map((item, i) => {
        const pct = total > 0 ? (item.market_value / total) * 100 : 0;
        const color = BAR_COLORS[i % BAR_COLORS.length];
        return (
          <div key={i} style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span style={{ color: "var(--text)", fontSize: 13 }}>{item.asset_name}</span>
              <span style={{ color: "var(--text2)", fontSize: 13 }}>
                {fmt(item.market_value)} <span style={{ opacity: 0.7 }}>({fmtPct(pct)})</span>
              </span>
            </div>
            <div style={{ height: 6, borderRadius: 3, background: "var(--bg3)", overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${pct}%`, borderRadius: 3, background: color, transition: "width 0.3s" }} />
            </div>
          </div>
        );
      })}
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
        <span style={{ color: "var(--text)", fontSize: 13, fontWeight: 700 }}>Total</span>
        <span style={{ color: "var(--text)", fontSize: 13, fontWeight: 700 }}>{fmt(total)}</span>
      </div>
    </div>
  );
}
