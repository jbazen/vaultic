/**
 * EstimatedPaymentsSection — 1040-ES quarterly estimated tax payments card.
 *
 * Displays:
 *   - Status banner (green if covered, yellow if shortfall)
 *   - Quarter-by-quarter schedule with due dates and status
 *   - Safe harbor A vs B comparison tiles
 *   - Arizona state tax summary (flat 2.5%, withholding vs liability)
 *   - Informational notes from the calculator
 */
import { useState } from "react";
import { getEstimatedPayments } from "../../api.js";

/** Whole-dollar formatter */
function fmt(v) {
  if (v == null) return "$0";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, minimumFractionDigits: 0 });
}

export default function EstimatedPaymentsSection({ estPayments, setEstPayments }) {
  const [otherIncome, setOtherIncome] = useState(0);
  const [loading, setLoading] = useState(false);

  function recalculate() {
    setLoading(true);
    getEstimatedPayments(2025, otherIncome)
      .then(setEstPayments)
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div className="flex-between flex-wrap gap-10" style={{ marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 16 }}>Quarterly Estimated Payments (1040-ES)</div>
        <div className="flex-center gap-8">
          <input
            type="number"
            placeholder="Other income"
            value={otherIncome || ""}
            onChange={e => setOtherIncome(Number(e.target.value) || 0)}
            className="form-input"
            style={{ width: 130, padding: "5px 10px", fontSize: 13 }}
          />
          <button className="btn-purple" onClick={recalculate} disabled={loading}>
            {loading ? "Calculating…" : "Recalculate"}
          </button>
        </div>
      </div>
      {!estPayments ? (
        <div style={{ color: "var(--text2)", fontSize: 14, textAlign: "center", padding: "16px 0" }}>
          Upload paystubs to calculate estimated quarterly payments.
        </div>
      ) : (
        <>
          {/* Status banner — green if no payments needed, yellow if shortfall */}
          {(() => {
            const s = estPayments.projection?.shortfall || 0;
            const perQtr = estPayments.recommended_per_quarter || 0;
            if (perQtr === 0) {
              return (
                <div className="status-banner ok">
                  ✓ Your withholding covers your projected tax — no estimated payments needed.
                </div>
              );
            }
            return (
              <div className="status-banner warn">
                ⚠ Estimated payments recommended: <strong>{fmt(perQtr)}/quarter</strong>
                {" · "}Annual shortfall: <strong>{fmt(s)}</strong>
                {" · "}Method: {estPayments.recommended_method === "prior_year" ? "Prior year safe harbor" : "90% of projected tax"}
              </div>
            );
          })()}

          {/* Quarter-by-quarter schedule table */}
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr className="table-header-row">
                  {["Quarter", "Period", "Due Date", "Amount", "Status"].map(h => (
                    <th key={h} scope="col" className={`th-cell${h !== "Quarter" && h !== "Period" ? " right" : ""}`} style={{ padding: "9px 12px" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(estPayments.quarters || []).map(q => {
                  const statusColor = q.status === "past" ? "var(--text2)" : q.status === "current" ? "#f59e0b" : "var(--purple)";
                  const statusLabel = q.status === "past" ? "Past" : q.status === "current" ? `Due in ${q.days_until_due}d` : `In ${q.days_until_due}d`;
                  return (
                    <tr key={q.quarter} className="tr-row" style={{ opacity: q.status === "past" ? 0.6 : 1 }}>
                      <td className="td-cell bold" style={{ padding: "10px 12px" }}>{q.label}</td>
                      <td className="td-cell dim" style={{ padding: "10px 12px" }}>{q.period}</td>
                      <td className="td-cell right" style={{ padding: "10px 12px" }}>{q.due}</td>
                      <td className="td-cell right" style={{ padding: "10px 12px", fontWeight: 700 }}>{fmt(q.amount)}</td>
                      <td className="td-cell right" style={{ padding: "10px 12px", color: statusColor, fontWeight: 600, fontSize: 12 }}>{statusLabel}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Safe harbor A vs B comparison tiles */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
            {[
              { key: "safe_harbor_a", label: "Safe Harbor A", sub: "90% of projected tax" },
              { key: "safe_harbor_b", label: "Safe Harbor B", sub: estPayments.safe_harbor_b?.label || "Prior year" },
            ].map(sh => {
              const d = estPayments[sh.key] || {};
              const isRec = (sh.key === "safe_harbor_a" && estPayments.recommended_method === "current_year") ||
                            (sh.key === "safe_harbor_b" && estPayments.recommended_method === "prior_year");
              return (
                <div key={sh.key} style={{ background: "var(--bg3)", borderRadius: 9, padding: "11px 14px", border: isRec ? "1px solid var(--purple)" : "1px solid var(--border)" }}>
                  <div className="section-label" style={{ color: isRec ? "var(--purple)" : undefined }}>
                    {sh.label}{isRec && " ✓ recommended"}
                  </div>
                  <div className="sub-label" style={{ marginBottom: 6 }}>{sh.sub}</div>
                  <div style={{ fontWeight: 700, fontSize: 17 }}>{fmt(d.per_quarter)}<span style={{ fontSize: 12, fontWeight: 400, color: "var(--text2)" }}>/qtr</span></div>
                  <div className="sub-label">Total needed: {fmt(d.total_needed)}</div>
                </div>
              );
            })}
          </div>

          {/* Arizona state tax summary */}
          {estPayments.arizona && (
            <div style={{ background: "var(--bg3)", borderRadius: 9, padding: "11px 14px", border: "1px solid var(--border)", marginBottom: 14 }}>
              <div className="section-label">Arizona State Tax (flat {(estPayments.arizona.rate * 100).toFixed(1)}%)</div>
              <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 6 }}>
                <div>
                  <span style={{ fontSize: 12, color: "var(--text2)" }}>Projected AZ Tax: </span>
                  <strong>{fmt(estPayments.arizona.tax)}</strong>
                </div>
                <div>
                  <span style={{ fontSize: 12, color: "var(--text2)" }}>AZ Withheld: </span>
                  <strong>{fmt(estPayments.arizona.proj_state_withheld)}</strong>
                </div>
                {(() => {
                  const azShortfall = (estPayments.arizona.tax || 0) - (estPayments.arizona.proj_state_withheld || 0);
                  if (azShortfall <= 0) return (
                    <div style={{ color: "var(--green)", fontWeight: 600, fontSize: 13 }}>AZ withholding covers state tax</div>
                  );
                  return (
                    <div>
                      <span style={{ fontSize: 12, color: "var(--text2)" }}>AZ Shortfall: </span>
                      <strong style={{ color: "var(--red)" }}>{fmt(azShortfall)}</strong>
                    </div>
                  );
                })()}
              </div>
            </div>
          )}

          {/* Informational notes from the calculator */}
          {(estPayments.notes || []).length > 0 && (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: "var(--text2)", lineHeight: 1.6 }}>
              {estPayments.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
