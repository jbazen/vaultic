/**
 * Taxes.jsx — Tax history dashboard.
 * Shows parsed 1040 data year-over-year with charts and key metrics.
 * Sage is the primary interface for tax questions and planning.
 */
import { useState, useEffect, useRef } from "react";
import { getTaxSummary, uploadTaxPdf, getPaystubs, uploadPaystub, getTaxProjection, getW4s, uploadW4, uploadTaxDoc, getTaxDocs, deleteTaxDoc, getDraftReturn } from "../api.js";

// Currency formatter
function fmt(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(v);
}

function pct(v) {
  if (v == null) return "—";
  return `${Number(v).toFixed(1)}%`;
}

export default function Taxes() {
  const [summary, setSummary] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null); // selected year for deduction detail
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState(null);
  const fileInputRef = useRef(null);

  const [paystubs, setPaystubs] = useState([]);
  const [stubUploading, setStubUploading] = useState(false);
  const [stubMsg, setStubMsg] = useState(null);
  const stubInputRef = useRef(null);

  const [projection, setProjection] = useState(null);
  const [w4s, setW4s] = useState([]);
  const [w4Uploading, setW4Uploading] = useState(false);
  const [w4Msg, setW4Msg] = useState(null);
  const w4InputRef = useRef(null);

  const [taxYear, setTaxYear] = useState(2025);
  const [taxDocs, setTaxDocs] = useState([]);
  const [draftReturn, setDraftReturn] = useState(null);
  const [docUploading, setDocUploading] = useState(false);
  const [docMsg, setDocMsg] = useState(null);
  const docInputRef = useRef(null);

  function loadSummary() {
    getTaxSummary()
      .then(setSummary)
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    loadSummary();
    getPaystubs().then(setPaystubs).catch(() => {});
    getTaxProjection(2025).then(setProjection).catch(() => {});
    getW4s().then(setW4s).catch(() => {});
    getTaxDocs(2025).then(setTaxDocs).catch(() => {});
    getDraftReturn(2025).then(setDraftReturn).catch(() => {});
  }, []);

  // Color for refund (green) vs owed (red)
  function refundColor(row) {
    if (row.refund > 0) return "#34d399";
    if (row.owed > 0) return "#f87171";
    return "var(--text2)";
  }

  function refundText(row) {
    if (row.refund > 0) return `+${fmt(row.refund)}`;
    if (row.owed > 0) return `-${fmt(row.owed)}`;
    return "—";
  }

  async function handleDocUpload(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setDocUploading(true);
    setDocMsg(null);
    const results = [];
    for (const file of files) {
      try {
        const res = await uploadTaxDoc(file);
        if (res.ok) results.push(`${res.doc_type_label} (${res.issuer || file.name}): parsed`);
        else results.push(`${file.name}: ${res.detail || "parse failed"}`);
      } catch (err) {
        results.push(`${file.name}: ${err.message}`);
      }
    }
    setDocMsg(results.join(" · "));
    setDocUploading(false);
    getTaxDocs(taxYear).then(setTaxDocs).catch(() => {});
    getDraftReturn(taxYear).then(setDraftReturn).catch(() => {});
    if (docInputRef.current) docInputRef.current.value = "";
  }

  async function handleDeleteDoc(id) {
    await deleteTaxDoc(id).catch(() => {});
    getTaxDocs(taxYear).then(setTaxDocs).catch(() => {});
    getDraftReturn(taxYear).then(setDraftReturn).catch(() => {});
  }

  async function handleW4Upload(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setW4Uploading(true);
    setW4Msg(null);
    const results = [];
    for (const file of files) {
      try {
        const res = await uploadW4(file);
        if (res.ok) results.push(`${res.employer}: parsed`);
        else results.push(`${file.name}: ${res.detail || "parse failed"}`);
      } catch (err) {
        results.push(`${file.name}: ${err.message}`);
      }
    }
    setW4Msg(results.join(" · "));
    setW4Uploading(false);
    getW4s().then(setW4s).catch(() => {});
    if (w4InputRef.current) w4InputRef.current.value = "";
  }

  async function handleStubUpload(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setStubUploading(true);
    setStubMsg(null);
    const results = [];
    for (const file of files) {
      try {
        const res = await uploadPaystub(file);
        if (res.ok) {
          results.push(`${res.employer || file.name} (${res.pay_date}): parsed`);
        } else {
          results.push(`${file.name}: ${res.detail || "parse failed"}`);
        }
      } catch (err) {
        results.push(`${file.name}: ${err.message}`);
      }
    }
    setStubMsg(results.join(" · "));
    setStubUploading(false);
    getPaystubs().then(setPaystubs).catch(() => {});
    if (stubInputRef.current) stubInputRef.current.value = "";
  }

  async function handleFileUpload(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploading(true);
    setUploadMsg(null);
    const results = [];
    for (const file of files) {
      try {
        const res = await uploadTaxPdf(file);
        if (res.ok) {
          results.push(`${res.tax_year}: parsed successfully`);
        } else {
          results.push(`${file.name}: ${res.detail || "parse failed"}`);
        }
      } catch (err) {
        results.push(`${file.name}: ${err.message}`);
      }
    }
    setUploadMsg(results.join(" · "));
    setUploading(false);
    // Reload summary after upload
    setLoading(true);
    loadSummary();
    // Reset input so the same file can be re-uploaded if needed
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2>Tax History</h2>
          <p style={{ color: "var(--text2)", fontSize: 14, marginTop: 4 }}>
            Powered by Sage · {summary.length} year{summary.length !== 1 ? "s" : ""} on file
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {uploadMsg && (
            <span style={{ fontSize: 12, color: "var(--text2)", maxWidth: 320, textAlign: "right" }}>
              {uploadMsg}
            </span>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf"
            multiple
            style={{ display: "none" }}
            onChange={handleFileUpload}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            style={{
              padding: "8px 16px",
              borderRadius: 8,
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              fontSize: 13,
              fontWeight: 600,
              cursor: uploading ? "not-allowed" : "pointer",
              opacity: uploading ? 0.7 : 1,
              whiteSpace: "nowrap",
            }}
          >
            {uploading ? "Parsing…" : "Upload 1040 PDF"}
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--text2)" }}>Loading…</div>
      ) : summary.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{ fontSize: 40, marginBottom: 16 }}>📄</div>
          <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>No tax returns imported yet</div>
          <div style={{ color: "var(--text2)", fontSize: 14, maxWidth: 400, margin: "0 auto 24px" }}>
            Upload your 1040 PDFs to get started. Sage will parse them automatically and provide
            year-over-year analysis, withholding recommendations, and tax optimization suggestions.
          </div>
          <button
            onClick={() => fileInputRef.current?.click()}
            style={{
              padding: "10px 24px",
              borderRadius: 8,
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              fontSize: 14,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Upload 1040 PDFs
          </button>
        </div>
      ) : (
        <>
          {/* Year-over-year summary table */}
          <div className="card" style={{ padding: 0, overflowX: "auto", marginBottom: 20 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--bg3)" }}>
                  {["Year", "W-2 Income", "AGI", "Deduction", "Taxable Inc", "Total Tax", "Eff. Rate", "Withheld", "Result"].map(h => (
                    <th key={h} style={{
                      padding: "12px 16px",
                      textAlign: h === "Year" ? "left" : "right",
                      fontWeight: 700,
                      fontSize: 12,
                      textTransform: "uppercase",
                      color: "var(--text2)",
                      letterSpacing: "0.5px",
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...summary].reverse().map((row, i) => (
                  <tr
                    key={row.tax_year}
                    onClick={() => setSelected(selected === row.tax_year ? null : row.tax_year)}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      cursor: "pointer",
                      background: selected === row.tax_year
                        ? "var(--bg3)"
                        : i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.01)",
                    }}
                  >
                    <td style={{ padding: "14px 16px", fontWeight: 700, color: "var(--accent)" }}>
                      {row.tax_year}
                    </td>
                    <td style={{ padding: "14px 16px", textAlign: "right" }}>{fmt(row.wages_w2)}</td>
                    <td style={{ padding: "14px 16px", textAlign: "right" }}>{fmt(row.agi)}</td>
                    <td style={{ padding: "14px 16px", textAlign: "right" }}>
                      <span style={{
                        fontSize: 11,
                        color: row.deduction_method === "itemized" ? "#a78bfa" : "var(--text2)",
                        marginRight: 4,
                      }}>
                        {row.deduction_method === "itemized" ? "ITEM" : "STD"}
                      </span>
                      {fmt(row.deduction_amount)}
                    </td>
                    <td style={{ padding: "14px 16px", textAlign: "right" }}>{fmt(row.taxable_income)}</td>
                    <td style={{ padding: "14px 16px", textAlign: "right" }}>{fmt(row.total_tax)}</td>
                    <td style={{ padding: "14px 16px", textAlign: "right", color: "#f59e0b", fontWeight: 600 }}>
                      {pct(row.effective_rate)}
                    </td>
                    <td style={{ padding: "14px 16px", textAlign: "right" }}>{fmt(row.w2_withheld)}</td>
                    <td style={{ padding: "14px 16px", textAlign: "right", fontWeight: 700, color: refundColor(row) }}>
                      {refundText(row)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Deduction breakdown for selected year */}
          {selected && (() => {
            const row = summary.find(r => r.tax_year === selected);
            if (!row) return null;
            // Standard deduction amounts by year (MFJ)
            const stdDed =
              row.tax_year >= 2024 ? 29200 :
              row.tax_year >= 2023 ? 27700 :
              row.tax_year >= 2022 ? 25900 :
              row.tax_year >= 2021 ? 25100 :
              row.tax_year >= 2020 ? 24800 : 24400;
            return (
              <div className="card" style={{ marginBottom: 20 }}>
                <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 16 }}>
                  {selected} Deduction Detail
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 16 }}>
                  {[
                    { label: "Mortgage Interest", value: row.mortgage_interest },
                    { label: "SALT (capped $10k)", value: row.salt_deduction },
                    { label: "Charitable (Cash)", value: row.charitable_cash },
                    { label: "Charitable (Non-cash)", value: row.charitable_noncash },
                    { label: "Total Itemized", value: row.total_itemized, highlight: true },
                    { label: "Standard Deduction", value: stdDed },
                  ].map(item => (
                    <div key={item.label} style={{
                      background: "var(--bg3)",
                      borderRadius: 10,
                      padding: "14px 16px",
                      border: item.highlight ? "1px solid var(--accent)" : "1px solid var(--border)",
                    }}>
                      <div style={{
                        fontSize: 11,
                        color: "var(--text2)",
                        marginBottom: 4,
                        textTransform: "uppercase",
                        letterSpacing: "0.5px",
                      }}>
                        {item.label}
                      </div>
                      <div style={{ fontWeight: 700, fontSize: 18 }}>{fmt(item.value)}</div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}

          {/* Tax Documents */}
          <div className="card" style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 16 }}>Tax Documents — {taxYear}</div>
                <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 2 }}>W-2s, 1099s, 1098s, giving statements — upload anything</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {docMsg && <span style={{ fontSize: 12, color: "var(--text2)", maxWidth: 300 }}>{docMsg}</span>}
                <input ref={docInputRef} type="file" accept=".pdf" multiple style={{ display: "none" }} onChange={handleDocUpload} />
                <button
                  onClick={() => docInputRef.current?.click()}
                  disabled={docUploading}
                  style={{ padding: "6px 14px", borderRadius: 8, background: "var(--accent)", color: "#fff", border: "none", fontSize: 13, fontWeight: 600, cursor: docUploading ? "not-allowed" : "pointer", opacity: docUploading ? 0.7 : 1, whiteSpace: "nowrap" }}
                >
                  {docUploading ? "Parsing…" : "Upload Documents"}
                </button>
              </div>
            </div>
            {taxDocs.length === 0 ? (
              <div style={{ color: "var(--text2)", fontSize: 14, textAlign: "center", padding: "20px 0" }}>
                No documents uploaded for {taxYear}. Upload W-2s, 1099s, 1098s, and giving statements to generate your draft return.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {taxDocs.map(doc => (
                  <div key={doc.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--bg3)", borderRadius: 8, padding: "10px 14px", border: "1px solid var(--border)" }}>
                    <div>
                      <span style={{ fontWeight: 600, fontSize: 13 }}>{doc.doc_type_label}</span>
                      {doc.issuer && <span style={{ color: "var(--text2)", fontSize: 12, marginLeft: 8 }}>· {doc.issuer}</span>}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span style={{ fontSize: 12, color: "var(--text2)" }}>{doc.source_file}</span>
                      <button
                        onClick={() => handleDeleteDoc(doc.id)}
                        style={{ background: "none", border: "none", color: "#f87171", cursor: "pointer", fontSize: 16, padding: "0 4px" }}
                        title="Remove"
                      >×</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Draft Return */}
          {draftReturn && draftReturn.has_docs && (
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

              {/* Key lines */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8, marginBottom: 16 }}>
                {[
                  { label: "AGI", value: fmt(draftReturn.agi) },
                  { label: "Deduction", value: fmt(draftReturn.deductions?.amount), sub: draftReturn.deductions?.method === "itemized" ? "ITEM" : "STD" },
                  { label: "Taxable Income", value: fmt(draftReturn.taxable_income) },
                  { label: "Gross Tax", value: fmt(draftReturn.gross_tax) },
                  { label: "Child Tax Credit", value: `-${fmt(draftReturn.child_tax_credit)}`, color: "#34d399" },
                  { label: "Net Tax", value: fmt(draftReturn.net_tax), bold: true },
                  { label: "Total Withheld", value: fmt(draftReturn.withholding?.total) },
                  {
                    label: draftReturn.refund != null ? "Est. Refund" : "Est. Owed",
                    value: draftReturn.refund != null ? `+${fmt(draftReturn.refund)}` : `-${fmt(draftReturn.owed)}`,
                    color: draftReturn.refund != null ? "#34d399" : "#f87171",
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
                      {item.sub && <span style={{ marginLeft: 6, color: "#a78bfa", fontSize: 10 }}>{item.sub}</span>}
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
                      Itemized saves <strong style={{ color: "#a78bfa" }}>{fmt(draftReturn.deductions.total_itemized - draftReturn.deductions.standard_deduction)}</strong> vs standard
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 2025 Tax Projection */}
          {projection && (
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
                    color: projection.refund ? "#34d399" : "#f87171",
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
                      {item.sub && <span style={{ marginLeft: 6, color: "#a78bfa", fontSize: 10 }}>{item.sub}</span>}
                    </div>
                    <div style={{ fontWeight: 700, fontSize: 18, color: item.color || "inherit" }}>{item.value}</div>
                  </div>
                ))}
              </div>
              {projection.effective_rate && (
                <div style={{ fontSize: 13, color: "var(--text2)" }}>
                  Projected effective rate: <strong style={{ color: "#f59e0b" }}>{projection.effective_rate}%</strong>
                  {" · "}Child tax credit applied: <strong>{fmt(projection.child_tax_credit)}</strong>
                </div>
              )}
            </div>
          )}

          {/* W-4s on file */}
          <div className="card" style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
              <div style={{ fontWeight: 700, fontSize: 16 }}>W-4s on File</div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {w4Msg && <span style={{ fontSize: 12, color: "var(--text2)" }}>{w4Msg}</span>}
                <input ref={w4InputRef} type="file" accept=".pdf" multiple style={{ display: "none" }} onChange={handleW4Upload} />
                <button
                  onClick={() => w4InputRef.current?.click()}
                  disabled={w4Uploading}
                  style={{ padding: "6px 14px", borderRadius: 8, background: "#7c3aed", color: "#fff", border: "none", fontSize: 13, fontWeight: 600, cursor: w4Uploading ? "not-allowed" : "pointer", opacity: w4Uploading ? 0.7 : 1, whiteSpace: "nowrap" }}
                >
                  {w4Uploading ? "Parsing…" : "Upload W-4"}
                </button>
              </div>
            </div>
            {w4s.length === 0 ? (
              <div style={{ color: "var(--text2)", fontSize: 14, textAlign: "center", padding: "16px 0" }}>
                No W-4s uploaded yet. Upload your current W-4s to enable the withholding optimizer.
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--bg3)" }}>
                      {["Employer", "Filing Status", "Dependents Credit", "Extra/Period", "Effective Date"].map(h => (
                        <th key={h} style={{ padding: "10px 12px", textAlign: h === "Employer" ? "left" : "right", fontWeight: 700, fontSize: 11, textTransform: "uppercase", color: "var(--text2)", letterSpacing: "0.5px" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {w4s.map(w => (
                      <tr key={w.id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "12px 12px", fontWeight: 600 }}>{w.employer || "—"}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right", textTransform: "capitalize" }}>{(w.filing_status || "—").replace(/_/g, " ")}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right" }}>{fmt(w.dependents_amount)}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right", color: w.extra_withholding > 0 ? "#34d399" : "inherit" }}>{w.extra_withholding > 0 ? fmt(w.extra_withholding) : "—"}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right", color: "var(--text2)" }}>{w.effective_date || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Paystubs — YTD summary */}
          <div className="card" style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
              <div style={{ fontWeight: 700, fontSize: 16 }}>Paystubs — YTD</div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {stubMsg && (
                  <span style={{ fontSize: 12, color: "var(--text2)" }}>{stubMsg}</span>
                )}
                <input
                  ref={stubInputRef}
                  type="file"
                  accept=".pdf"
                  multiple
                  style={{ display: "none" }}
                  onChange={handleStubUpload}
                />
                <button
                  onClick={() => stubInputRef.current?.click()}
                  disabled={stubUploading}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 8,
                    background: "var(--accent)",
                    color: "#fff",
                    border: "none",
                    fontSize: 13,
                    fontWeight: 600,
                    cursor: stubUploading ? "not-allowed" : "pointer",
                    opacity: stubUploading ? 0.7 : 1,
                    whiteSpace: "nowrap",
                  }}
                >
                  {stubUploading ? "Parsing…" : "Upload Paystub"}
                </button>
              </div>
            </div>

            {paystubs.length === 0 ? (
              <div style={{ color: "var(--text2)", fontSize: 14, textAlign: "center", padding: "20px 0" }}>
                No paystubs uploaded yet. Upload a recent paystub to see YTD totals.
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--bg3)" }}>
                      {["Employer", "Pay Date", "Gross (Period)", "YTD Gross", "YTD Federal", "YTD State", "YTD SS", "YTD Medicare", "YTD Net"].map(h => (
                        <th key={h} style={{ padding: "10px 12px", textAlign: h === "Employer" ? "left" : "right", fontWeight: 700, fontSize: 11, textTransform: "uppercase", color: "var(--text2)", letterSpacing: "0.5px" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {paystubs.map(p => (
                      <tr key={p.id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "12px 12px", fontWeight: 600 }}>{p.employer || "—"}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right", color: "var(--text2)" }}>{p.pay_date || "—"}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right" }}>{fmt(p.gross_pay)}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right", fontWeight: 600 }}>{fmt(p.ytd_gross)}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right", color: "#f87171" }}>{fmt(p.ytd_federal)}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right", color: "#f87171" }}>{fmt(p.ytd_state)}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right" }}>{fmt(p.ytd_social_security)}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right" }}>{fmt(p.ytd_medicare)}</td>
                        <td style={{ padding: "12px 12px", textAlign: "right", color: "#34d399", fontWeight: 600 }}>{fmt(p.ytd_net)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Sage prompt suggestions */}
          <div className="card">
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>Ask Sage</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {[
                "Prepare my 2025 taxes — walk me through everything",
                "Should we itemize or take standard in 2025?",
                "Should Jason adjust his W-4? What should it say?",
                "Are we on track with withholding this year?",
                "How has our effective tax rate changed over the years?",
                "What can we do to reduce our 2025 tax bill?",
                "How much more charitable giving would benefit us?",
                "Compare our 2024 vs 2025 situation",
              ].map(q => (
                <button
                  key={q}
                  onClick={() => {
                    window.dispatchEvent(new CustomEvent("sage:prompt", { detail: q }));
                  }}
                  style={{
                    padding: "8px 14px",
                    borderRadius: 20,
                    background: "var(--bg3)",
                    border: "1px solid var(--border)",
                    color: "var(--text)",
                    fontSize: 13,
                    cursor: "pointer",
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
