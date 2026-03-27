import { useState, useEffect } from "react";
import { getRecentTransactions } from "../api.js";
import { fmtAmount } from "../utils/format.js";

function fmt(amount) {
  const { text, color } = fmtAmount(amount);
  // Map to the { str, isCredit } shape used in this file
  return { str: text.replace(/^[-+]/, ""), isCredit: amount < 0 };
}

export default function Transactions() {
  const [txns, setTxns] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getRecentTransactions(100).then(setTxns).finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div className="page-header">
        <h2>Transactions</h2>
        <p>Recent activity across all accounts</p>
      </div>

      {loading ? (
        <div style={{ color: "var(--text2)" }}>Loading…</div>
      ) : txns.length === 0 ? (
        <div className="card empty-state">
          <p>No transactions yet.</p>
          <p style={{ fontSize: "13px" }}>Connect accounts and sync to see your transaction history.</p>
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["Date", "Description", "Account", "Category", "Amount"].map(h => (
                  <th key={h} style={{
                    textAlign: h === "Amount" ? "right" : "left",
                    padding: "12px 20px",
                    fontSize: "11px",
                    fontWeight: 600,
                    color: "var(--text2)",
                    textTransform: "uppercase",
                    letterSpacing: "0.6px",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {txns.map((t, i) => {
                const { str, isCredit } = fmt(t.amount);
                return (
                  <tr
                    key={t.transaction_id}
                    style={{
                      borderBottom: i < txns.length - 1 ? "1px solid var(--border)" : "none",
                    }}
                  >
                    <td style={{ padding: "12px 20px", fontSize: "13px", color: "var(--text2)", whiteSpace: "nowrap" }}>
                      {t.date}
                    </td>
                    <td style={{ padding: "12px 20px", fontSize: "14px" }}>
                      <div>{t.merchant_name || t.name}</div>
                      {t.pending && <span style={{ fontSize: "11px", color: "var(--yellow)" }}>Pending</span>}
                    </td>
                    <td style={{ padding: "12px 20px", fontSize: "13px", color: "var(--text2)" }}>
                      {t.account_name}
                    </td>
                    <td style={{ padding: "12px 20px", fontSize: "12px", color: "var(--text2)" }}>
                      {t.category || "—"}
                    </td>
                    <td style={{
                      padding: "12px 20px",
                      fontSize: "14px",
                      fontWeight: 600,
                      textAlign: "right",
                      color: isCredit ? "var(--green)" : "var(--text)",
                    }}>
                      {isCredit ? `+${str}` : str}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
