import { useState, useEffect } from "react";
import { i360MarketSummary } from "../../api.js";
import { fmt, fmtDate } from "../../utils/format.js";

export default function MarketSummaryCard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    i360MarketSummary()
      .then(d => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return null;
  if (!data || data.length === 0) return null;

  const snappedAt = data[0]?.snapped_at;

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div className="card-title" style={{ margin: 0 }}>Market Summary</div>
        {snappedAt && <span style={{ fontSize: 11, color: "var(--text2)" }}>{fmtDate(snappedAt)}</span>}
      </div>
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
        {data.map(idx => {
          const isUp = idx.net_change >= 0;
          const color = isUp ? "var(--green)" : "var(--red)";
          const isTreasury = idx.symbol?.includes("T") && idx.last_trade_amount < 20;
          return (
            <div key={idx.symbol} style={{ minWidth: 140 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text2)", marginBottom: 4 }}>
                {idx.name}
              </div>
              <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text)" }}>
                {isTreasury ? `${idx.last_trade_amount.toFixed(2)}%` : fmt(idx.last_trade_amount)}
              </div>
              <div style={{ fontSize: 12, color, fontWeight: 600 }}>
                {isUp ? "+" : ""}{isTreasury ? idx.net_change.toFixed(2) : fmt(idx.net_change)}
                <span style={{ marginLeft: 6, opacity: 0.8 }}>
                  ({isUp ? "+" : ""}{idx.percent_change.toFixed(2)}%)
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
