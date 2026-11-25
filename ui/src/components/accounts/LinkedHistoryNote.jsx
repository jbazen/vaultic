import { useState } from "react";
import { setAccountLinkActive } from "../../api.js";
import { fmtDate } from "../../utils/format.js";

// ── Linked History Note (#181) ────────────────────────────────────────────────
// Names the earlier account this one's history starts with, with Unlink /
// Relink. Unlinking changes no data: the charts just stop joining the two.
export default function LinkedHistoryNote({ account, onChanged }) {
  const [saving, setSaving] = useState(false);
  const from = `${account.linked_from_name}${account.linked_from_mask ? ` (...${account.linked_from_mask})` : ""}`;
  const active = !!account.link_active;

  async function toggle() {
    setSaving(true);
    try { await setAccountLinkActive(account.link_id, !active); await onChanged(); }
    finally { setSaving(false); }
  }

  return (
    <div style={{ color: "var(--text2)", fontSize: 12, marginBottom: 10 }}>
      {active
        ? <>History through {fmtDate(account.linked_until)} comes from {from}. </>
        : <>Not joined to the history of {from}. </>}
      <button onClick={toggle} disabled={saving}
        aria-label={active ? `Unlink history of ${from}` : `Relink history of ${from}`}
        style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: 12, padding: 0 }}>
        {saving ? "…" : active ? "Unlink" : "Relink"}
      </button>
    </div>
  );
}
