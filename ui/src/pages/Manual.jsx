import { useState, useEffect } from "react";
import { getManualEntries, addManualEntry, deleteManualEntry } from "../api.js";

const CATEGORIES = [
  { value: "home_value",       label: "Home Value" },
  { value: "car_value",        label: "Car Value" },
  { value: "credit_score",     label: "Credit Score" },
  { value: "other_asset",      label: "Other Asset" },
  { value: "other_liability",  label: "Other Liability" },
];

function fmt(v, cat) {
  if (cat === "credit_score") return Math.round(v).toString();
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);
}

export default function Manual() {
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

  // Group latest per category
  const latest = CATEGORIES.reduce((acc, { value: cat }) => {
    const found = entries.filter(e => e.category === cat).sort((a, b) => b.entered_at.localeCompare(a.entered_at));
    if (found.length) acc[cat] = found[0];
    return acc;
  }, {});

  return (
    <div>
      <div className="page-header">
        <h2>Manual Entries</h2>
        <p>Home value, car, credit score, and other assets/liabilities</p>
      </div>

      {/* Current values */}
      <div className="card">
        <div className="card-title">Current Values</div>
        <div className="category-grid">
          {CATEGORIES.map(({ value: cat, label }) => (
            <div className="category-card" key={cat}>
              <div className="label">{label}</div>
              <div className="value">
                {latest[cat] ? fmt(latest[cat].value, cat) : <span style={{ color: "var(--text2)" }}>Not set</span>}
              </div>
              {latest[cat] && (
                <div style={{ fontSize: "11px", color: "var(--text2)", marginTop: "4px" }}>{latest[cat].entered_at}</div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Add entry form */}
      <div className="card">
        <div className="card-title">Add / Update Entry</div>
        <form onSubmit={handleSubmit} style={{ maxWidth: "480px" }}>
          <div className="form-group">
            <label className="form-label">Name</label>
            <input
              className="form-input"
              placeholder="e.g. Primary Home, Toyota Camry"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label">Category</label>
            <select
              className="form-select"
              value={form.category}
              onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
            >
              {CATEGORIES.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">
              {form.category === "credit_score" ? "Score" : "Value ($)"}
            </label>
            <input
              className="form-input"
              type="number"
              min="0"
              step={form.category === "credit_score" ? "1" : "1000"}
              placeholder={form.category === "credit_score" ? "750" : "0"}
              value={form.value}
              onChange={e => setForm(f => ({ ...f, value: e.target.value }))}
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label">Notes (optional)</label>
            <input
              className="form-input"
              placeholder="e.g. Zillow estimate, KBB private party"
              value={form.notes}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
            />
          </div>
          {error && <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "12px" }}>{error}</p>}
          <button className="btn btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save Entry"}
          </button>
        </form>
      </div>

      {/* History */}
      {entries.length > 0 && (
        <div className="card">
          <div className="card-title">History</div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["Date", "Name", "Category", "Value", ""].map((h, i) => (
                  <th key={i} style={{
                    textAlign: i === 3 ? "right" : "left",
                    padding: "10px 16px",
                    fontSize: "11px",
                    fontWeight: 600,
                    color: "var(--text2)",
                    textTransform: "uppercase",
                    letterSpacing: "0.6px",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...entries].sort((a, b) => b.entered_at.localeCompare(a.entered_at)).map((e, i) => (
                <tr key={e.id} style={{ borderBottom: i < entries.length - 1 ? "1px solid var(--border)" : "none" }}>
                  <td style={{ padding: "10px 16px", fontSize: "13px", color: "var(--text2)" }}>{e.entered_at}</td>
                  <td style={{ padding: "10px 16px", fontSize: "14px" }}>{e.name}</td>
                  <td style={{ padding: "10px 16px", fontSize: "13px", color: "var(--text2)" }}>
                    {CATEGORIES.find(c => c.value === e.category)?.label ?? e.category}
                  </td>
                  <td style={{ padding: "10px 16px", fontSize: "14px", fontWeight: 600, textAlign: "right" }}>
                    {fmt(e.value, e.category)}
                  </td>
                  <td style={{ padding: "10px 16px", textAlign: "right" }}>
                    <button
                      onClick={() => handleDelete(e.id)}
                      style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: "14px" }}
                      title="Delete"
                    >✕</button>
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
