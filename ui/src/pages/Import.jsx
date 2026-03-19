import { useState, useEffect, useRef, useCallback } from "react";
import {
  getManualEntries, addManualEntry, deleteManualEntry,
  ingestPDF, saveParsedPDF,
} from "../api.js";

// ── Shared formatter ──────────────────────────────────────────────────────────
function fmt(v, cat) {
  if (cat === "credit_score") return Math.round(v).toString();
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(v);
}

// ── Manual entry categories (non-investment assets shown in the Dashboard grid)
const MANUAL_CATEGORIES = [
  { value: "home_value",      label: "Home Value" },
  { value: "car_value",       label: "Car Value" },
  { value: "credit_score",    label: "Credit Score" },
  { value: "other_asset",     label: "Other Asset" },
  { value: "other_liability", label: "Other Liability" },
];

// ── PDF import categories (investment and liquid accounts)
const PDF_CATEGORIES = [
  { value: "invested",        label: "Invested" },
  { value: "liquid",          label: "Liquid" },
  { value: "real_estate",     label: "Real Estate" },
  { value: "vehicles",        label: "Vehicles" },
  { value: "crypto",          label: "Crypto" },
  { value: "other_asset",     label: "Other Asset" },
  { value: "other_liability", label: "Liability" },
];

// ── Tab button style ──────────────────────────────────────────────────────────
function tabStyle(active) {
  return {
    padding: "8px 22px",
    fontWeight: active ? 700 : 500,
    fontSize: 14,
    border: "none",
    borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
    background: "none",
    color: active ? "var(--accent)" : "var(--text2)",
    cursor: "pointer",
    transition: "all 0.15s",
  };
}

// ── Manual Entry Tab ──────────────────────────────────────────────────────────
function ManualTab() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: "", category: "home_value", value: "", notes: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    getManualEntries().then(setEntries).finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      await addManualEntry({
        name: form.name,
        category: form.category,
        value: parseFloat(form.value),
        notes: form.notes || null,
      });
      setForm({ name: "", category: "home_value", value: "", notes: "" });
      await load();
    } catch {
      setError("Failed to save entry.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id) {
    if (!confirm("Delete this entry?")) return;
    await deleteManualEntry(id);
    await load();
  }

  // Latest entry per category for the Current Values grid
  const latest = MANUAL_CATEGORIES.reduce((acc, { value: cat }) => {
    const found = entries
      .filter(e => e.category === cat)
      .sort((a, b) => b.entered_at.localeCompare(a.entered_at));
    if (found.length) acc[cat] = found[0];
    return acc;
  }, {});

  return (
    <div>
      {/* Current values summary grid */}
      <div className="card">
        <div className="card-title">Current Values</div>
        <div className="category-grid">
          {MANUAL_CATEGORIES.map(({ value: cat, label }) => (
            <div className="category-card" key={cat}>
              <div className="label">{label}</div>
              <div className="value">
                {latest[cat]
                  ? fmt(latest[cat].value, cat)
                  : <span style={{ color: "var(--text2)" }}>Not set</span>}
              </div>
              {latest[cat] && (
                <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 4 }}>
                  {latest[cat].entered_at}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Add / Update form */}
      <div className="card">
        <div className="card-title">Add / Update Entry</div>
        <form onSubmit={handleSubmit} style={{ maxWidth: 480 }}>
          <div className="form-group">
            <label className="form-label">Name</label>
            <input className="form-input"
              placeholder="e.g. Primary Home, Toyota Camry"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              required />
          </div>
          <div className="form-group">
            <label className="form-label">Category</label>
            <select className="form-select" value={form.category}
              onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
              {MANUAL_CATEGORIES.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">
              {form.category === "credit_score" ? "Score" : "Value ($)"}
            </label>
            <input className="form-input" type="number" min="0"
              step={form.category === "credit_score" ? "1" : "any"}
              placeholder={form.category === "credit_score" ? "750" : "0"}
              value={form.value}
              onChange={e => setForm(f => ({ ...f, value: e.target.value }))}
              required />
          </div>
          <div className="form-group">
            <label className="form-label">Notes (optional)</label>
            <input className="form-input"
              placeholder="e.g. Zillow estimate, KBB private party"
              value={form.notes}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
          </div>
          {error && <p style={{ color: "var(--red)", fontSize: 13, marginBottom: 12 }}>{error}</p>}
          <button className="btn btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save Entry"}
          </button>
        </form>
      </div>

      {/* History table */}
      {!loading && entries.length > 0 && (
        <div className="card">
          <div className="card-title">History</div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["Date", "Name", "Category", "Value", ""].map((h, i) => (
                  <th key={i} style={{
                    textAlign: i === 3 ? "right" : "left",
                    padding: "10px 16px", fontSize: 11, fontWeight: 600,
                    color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.6px",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...entries].sort((a, b) => b.entered_at.localeCompare(a.entered_at)).map((e, i) => (
                <tr key={e.id} style={{ borderBottom: i < entries.length - 1 ? "1px solid var(--border)" : "none" }}>
                  <td style={{ padding: "10px 16px", fontSize: 13, color: "var(--text2)" }}>{e.entered_at}</td>
                  <td style={{ padding: "10px 16px", fontSize: 14 }}>{e.name}</td>
                  <td style={{ padding: "10px 16px", fontSize: 13, color: "var(--text2)" }}>
                    {MANUAL_CATEGORIES.find(c => c.value === e.category)?.label ?? e.category}
                  </td>
                  <td style={{ padding: "10px 16px", fontSize: 14, fontWeight: 600, textAlign: "right" }}>
                    {fmt(e.value, e.category)}
                  </td>
                  <td style={{ padding: "10px 16px", textAlign: "right" }}>
                    <button onClick={() => handleDelete(e.id)}
                      style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 14 }}
                      title="Delete">✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── PDF Import Tab ────────────────────────────────────────────────────────────
function PDFTab() {
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [entries, setEntries] = useState([]);
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

  function onDrop(e) { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files?.[0]; if (f) processFile(f); }
  function onDragOver(e) { e.preventDefault(); setDragging(true); }
  function onDragLeave() { setDragging(false); }
  function onFileChange(e) { const f = e.target.files?.[0]; if (f) processFile(f); e.target.value = ""; }
  function updateEntry(i, field, val) { setEntries(prev => prev.map((e, idx) => idx === i ? { ...e, [field]: val } : e)); }
  function reset() { setResult(null); setEntries([]); setSaved(false); setError(""); }

  async function handleSave() {
    const toSave = entries.filter(e => e.included);
    if (!toSave.length) return;
    setLoading(true);
    try { await saveParsedPDF(toSave); setSaved(true); }
    catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  const included = entries.filter(e => e.included);

  if (saved) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
        <div style={{ fontSize: 40, marginBottom: 12 }}>✓</div>
        <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>Saved successfully</div>
        <div style={{ color: "var(--text2)", fontSize: 14, marginBottom: 24 }}>
          {included.length} {included.length === 1 ? "entry" : "entries"} added. Go to Dashboard to see your updated net worth.
        </div>
        <button className="btn btn-primary" onClick={reset}>Import another PDF</button>
      </div>
    );
  }

  if (result) {
    return (
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
              Review and edit the extracted data. Uncheck entries you don't want to save.
            </p>
            <div className="card">
              {entries.map((e, i) => (
                <div key={i} style={{
                  display: "grid", gridTemplateColumns: "auto 1fr auto auto auto",
                  gap: 10, alignItems: "center", padding: "12px 0",
                  borderBottom: i < entries.length - 1 ? "1px solid var(--border)" : "none",
                  opacity: e.included ? 1 : 0.4,
                }}>
                  <input type="checkbox" checked={e.included}
                    onChange={ev => updateEntry(i, "included", ev.target.checked)}
                    style={{ width: 16, height: 16, accentColor: "var(--accent)", cursor: "pointer" }} />
                  <input className="form-input" value={e.name}
                    onChange={ev => updateEntry(i, "name", ev.target.value)} style={{ fontSize: 14 }} />
                  <select className="form-select" value={e.category}
                    onChange={ev => updateEntry(i, "category", ev.target.value)}
                    style={{ fontSize: 13, width: 140 }}>
                    {PDF_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                  <input className="form-input" type="number" value={e.value}
                    onChange={ev => updateEntry(i, "value", parseFloat(ev.target.value) || 0)}
                    style={{ width: 130, fontSize: 14, textAlign: "right" }} />
                  <div style={{ fontSize: 12, color: "var(--text2)", maxWidth: 200, whiteSpace: "nowrap",
                    overflow: "hidden", textOverflow: "ellipsis" }} title={e.notes}>{e.notes}</div>
                </div>
              ))}
            </div>
            {error && (
              <div style={{ margin: "12px 0", padding: "10px 14px", background: "rgba(248,113,113,0.1)",
                border: "1px solid var(--red)", borderRadius: 8, color: "var(--red)", fontSize: 14 }}>{error}</div>
            )}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16 }}>
              <div style={{ color: "var(--text2)", fontSize: 13 }}>
                {included.length} of {entries.length} entries selected ·{" "}
                Total: {fmt(included.filter(e => !e.category.includes("liability")).reduce((s, e) => s + e.value, 0))}
              </div>
              <button className="btn btn-primary" onClick={handleSave} disabled={loading || included.length === 0}>
                {loading ? "Saving…" : `Save ${included.length} entr${included.length === 1 ? "y" : "ies"}`}
              </button>
            </div>
          </>
        )}
      </div>
    );
  }

  // Drop zone
  return (
    <>
      <div
        onClick={() => inputRef.current?.click()}
        onDrop={onDrop} onDragOver={onDragOver} onDragLeave={onDragLeave}
        style={{
          border: `2px dashed ${dragging ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 12, padding: "60px 40px", textAlign: "center",
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
          border: "1px solid var(--red)", borderRadius: 8, color: "var(--red)", fontSize: 14 }}>{error}</div>
      )}
      <div className="card" style={{ marginTop: 24 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Tips for best results</div>
        <ul style={{ color: "var(--text2)", fontSize: 13, lineHeight: 2, margin: 0, paddingLeft: 20 }}>
          <li>Use the most recent statement PDF from your investment portal</li>
          <li>Investor360 PDFs: download the "Portfolio Summary" or "Account Statement"</li>
          <li>Text-based PDFs work best — scanned image PDFs are not yet supported</li>
          <li>Sage will extract account names, types, and balances automatically</li>
          <li>You'll review everything before saving</li>
          <li>Upload past statements to build a performance history for each account</li>
        </ul>
      </div>
    </>
  );
}

// ── Import Page ───────────────────────────────────────────────────────────────
export default function Import() {
  const [tab, setTab] = useState("manual");

  return (
    <div>
      <div className="page-header">
        <h2>Import</h2>
        <p>Manually enter assets and liabilities, or import investment statements via PDF</p>
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: 24 }}>
        <button style={tabStyle(tab === "manual")} onClick={() => setTab("manual")}>Manual Entry</button>
        <button style={tabStyle(tab === "pdf")} onClick={() => setTab("pdf")}>PDF Import</button>
      </div>

      {tab === "manual" ? <ManualTab /> : <PDFTab />}
    </div>
  );
}
