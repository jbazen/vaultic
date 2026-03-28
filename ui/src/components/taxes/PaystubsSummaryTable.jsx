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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
        <div style={{ fontWeight: 700, fontSize: 16 }}>Paystubs — YTD</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {msg && <span style={{ fontSize: 12, color: "var(--text2)" }}>{msg}</span>}
          <input ref={inputRef} type="file" accept=".pdf" multiple style={{ display: "none" }} onChange={handleUpload} />
          <button
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
            style={{
              padding: "6px 14px",
              borderRadius: 8,
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              fontSize: 13,
              fontWeight: 600,
              cursor: uploading ? "not-allowed" : "pointer",
              opacity: uploading ? 0.7 : 1,
              whiteSpace: "nowrap",
            }}
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
              <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--bg3)" }}>
                {["Employer", "Pay Date", "Gross (Period)", "YTD Gross", "YTD Federal", "YTD State", "YTD SS", "YTD Medicare", "YTD Net"].map(h => (
                  <th key={h} scope="col" style={{ padding: "10px 12px", textAlign: h === "Employer" ? "left" : "right", fontWeight: 700, fontSize: 11, textTransform: "uppercase", color: "var(--text2)", letterSpacing: "0.5px" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {paystubs.map(p => (
                <tr key={p.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "12px 12px", fontWeight: 600 }}>{p.employer || "—"}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right", color: "var(--text2)" }}>{p.pay_date || "—"}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right" }}>{fmt(p.gross_pay)}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right", fontWeight: 600 }}>{fmt(p.ytd_gross)}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right", color: "var(--red)" }}>{fmt(p.ytd_federal)}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right", color: "var(--red)" }}>{fmt(p.ytd_state)}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right" }}>{fmt(p.ytd_social_security)}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right" }}>{fmt(p.ytd_medicare)}</td>
                  <td style={{ padding: "12px 12px", textAlign: "right", color: "var(--green)", fontWeight: 600 }}>{fmt(p.ytd_net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
