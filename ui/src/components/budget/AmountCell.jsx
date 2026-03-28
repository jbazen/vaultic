/**
 * AmountCell — Inline-editable dollar amount cell for budget planned values.
 */
import { useState, useRef } from "react";
import { fmt } from "../../utils/format.js";

export default function AmountCell({ value, onSave }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const ref = useRef(null);

  function start(e) {
    e.stopPropagation();
    setDraft(value > 0 ? String(value) : "");
    setEditing(true);
    setTimeout(() => ref.current?.select(), 0);
  }

  async function commit() {
    // Strip leading $ or commas, then validate as a non-negative dollar amount
    const cleaned = draft.replace(/[$,]/g, "").trim();
    const v = parseFloat(cleaned);
    setEditing(false);
    if (!isNaN(v) && v >= 0 && v !== value) await onSave(Math.round(v * 100) / 100);
  }

  if (editing) {
    return (
      <input ref={ref} type="text" inputMode="decimal" value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={e => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
        onClick={e => e.stopPropagation()}
        style={{
          width: "100%", textAlign: "right", background: "var(--bg3)",
          border: "1px solid var(--accent)", borderRadius: 4,
          color: "var(--text)", fontSize: 13, padding: "2px 6px",
        }}
      />
    );
  }

  return (
    <span onClick={start} title="Click to edit planned amount"
      style={{
        cursor: "pointer", fontSize: 13, display: "block", textAlign: "right",
        color: value > 0 ? "var(--text)" : "var(--text2)",
        borderBottom: "1px dashed var(--border)",
      }}>
      {value > 0 ? fmt(value) : "$0.00"}
    </span>
  );
}
