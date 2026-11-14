import EditableNotes from "../EditableNotes.jsx";

// Badge + subtype/extra labels + editable notes + date — all on one line.
// badgeType/badgeLabel override the type-derived badge (e.g. "retirement" for 401k/IRA).
export default function AccountMetaLine({ type, subtype, notes, onSaveNotes, date, extra, badgeType, badgeLabel }) {
  const bType  = badgeType  || type;
  const bLabel = badgeLabel || type;
  return (
    <div className="account-meta" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0 8px" }}>
      <span className={`badge badge-${bType}`}>{bLabel}</span>
      {subtype && <span style={{ color: "var(--text2)" }}>{subtype}</span>}
      {extra}
      <EditableNotes notes={notes} onSave={onSaveNotes} inline />
      {date && <span style={{ color: "var(--text2)", opacity: 0.6 }}>· {date}</span>}
    </div>
  );
}
