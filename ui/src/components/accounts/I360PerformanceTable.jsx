import { fmtPercent as fmtPct } from "../../utils/format.js";

function retColor(val) {
  if (val > 0) return "var(--green)";
  if (val < 0) return "var(--red)";
  return "var(--text)";
}

function fmtRet(val) {
  if (val == null) return "—";
  return (val > 0 ? "+" : "") + fmtPct(val);
}

export default function I360PerformanceTable({ data }) {
  if (!Array.isArray(data) || data.length === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>No performance data available.</div>;
  }

  const cols = ["Period", "Portfolio", "S&P 500", "Bond Index", "T-Bill"];

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {cols.map(h => (
              <th key={h} scope="col" style={{
                padding: "6px 10px 8px",
                textAlign: h === "Period" ? "left" : "right",
                color: "var(--text2)", fontWeight: 600, whiteSpace: "nowrap", fontSize: 12,
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((r, i) => (
            <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
              <td style={{ padding: "8px 10px", color: "var(--text)", fontWeight: 500 }}>
                {r.display_name || r.time_period}
              </td>
              <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 700, color: retColor(r.portfolio_return) }}>
                {fmtRet(r.portfolio_return)}
              </td>
              <td style={{ padding: "8px 10px", textAlign: "right", color: retColor(r.benchmark_sp500) }}>
                {fmtRet(r.benchmark_sp500)}
              </td>
              <td style={{ padding: "8px 10px", textAlign: "right", color: retColor(r.benchmark_bond) }}>
                {fmtRet(r.benchmark_bond)}
              </td>
              <td style={{ padding: "8px 10px", textAlign: "right", color: retColor(r.benchmark_tbill) }}>
                {fmtRet(r.benchmark_tbill)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
