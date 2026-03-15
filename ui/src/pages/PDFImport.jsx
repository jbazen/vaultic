import { useState, useRef, useCallback } from "react";
import { ingestPDF, saveParsedPDF } from "../api.js";

const CATEGORIES = [
  { value: "invested",        label: "Invested" },
  { value: "liquid",          label: "Liquid" },
  { value: "real_estate",     label: "Real Estate" },
  { value: "vehicles",        label: "Vehicles" },
  { value: "crypto",          label: "Crypto" },
  { value: "other_asset",     label: "Other Asset" },
  { value: "other_liability", label: "Liability" },
];

function fmt(v) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);
}

export default function PDFImport() {
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);   // { filename, parsed, pages }
  const [entries, setEntries] = useState([]);    // editable parsed entries
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  const processFile = useCallback(async (file) => {
    if (!file) return;
    setError(""); setResult(null); setSaved(false);
    setLoading(true);
    try {
      const data = await ingestPDF(file);
      setResult(data);
      setEntries(data.parsed.map(e => ({ ...e, included: true })));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) processFile(file);
  }

  function onDragOver(e) { e.preventDefault(); setDragging(true); }
  function onDragLeave() { setDragging(false); }

  function onFileChange(e) {
    const file = e.target.files?.[0];
    if (file) processFile(file);
    e.target.value = "";
  }

  function updateEntry(i, field, val) {
    setEntries(prev => prev.map((e, idx) => idx === i ? { ...e, [field]: val } : e));
  }

  async function handleSave() {
    const toSave = entries.filter(e => e.included);
    if (!toSave.length) return;
    setLoading(true);
    try {
      await saveParsedPDF(toSave);
      setSaved(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function reset() {
    setResult(null); setEntries([]); setSaved(false); setError("");
  }

  const included = entries.filter(e => e.included);

  return (
    <div>
      <div className="page-header">
        <h2>PDF Import</h2>
        <p>Upload a financial statement PDF — Sage will extract your accounts and balances</p>
      </div>

      {!result && (
        <>
          {/* Drop zone */}
          <div
            onClick={() => inputRef.current?.click()}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            style={{
              border: `2px dashed ${dragging ? "var(--accent)" : "var(--border)"}`,
              borderRadius: 12,
              padding: "60px 40px",
              textAlign: "center",
              cursor: loading ? "wait" : "pointer",
              background: dragging ? "rgba(79,142,247,0.05)" : "var(--bg2)",
              transition: "all 0.2s",
            }}
          >
            <input ref={inputRef} type="file" accept=".pdf" style={{ display: "none" }} onChange={onFileChange} />
            {loading ? (
              <>
                <div style={{ fontSize: 40, marginBottom: 12 }}>⏳</div>
                <div style={{ fontWeight: 600, color: "var(--text)" }}>Extracting and parsing PDF…</div>
                <div style={{ color: "var(--text2)", fontSize: 13, marginTop: 6 }}>Sage is reading your statement</div>
              </>
            ) : (
              <>
                <div style={{ fontSize: 40, marginBottom: 12 }}>📄</div>
                <div style={{ fontWeight: 600, fontSize: 16, color: "var(--text)", marginBottom: 6 }}>
                  Drop a PDF here or click to browse
                </div>
                <div style={{ color: "var(--text2)", fontSize: 13 }}>
                  Supports Investor360, brokerage statements, bank statements — any PDF with account data
                </div>
              </>
            )}
          </div>
          {error && (
            <div style={{ marginTop: 16, padding: "12px 16px", background: "rgba(248,113,113,0.1)",
              border: "1px solid var(--red)", borderRadius: 8, color: "var(--red)", fontSize: 14 }}>
              {error}
            </div>
          )}
          <div className="card" style={{ marginTop: 24 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>Tips for best results</div>
            <ul style={{ color: "var(--text2)", fontSize: 13, lineHeight: 2, margin: 0, paddingLeft: 20 }}>
              <li>Use the most recent statement PDF from your investment portal</li>
              <li>Investor360 PDFs: download the "Portfolio Summary" or "Account Statement"</li>
              <li>Text-based PDFs work best — scanned image PDFs are not yet supported</li>
              <li>Sage will extract account names, types, and balances automatically</li>
              <li>You'll review everything before saving</li>
            </ul>
          </div>
        </>
      )}

      {result && !saved && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
            <div>
              <div style={{ fontWeight: 700, fontSize: 16 }}>{result.filename}</div>
              <div style={{ color: "var(--text2)", fontSize: 13, marginTop: 2 }}>
                {result.pages} page{result.pages !== 1 ? "s" : ""} processed · {result.parsed.length} entries found
              </div>
            </div>
            <button className="btn btn-secondary" onClick={reset}>Upload different file</button>
          </div>

          {entries.length === 0 ? (
            <div className="card empty-state">
              <p>No accounts or balances found in this PDF.</p>
              <p style={{ fontSize: 13 }}>Try a different statement or check that the PDF contains text (not just images).</p>
            </div>
          ) : (
            <>
              <p style={{ color: "var(--text2)", fontSize: 13, marginBottom: 16 }}>
                Review and edit the extracted data below. Uncheck any entries you don't want to save.
              </p>

              <div className="card">
                {entries.map((e, i) => (
                  <div key={i} style={{
                    display: "grid",
                    gridTemplateColumns: "auto 1fr auto auto auto",
                    gap: 10, alignItems: "center",
                    padding: "12px 0",
                    borderBottom: i < entries.length - 1 ? "1px solid var(--border)" : "none",
                    opacity: e.included ? 1 : 0.4,
                  }}>
                    <input type="checkbox" checked={e.included}
                      onChange={ev => updateEntry(i, "included", ev.target.checked)}
                      style={{ width: 16, height: 16, accentColor: "var(--accent)", cursor: "pointer" }} />
                    <input
                      className="form-input"
                      value={e.name}
                      onChange={ev => updateEntry(i, "name", ev.target.value)}
                      style={{ fontSize: 14 }}
                    />
                    <select
                      className="form-select"
                      value={e.category}
                      onChange={ev => updateEntry(i, "category", ev.target.value)}
                      style={{ fontSize: 13, width: 140 }}
                    >
                      {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                    </select>
                    <input
                      className="form-input"
                      type="number"
                      value={e.value}
                      onChange={ev => updateEntry(i, "value", parseFloat(ev.target.value) || 0)}
                      style={{ width: 130, fontSize: 14, textAlign: "right" }}
                    />
                    <div style={{ fontSize: 12, color: "var(--text2)", maxWidth: 200, whiteSpace: "nowrap",
                      overflow: "hidden", textOverflow: "ellipsis" }} title={e.notes}>
                      {e.notes}
                    </div>
                  </div>
                ))}
              </div>

              {error && (
                <div style={{ margin: "12px 0", padding: "10px 14px", background: "rgba(248,113,113,0.1)",
                  border: "1px solid var(--red)", borderRadius: 8, color: "var(--red)", fontSize: 14 }}>
                  {error}
                </div>
              )}

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16 }}>
                <div style={{ color: "var(--text2)", fontSize: 13 }}>
                  {included.length} of {entries.length} entries selected ·{" "}
                  Total: {fmt(included.filter(e => !e.category.includes("liability")).reduce((s, e) => s + e.value, 0))}
                </div>
                <button
                  className="btn btn-primary"
                  onClick={handleSave}
                  disabled={loading || included.length === 0}
                >
                  {loading ? "Saving…" : `Save ${included.length} entr${included.length === 1 ? "y" : "ies"}`}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {saved && (
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>✓</div>
          <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>Saved successfully</div>
          <div style={{ color: "var(--text2)", fontSize: 14, marginBottom: 24 }}>
            {included.length} entries added to your manual assets. Go to Dashboard to see your updated net worth.
          </div>
          <button className="btn btn-primary" onClick={reset}>Import another PDF</button>
        </div>
      )}
    </div>
  );
}
