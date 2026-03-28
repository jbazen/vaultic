/**
 * EstimatedPaymentsSection — 1040-ES quarterly estimated tax payments card.
 * Shows status banner, quarterly schedule, safe harbor comparison, and notes.
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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
        <div style={{ fontWeight: 700, fontSize: 16 }}>Quarterly Estimated Payments (1040-ES)</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="number"
            placeholder="Other income"
            value={otherIncome || ""}
            onChange={e => setOtherIncome(Number(e.target.value) || 0)}
            style={{ width: 130, padding: "5px 10px", borderRadius: 7, border: "1px solid var(--border)", background: "var(--bg3)", color: "inherit", fontSize: 13 }}
          />
          <button
            onClick={recalculate}
            disabled={loading}
            style={{ padding: "5px 12px", borderRadius: 7, background: "#7c3aed", color: "#fff", border: "none", fontSize: 13, fontWeight: 600, cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.7 : 1 }}
          >
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
                <div style={{ background: "rgba(52,211,153,0.12)", border: "1px solid var(--green)", borderRadius: 10, padding: "10px 14px", marginBottom: 14, fontSize: 13, color: "var(--green)", fontWeight: 600 }}>
                  ✓ Your withholding covers your projected tax — no estimated payments needed.
                </div>
              );
            }
            return (
              <div style={{ background: "rgba(251,191,36,0.12)", border: "1px solid #f59e0b", borderRadius: 10, padding: "10px 14px", marginBottom: 14, fontSize: 13, color: "#f59e0b", fontWeight: 600 }}>
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
                <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--bg3)" }}>
                  {["Quarter", "Period", "Due Date", "Amount", "Status"].map(h => (
                    <th key={h} scope="col" style={{ padding: "9px 12px", textAlign: h === "Quarter" || h === "Period" ? "left" : "right", fontWeight: 700, fontSize: 11, textTransform: "uppercase", color: "var(--text2)", letterSpacing: "0.5px" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(estPayments.quarters || []).map(q => {
                  const statusColor = q.status === "past" ? "var(--text2)" : q.status === "current" ? "#f59e0b" : "var(--purple)";
                  const statusLabel = q.status === "past" ? "Past" : q.status === "current" ? `Due in ${q.days_until_due}d` : `In ${q.days_until_due}d`;
                  return (
                    <tr key={q.quarter} style={{ borderBottom: "1px solid var(--border)", opacity: q.status === "past" ? 0.6 : 1 }}>
                      <td style={{ padding: "10px 12px", fontWeight: 600 }}>{q.label}</td>
                      <td style={{ padding: "10px 12px", color: "var(--text2)" }}>{q.period}</td>
                      <td style={{ padding: "10px 12px", textAlign: "right" }}>{q.due}</td>
                      <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 700 }}>{fmt(q.amount)}</td>
                      <td style={{ padding: "10px 12px", textAlign: "right", color: statusColor, fontWeight: 600, fontSize: 12 }}>{statusLabel}</td>
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
                  <div style={{ fontSize: 11, color: isRec ? "var(--purple)" : "var(--text2)", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>
                    {sh.label}{isRec && " ✓ recommended"}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 6 }}>{sh.sub}</div>
                  <div style={{ fontWeight: 700, fontSize: 17 }}>{fmt(d.per_quarter)}<span style={{ fontSize: 12, fontWeight: 400, color: "var(--text2)" }}>/qtr</span></div>
                  <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 2 }}>Total needed: {fmt(d.total_needed)}</div>
                </div>
              );
            })}
          </div>

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
