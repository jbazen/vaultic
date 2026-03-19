// Tax module — placeholder for future build (W-4 wizard, tax projection, estimated payments)
export default function Taxes() {
  return (
    <div>
      <div className="page-header">
        <h2>Taxes</h2>
        <p>W-4 wizard, tax projection, estimated payments, and capital gains — coming soon</p>
      </div>
      <div className="card" style={{ textAlign: "center", padding: "64px 24px" }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}>🏗</div>
        <div style={{ fontWeight: 700, fontSize: 20, marginBottom: 8 }}>Tax module coming soon</div>
        <div style={{ color: "var(--text2)", fontSize: 14, maxWidth: 480, margin: "0 auto" }}>
          Planned: W-4 multi-job wizard, federal and Arizona tax projection, quarterly estimated
          payment calculator, capital gains tracker, and tax document checklist.
        </div>
      </div>
    </div>
  );
}
