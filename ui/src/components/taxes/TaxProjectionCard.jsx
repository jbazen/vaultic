/**
 * TaxProjectionCard — Current-year tax projection based on YTD paystub data.
 *
 * Displays:
 *   - Federal: projected gross, deduction, taxable income, tax, withholding, refund/owed
 *   - Arizona: flat 2.5% state tax, state withholding, state refund/owed
 *   - Combined: total federal + AZ tax, total withheld, net refund/owed
 */

/** Whole-dollar formatter */
function fmt(v) {
  if (v == null) return "$0";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, minimumFractionDigits: 0 });
}

export default function TaxProjectionCard({ projection }) {
  if (!projection) return null;

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 4 }}>2025 Tax Projection</div>
      <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 16 }}>
        Based on YTD data as of {projection.as_of_pay_date} · {Math.round((projection.year_fraction_elapsed || 0) * 100)}% through the year
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginBottom: 16 }}>
        {[
          { label: "Projected Gross", value: fmt(projection.proj_gross) },
          { label: "Deduction", value: fmt(projection.deduction_amount), sub: projection.deduction_method?.includes("itemized") ? "ITEM" : "STD" },
          { label: "Taxable Income", value: fmt(projection.taxable_income) },
          { label: "Est. Tax Owed", value: fmt(projection.net_tax) },
          { label: "Proj. Withheld", value: fmt(projection.proj_federal_withheld) },
          {
            label: projection.refund ? "Est. Refund" : "Est. Owed",
            value: projection.refund ? `+${fmt(projection.refund)}` : `-${fmt(projection.owed)}`,
            color: projection.refund ? "var(--green)" : "var(--red)",
            highlight: true,
          },
        ].map(item => (
          <div key={item.label} style={{
            background: "var(--bg3)",
            borderRadius: 10,
            padding: "12px 14px",
            border: item.highlight ? `1px solid ${item.color || "var(--accent)"}` : "1px solid var(--border)",
          }}>
            <div style={{ fontSize: 11, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>
              {item.label}
              {item.sub && <span style={{ marginLeft: 6, color: "var(--purple)", fontSize: 10 }}>{item.sub}</span>}
            </div>
            <div style={{ fontWeight: 700, fontSize: 18, color: item.color || "inherit" }}>{item.value}</div>
          </div>
        ))}
      </div>
      {/* Arizona State Tax */}
      {projection.arizona && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 8 }}>Arizona State Tax (flat {(projection.arizona.rate * 100).toFixed(1)}%)</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
            {[
              { label: "AZ Tax", value: fmt(projection.arizona.tax) },
              { label: "AZ Withheld", value: fmt(projection.arizona.proj_state_withheld) },
              {
                label: projection.arizona.refund != null ? "AZ Refund" : "AZ Owed",
                value: projection.arizona.refund != null ? `+${fmt(projection.arizona.refund)}` : `-${fmt(projection.arizona.owed)}`,
                color: projection.arizona.refund != null ? "var(--green)" : "var(--red)",
              },
            ].map(item => (
              <div key={item.label} style={{ background: "var(--bg3)", borderRadius: 10, padding: "12px 14px", border: "1px solid var(--border)" }}>
                <div style={{ fontSize: 11, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>{item.label}</div>
                <div style={{ fontWeight: 700, fontSize: 18, color: item.color || "inherit" }}>{item.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Combined Federal + State */}
      {projection.combined && (
        <div style={{
          background: "var(--bg3)", borderRadius: 10, padding: "12px 14px", marginBottom: 16,
          border: `1px solid ${projection.combined.refund != null ? "var(--green)" : "var(--red)"}`,
        }}>
          <div style={{ fontSize: 11, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>
            Combined Federal + AZ
          </div>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline" }}>
            <div>
              <span style={{ fontSize: 12, color: "var(--text2)" }}>Total Tax: </span>
              <strong style={{ fontSize: 16 }}>{fmt(projection.combined.total_tax)}</strong>
            </div>
            <div>
              <span style={{ fontSize: 12, color: "var(--text2)" }}>Total Withheld: </span>
              <strong style={{ fontSize: 16 }}>{fmt(projection.combined.total_withheld)}</strong>
            </div>
            <div>
              <span style={{ fontSize: 12, color: "var(--text2)" }}>
                {projection.combined.refund != null ? "Refund: " : "Owed: "}
              </span>
              <strong style={{ fontSize: 18, color: projection.combined.refund != null ? "var(--green)" : "var(--red)" }}>
                {projection.combined.refund != null ? `+${fmt(projection.combined.refund)}` : `-${fmt(projection.combined.owed)}`}
              </strong>
            </div>
          </div>
        </div>
      )}

      {projection.effective_rate && (
        <div style={{ fontSize: 13, color: "var(--text2)" }}>
          Projected federal effective rate: <strong style={{ color: "#f59e0b" }}>{projection.effective_rate}%</strong>
          {" · "}Child tax credit applied: <strong>{fmt(projection.child_tax_credit)}</strong>
        </div>
      )}
    </div>
  );
}
