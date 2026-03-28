/**
 * TaxDocumentsSection — Uploaded tax documents list (W-2s, 1099s, 1098s, etc.)
 * with upload and delete actions for a given tax year.
 */
import { useRef, useState } from "react";
import { uploadTaxDoc, deleteTaxDoc, getTaxDocs, getDraftReturn } from "../../api.js";

export default function TaxDocumentsSection({ taxYear, taxDocs, setTaxDocs, setDraftReturn }) {
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState(null);
  const inputRef = useRef(null);

  /** Upload one or more tax document PDFs and refresh the list */
  async function handleUpload(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploading(true);
    setMsg(null);
    const results = [];
    for (const file of files) {
      try {
        const res = await uploadTaxDoc(file);
        if (res.ok) results.push(`${res.doc_type_label} (${res.issuer || file.name}): parsed`);
        else results.push(`${file.name}: ${res.detail || "parse failed"}`);
      } catch (err) {
        results.push(`${file.name}: ${err.message}`);
      }
    }
    setMsg(results.join(" · "));
    setUploading(false);
    getTaxDocs(taxYear).then(setTaxDocs).catch(() => {});
    getDraftReturn(taxYear).then(setDraftReturn).catch(() => {});
    if (inputRef.current) inputRef.current.value = "";
  }

  /** Remove a single document and refresh */
  async function handleDelete(id) {
    await deleteTaxDoc(id).catch(() => {});
    getTaxDocs(taxYear).then(setTaxDocs).catch(() => {});
    getDraftReturn(taxYear).then(setDraftReturn).catch(() => {});
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 16 }}>Tax Documents — {taxYear}</div>
          <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 2 }}>W-2s, 1099s, 1098s, giving statements — upload anything</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {msg && <span style={{ fontSize: 12, color: "var(--text2)", maxWidth: 300 }}>{msg}</span>}
          <input ref={inputRef} type="file" accept=".pdf" multiple style={{ display: "none" }} onChange={handleUpload} />
          <button
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
            style={{ padding: "6px 14px", borderRadius: 8, background: "var(--accent)", color: "#fff", border: "none", fontSize: 13, fontWeight: 600, cursor: uploading ? "not-allowed" : "pointer", opacity: uploading ? 0.7 : 1, whiteSpace: "nowrap" }}
          >
            {uploading ? "Parsing…" : "Upload Documents"}
          </button>
        </div>
      </div>
      {taxDocs.length === 0 ? (
        <div style={{ color: "var(--text2)", fontSize: 14, textAlign: "center", padding: "20px 0" }}>
          No documents uploaded for {taxYear}. Upload W-2s, 1099s, 1098s, and giving statements to generate your draft return.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {taxDocs.map(doc => (
            <div key={doc.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--bg3)", borderRadius: 8, padding: "10px 14px", border: "1px solid var(--border)" }}>
              <div>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{doc.doc_type_label}</span>
                {doc.issuer && <span style={{ color: "var(--text2)", fontSize: 12, marginLeft: 8 }}>· {doc.issuer}</span>}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ fontSize: 12, color: "var(--text2)" }}>{doc.source_file}</span>
                <button
                  onClick={() => handleDelete(doc.id)}
                  style={{ background: "none", border: "none", color: "var(--red)", cursor: "pointer", fontSize: 16, padding: "0 4px" }}
                  title="Remove"
                  aria-label="Remove document"
                >×</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
