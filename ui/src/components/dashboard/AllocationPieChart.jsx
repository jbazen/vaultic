import { useState } from "react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import { isRetirementAccount } from "../../utils/accounts.js";
import { fmt } from "../../utils/format.js";

// ── Category definitions for the allocation pie chart ────────────────────────

const PIE_CATS = {
  checking:          { label: "Checking",          color: "#34d399" },
  savings:           { label: "Savings",           color: "#4f8ef7" },
  retirement:        { label: "Retirement",        color: "#a78bfa" },
  other_investments: { label: "Other Investments", color: "#22d3ee" },
  crypto:            { label: "Crypto",            color: "#fb923c" },
  liquid_cash:       { label: "Liquid Cash",       color: "#86efac" },
  real_estate:       { label: "Real Estate",       color: "#f59e0b" },
  other_assets:      { label: "Other Assets",      color: "#f87171" },
};

// Compute dollar totals per category from live account + manual entry data.
// Liabilities (credit/loan) are excluded — this chart shows where assets are, not net worth.
function computeAllocation(accounts, manualEntries) {
  const out = Object.fromEntries(Object.keys(PIE_CATS).map(k => [k, 0]));

  for (const a of accounts) {
    const val = a.current || 0;
    if (val <= 0) continue; // skip credit cards and negative balances
    if (a.type === "crypto") {
      out.crypto += val;
    } else if (a.type === "depository") {
      if (a.subtype === "checking") out.checking += val;
      else out.savings += val; // savings, money market, cd, hsa (Plaid-connected)
    } else if (a.type === "investment") {
      if (isRetirementAccount(a.subtype, a.name)) out.retirement += val;
      else out.other_investments += val;
    }
  }

  for (const e of manualEntries) {
    if (e.exclude_from_net_worth) continue;
    const val = e.value || 0;
    if (val <= 0) continue;
    if (e.category === "invested") {
      // Route retirement accounts (401k, IRA, Roth, etc.) separately from other investments
      if (isRetirementAccount(null, e.name)) out.retirement += val;
      else out.other_investments += val;
    } else if (e.category === "liquid")                                  out.liquid_cash += val;
    else if (e.category === "crypto")                                    out.crypto += val;
    else if (e.category === "home_value" || e.category === "real_estate") out.real_estate += val;
    else if (["car_value", "vehicles", "other_asset"].includes(e.category)) out.other_assets += val;
  }

  return out;
}

// Tooltip rendered by recharts on hover: shows category, dollar amount, % of view total.
function PieTooltipContent({ active, payload, total }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const pct = total > 0 ? ((d.value / total) * 100).toFixed(1) : "0.0";
  return (
    <div style={{
      background: "#171b26", border: "1px solid #2a2f3e",
      borderRadius: 8, padding: "10px 14px", pointerEvents: "none",
    }}>
      <div style={{ fontWeight: 700, color: d.color, marginBottom: 4 }}>{d.name}</div>
      <div style={{ color: "#e8eaf0", fontSize: 14, fontWeight: 600 }}>{fmt(d.value)}</div>
      <div style={{ color: "#8b92a8", fontSize: 12, marginTop: 2 }}>{pct}% of portfolio</div>
    </div>
  );
}

export default function AllocationPieChart({ accounts, manualEntries }) {
  const [view, setView] = useState("all");

  const alloc = computeAllocation(accounts, manualEntries);

  const ALL_KEYS        = Object.keys(PIE_CATS);
  const FINANCIAL_KEYS  = ["checking", "savings", "retirement", "other_investments", "crypto", "liquid_cash"];
  const activeKeys      = view === "all" ? ALL_KEYS : FINANCIAL_KEYS;

  const data = activeKeys
    .map(k => ({ key: k, name: PIE_CATS[k].label, value: alloc[k], color: PIE_CATS[k].color }))
    .filter(d => d.value > 0);

  const total = data.reduce((s, d) => s + d.value, 0);

  if (total === 0) {
    return <div style={{ color: "var(--text2)", fontSize: 13, textAlign: "center", padding: "40px 0" }}>No asset data yet.</div>;
  }

  // Selector tab style (reused from other tab bars in the app)
  const tab = active => ({
    background: active ? "var(--accent)" : "var(--bg3)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "4px 12px",
    fontSize: 12, fontWeight: active ? 700 : 400,
    cursor: "pointer",
  });

  return (
    <div>
      {/* View selector */}
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <button style={tab(view === "all")} onClick={() => setView("all")}>All Assets</button>
        <button style={tab(view === "financial")} onClick={() => setView("financial")}>Financial Only</button>
      </div>

      {/* Pie + legend side by side */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: "0 0 200px" }}>
          <ResponsiveContainer width={200} height={200}>
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                cx="50%" cy="50%"
                outerRadius={90} innerRadius={50}
                paddingAngle={2}
                strokeWidth={0}
              >
                {data.map(d => <Cell key={d.key} fill={d.color} />)}
              </Pie>
              <Tooltip content={(props) => <PieTooltipContent {...props} total={total} />} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Legend */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 5, minWidth: 140 }}>
          {data.map(d => {
            const pct = ((d.value / total) * 100).toFixed(1);
            return (
              <div key={d.key} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}>
                <div style={{ width: 10, height: 10, borderRadius: 3, background: d.color, flexShrink: 0 }} />
                <span style={{ color: "var(--text2)", flex: 1 }}>{d.name}</span>
                <span style={{ color: "var(--text)", fontWeight: 600, fontFamily: "monospace" }}>{pct}%</span>
              </div>
            );
          })}
          <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid var(--border)", fontSize: 12 }}>
            <span style={{ color: "var(--text2)" }}>Total Assets: </span>
            <span style={{ fontWeight: 700, color: "var(--text)" }}>{fmt(total)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
