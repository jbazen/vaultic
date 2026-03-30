/**
 * TaxDocumentChecklist — Auto-generated checklist of expected tax documents.
 *
 * Scans connected accounts and paystub employers to determine which tax
 * documents the user should expect for the current year. Shows received
 * (green check) vs missing (empty circle) status for each expected doc.
 *
 * Props:
 *   checklist  {Object|null}  Response from GET /api/tax/checklist/{year}
 */

export default function TaxDocumentChecklist({ checklist }) {
  if (!checklist || !checklist.checklist?.length) return null;

  const { total_expected, total_received, checklist: items } = checklist;
  const allReceived = total_received === total_expected;

  // Group items by doc_type for organized display
  const groups = {};
  for (const item of items) {
    const key = item.label;
    if (!groups[key]) groups[key] = [];
    groups[key].push(item);
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div className="flex-between flex-wrap gap-10" style={{ marginBottom: 16 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 16 }}>Document Checklist — {checklist.year}</div>
          <div className="sub-label">Auto-generated from your connected accounts and employers</div>
        </div>
        <div style={{
          padding: "6px 14px",
          borderRadius: 20,
          fontSize: 13,
          fontWeight: 600,
          background: allReceived ? "rgba(34,197,94,0.12)" : "rgba(245,158,11,0.12)",
          color: allReceived ? "var(--green)" : "#f59e0b",
          border: `1px solid ${allReceived ? "var(--green)" : "#f59e0b"}`,
        }}>
          {total_received} / {total_expected} received
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {Object.entries(groups).map(([label, groupItems]) => (
          groupItems.map((item) => (
            <div
              key={`${item.doc_type}-${item.source}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 14px",
                background: "var(--bg3)",
                borderRadius: 8,
                border: "1px solid var(--border)",
                opacity: item.received ? 0.75 : 1,
              }}
            >
              {/* Status indicator */}
              <div style={{
                width: 22, height: 22,
                borderRadius: "50%",
                display: "flex", alignItems: "center", justifyContent: "center",
                background: item.received ? "var(--green)" : "transparent",
                border: item.received ? "none" : "2px solid var(--text2)",
                color: "#fff",
                fontSize: 13,
                fontWeight: 700,
                flexShrink: 0,
              }}>
                {item.received ? "\u2713" : ""}
              </div>

              {/* Document info */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 13 }}>
                  {item.label}
                  <span style={{ fontWeight: 400, color: "var(--text2)", marginLeft: 8 }}>
                    from {item.source}
                  </span>
                </div>
                {item.condition && (
                  <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 2 }}>
                    {item.condition}
                  </div>
                )}
              </div>

              {/* Status label */}
              <div style={{
                fontSize: 12,
                fontWeight: 600,
                color: item.received ? "var(--green)" : "var(--text2)",
                flexShrink: 0,
              }}>
                {item.received ? "Received" : "Expected"}
              </div>
            </div>
          ))
        ))}
      </div>
    </div>
  );
}
