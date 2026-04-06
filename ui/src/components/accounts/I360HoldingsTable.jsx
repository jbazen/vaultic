import { fmt, fmtNum, fmtPercent as fmtPct, fmtDate } from "../../utils/format.js";

const TH = { padding: "6px 10px 8px", color: "var(--text2)", fontWeight: 600, whiteSpace: "nowrap", fontSize: 12 };
const TD = { padding: "8px 10px" };
const RIGHT = { textAlign: "right" };
const MONO = { fontFamily: "monospace", color: "var(--text2)" };

function glColor(val) {
  if (val > 0) return "var(--green)";
  if (val < 0) return "var(--red)";
  return "var(--text)";
}

function fmtGl(val) {
  if (val == null) return "—";
  return (val > 0 ? "+" : "") + fmt(val);
}

function fmtGlPct(val) {
  if (val == null) return "—";
  return (val > 0 ? "+" : "") + fmtPct(val);
}

export default function I360HoldingsTable({ holdings, totals }) {
  if (!holdings || holdings.length === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>No holdings data available.</div>;
  }

  const cols = [
    "Security", "Ticker", "Asset Class", "Qty", "Price", "Value",
    "Cost Basis", "Gain/Loss $", "Gain/Loss %",
    "Annual Income", "Yield %", "1-Day Chg",
  ];

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 1100 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {cols.map(h => (
              <th key={h} scope="col" style={{
                ...TH,
                textAlign: ["Security", "Ticker", "Asset Class"].includes(h) ? "left" : "right",
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {holdings.map((h, i) => {
            const gl = h.est_tax_cost_gain_loss_dollars;
            const glPct = h.est_tax_cost_gain_loss_pct;
            const dayChg = h.one_day_value_change_dollars;
            return (
              <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ ...TD, color: "var(--text)", maxWidth: 200, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
                  title={h.description}>{h.description || "—"}</td>
                <td style={{ ...TD, ...MONO }}>{h.symbol || "—"}</td>
                <td style={{ ...TD, color: "var(--text2)", fontSize: 12 }}>{h.primary_asset_class || h.asset_category || "—"}</td>
                <td style={{ ...TD, ...RIGHT, color: "var(--text)" }}>{fmtNum(h.quantity, 4)}</td>
                <td style={{ ...TD, ...RIGHT, color: "var(--text)" }}>{h.price != null ? fmt(h.price) : "—"}</td>
                <td style={{ ...TD, ...RIGHT, fontWeight: 600, color: "var(--text)" }}>{h.value_dollars != null ? fmt(h.value_dollars) : "—"}</td>
                <td style={{ ...TD, ...RIGHT, color: "var(--text2)" }}>{h.est_tax_cost_dollars != null ? fmt(h.est_tax_cost_dollars) : "—"}</td>
                <td style={{ ...TD, ...RIGHT, fontWeight: 600, color: glColor(gl) }}>{fmtGl(gl)}</td>
                <td style={{ ...TD, ...RIGHT, fontWeight: 600, color: glColor(glPct) }}>{fmtGlPct(glPct)}</td>
                <td style={{ ...TD, ...RIGHT, color: "var(--text2)" }}>{h.estimated_annual_income != null ? fmt(h.estimated_annual_income) : "—"}</td>
                <td style={{ ...TD, ...RIGHT, color: "var(--text2)" }}>{h.current_yield_pct != null ? fmtPct(h.current_yield_pct) : "—"}</td>
                <td style={{ ...TD, ...RIGHT, fontWeight: 600, color: glColor(dayChg) }}>{fmtGl(dayChg)}</td>
              </tr>
            );
          })}
        </tbody>
        {totals && totals.value > 0 && (
          <tfoot>
            <tr style={{ borderTop: "2px solid var(--border)" }}>
              <td colSpan={5} style={{ ...TD, fontWeight: 700, color: "var(--text)" }}>Total</td>
              <td style={{ ...TD, ...RIGHT, fontWeight: 700, color: "var(--text)" }}>{fmt(totals.value)}</td>
              <td style={{ ...TD, ...RIGHT, fontWeight: 600, color: "var(--text2)" }}>{fmt(totals.cost)}</td>
              <td style={{ ...TD, ...RIGHT, fontWeight: 700, color: glColor(totals.gain_loss) }}>{fmtGl(totals.gain_loss)}</td>
              <td />
              <td style={{ ...TD, ...RIGHT, fontWeight: 600, color: "var(--text2)" }}>{fmt(totals.annual_income)}</td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
