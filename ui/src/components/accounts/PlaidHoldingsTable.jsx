import { fmtMoney, fmtPercent as fmtPct, fmtNum } from "../../utils/format.js";

// ── Plaid Holdings Table ───────────────────────────────────────────────────────
export default function PlaidHoldingsTable({ holdings, totals }) {
  if (!holdings || holdings.length === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>No holdings data available.</div>;
  }
  // One footer row per currency: amounts in different currencies are never added together (#141).
  const shownTotals = (totals || []).filter(t => t.total_value > 0);
  const mixed = (totals || []).length > 1;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {["Security", "Ticker", "Type", "Qty", "Price", "Value", "Cost Basis", "Gain/Loss $", "Gain/Loss %", "% Assets"].map(h => (
              <th key={h} scope="col" style={{ padding: "6px 10px 8px", textAlign: h === "Security" || h === "Type" ? "left" : "right",
                color: "var(--text2)", fontWeight: 600, whiteSpace: "nowrap", fontSize: 12 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {holdings.map((h, i) => {
            const glPos = h.gain_loss_dollars > 0;
            const glNeg = h.gain_loss_dollars < 0;
            const glColor = glPos ? "var(--green)" : glNeg ? "var(--red)" : "var(--text)";
            const money = v => fmtMoney(v, h.iso_currency_code, h.unofficial_currency_code);
            return (
              <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "8px 10px", color: "var(--text)", maxWidth: 200, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={h.name}>{h.name || "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace", color: "var(--text2)" }}>{h.ticker_symbol || "—"}</td>
                <td style={{ padding: "8px 10px", color: "var(--text2)", fontSize: 12 }}>{h.security_type || "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{fmtNum(h.quantity, 4)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{money(h.institution_price)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: "var(--text)" }}>{money(h.institution_value)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{money(h.cost_basis)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: glColor }}>
                  {h.gain_loss_dollars != null ? (glPos ? "+" : "") + money(h.gain_loss_dollars) : "—"}
                </td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: glColor }}>
                  {h.gain_loss_pct != null ? (h.gain_loss_pct > 0 ? "+" : "") + fmtPct(h.gain_loss_pct) : "—"}
                </td>
                <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{fmtPct(h.pct_assets)}</td>
              </tr>
            );
          })}
        </tbody>
        {shownTotals.length > 0 && (
          <tfoot>
            {shownTotals.map((t, i) => {
              const code = t.iso_currency_code || t.unofficial_currency_code;
              return (
                <tr key={code} style={i === 0 ? { borderTop: "2px solid var(--border)" } : undefined}>
                  <td colSpan={5} style={{ padding: "8px 10px", fontWeight: 700, color: "var(--text)" }}>
                    {mixed ? `Total (${code})` : "Total"}
                  </td>
                  <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 700, color: "var(--text)" }}>
                    {fmtMoney(t.total_value, t.iso_currency_code, t.unofficial_currency_code)}
                  </td>
                  <td colSpan={4} />
                </tr>
              );
            })}
          </tfoot>
        )}
      </table>
    </div>
  );
}
