import { useState, useEffect } from "react";
import {
  getFunds, createFund, updateFund, deleteFund,
  getFundTransactions, addFundTransaction, deleteFundTransaction,
  getSheetFundFinancials,
} from "../api.js";

// ── Formatters ────────────────────────────────────────────────────────────────
function fmt(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(Math.abs(v));
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

// ── Fund transaction history ──────────────────────────────────────────────────
function FundHistory({ fund, onChanged }) {
  const [txns, setTxns] = useState(null);
  const [addMode, setAddMode] = useState(null); // "add" | "remove" | null
  const [form, setForm] = useState({ amount: "", description: "", date: today() });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getFundTransactions(fund.id).then(setTxns);
  }, [fund.id]);

  async function handleSubmit() {
    const amount = parseFloat(form.amount);
    if (!amount || amount <= 0) return;
    setSaving(true);
    try {
      await addFundTransaction(fund.id, {
        amount: addMode === "add" ? amount : -amount,
        date: form.date,
        description: form.description || null,
      });
      setForm({ amount: "", description: "", date: today() });
      setAddMode(null);
      const updated = await getFundTransactions(fund.id);
      setTxns(updated);
      onChanged();
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id) {
    if (!confirm("Remove this transaction?")) return;
    await deleteFundTransaction(id);
    setTxns(ts => ts.filter(t => t.id !== id));
    onChanged();
  }

  return (
    <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", background: "var(--bg)" }}>
      {/* Add / Remove buttons */}
      <div style={{ display: "flex", gap: 8, marginBottom: addMode ? 12 : 0 }}>
        <button className="btn btn-primary" style={{ fontSize: 12, padding: "4px 14px" }}
          onClick={() => setAddMode(m => m === "add" ? null : "add")}>
          ＋ Add Money
        </button>
        <button className="btn btn-secondary" style={{ fontSize: 12, padding: "4px 14px" }}
          onClick={() => setAddMode(m => m === "remove" ? null : "remove")}>
          − Remove Money
        </button>
      </div>

      {/* Inline transaction form */}
      {addMode && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
          <input type="number" min="0" step="any" placeholder="Amount"
            value={form.amount} onChange={e => setForm(f => ({ ...f, amount: e.target.value }))}
            style={{ width: 120, background: "var(--bg3)", border: "1px solid var(--accent)",
              borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "5px 10px" }} />
          <input type="date" value={form.date}
            onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
            style={{ background: "var(--bg3)", border: "1px solid var(--border)",
              borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "5px 10px" }} />
          <input placeholder="Description (optional)"
            value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            style={{ flex: 1, minWidth: 140, background: "var(--bg3)", border: "1px solid var(--border)",
              borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "5px 10px" }} />
          <button className="btn btn-primary" style={{ fontSize: 12, padding: "5px 14px" }}
            onClick={handleSubmit} disabled={saving}>
            {saving ? "…" : addMode === "add" ? "Add" : "Remove"}
          </button>
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: "5px 10px" }}
            onClick={() => setAddMode(null)}>✕</button>
        </div>
      )}

      {/* Transaction history */}
      {txns === null && <div style={{ color: "var(--text2)", fontSize: 12, marginTop: 12 }}>Loading…</div>}
      {txns !== null && txns.length === 0 && (
        <div style={{ color: "var(--text2)", fontSize: 12, marginTop: 12 }}>No transactions yet.</div>
      )}
      {txns !== null && txns.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, marginTop: 12 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              {["Date", "Description", "Amount", ""].map((h, i) => (
                <th key={h} style={{ padding: "4px 8px", textAlign: i === 2 ? "right" : "left",
                  color: "var(--text2)", fontWeight: 600, fontSize: 11 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {txns.map(t => (
              <tr key={t.id} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "5px 8px", color: "var(--text2)", whiteSpace: "nowrap" }}>{t.date}</td>
                <td style={{ padding: "5px 8px", color: "var(--text)" }}>{t.description || "—"}</td>
                <td style={{ padding: "5px 8px", textAlign: "right", fontWeight: 600,
                  color: t.amount >= 0 ? "var(--green)" : "var(--red)" }}>
                  {t.amount >= 0 ? "+" : "−"}{fmt(t.amount)}
                </td>
                <td style={{ padding: "5px 8px", textAlign: "right" }}>
                  <button onClick={() => handleDelete(t.id)}
                    style={{ background: "none", border: "none", color: "var(--text2)",
                      cursor: "pointer", fontSize: 12 }}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Fund card ─────────────────────────────────────────────────────────────────
function FundCard({ fund, onUpdate }) {
  const [expanded, setExpanded] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(fund.name);
  const [editingTarget, setEditingTarget] = useState(false);
  const [targetDraft, setTargetDraft] = useState(fund.target_amount ? String(fund.target_amount) : "");

  const pct = fund.target_amount ? Math.min((fund.balance / fund.target_amount) * 100, 100) : null;
  const pctColor = pct == null ? "var(--accent)" : pct >= 100 ? "var(--green)" : pct > 60 ? "var(--accent)" : "#f59e0b";

  async function saveName() {
    if (nameDraft.trim() && nameDraft.trim() !== fund.name) {
      await updateFund(fund.id, { name: nameDraft.trim() });
      onUpdate();
    }
    setEditingName(false);
  }

  async function saveTarget() {
    const t = parseFloat(targetDraft);
    await updateFund(fund.id, { target_amount: isNaN(t) || t <= 0 ? null : t });
    setEditingTarget(false);
    onUpdate();
  }

  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      {/* Fund header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 16px",
        cursor: "pointer", background: "var(--bg2)" }}
        onClick={() => setExpanded(e => !e)}>
        <span style={{ color: "var(--text2)", fontSize: 11 }}>{expanded ? "▼" : "▶"}</span>

        {/* Name */}
        {editingName ? (
          <input value={nameDraft} onChange={e => setNameDraft(e.target.value)}
            onBlur={saveName} onKeyDown={e => { if (e.key === "Enter") saveName(); if (e.key === "Escape") setEditingName(false); }}
            autoFocus onClick={e => e.stopPropagation()}
            style={{ flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
              borderRadius: 4, color: "var(--text)", fontSize: 15, fontWeight: 700, padding: "2px 8px" }} />
        ) : (
          <span style={{ flex: 1, fontWeight: 700, fontSize: 15 }}
            onDoubleClick={e => { e.stopPropagation(); setEditingName(true); setExpanded(true); }}
            title="Double-click to rename">{fund.name}</span>
        )}

        {/* Balance */}
        <div style={{ textAlign: "right" }}>
          <div style={{ fontWeight: 700, fontSize: 16, color: fund.balance >= 0 ? "var(--text)" : "var(--red)" }}>
            {fmt(fund.balance)}
          </div>
          {/* Target */}
          {editingTarget ? (
            <input type="number" min="0" step="any" value={targetDraft}
              onChange={e => setTargetDraft(e.target.value)}
              onBlur={saveTarget} onKeyDown={e => { if (e.key === "Enter") saveTarget(); if (e.key === "Escape") setEditingTarget(false); }}
              autoFocus onClick={e => e.stopPropagation()}
              style={{ width: 100, textAlign: "right", background: "var(--bg3)", border: "1px solid var(--accent)",
                borderRadius: 4, color: "var(--text)", fontSize: 11, padding: "1px 6px" }} />
          ) : (
            <div style={{ fontSize: 11, color: "var(--text2)", cursor: "pointer" }}
              onClick={e => { e.stopPropagation(); setEditingTarget(true); setExpanded(true); }}
              title="Click to set target">
              {fund.target_amount ? `goal: ${fmt(fund.target_amount)} (${Math.round(pct)}%)` : "set goal ✎"}
            </div>
          )}
        </div>

        {/* Delete */}
        <button onClick={e => { e.stopPropagation(); if (confirm(`Delete "${fund.name}"? This cannot be undone.`)) deleteFund(fund.id).then(onUpdate); }}
          style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 13 }}
          title="Delete fund">✕</button>
      </div>

      {/* Progress bar */}
      {pct !== null && (
        <div style={{ height: 4, background: "var(--bg3)" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: pctColor, transition: "width 0.3s" }} />
        </div>
      )}

      {/* Expanded: transaction history */}
      {expanded && <FundHistory fund={fund} onChanged={onUpdate} />}
    </div>
  );
}

// ── Google Sheet viewer ───────────────────────────────────────────────────────
function SheetView() {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  // Which person column to show: "TOTAL" by default
  const [person, setPerson]   = useState("TOTAL");

  useEffect(() => {
    getSheetFundFinancials()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color: "var(--text2)", padding: "40px 0", textAlign: "center" }}>Loading sheet…</div>;
  if (error)   return <div style={{ color: "var(--red)", padding: "40px 0", textAlign: "center" }}>Failed to load: {error}</div>;
  if (!data)   return null;

  const { months, persons, categories } = data;
  const currentMonth = months[months.length - 1];

  // Available person tabs — show all found in sheet
  const personTabs = persons.filter(p => p);

  return (
    <div>
      {/* Person selector */}
      {personTabs.length > 1 && (
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          {personTabs.map(p => (
            <button key={p} onClick={() => setPerson(p)}
              style={{
                padding: "5px 16px", borderRadius: 20, fontSize: 12, fontWeight: 700,
                cursor: "pointer", border: "1px solid var(--border)",
                background: person === p ? "var(--accent)" : "var(--bg2)",
                color: person === p ? "#fff" : "var(--text2)",
              }}>
              {p}
            </button>
          ))}
        </div>
      )}

      {/* Month columns header */}
      <div style={{
        display: "grid",
        gridTemplateColumns: `1fr repeat(${months.length}, 90px)`,
        gap: 4, padding: "6px 12px",
        background: "var(--bg3)", borderRadius: 8, marginBottom: 4,
        fontSize: 10, fontWeight: 700, color: "var(--text2)", textTransform: "uppercase",
      }}>
        <div>Category</div>
        {months.map(m => (
          <div key={m} style={{ textAlign: "right", color: m === currentMonth ? "var(--accent)" : "var(--text2)" }}>
            {m}
          </div>
        ))}
      </div>

      {/* Category rows */}
      {categories.map((cat, ci) => {
        const personRow = cat.rows.find(r => r.person === person);
        if (!personRow) return null;

        const currentVal = personRow.values[currentMonth];

        return (
          <div key={ci} style={{
            display: "grid",
            gridTemplateColumns: `1fr repeat(${months.length}, 90px)`,
            gap: 4, padding: "8px 12px",
            borderBottom: "1px solid var(--border)",
            background: ci % 2 === 0 ? "var(--bg2)" : "transparent",
            borderRadius: ci % 2 === 0 ? 4 : 0,
            alignItems: "center",
          }}>
            {/* Category name */}
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>{cat.name}</div>

            {/* Values per month */}
            {months.map(m => {
              const val = personRow.values[m];
              const isCurrent = m === currentMonth;
              return (
                <div key={m} style={{
                  textAlign: "right", fontSize: 12, fontWeight: isCurrent ? 700 : 400,
                  color: val == null ? "var(--text2)"
                    : val < 0 ? "var(--red)"
                    : isCurrent ? "var(--accent)" : "var(--text)",
                }}>
                  {val == null ? "—" : `$${Math.abs(val).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                </div>
              );
            })}
          </div>
        );
      })}

      <div style={{ fontSize: 10, color: "var(--text2)", marginTop: 12, textAlign: "right" }}>
        Read-only · Current month: <strong>{currentMonth}</strong>
      </div>
    </div>
  );
}


// ── Fund Financials Page ──────────────────────────────────────────────────────
export default function FundFinancials() {
  const [tab, setTab]         = useState("sheet"); // "sheet" | "native"
  const [funds, setFunds]     = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding]   = useState(false);
  const [form, setForm]       = useState({ name: "", description: "", target_amount: "" });
  const [saving, setSaving]   = useState(false);

  async function load() {
    getFunds().then(setFunds).finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  async function handleAdd() {
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      const target = parseFloat(form.target_amount);
      await createFund({
        name: form.name.trim(),
        description: form.description.trim() || null,
        target_amount: isNaN(target) || target <= 0 ? null : target,
      });
      setForm({ name: "", description: "", target_amount: "" });
      setAdding(false);
      await load();
    } finally {
      setSaving(false);
    }
  }

  const totalBalance = funds.reduce((s, f) => s + (f.balance || 0), 0);

  return (
    <div>
      <div className="page-header">
        <h2>Fund Financials</h2>
        <p>Savings fund balances and history</p>
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: 20 }}>
        {[
          { key: "sheet",  label: "Google Sheet" },
          { key: "native", label: "Native Funds" },
        ].map(({ key, label }) => (
          <button key={key} onClick={() => setTab(key)}
            style={{
              padding: "8px 20px", fontSize: 13, fontWeight: tab === key ? 700 : 400,
              background: "none", border: "none", cursor: "pointer",
              borderBottom: `2px solid ${tab === key ? "var(--accent)" : "transparent"}`,
              color: tab === key ? "var(--text)" : "var(--text2)",
            }}>
            {label}
          </button>
        ))}
      </div>

      {tab === "sheet" && <SheetView />}
      {tab === "native" && (<>

      {/* Total banner */}
      {!loading && funds.length > 0 && (
        <div className="card" style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "14px 20px", marginBottom: 4 }}>
          <span style={{ color: "var(--text2)", fontSize: 13, fontWeight: 600 }}>Total across all funds</span>
          <span style={{ fontWeight: 700, fontSize: 22, color: totalBalance >= 0 ? "var(--green)" : "var(--red)" }}>
            {fmt(totalBalance)}
          </span>
        </div>
      )}

      {loading && <div style={{ color: "var(--text2)", padding: "40px 0", textAlign: "center" }}>Loading…</div>}

      {!loading && funds.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{ fontSize: 40, marginBottom: 16 }}>💰</div>
          <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>No funds yet</div>
          <div style={{ color: "var(--text2)", fontSize: 14, marginBottom: 24 }}>
            Create named savings buckets for anything you're saving toward — vacation,
            holiday gifts, car repairs, or any recurring planned expense.
          </div>
          <button className="btn btn-primary" onClick={() => setAdding(true)}>Create your first fund</button>
        </div>
      )}

      {!loading && funds.map(fund => (
        <FundCard key={fund.id} fund={fund} onUpdate={load} />
      ))}

      {/* Add fund form */}
      {adding ? (
        <div className="card" style={{ marginTop: 8 }}>
          <div className="card-title">New Fund</div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div style={{ flex: "2 1 160px" }}>
              <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 4 }}>Fund Name *</div>
              <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Vacation, Holiday Gifts, Car Repair"
                onKeyDown={e => e.key === "Enter" && handleAdd()}
                style={{ width: "100%", background: "var(--bg3)", border: "1px solid var(--accent)",
                  borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "6px 12px" }} />
            </div>
            <div style={{ flex: "1 1 120px" }}>
              <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 4 }}>Goal Amount (optional)</div>
              <input type="number" min="0" step="any" value={form.target_amount}
                onChange={e => setForm(f => ({ ...f, target_amount: e.target.value }))}
                placeholder="0.00"
                style={{ width: "100%", background: "var(--bg3)", border: "1px solid var(--border)",
                  borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "6px 12px" }} />
            </div>
            <div style={{ flex: "2 1 180px" }}>
              <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 4 }}>Description (optional)</div>
              <input value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="What is this fund for?"
                style={{ width: "100%", background: "var(--bg3)", border: "1px solid var(--border)",
                  borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "6px 12px" }} />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn-primary" style={{ padding: "6px 18px" }} onClick={handleAdd} disabled={saving}>
                {saving ? "…" : "Create"}
              </button>
              <button className="btn btn-secondary" style={{ padding: "6px 14px" }} onClick={() => setAdding(false)}>Cancel</button>
            </div>
          </div>
        </div>
      ) : (
        !loading && (
          <button className="btn btn-secondary" style={{ width: "100%", padding: 10, marginTop: 8 }}
            onClick={() => setAdding(true)}>+ New fund</button>
        )
      )}
      </>)}
    </div>
  );
}
