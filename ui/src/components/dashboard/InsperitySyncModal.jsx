import { useState } from "react";
import { insperitySync } from "../../api.js";

export default function InsperitySyncModal({ open, onClose, onSynced }) {
  const [curl, setCurl] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  if (!open) return null;

  async function handleSync() {
    const trimmed = curl.trim();
    if (!trimmed) return;
    setSyncing(true);
    setResult(null);
    setError(null);
    try {
      const data = await insperitySync(trimmed);
      if (data.ok) {
        setResult(data);
        setCurl("");
        if (onSynced) onSynced();
      } else {
        setError(data.detail || "Sync failed");
      }
    } catch (e) {
      const msg = e?.message || "Sync failed";
      if (msg.includes("401") || msg.includes("403") || msg.includes("expired")) {
        setError("Session expired. Please get a fresh cURL from retirement.insperity.com.");
      } else {
        setError(msg);
      }
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)",
      display: "flex", alignItems: "flex-start", justifyContent: "center",
      zIndex: 1000, padding: "60px 16px", overflowY: "auto",
    }} onClick={e => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" style={{
        background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 14,
        width: "100%", maxWidth: 600, padding: 28,
      }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 18 }}>Sync Insperity 401K</div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "var(--text2)", fontSize: 22, cursor: "pointer", padding: 4 }} aria-label="Close">
            ✕
          </button>
        </div>

        {/* Instructions */}
        <div style={{ fontSize: 13, color: "var(--text2)", lineHeight: 1.6, marginBottom: 18 }}>
          <div style={{ fontWeight: 600, color: "var(--text)", marginBottom: 6 }}>How to get your session:</div>
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            <li>Log in to <strong>retirement.insperity.com</strong></li>
            <li>Press <strong>F12</strong> to open DevTools &rarr; <strong>Network</strong> tab</li>
            <li>Click on any request to <strong>retirement.insperity.com</strong></li>
            <li>Right-click the request &rarr; <strong>Copy</strong> &rarr; <strong>Copy as cURL</strong></li>
            <li>Paste the full cURL command below and click Sync</li>
          </ol>
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--text2)", opacity: 0.8 }}>
            Sessions expire quickly. Keep your Insperity tab open while syncing.
          </div>
        </div>

        {/* cURL input */}
        <div style={{ marginBottom: 16 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text2)", display: "block", marginBottom: 6 }}>
            cURL Command
          </label>
          <textarea
            className="form-input"
            value={curl}
            onChange={e => setCurl(e.target.value)}
            placeholder='curl "https://retirement.insperity.com/..." -b "ASP.NET_SessionId=..."'
            disabled={syncing}
            rows={4}
            style={{ width: "100%", fontFamily: "monospace", fontSize: 11, resize: "vertical" }}
            autoFocus
          />
        </div>

        {/* Sync button */}
        <button
          className="btn btn-primary"
          onClick={handleSync}
          disabled={syncing || !curl.trim()}
          style={{ width: "100%", padding: "10px 0", fontSize: 14, fontWeight: 600 }}
        >
          {syncing ? "Syncing... (scraping 7 pages)" : "Sync Now"}
        </button>

        {/* Error */}
        {error && (
          <div style={{
            marginTop: 14, padding: "10px 14px", borderRadius: 8,
            background: "rgba(239,68,68,0.12)", border: "1px solid var(--red)",
            color: "var(--red)", fontSize: 13,
          }}>
            {error}
          </div>
        )}

        {/* Success result */}
        {result && (
          <div style={{
            marginTop: 14, padding: "14px 16px", borderRadius: 8,
            background: "rgba(34,197,94,0.1)", border: "1px solid var(--green)",
          }}>
            <div style={{ fontWeight: 700, color: "var(--green)", marginBottom: 8, fontSize: 14 }}>
              Sync {result.status === "success" ? "Complete" : "Partial"}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 20px", fontSize: 13 }}>
              <div style={{ color: "var(--text2)" }}>Holdings</div>
              <div style={{ fontWeight: 600 }}>{result.holdings}</div>
              <div style={{ color: "var(--text2)" }}>Total Balance</div>
              <div style={{ fontWeight: 600 }}>
                {(result.total_balance || 0).toLocaleString("en-US", { style: "currency", currency: "USD" })}
              </div>
              <div style={{ color: "var(--text2)" }}>Duration</div>
              <div style={{ fontWeight: 600 }}>{((result.duration_ms || 0) / 1000).toFixed(1)}s</div>
            </div>
            {result.errors?.length > 0 && (
              <div style={{ marginTop: 10, fontSize: 12, color: "var(--yellow)" }}>
                {result.errors.length} page{result.errors.length > 1 ? "s" : ""} failed (partial sync)
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
