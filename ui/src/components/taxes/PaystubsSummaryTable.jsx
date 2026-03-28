/**
 * PaystubsSummaryTable — YTD paystub data with upload capability.
 * Shows per-employer rows with gross, federal, state, SS, Medicare, and net YTD totals.
 */
import { useRef, useState } from "react";
import { uploadPaystub, getPaystubs } from "../../api.js";

/** Whole-dollar formatter */
function fmt(v) {
  if (v == null) return "$0";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, minimumFractionDigits: 0 });
}

export default function PaystubsSummaryTable({ paystubs, setPaystubs }) {
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState(null);
  const inputRef = useRef(null);

  /** Upload one or more paystub PDFs and refresh the list */
  async function handleUpload(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploading(true);
    setMsg(null);
    const results = [];
    for (const file of files) {
      try {
        const res = await uploadPaystub(file);
        if (res.ok) {
          results.push(`${res.employer || file.name} (${res.pay_date}): parsed`);
        } else {
          results.push(`${file.name}: ${res.detail || "parse failed"}`);
        }
      } catch (err) {
        results.push(`${file.name}: ${err.message}`);
      }
    }
    setMsg(results.join(" · "));
    setUploading(false);
    getPaystubs().then(setPaystubs).catch(() => {});
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div className="flex-between flex-wrap gap-10" style={{ marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 16 }}>Paystubs — YTD</div>
        <div className="flex-center gap-8">
          {msg && <span className="sub-label">{msg}</span>}
          <input ref={inputRef} type="file" accept=".pdf" multiple style={{ display: "none" }} onChange={handleUpload} />
          <button
            className="btn-upload"
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? "Parsing…" : "Upload Paystub"}
          </button>
        </div>
      </div>

      {paystubs.length === 0 ? (
        <div style={{ color: "var(--text2)", fontSize: 14, textAlign: "center", padding: "20px 0" }}>
          No paystubs uploaded yet. Upload a recent paystub to see YTD totals.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr className="table-header-row">
                {["Employer", "Pay Date", "Gross (Period)", "YTD Gross", "YTD Federal", "YTD State", "YTD SS", "YTD Medicare", "YTD Net"].map(h => (
                  <th key={h} scope="col" className={`th-cell${h !== "Employer" ? " right" : ""}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {paystubs.map(p => (
                <tr key={p.id} className="tr-row">
                  <td className="td-cell bold">{p.employer || "—"}</td>
                  <td className="td-cell right dim">{p.pay_date || "—"}</td>
                  <td className="td-cell right">{fmt(p.gross_pay)}</td>
                  <td className="td-cell right bold">{fmt(p.ytd_gross)}</td>
                  <td className="td-cell right negative">{fmt(p.ytd_federal)}</td>
                  <td className="td-cell right negative">{fmt(p.ytd_state)}</td>
                  <td className="td-cell right">{fmt(p.ytd_social_security)}</td>
                  <td className="td-cell right">{fmt(p.ytd_medicare)}</td>
                  <td className="td-cell right positive bold">{fmt(p.ytd_net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
