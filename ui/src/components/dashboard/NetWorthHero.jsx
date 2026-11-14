import StatCard from "./StatCard.jsx";
import { fmt, fmtSigned, fmtDate } from "../../utils/format.js";

// ── Category config ───────────────────────────────────────────────────────────

const CATS = [
  { key: "liquid",       label: "Liquid",       color: "var(--green)", icon: "💵" },
  { key: "invested",     label: "Invested",     color: "var(--accent)", icon: "📈" },
  { key: "real_estate",  label: "Real Estate",  color: "var(--purple)", icon: "🏠" },
  { key: "vehicles",     label: "Vehicles",     color: "var(--yellow)", icon: "🚗" },
  { key: "crypto",       label: "Crypto",       color: "var(--orange)", icon: "₿" },
  { key: "other_assets", label: "Other Assets", color: "var(--teal)", icon: "📦" },
  { key: "liabilities",  label: "Liabilities",  color: "var(--red)", icon: "💳" },
];

// Net worth total, investable net worth, category tiles, and the compact row of
// credit score / home / car values and market rates.
export default function NetWorthHero({ nw, manualEntries, marketRates }) {
  // Latest manual entry per category
  const latestManual = manualEntries.reduce((acc, e) => {
    if (!acc[e.category] || e.entered_at > acc[e.category].entered_at) acc[e.category] = e;
    return acc;
  }, {});
  const creditScore = latestManual["credit_score"];
  const homeValue = latestManual["home_value"];
  const carValue = latestManual["car_value"];
  const total = nw?.total ?? null;

  return (
    <div className="card">
      <div style={{ display: "flex", gap: 40, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 4 }}>
        <div>
          <div className="card-title" style={{ marginBottom: 4 }}>Net Worth</div>
          <div className={`nw-total ${total > 0 ? "positive" : ""}`} style={{ marginBottom: 0 }}>
            {total != null ? fmtSigned(total) : "—"}
          </div>
        </div>
        {nw?.investable != null && (
          <div style={{ paddingBottom: 4 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 4 }}>
              Investable Net Worth
            </div>
            <div style={{ fontSize: 28, fontWeight: 700, color: "var(--accent)" }}>
              {fmt(nw.investable)}
            </div>
            <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 2 }}>
              net worth excl. home &amp; car
            </div>
          </div>
        )}
      </div>
      {nw?.snapped_at && (
        <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 6 }}>
          Last updated {fmtDate(nw.snapped_at)}
        </div>
      )}
      <div className="category-grid">
        {CATS.map(({ key, label, color, icon }) => {
          const val = nw?.[key] ?? 0;
          if (!val) return null;
          return (
            <StatCard key={key} label={label} value={val} color={color} icon={icon}
              negative={key === "liabilities"} />
          );
        })}
      </div>

      {/* Credit score, home value, car value + market rates — compact inline row */}
      {(creditScore || homeValue || carValue || marketRates.length > 0) && (
        <div style={{ display: "flex", gap: 28, flexWrap: "wrap", alignItems: "flex-start", paddingTop: 14, marginTop: 10, borderTop: "1px solid var(--border)" }}>
          {creditScore && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>Credit Score</div>
              <span style={{ fontSize: 22, fontWeight: 800, color: creditScore.value >= 750 ? "var(--green)" : creditScore.value >= 670 ? "var(--yellow)" : "var(--red)" }}>
                {creditScore.value}
              </span>
              <span style={{ fontSize: 12, color: "var(--text2)", marginLeft: 6 }}>
                {creditScore.value >= 800 ? "Exceptional" : creditScore.value >= 750 ? "Very Good" : creditScore.value >= 700 ? "Good" : creditScore.value >= 670 ? "Fair" : "Needs Work"}
              </span>
            </div>
          )}
          {homeValue && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>🏠 Home</div>
              <span style={{ fontSize: 22, fontWeight: 700, color: "var(--purple)" }}>{fmt(homeValue.value)}</span>
            </div>
          )}
          {carValue && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>🚗 Car</div>
              <span style={{ fontSize: 22, fontWeight: 700, color: "var(--yellow)" }}>{fmt(carValue.value)}</span>
            </div>
          )}
          {/* Market rates — separated by a vertical rule when other items are present */}
          {marketRates.length > 0 && (
            <>
              {(creditScore || homeValue || carValue) && (
                <div style={{ width: 1, background: "var(--border)", alignSelf: "stretch", margin: "0 4px" }} />
              )}
              {marketRates.map(r => (
                <div key={r.label}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>{r.label}</div>
                  <span style={{ fontSize: 22, fontWeight: 700, color: "#22d3ee" }}>{r.value.toFixed(2)}%</span>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
