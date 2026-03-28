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
      <div className="flex-between flex-wrap gap-10" style={{ marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 16 }}>W-4s on File</div>
        <div className="flex-center gap-8 flex-wrap">
          {msg && <span className="sub-label">{msg}</span>}
          <input ref={inputRef} type="file" accept=".pdf" multiple style={{ display: "none" }} onChange={handleUpload} />
          <button
            onClick={onOpenWizard}
            style={{ padding: "6px 14px", borderRadius: 8, background: "rgba(52,211,153,0.15)", color: "var(--green)", border: "1px solid var(--green)", fontSize: 13, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}
          >
            ✦ W-4 Optimizer
          </button>
          <button className="btn-purple" onClick={() => inputRef.current?.click()} disabled={uploading}>
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
              <tr className="table-header-row">
                {["Employer", "Filing Status", "Dependents Credit", "Extra/Period", "Effective Date"].map(h => (
                  <th key={h} scope="col" className={`th-cell${h !== "Employer" ? " right" : ""}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {w4s.map(w => (
                <tr key={w.id} className="tr-row">
                  <td className="td-cell bold">{w.employer || "—"}</td>
                  <td className="td-cell right" style={{ textTransform: "capitalize" }}>{(w.filing_status || "—").replace(/_/g, " ")}</td>
                  <td className="td-cell right">{fmt(w.dependents_amount)}</td>
                  <td className="td-cell right" style={{ color: w.extra_withholding > 0 ? "var(--green)" : "inherit" }}>{w.extra_withholding > 0 ? fmt(w.extra_withholding) : "—"}</td>
                  <td className="td-cell right dim">{w.effective_date || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
