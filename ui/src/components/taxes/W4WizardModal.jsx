/**
 * W4WizardModal — Multi-job W-4 withholding optimizer wizard.
 *
 * Calculates the exact extra withholding (Step 4c) for each employer's W-4
 * so the household neither owes nor over-withholds at filing time.
 *
 * All wizard state is internal — parent only controls open/close.
 */
import { useState } from "react";
import { getW4WizardPrefill, runW4Wizard } from "../../api.js";

/** Whole-dollar formatter shared with Taxes page */
function fmt(v) {
  if (v == null) return "$0";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, minimumFractionDigits: 0 });
}

export default function W4WizardModal({ open, onClose }) {
  const [jobs, setJobs] = useState([]);
  const [year, setYear] = useState(2025);
  const [numChildren, setNumChildren] = useState(2);
  const [otherIncome, setOtherIncome] = useState(0);
  const [extraDeductions, setExtraDeductions] = useState(0);
  const [otherCredits, setOtherCredits] = useState(0);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  /** Fetch prefill data from uploaded W-4s/paystubs when modal first opens */
  async function loadPrefill() {
    setResult(null);
    try {
      const prefill = await getW4WizardPrefill();
      setJobs((prefill.jobs || []).map(j => ({
        employer: j.employer,
        annual_income: j.annual_income,
        pay_frequency: "biweekly",
        current_extra_per_period: j.current_extra_per_period || 0,
      })));
    } catch {
      setJobs([{ employer: "", annual_income: 0, pay_frequency: "biweekly", current_extra_per_period: 0 }]);
    }
  }

  /** Run the optimizer against the backend */
  async function calculate() {
    setLoading(true);
    setResult(null);
    try {
      const res = await runW4Wizard({
        year,
        filing_status: "married_filing_jointly",
        num_children: numChildren,
        other_income: otherIncome,
        extra_deductions: extraDeductions,
        other_credits: otherCredits,
        jobs,
      });
      setResult(res);
    } catch (err) {
      setResult({ error: err.message });
    }
    setLoading(false);
  }

  if (!open) return null;

  // Load prefill on first render when open
  if (jobs.length === 0 && !loading) loadPrefill();

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)",
      display: "flex", alignItems: "flex-start", justifyContent: "center",
      zIndex: 1000, padding: "40px 16px", overflowY: "auto",
    }} onClick={e => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" style={{
        background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 14,
        width: "100%", maxWidth: 700, padding: 28,
      }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 22 }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 18 }}>W-4 Withholding Optimizer</div>
            <div style={{ color: "var(--text2)", fontSize: 13, marginTop: 3 }}>
              Calculates the exact extra withholding (Step 4c) to enter on each employer's W-4 so you neither owe nor over-withhold at filing.
            </div>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "var(--text2)", fontSize: 22, cursor: "pointer", padding: 4 }} aria-label="Close wizard">✕</button>
        </div>

        {/* Household-level inputs */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 20 }}>
          <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4 }}>
            Tax Year
            <select value={year} onChange={e => setYear(Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }}>
              <option value={2025}>2025</option>
              <option value={2024}>2024</option>
            </select>
          </label>
          <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4 }}>
            Qualifying Children
            <input type="number" min={0} value={numChildren}
              onChange={e => setNumChildren(Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }} />
          </label>
          <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4 }}>
            Other Non-Job Income (annual)
            <input type="number" min={0} value={otherIncome}
              onChange={e => setOtherIncome(Number(e.target.value))}
              placeholder="Interest, dividends, side income…"
              style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }} />
          </label>
          <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4 }}>
            Extra Deductions Above Standard
            <input type="number" min={0} value={extraDeductions}
              onChange={e => setExtraDeductions(Number(e.target.value))}
              placeholder="Mortgage interest, charitable…"
              style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }} />
          </label>
          <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4 }}>
            Other Credits (annual)
            <input type="number" min={0} value={otherCredits}
              onChange={e => setOtherCredits(Number(e.target.value))}
              placeholder="Education, EV, solar…"
              style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }} />
          </label>
        </div>

        {/* Per-job income sources */}
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>Income Sources</div>
        {jobs.map((job, i) => (
          <div key={i} style={{ background: "var(--bg3)", border: "1px solid var(--border)", borderRadius: 8, padding: 14, marginBottom: 10 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr auto", gap: 10, alignItems: "end" }}>
              <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4 }}>
                Employer
                <input value={job.employer} onChange={e => {
                  const j = [...jobs]; j[i] = { ...j[i], employer: e.target.value }; setJobs(j);
                }} style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg2)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }} />
              </label>
              <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4 }}>
                Annual Income
                <input type="number" min={0} value={job.annual_income} onChange={e => {
                  const j = [...jobs]; j[i] = { ...j[i], annual_income: Number(e.target.value) }; setJobs(j);
                }} style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg2)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }} />
              </label>
              <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4 }}>
                Pay Frequency
                <select value={job.pay_frequency} onChange={e => {
                  const j = [...jobs]; j[i] = { ...j[i], pay_frequency: e.target.value }; setJobs(j);
                }} style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg2)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }}>
                  <option value="weekly">Weekly (52×)</option>
                  <option value="biweekly">Biweekly (26×)</option>
                  <option value="semimonthly">Semimonthly (24×)</option>
                  <option value="monthly">Monthly (12×)</option>
                </select>
              </label>
              <button onClick={() => setJobs(jobs.filter((_, idx) => idx !== i))}
                style={{ background: "rgba(248,113,113,0.15)", border: "1px solid var(--red)", color: "var(--red)", borderRadius: 6, padding: "7px 10px", cursor: "pointer", fontSize: 13, alignSelf: "end" }} aria-label="Remove income source">✕</button>
            </div>
            <div style={{ marginTop: 10 }}>
              <label style={{ fontSize: 12, color: "var(--text2)", display: "flex", flexDirection: "column", gap: 4, maxWidth: 200 }}>
                Current Step 4c (extra/period)
                <input type="number" min={0} step={0.01} value={job.current_extra_per_period} onChange={e => {
                  const j = [...jobs]; j[i] = { ...j[i], current_extra_per_period: Number(e.target.value) }; setJobs(j);
                }} style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg2)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 13 }} />
              </label>
            </div>
          </div>
        ))}
        <button onClick={() => setJobs([...jobs, { employer: "", annual_income: 0, pay_frequency: "biweekly", current_extra_per_period: 0 }])}
          style={{ background: "none", border: "1px dashed var(--border)", color: "var(--text2)", borderRadius: 8, padding: "8px 16px", cursor: "pointer", fontSize: 13, width: "100%", marginBottom: 20 }}>
          + Add Income Source
        </button>

        {/* Calculate button */}
        <button onClick={calculate} disabled={loading || jobs.length === 0}
          style={{ width: "100%", padding: "11px", borderRadius: 8, background: "var(--green)", color: "#0d2b1e", border: "none", fontWeight: 700, fontSize: 15, cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.7 : 1, marginBottom: 24 }}>
          {loading ? "Calculating…" : "Calculate Optimal Withholding"}
        </button>

        {/* Results */}
        {result && !result.error && (
          <div>
            {/* Household summary */}
            <div style={{ background: "var(--bg3)", borderRadius: 10, padding: 16, marginBottom: 16 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>
                {result.year} Household Summary — {(result.filing_status || "").replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
                {[
                  ["Total Income", fmt(result.household.total_income)],
                  ["Deduction", fmt(result.household.total_deduction)],
                  ["Taxable Income", fmt(result.household.taxable_income)],
                  ["Gross Tax", fmt(result.household.gross_tax)],
                  ["Credits", fmt(result.household.dependent_credits + result.household.other_credits)],
                  ["Net Tax Owed", fmt(result.household.net_tax)],
                  ["Effective Rate", result.household.effective_rate_pct + "%"],
                  ["Marginal Rate", result.household.marginal_rate_pct + "%"],
                ].map(([label, value]) => (
                  <div key={label}>
                    <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 2 }}>{label}</div>
                    <div style={{ fontWeight: 700, fontSize: 14 }}>{value}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Withholding gap banner */}
            {(() => {
              const gap = result.withholding.gap;
              const isOwe = gap > 0;
              return (
                <div style={{
                  background: isOwe ? "rgba(248,113,113,0.1)" : "rgba(52,211,153,0.1)",
                  border: `1px solid ${isOwe ? "var(--red)" : "var(--green)"}`,
                  borderRadius: 10, padding: 16, marginBottom: 16, textAlign: "center",
                }}>
                  <div style={{ fontSize: 13, color: "var(--text2)", marginBottom: 4 }}>
                    {isOwe ? "Projected to Owe at Filing (without changes)" : "Projected Refund (without changes)"}
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 800, color: isOwe ? "var(--red)" : "var(--green)" }}>
                    {isOwe ? "-" : "+"}{fmt(Math.abs(gap))}
                  </div>
                </div>
              );
            })()}

            {/* Per-job W-4 recommendations */}
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>Recommended W-4 Changes</div>
            {result.recommendations.map((rec, i) => (
              <div key={i} style={{ background: "var(--bg3)", border: "1px solid var(--border)", borderRadius: 10, padding: 16, marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 15 }}>{rec.employer}</div>
                    <div style={{ fontSize: 12, color: "var(--text2)" }}>{fmt(rec.annual_income)}/yr · {rec.pay_frequency} ({rec.pay_periods} pay periods)</div>
                  </div>
                  {rec.claim_dependents_here && (
                    <span style={{ background: "rgba(124,58,237,0.2)", color: "var(--purple)", borderRadius: 12, padding: "3px 10px", fontSize: 11, fontWeight: 700 }}>
                      Claim Dependents Here
                    </span>
                  )}
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
                  <div>
                    <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 2 }}>Step 3 (Dependents)</div>
                    <div style={{ fontWeight: 700, fontSize: 15, color: rec.claim_dependents_here ? "var(--purple)" : "var(--text2)" }}>
                      {rec.recommended_step3_dependents > 0 ? fmt(rec.recommended_step3_dependents) : "$0"}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 2 }}>Step 4c (Extra/Period)</div>
                    <div style={{ fontWeight: 700, fontSize: 15, color: rec.recommended_extra_per_period > 0 ? "var(--yellow)" : "var(--green)" }}>
                      {rec.recommended_extra_per_period > 0 ? fmt(rec.recommended_extra_per_period) : "$0"}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 2 }}>Change vs Current</div>
                    <div style={{ fontWeight: 700, fontSize: 15, color: rec.change_per_period > 0 ? "var(--red)" : rec.change_per_period < 0 ? "var(--green)" : "var(--text2)" }}>
                      {rec.change_per_period > 0 ? "+" : ""}{rec.change_per_period !== 0 ? fmt(rec.change_per_period) + "/period" : "No change"}
                    </div>
                  </div>
                </div>
              </div>
            ))}

            {/* Info notes from the optimizer */}
            {result.notes.length > 0 && (
              <div style={{ background: "rgba(79,142,247,0.08)", border: "1px solid rgba(79,142,247,0.3)", borderRadius: 8, padding: 14 }}>
                {result.notes.map((n, i) => (
                  <div key={i} style={{ fontSize: 13, color: "var(--text2)", marginBottom: i < result.notes.length - 1 ? 8 : 0 }}>ℹ {n}</div>
                ))}
              </div>
            )}
          </div>
        )}
        {result?.error && (
          <div style={{ color: "var(--red)", fontSize: 13, textAlign: "center" }}>Error: {result.error}</div>
        )}
      </div>
    </div>
  );
}
