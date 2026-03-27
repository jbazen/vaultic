/**
 * Documents.jsx — Document Vault
 * Stores and organizes all financial documents year-over-year.
 * Includes tax document checklist and charitable deduction tracker.
 */
import { useState, useEffect, useRef } from "react";
import {
  getVaultYears, getVaultDocuments, getVaultChecklist, getDeductionTracker,
  uploadToVault, downloadVaultDoc, deleteVaultDoc,
} from "../api.js";
import { fmt as fmtBase } from "../utils/format.js";

const CATEGORIES = [
  { value: "tax_return",           label: "Tax Return (1040)" },
  { value: "w2",                   label: "W-2" },
  { value: "1098",                 label: "1098 Mortgage Interest" },
  { value: "1099_int",             label: "1099-INT Interest" },
  { value: "1099_div",             label: "1099-DIV Dividends" },
  { value: "1099_b",               label: "1099-B Investment Sales" },
  { value: "1099_r",               label: "1099-R Retirement" },
  { value: "1099_g",               label: "1099-G State Refund" },
  { value: "giving_statement",     label: "Charitable Giving Statement" },
  { value: "1098_sa",              label: "1098-SA HSA Distributions" },
  { value: "5498_sa",              label: "5498-SA HSA Contributions" },
  { value: "w4",                   label: "W-4 Withholding" },
  { value: "paystub",              label: "Pay Stub" },
  { value: "investment_statement", label: "Investment Statement" },
  { value: "bank_statement",       label: "Bank Statement" },
  { value: "insurance",            label: "Insurance Document" },
  { value: "other",                label: "Other" },
];

// Documents page uses whole-dollar formatting
function fmt(v) { return fmtBase(v, { maximumFractionDigits: 0, minimumFractionDigits: 0 }); }

function groupByCategory(docs) {
  const groups = {};
  for (const doc of docs) {
    const label = doc.category_label || doc.category;
    if (!groups[label]) groups[label] = [];
    groups[label].push(doc);
  }
  return groups;
}

export default function Documents() {
  const currentYear = new Date().getFullYear();
  const [years, setYears] = useState([]);
  const [selectedYear, setSelectedYear] = useState(currentYear);
  const [docs, setDocs] = useState([]);
  const [checklist, setChecklist] = useState(null);
  const [deductions, setDeductions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filesOnly, setFilesOnly] = useState(false);

  // Upload modal state
  const [showUpload, setShowUpload] = useState(false);
  const [uploadYear, setUploadYear] = useState(currentYear);
  const [uploadCategory, setUploadCategory] = useState("other");
  const [uploadIssuer, setUploadIssuer] = useState("");
  const [uploadDesc, setUploadDesc] = useState("");
  const [autoRename, setAutoRename] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState(null);
  const fileInputRef = useRef(null);

  function reload(year) {
    setLoading(true);
    Promise.all([
      getVaultDocuments(year).catch(() => []),
      getVaultChecklist(year).catch(() => null),
      getDeductionTracker(year).catch(() => null),
    ]).then(([d, c, ded]) => {
      setDocs(d);
      setChecklist(c);
      setDeductions(ded);
      setLoading(false);
    });
  }

  useEffect(() => {
    getVaultYears().then(yrs => {
      const allYears = Array.from(new Set([currentYear, currentYear - 1, ...(yrs || [])])).sort((a, b) => b - a);
      setYears(allYears);
    }).catch(() => setYears([currentYear, currentYear - 1]));
    reload(selectedYear);
  }, []);

  function selectYear(y) {
    setSelectedYear(y);
    reload(y);
  }

  async function handleUpload(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploading(true);
    setUploadMsg(null);
    const results = [];
    for (const file of files) {
      try {
        const res = await uploadToVault(file, uploadYear, uploadCategory, uploadIssuer || null, uploadDesc || null, autoRename);
        if (res.ok) {
          const label = res.category_label || uploadCategory;
          const name = res.display_name || file.name;
          results.push(autoRename ? `${name} → ${label} (${res.year})` : `${label}: saved`);
        } else {
          results.push(`${file.name}: ${res.detail || "failed"}`);
        }
      } catch (err) {
        results.push(`${file.name}: ${err.message}`);
      }
    }
    setUploadMsg(results.join(" · "));
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
    reload(selectedYear);
    if (!years.includes(uploadYear)) setYears(prev => [...prev, uploadYear].sort((a, b) => b - a));
  }

  async function handleDelete(id) {
    await deleteVaultDoc(id).catch(() => {});
    reload(selectedYear);
  }

  const filteredDocs = filesOnly ? docs.filter(d => d.has_file) : docs;
  const grouped = groupByCategory(filteredDocs);
  const fileCount = docs.filter(d => d.has_file).length;

  return (
    <div>
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2>Document Vault</h2>
          <p style={{ color: "var(--text2)", fontSize: 14, marginTop: 4 }}>
            {fileCount} PDF{fileCount !== 1 ? "s" : ""} stored · {docs.length - fileCount} data-only · {selectedYear}
          </p>
        </div>
        <button
          onClick={() => { setShowUpload(true); setUploadMsg(null); }}
          style={{ padding: "8px 16px", borderRadius: 8, background: "var(--accent)", color: "#fff", border: "none", fontSize: 13, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}
        >
          + Upload Document
        </button>
      </div>

      {/* Year selector + filter */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20, flexWrap: "wrap" }}>
        {years.map(y => (
          <button
            key={y}
            onClick={() => selectYear(y)}
            style={{
              padding: "6px 16px", borderRadius: 20, fontSize: 13, fontWeight: 600, cursor: "pointer",
              background: selectedYear === y ? "var(--accent)" : "var(--bg3)",
              color: selectedYear === y ? "#fff" : "var(--text)",
              border: selectedYear === y ? "none" : "1px solid var(--border)",
            }}
          >
            {y}
          </button>
        ))}
        <div style={{ marginLeft: "auto" }}>
          <button
            onClick={() => setFilesOnly(f => !f)}
            style={{
              padding: "6px 14px", borderRadius: 20, fontSize: 12, fontWeight: 600, cursor: "pointer",
              background: filesOnly ? "#f59e0b" : "var(--bg3)",
              color: filesOnly ? "#000" : "var(--text2)",
              border: filesOnly ? "none" : "1px solid var(--border)",
            }}
          >
            {filesOnly ? "📄 PDFs only" : "📄 PDFs only"}
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--text2)" }}>Loading…</div>
      ) : (
        <>
          {/* Tax Document Checklist */}
          {checklist && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                <div style={{ fontWeight: 700, fontSize: 16 }}>
                  {selectedYear} Tax Document Checklist
                </div>
                <div style={{ fontSize: 13, color: checklist.received_count === checklist.total_count ? "#34d399" : "#f59e0b", fontWeight: 600 }}>
                  {checklist.received_count} / {checklist.total_count} received
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {checklist.checklist.map((item, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 14px", borderRadius: 8,
                    background: item.received ? "rgba(52,211,153,0.08)" : "rgba(248,113,113,0.06)",
                    border: `1px solid ${item.received ? "rgba(52,211,153,0.2)" : "rgba(248,113,113,0.2)"}`,
                  }}>
                    <span style={{ fontSize: 18 }}>{item.received ? "✓" : "○"}</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{item.description}</div>
                      <div style={{ fontSize: 12, color: "var(--text2)" }}>{item.category_label}{item.issuer ? ` · ${item.issuer}` : ""}</div>
                    </div>
                    {!item.received && (
                      <span style={{ fontSize: 12, color: "#f87171", fontWeight: 600 }}>Missing</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Deduction Tracker */}
          {deductions && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 14 }}>
                {selectedYear} Charitable Deduction Tracker
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginBottom: deductions.transactions?.length > 0 ? 16 : 0 }}>
                {[
                  { label: "From Giving Statements", value: deductions.from_giving_statements },
                  { label: "From Transactions", value: deductions.from_transactions },
                  { label: "Best Estimate", value: deductions.combined_estimate, highlight: true },
                  { label: `${selectedYear - 1} Total`, value: deductions.prior_year_total, color: "var(--text2)" },
                ].map(item => (
                  <div key={item.label} style={{
                    background: "var(--bg3)", borderRadius: 10, padding: "12px 14px",
                    border: item.highlight ? "1px solid var(--accent)" : "1px solid var(--border)",
                  }}>
                    <div style={{ fontSize: 11, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>{item.label}</div>
                    <div style={{ fontWeight: 700, fontSize: 18, color: item.color || "inherit" }}>{fmt(item.value)}</div>
                  </div>
                ))}
              </div>
              {deductions.transactions?.length > 0 && (
                <div style={{ fontSize: 12, color: "var(--text2)" }}>
                  {deductions.transactions.length} transaction{deductions.transactions.length !== 1 ? "s" : ""} matched from budget categories
                </div>
              )}
            </div>
          )}

          {/* Document vault browser */}
          {docs.length === 0 ? (
            <div className="card" style={{ textAlign: "center", padding: "40px 24px" }}>
              <div style={{ fontSize: 36, marginBottom: 12 }}>🗄</div>
              <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 8 }}>No documents for {selectedYear}</div>
              <div style={{ color: "var(--text2)", fontSize: 14, marginBottom: 20 }}>
                Upload W-2s, 1099s, tax returns, investment statements, and more.
              </div>
              <button
                onClick={() => { setShowUpload(true); setUploadYear(selectedYear); }}
                style={{ padding: "8px 20px", borderRadius: 8, background: "var(--accent)", color: "#fff", border: "none", fontSize: 13, fontWeight: 600, cursor: "pointer" }}
              >
                Upload Documents
              </button>
            </div>
          ) : (
            Object.entries(grouped).map(([categoryLabel, categoryDocs]) => (
              <div key={categoryLabel} className="card" style={{ marginBottom: 16, padding: 0 }}>
                <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", fontWeight: 700, fontSize: 14, background: "var(--bg3)", borderRadius: "10px 10px 0 0" }}>
                  {categoryLabel}
                  <span style={{ marginLeft: 8, fontWeight: 400, color: "var(--text2)", fontSize: 12 }}>{categoryDocs.length} file{categoryDocs.length !== 1 ? "s" : ""}</span>
                </div>
                {categoryDocs.map(doc => (
                  <div key={doc.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", borderBottom: "1px solid var(--border)" }}>
                    <span style={{ fontSize: 20 }}>📄</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {doc.description || doc.original_name}
                      </div>
                      <div style={{ fontSize: 12, color: "var(--text2)" }}>
                        {doc.issuer && <span>{doc.issuer} · </span>}
                        {doc.original_name}
                        {doc.parsed ? <span style={{ marginLeft: 6, color: "#34d399", fontSize: 11 }}>● parsed</span> : ""}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                      {doc.has_file ? (
                        <button
                          onClick={() => downloadVaultDoc(doc.id, doc.original_name)}
                          style={{ padding: "4px 12px", borderRadius: 6, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 12, cursor: "pointer" }}
                        >
                          ↓ Download
                        </button>
                      ) : (
                        <span style={{ fontSize: 11, color: "var(--text2)", padding: "4px 8px" }}>data only</span>
                      )}
                      <button
                        onClick={() => handleDelete(doc.id)}
                        style={{ padding: "4px 8px", borderRadius: 6, background: "none", border: "none", color: "#f87171", fontSize: 16, cursor: "pointer" }}
                        title="Delete"
                        aria-label="Delete document"
                      >×</button>
                    </div>
                  </div>
                ))}
              </div>
            ))
          )}
        </>
      )}

      {/* Upload modal */}
      {showUpload && (
        <div
          onClick={() => !uploading && setShowUpload(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
        >
          <div
            role="dialog"
            aria-modal="true"
            onClick={e => e.stopPropagation()}
            style={{ background: "var(--bg2)", borderRadius: 14, padding: 28, width: "100%", maxWidth: 460, border: "1px solid var(--border)", maxHeight: "90vh", overflowY: "auto" }}
          >
            <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 20 }}>Upload Document</div>

            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {/* Smart rename toggle */}
              <div
                onClick={() => setAutoRename(v => !v)}
                style={{
                  display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
                  borderRadius: 8, cursor: "pointer",
                  background: autoRename ? "rgba(79,142,247,0.1)" : "var(--bg3)",
                  border: `1px solid ${autoRename ? "var(--accent)" : "var(--border)"}`,
                }}
              >
                <div style={{
                  width: 36, height: 20, borderRadius: 10, position: "relative",
                  background: autoRename ? "var(--accent)" : "var(--border)", transition: "background 0.2s",
                }}>
                  <div style={{
                    position: "absolute", top: 2, left: autoRename ? 18 : 2, width: 16, height: 16,
                    borderRadius: "50%", background: "#fff", transition: "left 0.2s",
                  }} />
                </div>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>Smart rename & auto-categorize</div>
                  <div style={{ fontSize: 11, color: "var(--text2)" }}>
                    Sage reads the PDF and fills in name, type, year, and account details automatically
                  </div>
                </div>
              </div>

              {/* Manual fields — shown when smart rename is off */}
              {!autoRename && (
                <>
                  <div>
                    <label style={{ fontSize: 12, color: "var(--text2)", display: "block", marginBottom: 4 }}>YEAR</label>
                    <input
                      type="number"
                      value={uploadYear}
                      onChange={e => setUploadYear(Number(e.target.value))}
                      style={{ width: "100%", padding: "8px 12px", borderRadius: 8, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 14, boxSizing: "border-box" }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12, color: "var(--text2)", display: "block", marginBottom: 4 }}>DOCUMENT TYPE</label>
                    <select
                      value={uploadCategory}
                      onChange={e => setUploadCategory(e.target.value)}
                      style={{ width: "100%", padding: "8px 12px", borderRadius: 8, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 14 }}
                    >
                      {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={{ fontSize: 12, color: "var(--text2)", display: "block", marginBottom: 4 }}>ISSUER (optional)</label>
                    <input
                      type="text"
                      value={uploadIssuer}
                      onChange={e => setUploadIssuer(e.target.value)}
                      placeholder="e.g. Chase, Vanguard, Rocket Mortgage"
                      style={{ width: "100%", padding: "8px 12px", borderRadius: 8, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 14, boxSizing: "border-box" }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12, color: "var(--text2)", display: "block", marginBottom: 4 }}>DESCRIPTION (optional)</label>
                    <input
                      type="text"
                      value={uploadDesc}
                      onChange={e => setUploadDesc(e.target.value)}
                      placeholder="e.g. Parker Financial Q4 2024 Statement"
                      style={{ width: "100%", padding: "8px 12px", borderRadius: 8, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 14, boxSizing: "border-box" }}
                    />
                  </div>
                </>
              )}
            </div>

            {uploadMsg && (
              <div style={{ marginTop: 12, fontSize: 13, color: "var(--text2)", maxHeight: 200, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {uploadMsg.split(" · ").map((line, i) => <div key={i} style={{ padding: "3px 0", borderBottom: i < uploadMsg.split(" · ").length - 1 ? "1px solid var(--border)" : "none" }}>{line}</div>)}
              </div>
            )}

            <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
              <input ref={fileInputRef} type="file" accept=".pdf" multiple style={{ display: "none" }} onChange={handleUpload} />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                style={{ flex: 1, padding: "10px", borderRadius: 8, background: "var(--accent)", color: "#fff", border: "none", fontSize: 14, fontWeight: 600, cursor: uploading ? "not-allowed" : "pointer", opacity: uploading ? 0.7 : 1 }}
              >
                {uploading ? "Uploading…" : "Choose Files"}
              </button>
              <button
                onClick={() => setShowUpload(false)}
                style={{ padding: "10px 20px", borderRadius: 8, background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 14, cursor: "pointer" }}
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
