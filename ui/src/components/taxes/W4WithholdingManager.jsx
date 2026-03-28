/**
 * W4WithholdingManager — W-4s on file table with upload and optimizer launch button.
 */
import { useRef, useState } from "react";
import { uploadW4, getW4s } from "../../api.js";

/** Whole-dollar formatter */
function fmt(v) {
  if (v == null) return "$0";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, minimumFractionDigits: 0 });
}

export default function W4WithholdingManager({ w4s, setW4s, onOpenWizard }) {
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState(null);
  const inputRef = useRef(null);

  /** Upload one or more W-4 PDFs and refresh the list */
  async function handleUpload(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploading(true);
    setMsg(null);
    const results = [];
    for (const file of files) {
      try {
        const res = await uploadW4(file);
        if (res.ok) results.push(`${res.employer}: parsed`);
        else results.push(`${file.name}: ${res.detail || "parse failed"}`);
      } catch (err) {
        results.push(`${file.name}: ${err.message}`);
      }
    }
    setMsg(results.join(" · "));
    setUploading(false);
    getW4s().then(setW4s).catch(() => {});
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
        <div style={{ fontWeight: 700, fontSize: 16 }}>W-4s on File</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {msg && <span style={{ fontSize: 12, color: "var(--text2)" }}>{msg}</span>}
          <input ref={inputRef} type="file" accept=".pdf" multiple style={{ display: "none" }} onChange={handleUpload} />
          <button
            onClick={onOpenWizard}
            style={{ padding: "6px 14px", borderRadius: 8, background: "rgba(52,211,153,0.15)", color: "var(--green)", border: "1px solid var(--green)", fontSize: 13, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}
          >
            ✦ W-4 Optimizer
          </button>
          <button
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
            style={{ padding: "6px 14px", borderRadius: 8, background: "#7c3aed", color: "#fff", border: "none", fontSize: 13, fontWeight: 600, cursor: uploading ? "not-allowed" : "pointer", opacity: uploading ? 0.7 : 1, whiteSpace: "nowrap" }}
          >
            {uploading ? "Parsing…" : "Upload W-4"}
          </button>
        </div>
      </div>
      {w4s.length === 0 ? (
        <div style={{ color: "var(--text2)", fontSize: 14, textAlign: "center", padding: "16px 0" }}>
          No W-4s uploaded yet. Upload your current W-4s to enable the withholding optimizer.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--bg3)" }}>
                {["Employer", "Filing Status", "Dependents Credit", "Extra/Period", "Effective Date"].map(h => (
                  <th key={h} scope="col" style={{ padding: "10px 12px", textAlign: h === "Employer" ? "left" : "right", fontWeight: 700, fontSize: 11, textTransform: "uppercase", color: "var(--text2)", letterSpacing: "0.5px" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {w4s.map(w => (
                <tr key={w.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "12px 12px", fontWeight: 600 }}>{w.employer || "—"}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right", textTransform: "capitalize" }}>{(w.filing_status || "—").replace(/_/g, " ")}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right" }}>{fmt(w.dependents_amount)}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right", color: w.extra_withholding > 0 ? "var(--green)" : "inherit" }}>{w.extra_withholding > 0 ? fmt(w.extra_withholding) : "—"}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right", color: "var(--text2)" }}>{w.effective_date || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
