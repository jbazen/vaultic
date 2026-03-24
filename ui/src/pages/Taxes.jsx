/**
 * Taxes.jsx — Tax history dashboard.
 * Shows parsed 1040 data year-over-year with charts and key metrics.
 * Sage is the primary interface for tax questions and planning.
 */
import { useState, useEffect, useRef } from "react";
import { getTaxSummary, uploadTaxPdf } from "../api.js";

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

  function loadSummary() {
    getTaxSummary()
      .then(setSummary)
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    loadSummary();
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

          {/* Sage prompt suggestions */}
          <div className="card">
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>Ask Sage</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {[
                "Should we itemize or take standard in 2025?",
                "Are we on track with withholding this year?",
                "How has our effective tax rate changed over the years?",
                "What can we do to reduce our tax bill?",
                "Should Jason adjust his W-4?",
                "How much more charitable giving would benefit us?",
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
