import { fmt, fmtPercent as fmtPct } from "../../utils/format.js";

// ── Top Holdings Table ────────────────────────────────────────────────────────

// `total` is the whole portfolio when `holdings` is already cut to the top 10;
// without it the share is of the holdings passed in.
export default function TopHoldingsTable({ holdings, total: portfolioTotal }) {
  if (!holdings || holdings.length === 0) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No holdings data.</div>;

  // Deduplicate by symbol (same fund across accounts), sum values, take top 10
  const bySymbol = {};
  for (const h of holdings) {
    const key = h.symbol || h.description;
    if (!bySymbol[key]) bySymbol[key] = { description: h.description, symbol: h.symbol, value: 0 };
    bySymbol[key].value += h.value_dollars || 0;
  }
  const top10 = Object.values(bySymbol).sort((a, b) => b.value - a.value).slice(0, 10);
  const total = portfolioTotal ?? holdings.reduce((s, h) => s + (h.value_dollars || 0), 0);

  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
      <thead>
        <tr style={{ borderBottom: "1px solid var(--border)" }}>
          <th scope="col" style={{ padding: "4px 8px", textAlign: "left", color: "var(--text2)", fontWeight: 600 }}>Holding</th>
          <th scope="col" style={{ padding: "4px 8px", textAlign: "right", color: "var(--text2)", fontWeight: 600 }}>Symbol</th>
          <th scope="col" style={{ padding: "4px 8px", textAlign: "right", color: "var(--text2)", fontWeight: 600 }}>Value</th>
          <th scope="col" style={{ padding: "4px 8px", textAlign: "right", color: "var(--text2)", fontWeight: 600 }}>% Portfolio</th>
        </tr>
      </thead>
      <tbody>
        {top10.map((h, i) => (
          <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
            <td style={{ padding: "6px 8px", color: "var(--text)", maxWidth: 200, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
              title={h.description}>{h.description}</td>
            <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "monospace", color: "var(--text2)" }}>{h.symbol || "—"}</td>
            <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 600, color: "var(--text)" }}>{fmt(h.value)}</td>
            <td style={{ padding: "6px 8px", textAlign: "right", color: "var(--text2)" }}>
              {total > 0 ? fmtPct((h.value / total) * 100) : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
