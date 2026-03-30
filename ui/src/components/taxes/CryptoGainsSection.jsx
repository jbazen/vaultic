/**
 * CryptoGainsSection.jsx — Crypto capital gains tracker display.
 *
 * Shows realized crypto gains/losses by tax year with short-term vs long-term
 * breakdown for Schedule D reporting. Includes sync + recalculate actions
 * and a trade history table.
 *
 * Props:
 *   gains        — gains summary object from GET /api/crypto/gains/{year}
 *   trades       — array of trade records from GET /api/crypto/trades
 *   onSync       — callback to trigger sync + recalculate
 *   syncing      — boolean, true while sync is in progress
 */
import { fmt as fmtBase } from "../../utils/format.js";

function fmt(v) { return fmtBase(v, { maximumFractionDigits: 2, minimumFractionDigits: 2 }); }
function fmtWhole(v) { return fmtBase(v, { maximumFractionDigits: 0, minimumFractionDigits: 0 }); }

export default function CryptoGainsSection({ gains, trades, onSync, syncing }) {
  const summary = gains?.summary;
  const hasTrades = trades && trades.length > 0;
  const hasGains = summary && summary.transaction_count > 0;

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 16 }}>Crypto Capital Gains</div>
          <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 2 }}>
            FIFO cost basis &middot; Coinbase trade history
          </div>
        </div>
        <button
          onClick={onSync}
          disabled={syncing}
          style={{
            padding: "8px 16px",
            borderRadius: 8,
            background: "var(--accent)",
            color: "#fff",
            border: "none",
            fontSize: 13,
            fontWeight: 600,
            cursor: syncing ? "not-allowed" : "pointer",
            opacity: syncing ? 0.7 : 1,
          }}
        >
          {syncing ? "Syncing..." : "Sync & Calculate"}
        </button>
      </div>

      {/* Summary tiles */}
      {hasGains && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginBottom: 20 }}>
          {[
            { label: "Short-Term Net", value: summary.short_term_net, color: summary.short_term_net >= 0 },
            { label: "Long-Term Net", value: summary.long_term_net, color: summary.long_term_net >= 0 },
            { label: "Total Net Gain/Loss", value: summary.net_gain_loss, color: summary.net_gain_loss >= 0, highlight: true },
            { label: "Total Proceeds", value: summary.total_proceeds },
            { label: "Total Cost Basis", value: summary.total_cost_basis },
            { label: "Transactions", value: summary.transaction_count, raw: true },
          ].map(tile => (
            <div key={tile.label} style={{
              background: "var(--bg3)",
              borderRadius: 10,
              padding: "14px 16px",
              border: tile.highlight ? "1px solid var(--accent)" : "1px solid var(--border)",
            }}>
              <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                {tile.label}
              </div>
              <div style={{
                fontWeight: 700,
                fontSize: 18,
                color: tile.color !== undefined
                  ? (tile.color ? "var(--green)" : "var(--red)")
                  : "var(--text)",
              }}>
                {tile.raw ? tile.value : fmtWhole(tile.value)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Gains detail table */}
      {hasGains && gains.transactions && gains.transactions.length > 0 && (
        <>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>Realized Gains/Losses</div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--bg3)" }}>
                  {["Date", "Currency", "Qty", "Proceeds", "Cost Basis", "Gain/Loss", "Type"].map(h => (
                    <th key={h} scope="col" style={{
                      padding: "10px 12px",
                      textAlign: h === "Date" || h === "Currency" || h === "Type" ? "left" : "right",
                      fontWeight: 700, fontSize: 11, textTransform: "uppercase", color: "var(--text2)",
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {gains.transactions.map((g, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "10px 12px" }}>{g.sale_date}</td>
                    <td style={{ padding: "10px 12px", fontWeight: 600 }}>{g.currency}</td>
                    <td style={{ padding: "10px 12px", textAlign: "right" }}>{fmt(g.quantity)}</td>
                    <td style={{ padding: "10px 12px", textAlign: "right" }}>{fmtWhole(g.proceeds)}</td>
                    <td style={{ padding: "10px 12px", textAlign: "right" }}>{fmtWhole(g.cost_basis)}</td>
                    <td style={{
                      padding: "10px 12px", textAlign: "right", fontWeight: 700,
                      color: g.gain_loss >= 0 ? "var(--green)" : "var(--red)",
                    }}>
                      {g.gain_loss >= 0 ? "+" : ""}{fmtWhole(g.gain_loss)}
                    </td>
                    <td style={{
                      padding: "10px 12px",
                      fontSize: 11,
                      color: g.gain_type === "long_term" ? "var(--green)" : "var(--text2)",
                    }}>
                      {g.gain_type === "long_term" ? "Long" : "Short"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Trade history */}
      {hasTrades && (
        <>
          <div style={{ fontWeight: 600, fontSize: 14, marginTop: 20, marginBottom: 8 }}>
            Recent Trades ({trades.length})
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--bg3)" }}>
                  {["Date", "Pair", "Side", "Size", "Price", "Fee"].map(h => (
                    <th key={h} scope="col" style={{
                      padding: "10px 12px",
                      textAlign: h === "Date" || h === "Pair" || h === "Side" ? "left" : "right",
                      fontWeight: 700, fontSize: 11, textTransform: "uppercase", color: "var(--text2)",
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 50).map((t, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "10px 12px" }}>{(t.trade_time || "").slice(0, 10)}</td>
                    <td style={{ padding: "10px 12px", fontWeight: 600 }}>{t.product_id}</td>
                    <td style={{
                      padding: "10px 12px",
                      color: t.side === "BUY" ? "var(--green)" : "var(--red)",
                      fontWeight: 600,
                    }}>
                      {t.side}
                    </td>
                    <td style={{ padding: "10px 12px", textAlign: "right" }}>{fmt(t.size)}</td>
                    <td style={{ padding: "10px 12px", textAlign: "right" }}>{fmtWhole(t.price)}</td>
                    <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--text2)" }}>{fmt(t.fee)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Empty state */}
      {!hasTrades && !hasGains && (
        <div style={{ textAlign: "center", padding: "24px 16px", color: "var(--text2)" }}>
          No crypto trades synced yet. Click "Sync & Calculate" to fetch trades from Coinbase
          and compute FIFO capital gains.
        </div>
      )}
    </div>
  );
}
