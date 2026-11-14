// Draggable card shell for one account block (an institution or a manual section).
// Pure display: the parent owns drag state and handlers. Only the header is the
// drag handle, so clicks inside the body (expand rows, buttons) are unaffected.
export default function AccountBlockCard({
  blockKey, title, subtitle, warn, actions,
  onDragStart, onDragOver, onDrop, onDragEnd, children,
}) {
  return (
    <div
      className="card inst-drop-target"
      onDragOver={onDragOver}
      onDrop={(e) => onDrop(e, blockKey)}
    >
      <div
        draggable
        onDragStart={(e) => onDragStart(e, blockKey)}
        onDragEnd={onDragEnd}
        title="Drag to reorder"
        style={{
          display: "flex", justifyContent: "space-between", alignItems: "flex-start",
          marginBottom: 16, cursor: "grab",
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <span style={{ color: "var(--text2)", fontSize: 16, userSelect: "none", marginTop: 1 }}>⋮⋮</span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>{title}</div>
            {subtitle && (
              <div style={{ fontSize: 12, color: warn ? "var(--red)" : "var(--text2)", marginTop: 2 }}>{subtitle}</div>
            )}
          </div>
        </div>
        {actions}
      </div>
      {children}
    </div>
  );
}
