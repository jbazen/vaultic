import { useState, useEffect } from "react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
         AreaChart, Area, XAxis, YAxis, Tooltip as RechartsTooltip } from "recharts";
import { i360Holdings, i360Performance, i360ActivitySummary,
         i360AssetAllocation, i360BalanceHistory, i360Status } from "../../api.js";
import { fmt, fmtDate, fmtCompact, fmtPercent as fmtPct } from "../../utils/format.js";
import I360ActivitySummary from "../accounts/I360ActivitySummary.jsx";
import I360PerformanceTable from "../accounts/I360PerformanceTable.jsx";

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

function AssetCategoryDonut({ data }) {
  if (!data || data.length === 0) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No allocation data.</div>;

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

// ── Balance History Chart ─────────────────────────────────────────────────────

function BalanceHistoryChart({ data }) {
  if (!data || data.length === 0) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No balance history.</div>;

  const ChartTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    return (
      <div style={{ background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 14px" }}>
        <div style={{ color: "var(--text2)", fontSize: 11, marginBottom: 4 }}>{fmtDate(label)}</div>
        {payload.map(p => (
          <div key={p.dataKey} style={{ color: p.color, fontSize: 13, fontWeight: 600 }}>
            {p.name}: {fmt(p.value)}
          </div>
        ))}
      </div>
    );
  };

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="grad-mv" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.3} />
            <stop offset="95%" stopColor="var(--accent)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="balance_date" tickFormatter={s => fmtDate(s)}
          tick={{ fontSize: 10, fill: "var(--text2)" }} axisLine={false} tickLine={false}
          interval="preserveStartEnd" />
        <YAxis tickFormatter={fmtCompact}
          tick={{ fontSize: 10, fill: "var(--text2)" }} axisLine={false} tickLine={false}
          width={55} domain={["auto", "auto"]} />
        <RechartsTooltip content={<ChartTooltip />} />
        <Area type="monotone" dataKey="market_value" name="Market Value"
          stroke="var(--accent)" strokeWidth={2} fill="url(#grad-mv)" dot={false} />
        <Area type="monotone" dataKey="net_investment" name="Net Investment"
          stroke="var(--green)" strokeWidth={1.5} fill="none" dot={false}
          strokeDasharray="5 3" />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── Top Holdings Table ────────────────────────────────────────────────────────

function TopHoldingsTable({ holdings }) {
  if (!holdings || holdings.length === 0) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No holdings data.</div>;

  // Deduplicate by symbol (same fund across accounts), sum values, take top 10
  const bySymbol = {};
  for (const h of holdings) {
    const key = h.symbol || h.description;
    if (!bySymbol[key]) bySymbol[key] = { description: h.description, symbol: h.symbol, value: 0 };
    bySymbol[key].value += h.value_dollars || 0;
  }
  const top10 = Object.values(bySymbol).sort((a, b) => b.value - a.value).slice(0, 10);
  const total = holdings.reduce((s, h) => s + (h.value_dollars || 0), 0);

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

// ── Main Section ──────────────────────────────────────────────────────────────

export default function I360SummarySection() {
  const [status, setStatus] = useState(null);
  const [activity, setActivity] = useState(null);
  const [allocation, setAllocation] = useState(null);
  const [balanceHistory, setBalanceHistory] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [holdings, setHoldings] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      i360Status().catch(() => null),
      i360ActivitySummary().catch(() => null),
      i360AssetAllocation().catch(() => null),
      i360BalanceHistory().catch(() => null),
      i360Performance().catch(() => null),
      i360Holdings().catch(() => null),
    ]).then(([st, act, alloc, hist, perf, hold]) => {
      setStatus(st);
      setActivity(act);
      setAllocation(alloc);
      setBalanceHistory(hist);
      setPerformance(perf);
      setHoldings(hold);
    }).finally(() => setLoading(false));
  }, []);

  if (loading) return null;
  if (!status?.configured) return null;

  // Flatten all holdings across accounts for top 10
  const allHoldings = holdings?.accounts?.flatMap(a => a.holdings) || [];

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div className="card-title" style={{ margin: 0 }}>Parker Financial — Portfolio Summary</div>
        <div style={{ fontSize: 12, color: "var(--text2)" }}>
          {holdings?.totals?.value > 0 && (
            <span style={{ fontWeight: 700, color: "var(--text)", fontSize: 18, marginRight: 12 }}>
              {fmt(holdings.totals.value)}
            </span>
          )}
          {status.last_sync && <span>Synced {fmtDate(status.last_sync)}</span>}
          {status.stale && <span style={{ color: "var(--orange)", marginLeft: 8 }}>Stale</span>}
        </div>
      </div>

      {/* Row 1: Account Summary + Asset Category donut */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 20 }}>
        <div style={{ flex: "1 1 380px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Account Summary</div>
          <I360ActivitySummary data={activity} />
        </div>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Asset Category</div>
          <AssetCategoryDonut data={allocation} />
        </div>
      </div>

      {/* Row 2: Balance History + Top 10 Holdings */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 20 }}>
        <div style={{ flex: "1 1 400px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Balance History</div>
          <BalanceHistoryChart data={balanceHistory} />
        </div>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Top 10 Holdings</div>
          <TopHoldingsTable holdings={allHoldings} />
        </div>
      </div>

      {/* Row 3: Portfolio Performance (full width) */}
      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Portfolio Performance</div>
        <I360PerformanceTable data={performance} />
      </div>
    </div>
  );
}
