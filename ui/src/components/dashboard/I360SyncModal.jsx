import { useState } from "react";
import { i360Sync, i360Status } from "../../api.js";
import { fmtDate } from "../../utils/format.js";

export default function I360SyncModal({ open, onClose, onSynced }) {
  const [cookie, setCookie] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  if (!open) return null;

  async function handleSync() {
    const trimmed = cookie.trim();
    if (!trimmed) return;
    setSyncing(true);
    setResult(null);
    setError(null);
    try {
      const data = await i360Sync(trimmed);
      if (data.ok) {
        setResult(data);
        setCookie("");
        if (onSynced) onSynced();
      } else {
        setError(data.detail || "Sync failed");
      }
    } catch (e) {
      const msg = e?.message || "Sync failed";
      if (msg.includes("401") || msg.includes("403")) {
        setError("Session expired or invalid. Please get a fresh cookie from Investor360.");
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
        width: "100%", maxWidth: 520, padding: 28,
      }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 18 }}>Sync Parker Financial</div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "var(--text2)", fontSize: 22, cursor: "pointer", padding: 4 }} aria-label="Close">
            ✕
          </button>
        </div>

        {/* Instructions */}
        <div style={{ fontSize: 13, color: "var(--text2)", lineHeight: 1.6, marginBottom: 18 }}>
          <div style={{ fontWeight: 600, color: "var(--text)", marginBottom: 6 }}>How to get your session cookie:</div>
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            <li>Log in to <strong>my.investor360.com</strong></li>
            <li>Press <strong>F12</strong> to open DevTools</li>
            <li>Go to <strong>Application</strong> tab &rarr; <strong>Cookies</strong> &rarr; <strong>my.investor360.com</strong></li>
            <li>Find <strong>CFNSession</strong> and copy its value</li>
            <li>Paste it below and click Sync</li>
          </ol>
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--text2)", opacity: 0.8 }}>
            Sessions expire after ~15 minutes of inactivity. Keep your Investor360 tab open while syncing.
          </div>
        </div>

        {/* Cookie input */}
        <div style={{ marginBottom: 16 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text2)", display: "block", marginBottom: 6 }}>
            CFNSession Cookie
          </label>
          <input
            type="text"
            className="form-input"
            value={cookie}
            onChange={e => setCookie(e.target.value)}
            placeholder="e.g. f4672582-b92c-43ff-b74c-b432ea109b2c"
            disabled={syncing}
            style={{ width: "100%", fontFamily: "monospace", fontSize: 13 }}
            autoFocus
          />
        </div>

        {/* Sync button */}
        <button
          className="btn btn-primary"
          onClick={handleSync}
          disabled={syncing || !cookie.trim()}
          style={{ width: "100%", padding: "10px 0", fontSize: 14, fontWeight: 600 }}
        >
          {syncing ? "Syncing... (this takes ~6 seconds)" : "Sync Now"}
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
              Sync Complete
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 20px", fontSize: 13 }}>
              <div style={{ color: "var(--text2)" }}>Accounts</div>
              <div style={{ fontWeight: 600 }}>{result.accounts}</div>
              <div style={{ color: "var(--text2)" }}>Holdings</div>
              <div style={{ fontWeight: 600 }}>{result.holdings}</div>
              <div style={{ color: "var(--text2)" }}>Total Value</div>
              <div style={{ fontWeight: 600 }}>
                {(result.total_value || 0).toLocaleString("en-US", { style: "currency", currency: "USD" })}
              </div>
              <div style={{ color: "var(--text2)" }}>Duration</div>
              <div style={{ fontWeight: 600 }}>{((result.duration_ms || 0) / 1000).toFixed(1)}s</div>
            </div>
            {result.warnings?.length > 0 && (
              <div style={{ marginTop: 10, fontSize: 12, color: "var(--yellow)" }}>
                {result.warnings.length} warning{result.warnings.length > 1 ? "s" : ""} (non-critical)
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
