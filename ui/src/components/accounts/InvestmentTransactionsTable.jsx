import { fmt, fmtNum } from "../../utils/format.js";

// ── Investment Transactions Table ──────────────────────────────────────────────
export default function InvestmentTransactionsTable({ transactions }) {
  if (!transactions || transactions.length === 0) {
    return (
      <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>
        No investment transactions found. Many 401k and IRA providers don't support
        transaction history via Plaid — holdings and balance history still sync normally.
      </div>
    );
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {["Date", "Type", "Subtype", "Security", "Ticker", "Qty", "Amount", "Fees"].map(h => (
              <th key={h} scope="col" style={{ padding: "6px 10px 8px",
                textAlign: ["Date", "Security", "Type", "Subtype"].includes(h) ? "left" : "right",
                color: "var(--text2)", fontWeight: 600, whiteSpace: "nowrap", fontSize: 12 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {transactions.map((t, i) => (
            <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
              <td style={{ padding: "8px 10px", color: "var(--text2)", whiteSpace: "nowrap" }}>{t.date}</td>
              <td style={{ padding: "8px 10px", color: "var(--text)" }}>{t.type || "—"}</td>
              <td style={{ padding: "8px 10px", color: "var(--text2)", fontSize: 12 }}>{t.subtype || "—"}</td>
              <td style={{ padding: "8px 10px", color: "var(--text)", maxWidth: 180, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={t.security_name}>{t.security_name || t.name || "—"}</td>
              <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace", color: "var(--text2)" }}>{t.ticker || "—"}</td>
              <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text)" }}>{fmtNum(t.quantity, 4)}</td>
              <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600, color: "var(--text)" }}>{t.amount != null ? fmt(t.amount) : "—"}</td>
              <td style={{ padding: "8px 10px", textAlign: "right", color: "var(--text2)" }}>{t.fees != null ? fmt(t.fees) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
