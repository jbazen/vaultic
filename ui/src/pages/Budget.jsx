import { useState, useEffect, useRef } from "react";
import {
  getBudget, seedBudgetTemplate, importBudgetCSV, importBudgetJSON,
  createBudgetGroup, updateBudgetGroup, deleteBudgetGroup,
  createBudgetItem, updateBudgetItem, deleteBudgetItem,
  setBudgetAmount, getUnassignedTransactions, assignTransaction, unassignTransaction,
} from "../api.js";

// ── Formatters ────────────────────────────────────────────────────────────────
function fmt(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(Math.abs(v));
}

function fmtSigned(v) {
  if (v == null) return "—";
  const s = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(Math.abs(v));
  return v < 0 ? `-${s}` : s;
}

function monthLabel(m) {
  const [y, mo] = m.split("-");
  return new Date(parseInt(y), parseInt(mo) - 1, 1)
    .toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

function prevMonth(m) {
  const [y, mo] = m.split("-").map(Number);
  const d = new Date(y, mo - 2, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function nextMonth(m) {
  const [y, mo] = m.split("-").map(Number);
  const d = new Date(y, mo, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

// ── Inline editable amount cell ───────────────────────────────────────────────
function AmountCell({ value, onSave }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef(null);

  function startEdit() {
    setDraft(value > 0 ? String(value) : "");
    setEditing(true);
    setTimeout(() => inputRef.current?.select(), 0);
  }

  async function commit() {
    const v = parseFloat(draft);
    setEditing(false);
    if (!isNaN(v) && v !== value) await onSave(v);
  }

  if (editing) {
    return (
      <input ref={inputRef}
        type="number" min="0" step="any"
        value={draft} onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={e => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
        style={{
          width: 90, textAlign: "right", background: "var(--bg3)",
          border: "1px solid var(--accent)", borderRadius: 4,
          color: "var(--text)", fontSize: 13, padding: "2px 6px",
        }} />
    );
  }

  return (
    <span onClick={startEdit} title="Click to edit planned amount"
      style={{ cursor: "pointer", color: value > 0 ? "var(--text)" : "var(--text2)",
        borderBottom: "1px dashed var(--border)", fontSize: 13 }}>
      {value > 0 ? fmt(value) : "Set"}
    </span>
  );
}

// ── Progress bar ──────────────────────────────────────────────────────────────
function ProgressBar({ spent, planned, type }) {
  if (!planned) return null;
  const pct = Math.min((spent / planned) * 100, 100);
  const over = spent > planned;
  const color = type === "income"
    ? "var(--green)"
    : over ? "var(--red)" : pct > 80 ? "#f59e0b" : "var(--accent)";
  return (
    <div style={{ background: "var(--bg3)", borderRadius: 3, height: 4, width: "100%", marginTop: 4 }}>
      <div style={{ width: `${pct}%`, height: "100%", borderRadius: 3, background: color, transition: "width 0.3s" }} />
    </div>
  );
}

// ── Budget item row ───────────────────────────────────────────────────────────
function ItemRow({ item, month, groupType, allGroups, onUpdate, onDelete }) {
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(item.name);
  const remaining = item.planned - item.spent;
  const overBudget = item.spent > item.planned && item.planned > 0;
  const remainColor = overBudget ? "var(--red)" : remaining > 0 ? "var(--green)" : "var(--text2)";

  async function saveName() {
    if (!nameDraft.trim() || nameDraft.trim() === item.name) { setEditingName(false); return; }
    await updateBudgetItem(item.id, nameDraft.trim());
    setEditingName(false);
    onUpdate();
  }

  return (
    <div style={{
      display: "grid", gridTemplateColumns: "1fr 110px 100px 100px 24px",
      gap: 8, alignItems: "center", padding: "8px 16px 4px",
      borderBottom: "1px solid var(--border)",
    }}>
      {/* Name */}
      <div>
        {editingName ? (
          <input value={nameDraft} onChange={e => setNameDraft(e.target.value)}
            onBlur={saveName} onKeyDown={e => { if (e.key === "Enter") saveName(); if (e.key === "Escape") setEditingName(false); }}
            autoFocus style={{ background: "var(--bg3)", border: "1px solid var(--accent)", borderRadius: 4,
              color: "var(--text)", fontSize: 13, padding: "2px 6px", width: "100%" }} />
        ) : (
          <span onClick={() => setEditingName(true)} style={{ fontSize: 13, cursor: "pointer" }}
            title="Click to rename">{item.name}</span>
        )}
        <ProgressBar spent={item.spent} planned={item.planned} type={groupType} />
      </div>

      {/* Planned — click to edit inline */}
      <div style={{ textAlign: "right" }}>
        <AmountCell value={item.planned} onSave={v => setBudgetAmount(item.id, month, v).then(onUpdate)} />
      </div>

      {/* Spent */}
      <div style={{ textAlign: "right", fontSize: 13, color: item.spent > 0 ? "var(--text)" : "var(--text2)" }}>
        {item.spent > 0 ? fmt(item.spent) : "—"}
      </div>

      {/* Remaining */}
      <div style={{ textAlign: "right", fontSize: 13, fontWeight: 600, color: remainColor }}>
        {item.planned > 0 ? fmtSigned(remaining) : "—"}
      </div>

      {/* Delete */}
      <button onClick={() => { if (confirm(`Delete "${item.name}"?`)) deleteBudgetItem(item.id).then(onUpdate); }}
        style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 13, padding: 0 }}
        title="Remove item">✕</button>
    </div>
  );
}

// ── Budget group card ─────────────────────────────────────────────────────────
function GroupCard({ group, month, allGroups, onUpdate }) {
  const [collapsed, setCollapsed] = useState(false);
  const [addingItem, setAddingItem] = useState(false);
  const [newItemName, setNewItemName] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(group.name);

  const isIncome = group.type === "income";
  const accent = isIncome ? "var(--green)" : "var(--accent)";
  const overBudget = group.total_spent > group.total_planned && group.total_planned > 0;

  async function addItem() {
    if (!newItemName.trim()) return;
    await createBudgetItem(group.id, newItemName.trim());
    setNewItemName("");
    setAddingItem(false);
    onUpdate();
  }

  async function saveName() {
    if (!nameDraft.trim() || nameDraft.trim() === group.name) { setEditingName(false); return; }
    await updateBudgetGroup(group.id, { name: nameDraft.trim() });
    setEditingName(false);
    onUpdate();
  }

  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      {/* Group header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10, padding: "12px 16px",
        background: "var(--bg2)", borderBottom: collapsed ? "none" : "1px solid var(--border)",
        cursor: "pointer",
      }} onClick={() => setCollapsed(c => !c)}>
        <span style={{ color: "var(--text2)", fontSize: 11 }}>{collapsed ? "▶" : "▼"}</span>

        {editingName ? (
          <input value={nameDraft} onChange={e => setNameDraft(e.target.value)}
            onBlur={saveName} onKeyDown={e => { if (e.key === "Enter") saveName(); if (e.key === "Escape") setEditingName(false); }}
            autoFocus onClick={e => e.stopPropagation()}
            style={{ background: "var(--bg3)", border: "1px solid var(--accent)", borderRadius: 4,
              color: "var(--text)", fontSize: 14, fontWeight: 700, padding: "2px 6px", flex: 1 }} />
        ) : (
          <span style={{ fontWeight: 700, fontSize: 14, flex: 1 }}
            onDoubleClick={e => { e.stopPropagation(); setEditingName(true); setCollapsed(false); }}
            title="Double-click to rename">{group.name}</span>
        )}

        <span style={{ fontSize: 11, color: "var(--text2)" }}>
          {isIncome ? "received" : "spent"}:{" "}
          <strong style={{ color: overBudget ? "var(--red)" : accent }}>
            {fmt(group.total_spent)}
          </strong>
          {" / "}
          <span>{fmt(group.total_planned)}</span>
        </span>

        <button onClick={e => { e.stopPropagation(); if (confirm(`Delete group "${group.name}" and all its items?`)) deleteBudgetGroup(group.id).then(onUpdate); }}
          style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, marginLeft: 4 }}
          title="Delete group">✕</button>
      </div>

      {!collapsed && (
        <>
          {/* Column headers */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 110px 100px 100px 24px",
            gap: 8, padding: "4px 16px", borderBottom: "1px solid var(--border)" }}>
            {["Item", "Planned", isIncome ? "Received" : "Spent", "Remaining", ""].map((h, i) => (
              <div key={i} style={{ fontSize: 10, fontWeight: 600, color: "var(--text2)",
                textTransform: "uppercase", letterSpacing: "0.5px",
                textAlign: i > 0 && i < 4 ? "right" : "left" }}>{h}</div>
            ))}
          </div>

          {/* Items */}
          {group.items.map(item => (
            <ItemRow key={item.id} item={item} month={month} groupType={group.type}
              allGroups={allGroups} onUpdate={onUpdate} />
          ))}

          {/* Add item */}
          <div style={{ padding: "8px 16px" }}>
            {addingItem ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input value={newItemName} onChange={e => setNewItemName(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") addItem(); if (e.key === "Escape") setAddingItem(false); }}
                  autoFocus placeholder="Item name…"
                  style={{ flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
                    borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "4px 10px" }} />
                <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={addItem}>Add</button>
                <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => setAddingItem(false)}>Cancel</button>
              </div>
            ) : (
              <button onClick={() => setAddingItem(true)}
                style={{ background: "none", border: "none", color: accent, fontSize: 12,
                  cursor: "pointer", fontWeight: 600, padding: 0 }}>+ Add item</button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Unassigned transactions panel ─────────────────────────────────────────────
function UnassignedPanel({ month, allGroups, onAssign }) {
  const [txns, setTxns] = useState([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    getUnassignedTransactions(month).then(setTxns).finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, [month]);

  // Flatten all items from all groups for the assign dropdown
  const allItems = allGroups.flatMap(g => g.items.map(i => ({ ...i, groupName: g.name })));

  async function handleAssign(txnId, itemId) {
    if (!itemId) return;
    await assignTransaction(txnId, parseInt(itemId));
    load();
    onAssign();
  }

  if (loading) return null;
  if (txns.length === 0) return (
    <div className="card" style={{ textAlign: "center", padding: "24px", color: "var(--text2)", fontSize: 13 }}>
      ✓ All transactions for this month are assigned to a budget category.
    </div>
  );

  return (
    <div className="card">
      <div className="card-title" style={{ marginBottom: 0 }}>
        Unassigned Transactions
        <span style={{ marginLeft: 8, fontWeight: 400, color: "var(--text2)", fontSize: 12 }}>
          ({txns.length} pending)
        </span>
      </div>
      <p style={{ color: "var(--text2)", fontSize: 12, margin: "4px 0 12px" }}>
        Assign each transaction to a budget category so every dollar is accounted for.
      </p>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              {["Date", "Merchant", "Amount", "Assign to"].map((h, i) => (
                <th key={h} style={{ padding: "6px 12px 8px", textAlign: i === 2 ? "right" : "left",
                  color: "var(--text2)", fontWeight: 600, fontSize: 11,
                  textTransform: "uppercase", letterSpacing: "0.5px" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {txns.map(t => (
              <tr key={t.transaction_id} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "8px 12px", color: "var(--text2)", whiteSpace: "nowrap" }}>{t.date}</td>
                <td style={{ padding: "8px 12px", color: "var(--text)" }}>
                  {t.merchant_name || t.name || "—"}
                </td>
                <td style={{ padding: "8px 12px", textAlign: "right", fontWeight: 600,
                  color: t.amount >= 0 ? "var(--red)" : "var(--green)" }}>
                  {t.amount >= 0 ? "-" : "+"}{fmt(t.amount)}
                </td>
                <td style={{ padding: "8px 12px" }}>
                  <select defaultValue=""
                    onChange={e => handleAssign(t.transaction_id, e.target.value)}
                    className="form-select" style={{ fontSize: 12, padding: "3px 8px" }}>
                    <option value="" disabled>Select category…</option>
                    {allGroups.map(g => (
                      <optgroup key={g.id} label={g.name}>
                        {g.items.map(i => (
                          <option key={i.id} value={i.id}>{i.name}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Shared import result summary ──────────────────────────────────────────────
function ImportResult({ result }) {
  if (!result) return null;
  return (
    <div style={{ marginTop: 12, padding: "14px 16px", background: "var(--bg3)",
      borderRadius: 8, fontSize: 13 }}>
      <div style={{ fontWeight: 700, color: "var(--green)", marginBottom: 8 }}>✓ Import complete</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 24px", color: "var(--text2)" }}>
        {result.month && <div>Month: <strong style={{ color: "var(--text)" }}>{result.month}</strong></div>}
        {result.files_processed != null && <div>Files: <strong style={{ color: "var(--text)" }}>{result.files_processed}</strong></div>}
        <div>Transactions imported: <strong style={{ color: "var(--text)" }}>{result.rows_imported}</strong></div>
        {result.months_covered?.length > 0 && <div>Months covered: <strong style={{ color: "var(--text)" }}>{result.months_covered.length}</strong></div>}
        <div>Auto-rules seeded: <strong style={{ color: "var(--text)" }}>{result.rules_seeded}</strong></div>
        {result.groups_created > 0 && <div>Groups created: <strong style={{ color: "var(--text)" }}>{result.groups_created}</strong></div>}
        {result.items_created > 0 && <div>Items created: <strong style={{ color: "var(--text)" }}>{result.items_created}</strong></div>}
      </div>
      {result.months_covered?.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--text2)" }}>
          Months: {result.months_covered.sort().join(", ")}
        </div>
      )}
    </div>
  );
}

// ── EveryDollar JSON paste tab ────────────────────────────────────────────────
// User copies the raw API JSON from DevTools Network tab and pastes it here.
// Works for one month at a time — repeat for each historical month needed.
function JSONPasteTab({ onImported }) {
  const [text, setText] = useState("");
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function handleImport() {
    setError(null);
    setResult(null);
    let parsed;
    try {
      parsed = JSON.parse(text.trim());
    } catch {
      setError("Invalid JSON — make sure you copied the full response body from DevTools.");
      return;
    }
    setImporting(true);
    try {
      const res = await importBudgetJSON(parsed);
      setResult(res);
      setText("");
      onImported?.();
    } catch (e) {
      setError(e.message ?? "Import failed");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div>
      {/* How-to instructions */}
      <div style={{ background: "var(--bg3)", borderRadius: 6, padding: "10px 14px",
        fontSize: 12, color: "var(--text2)", marginBottom: 12, lineHeight: 1.6 }}>
        <strong style={{ color: "var(--text)" }}>How to get the JSON from EveryDollar:</strong>
        <ol style={{ margin: "6px 0 0 16px", padding: 0 }}>
          <li>Open <strong>everydollar.com</strong> and navigate to any budget month</li>
          <li>Open <strong>DevTools</strong> (F12) → <strong>Network</strong> tab → filter by <strong>Fetch/XHR</strong></li>
          <li>Refresh the page — look for a request whose Response contains <code>"groups"</code> and <code>"budgetItems"</code></li>
          <li>Click it → <strong>Response</strong> tab → select all → copy</li>
          <li>Paste below and click Import — repeat for each historical month</li>
        </ol>
      </div>

      <textarea
        value={text}
        onChange={e => { setText(e.target.value); setResult(null); setError(null); }}
        placeholder='Paste EveryDollar JSON here…  {"id":"…","date":"2026-03-01","groups":[…]}'
        rows={8}
        style={{
          width: "100%", boxSizing: "border-box",
          background: "var(--bg3)", border: "1px solid var(--border)",
          borderRadius: 6, color: "var(--text)", fontSize: 12,
          padding: "10px 12px", fontFamily: "monospace", resize: "vertical",
        }}
      />

      <button className="btn btn-primary" style={{ marginTop: 8 }}
        onClick={handleImport} disabled={importing || !text.trim()}>
        {importing ? "Importing…" : "Import month"}
      </button>

      {error && (
        <div style={{ marginTop: 10, padding: "10px 14px", background: "rgba(239,68,68,0.1)",
          borderRadius: 6, color: "var(--red)", fontSize: 13 }}>
          {error}
        </div>
      )}
      <ImportResult result={result} />
    </div>
  );
}

// ── CSV upload tab ────────────────────────────────────────────────────────────
function CSVUploadTab({ onImported }) {
  const [files, setFiles] = useState([]);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  function addFiles(newFiles) {
    const csvs = [...newFiles].filter(f => f.name.endsWith(".csv"));
    if (!csvs.length) return;
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name));
      return [...prev, ...csvs.filter(f => !names.has(f.name))];
    });
    setResult(null);
    setError(null);
  }

  async function handleImport() {
    if (!files.length) return;
    setImporting(true);
    setError(null);
    try {
      const res = await importBudgetCSV(files);
      setResult(res);
      setFiles([]);
      onImported?.();
    } catch (e) {
      setError(e.message ?? "Import failed");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div>
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        style={{
          border: `2px dashed ${dragging ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 8, padding: "24px", textAlign: "center",
          cursor: "pointer", background: dragging ? "var(--bg3)" : "transparent",
          transition: "all 0.15s",
        }}
      >
        <div style={{ fontSize: 28, marginBottom: 8 }}>📂</div>
        <div style={{ fontSize: 13, color: "var(--text2)" }}>
          Drag &amp; drop CSV files here, or <span style={{ color: "var(--accent)", fontWeight: 600 }}>click to browse</span>
        </div>
        <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 4 }}>
          EveryDollar monthly CSV export format — select multiple months at once
        </div>
        <input ref={inputRef} type="file" accept=".csv" multiple
          style={{ display: "none" }}
          onChange={e => addFiles(e.target.files)} />
      </div>

      {files.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 6 }}>
            {files.length} file{files.length !== 1 ? "s" : ""} queued:
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {files.map(f => (
              <div key={f.name} style={{
                background: "var(--bg3)", border: "1px solid var(--border)",
                borderRadius: 20, padding: "3px 10px 3px 12px",
                fontSize: 12, display: "flex", alignItems: "center", gap: 6,
              }}>
                <span>{f.name}</span>
                <button onClick={e => { e.stopPropagation(); setFiles(p => p.filter(x => x.name !== f.name)); }}
                  style={{ background: "none", border: "none", color: "var(--text2)",
                    cursor: "pointer", fontSize: 13, padding: 0, lineHeight: 1 }}>✕</button>
              </div>
            ))}
          </div>
          <button className="btn btn-primary" style={{ marginTop: 12 }}
            onClick={handleImport} disabled={importing}>
            {importing ? "Importing…" : `Import ${files.length} file${files.length !== 1 ? "s" : ""}`}
          </button>
        </div>
      )}

      {error && (
        <div style={{ marginTop: 12, padding: "10px 14px", background: "rgba(239,68,68,0.1)",
          borderRadius: 6, color: "var(--red)", fontSize: 13 }}>
          {error}
        </div>
      )}
      <ImportResult result={result} />
    </div>
  );
}

// ── Import Panel (tabbed: JSON paste + CSV upload) ────────────────────────────
function CSVImportPanel({ onImported, collapsed: initialCollapsed = true }) {
  const [open, setOpen] = useState(!initialCollapsed);
  // Default to JSON tab — it's the better path (has planned amounts, no file downloads)
  const [tab, setTab] = useState("json");

  const tabStyle = (active) => ({
    padding: "6px 16px", fontSize: 12, fontWeight: 600, cursor: "pointer",
    background: "none", border: "none",
    borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
    color: active ? "var(--accent)" : "var(--text2)",
  });

  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      {/* Collapsible header */}
      <div
        style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px",
          background: "var(--bg2)", cursor: "pointer", userSelect: "none" }}
        onClick={() => setOpen(o => !o)}
      >
        <span style={{ color: "var(--text2)", fontSize: 11 }}>{open ? "▼" : "▶"}</span>
        <span style={{ fontWeight: 700, fontSize: 14, flex: 1 }}>Import EveryDollar History</span>
        <span style={{ fontSize: 12, color: "var(--text2)" }}>
          Paste API JSON (recommended) or upload CSV exports
        </span>
      </div>

      {open && (
        <div>
          {/* Tab bar */}
          <div style={{ display: "flex", borderBottom: "1px solid var(--border)", paddingLeft: 16 }}>
            <button style={tabStyle(tab === "json")} onClick={() => setTab("json")}>
              Paste JSON (DevTools)
            </button>
            <button style={tabStyle(tab === "csv")} onClick={() => setTab("csv")}>
              Upload CSV
            </button>
          </div>
          <div style={{ padding: 16 }}>
            {tab === "json" ? <JSONPasteTab onImported={onImported} /> : <CSVUploadTab onImported={onImported} />}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Budget Page ───────────────────────────────────────────────────────────────
export default function Budget() {
  const [month, setMonth] = useState(currentMonth);
  const [budget, setBudget] = useState(null);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [addingGroup, setAddingGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupType, setNewGroupType] = useState("expense");

  async function load() {
    setLoading(true);
    try { setBudget(await getBudget(month)); }
    catch { setBudget(null); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [month]);

  async function handleSeedTemplate() {
    setSeeding(true);
    await seedBudgetTemplate();
    await load();
    setSeeding(false);
  }

  async function handleAddGroup() {
    if (!newGroupName.trim()) return;
    await createBudgetGroup(newGroupName.trim(), newGroupType);
    setNewGroupName("");
    setAddingGroup(false);
    await load();
  }

  const summary = budget?.summary;
  const groups = budget?.groups ?? [];
  const hasGroups = groups.length > 0;

  return (
    <div>
      {/* Page header */}
      <div className="page-header">
        <h2>Monthly Budget</h2>
        <p>Zero-based budget — plan every dollar before the month begins</p>
      </div>

      {/* Month navigation */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
        <button className="btn btn-secondary" style={{ padding: "6px 14px" }} onClick={() => setMonth(prevMonth)}>←</button>
        <span style={{ fontWeight: 700, fontSize: 18, minWidth: 180, textAlign: "center" }}>
          {monthLabel(month)}
        </span>
        <button className="btn btn-secondary" style={{ padding: "6px 14px" }} onClick={() => setMonth(nextMonth)}>→</button>
        <button className="btn btn-secondary" style={{ padding: "6px 14px", fontSize: 12 }}
          onClick={() => setMonth(currentMonth())}>Today</button>
      </div>

      {loading && <div style={{ color: "var(--text2)", padding: "40px 0", textAlign: "center" }}>Loading…</div>}

      {!loading && !hasGroups && (
        <>
          <div className="card" style={{ textAlign: "center", padding: "48px 24px", marginBottom: 16 }}>
            <div style={{ fontSize: 40, marginBottom: 16 }}>📋</div>
            <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>No budget categories yet</div>
            <div style={{ color: "var(--text2)", fontSize: 14, marginBottom: 24 }}>
              Start with a standard set of budget categories, or import your EveryDollar history to auto-build them.
            </div>
            <button className="btn btn-primary" onClick={handleSeedTemplate} disabled={seeding}>
              {seeding ? "Setting up…" : "Use standard categories"}
            </button>
          </div>
          {/* Show CSV importer expanded on empty state — importing history auto-creates categories */}
          <CSVImportPanel onImported={load} collapsed={false} />
        </>
      )}

      {!loading && hasGroups && (
        <>
          {/* Summary bar */}
          {summary && (
            <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 24 }}>
              {[
                { label: "Income Planned", value: summary.total_income_planned, color: "var(--green)" },
                { label: "Total Budgeted", value: summary.total_expense_planned, color: "var(--accent)" },
                { label: "Total Spent", value: summary.total_expense_spent, color: "var(--text)" },
                {
                  label: "Remaining to Budget",
                  value: summary.remaining_to_budget,
                  color: summary.remaining_to_budget > 0 ? "var(--red)"
                    : summary.remaining_to_budget < 0 ? "var(--red)" : "var(--green)",
                  note: summary.remaining_to_budget === 0 ? "✓ Fully budgeted"
                    : summary.remaining_to_budget > 0 ? "income not fully allocated"
                    : "over-allocated",
                },
              ].map(({ label, value, color, note }) => (
                <div key={label} className="card" style={{ flex: "1 1 160px", padding: "14px 18px" }}>
                  <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.5px" }}>{label}</div>
                  <div style={{ fontSize: 22, fontWeight: 700, color }}>{fmt(value)}</div>
                  {note && <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 3 }}>{note}</div>}
                </div>
              ))}
            </div>
          )}

          {/* Budget groups */}
          {groups.map(group => (
            <GroupCard key={group.id} group={group} month={month} allGroups={groups} onUpdate={load} />
          ))}

          {/* Add group */}
          <div style={{ marginTop: 8 }}>
            {addingGroup ? (
              <div className="card" style={{ display: "flex", gap: 10, alignItems: "center", padding: "12px 16px" }}>
                <input value={newGroupName} onChange={e => setNewGroupName(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") handleAddGroup(); if (e.key === "Escape") setAddingGroup(false); }}
                  autoFocus placeholder="Group name (e.g. Housing, Food)…"
                  style={{ flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
                    borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "6px 12px" }} />
                <select value={newGroupType} onChange={e => setNewGroupType(e.target.value)}
                  className="form-select" style={{ fontSize: 13, width: 120 }}>
                  <option value="expense">Expense</option>
                  <option value="income">Income</option>
                </select>
                <button className="btn btn-primary" style={{ padding: "6px 16px" }} onClick={handleAddGroup}>Add</button>
                <button className="btn btn-secondary" style={{ padding: "6px 16px" }} onClick={() => setAddingGroup(false)}>Cancel</button>
              </div>
            ) : (
              <button className="btn btn-secondary" style={{ width: "100%", padding: "10px" }}
                onClick={() => setAddingGroup(true)}>+ Add budget group</button>
            )}
          </div>

          {/* Unassigned transactions */}
          <div style={{ marginTop: 32 }}>
            <UnassignedPanel month={month} allGroups={groups} onAssign={load} />
          </div>

          {/* CSV history importer — collapsed by default when budget is already set up */}
          <div style={{ marginTop: 16 }}>
            <CSVImportPanel onImported={load} collapsed={true} />
          </div>
        </>
      )}
    </div>
  );
}
