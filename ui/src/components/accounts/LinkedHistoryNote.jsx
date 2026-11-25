import { useState } from "react";
import { setAccountLinkActive } from "../../api.js";
import { fmtDate } from "../../utils/format.js";

// ── Linked History Note (#181) ────────────────────────────────────────────────
// Names the earlier account this one's history starts with, with Unlink /
// Relink. Unlinking changes no data: the charts just stop joining the two.
export default function LinkedHistoryNote({ account, onChanged }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const from = `${account.linked_from_name}${account.linked_from_mask ? ` (...${account.linked_from_mask})` : ""}`;
  const active = !!account.link_active;

  async function toggle() {
    setSaving(true);
    setError(null);
    try {
      await setAccountLinkActive(account.link_id, !active);
      await onChanged();
    } catch (e) {
      setError(e.message || "Could not change the link");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ color: "var(--text2)", fontSize: 12, marginBottom: 10 }}>
      {!active
        ? <>Not joined to the history of {from}. </>
        : account.linked_until
          ? <>History through {fmtDate(account.linked_until)} comes from {from}. </>
          : <>History joined to {from}. </>}
      <button onClick={toggle} disabled={saving}
        aria-label={active ? `Unlink history of ${from}` : `Relink history of ${from}`}
        style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: 12, padding: 0 }}>
        {saving ? "…" : active ? "Unlink" : "Relink"}
      </button>
      {error && <span role="alert" style={{ color: "var(--red)", marginLeft: 8 }}>{error}</span>}
    </div>
  );
}
