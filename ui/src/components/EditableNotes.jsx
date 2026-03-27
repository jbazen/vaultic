import { useState } from "react";

// Inline editable notes: shows current notes (or placeholder) with a pencil icon.
// onSave(newNotes) is called when the user commits the edit.
//
// Dashboard uses: <EditableNotes notes={...} onSave={...} placeholder="Add description…" />
//   - editing wrapper: div, width 220
// Accounts uses:  <EditableNotes notes={...} onSave={...} />
//   - editing wrapper: span (inline-flex), width 200
//
// The `inline` prop controls which variant renders:
//   inline=true  → span wrapper, width 200  (Accounts behavior)
//   inline=false → div wrapper, width 220   (Dashboard behavior, default)
function EditableNotes({ notes, onSave, placeholder = "Add description…", inline = false }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(notes || "");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try { await onSave(draft.trim()); setEditing(false); }
    finally { setSaving(false); }
  }

  if (editing) {
    const Wrapper = inline ? "span" : "div";
    const wrapperStyle = inline
      ? { display: "inline-flex", alignItems: "center", gap: 5 }
      : { display: "flex", alignItems: "center", gap: 6, marginTop: 2 };
    const inputWidth = inline ? 200 : 220;

    return (
      <Wrapper style={wrapperStyle}>
        <input className="form-input" style={{ width: inputWidth, padding: "2px 6px", fontSize: 12 }}
          value={draft} onChange={e => setDraft(e.target.value)} autoFocus
          onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} />
        <button className="btn btn-primary" style={{ padding: "2px 8px", fontSize: 11 }} onClick={save} disabled={saving}>{saving ? "…" : "Save"}</button>
        <button className="btn btn-secondary" style={{ padding: "2px 8px", fontSize: 11 }} onClick={() => setEditing(false)}>✕</button>
      </Wrapper>
    );
  }
  return (
    <span style={{ color: "var(--text2)", fontSize: 12 }}>
      {notes || ""}
      <button onClick={() => { setDraft(notes || ""); setEditing(true); }} title="Edit description"
        style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 11, padding: "0 3px", opacity: 0.6 }}>✎</button>
    </span>
  );
}

export default EditableNotes;
