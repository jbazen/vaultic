/**
 * DraftReturnCard — Calculated draft tax return from uploaded documents.
 * Shows income breakdown, key tax lines, effective rate, and itemized vs standard comparison.
 */

/** Whole-dollar formatter */
function fmt(v) {
  if (v == null) return "$0";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, minimumFractionDigits: 0 });
}

export default function DraftReturnCard({ taxYear, draftReturn }) {
  if (!draftReturn || !draftReturn.has_docs) return null;

  return (
    <div className="card" style={{ marginBottom: 20, border: "1px solid #6366f1" }}>
      <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 4 }}>Draft Return — {taxYear}</div>
      <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 16 }}>
        Calculated from uploaded documents · {Object.keys(draftReturn.doc_summary || {}).length} document type(s)
      </div>

      {/* Income breakdown */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 8 }}>Income</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8 }}>
          {[
            { label: "W-2 Wages", value: draftReturn.income?.wages },
            { label: "Interest", value: draftReturn.income?.interest },
            { label: "Dividends", value: draftReturn.income?.ordinary_dividends },
            { label: "Cap. Gains", value: draftReturn.income?.capital_gains },
            { label: "Retirement", value: draftReturn.income?.retirement_distributions },
            { label: "Total Income", value: draftReturn.income?.total, bold: true },
          ].filter(i => i.value).map(item => (
            <div key={item.label} style={{ background: "var(--bg3)", borderRadius: 8, padding: "10px 12px", border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 11, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>{item.label}</div>
              <div style={{ fontWeight: item.bold ? 700 : 500, fontSize: 15 }}>{fmt(item.value)}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Key tax lines: AGI, deduction, taxable income, credits, withholding, result */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8, marginBottom: 16 }}>
        {[
          { label: "AGI", value: fmt(draftReturn.agi) },
          { label: "Deduction", value: fmt(draftReturn.deductions?.amount), sub: draftReturn.deductions?.method === "itemized" ? "ITEM" : "STD" },
          { label: "Taxable Income", value: fmt(draftReturn.taxable_income) },
          { label: "Gross Tax", value: fmt(draftReturn.gross_tax) },
          { label: "Child Tax Credit", value: `-${fmt(draftReturn.child_tax_credit)}`, color: "var(--green)" },
          { label: "Net Tax", value: fmt(draftReturn.net_tax), bold: true },
          { label: "Total Withheld", value: fmt(draftReturn.withholding?.total) },
          {
            label: draftReturn.refund != null ? "Est. Refund" : "Est. Owed",
            value: draftReturn.refund != null ? `+${fmt(draftReturn.refund)}` : `-${fmt(draftReturn.owed)}`,
            color: draftReturn.refund != null ? "var(--green)" : "var(--red)",
            bold: true,
            highlight: true,
          },
        ].map(item => (
          <div key={item.label} style={{
            background: "var(--bg3)",
            borderRadius: 8,
            padding: "10px 12px",
            border: item.highlight ? `1px solid ${item.color}` : "1px solid var(--border)",
          }}>
            <div style={{ fontSize: 11, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>
              {item.label}
              {item.sub && <span style={{ marginLeft: 6, color: "var(--purple)", fontSize: 10 }}>{item.sub}</span>}
            </div>
            <div style={{ fontWeight: item.bold ? 700 : 500, fontSize: 15, color: item.color || "inherit" }}>{item.value}</div>
          </div>
        ))}
      </div>

      {draftReturn.effective_rate && (
        <div style={{ fontSize: 13, color: "var(--text2)" }}>
          Effective rate: <strong style={{ color: "#f59e0b" }}>{draftReturn.effective_rate}%</strong>
          {draftReturn.deductions?.method === "itemized" && (
            <span style={{ marginLeft: 12 }}>
              Itemized saves <strong style={{ color: "var(--purple)" }}>{fmt(draftReturn.deductions.total_itemized - draftReturn.deductions.standard_deduction)}</strong> vs standard
            </span>
          )}
        </div>
      )}
    </div>
  );
}
