// ── Asset class allocation bar + legend ──────────────────────────────────────

export const ASSET_CLASS_COLORS = {
  equities:     "#4f8ef7",
  fixed_income: "#a78bfa",
  cash:         "#34d399",
  alternatives: "#fbbf24",
  other:        "#8b92a8",
};

export const ASSET_CLASS_LABELS = {
  equities: "Equities", fixed_income: "Fixed Income",
  cash: "Cash", alternatives: "Alternatives", other: "Other",
};

function fmt(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(Math.abs(v));
}

// style variants to preserve exact behavior in each page:
//   Dashboard: no marginBottom on outer div, "6px 16px" legend gap, transition on bar segments
//   Accounts:  marginBottom: 12, "4px 16px" legend gap, no transition
export default function AllocationBar({ allocation, total, style = "dashboard" }) {
  if (!total) return null;
  const entries = Object.entries(allocation).sort((a, b) => b[1] - a[1]);

  const isAccounts = style === "accounts";
  const outerStyle = isAccounts ? { marginBottom: 12 } : {};
  const legendGap = isAccounts ? "4px 16px" : "6px 16px";

  return (
    <div style={outerStyle}>
      <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", marginBottom: 8 }}>
        {entries.map(([cls, val]) => (
          <div key={cls} style={{
            width: `${(val / total * 100).toFixed(1)}%`,
            background: ASSET_CLASS_COLORS[cls] || "#8b92a8",
            ...(isAccounts ? {} : { transition: "width 0.3s" }),
          }} />
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: legendGap }}>
        {entries.map(([cls, val]) => (
          <div key={cls} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: ASSET_CLASS_COLORS[cls] || "#8b92a8" }} />
            <span style={{ color: "var(--text2)" }}>{ASSET_CLASS_LABELS[cls] || cls}</span>
            <span style={{ color: "var(--text)", fontWeight: 600 }}>{(val / total * 100).toFixed(1)}%</span>
            <span style={{ color: "var(--text2)" }}>{fmt(val)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
