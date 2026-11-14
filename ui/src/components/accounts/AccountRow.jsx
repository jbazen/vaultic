import { useState } from "react";
import { renameAccount, updateAccountNotes } from "../../api.js";
import { fmt, fmtDate } from "../../utils/format.js";
import { isRetirementAccount } from "../../utils/accounts.js";
import { isLiability, isPlaidInvestment } from "./accountsUtils.jsx";
import CryptoAccountRow from "./CryptoAccountRow.jsx";
import I360AccountCard from "./I360AccountCard.jsx";
import PlaidInvestmentCard from "./PlaidInvestmentCard.jsx";
import AccountMetaLine from "./AccountMetaLine.jsx";

// ── Standard Account Row ───────────────────────────────────────────────────────
// Checking, savings, credit cards, loans. All use the same bordered box style.
// Shows available balance for depository accounts and credit limit for credit accounts.
export default function AccountRow({ account, onRenamed }) {
  if (account.type === "crypto") return <CryptoAccountRow account={account} onRenamed={onRenamed} />;
  if (account.source === "investor360") return <I360AccountCard account={account} onRenamed={onRenamed} />;
  if (isPlaidInvestment(account)) return <PlaidInvestmentCard account={account} onRenamed={onRenamed} />;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(account.display_name || account.name);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    try { await renameAccount(account.id, draft.trim()); onRenamed(); setEditing(false); }
    finally { setSaving(false); }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") { setEditing(false); setDraft(account.display_name || account.name); }
  }

  const mask = account.mask ? ` (...${account.mask})` : "";
  const label = account.display_name || account.name;
  const isLiab = isLiability(account.type);

  // Extra inline labels on the meta line
  const extra = (
    <>
      {account.type === "depository" && account.available != null && account.available !== account.current && (
        <span style={{ color: "var(--text2)", fontSize: 12 }}>Avail: {fmt(account.available)}</span>
      )}
      {account.type === "credit" && account.limit_amount != null && (
        <span style={{ color: "var(--text2)", fontSize: 12 }}>Limit: {fmt(account.limit_amount)}</span>
      )}
    </>
  );

  return (
    <div className="account-row">
      <div className="account-info">
        {editing ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input className="form-input" style={{ width: 200, padding: "5px 8px", fontSize: 14 }}
              value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={handleKeyDown} autoFocus />
            <span style={{ color: "var(--text2)", fontSize: 13 }}>{mask}</span>
            <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={handleSave} disabled={saving}>{saving ? "…" : "Save"}</button>
            <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => { setEditing(false); setDraft(label); }}>Cancel</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div className="account-name">{label}{mask}</div>
            <button onClick={() => setEditing(true)} title="Rename"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 12, padding: "2px 4px" }} aria-label="Rename">✎</button>
          </div>
        )}
        <AccountMetaLine
          type={account.type}
          subtype={account.subtype}
          notes={account.notes}
          onSaveNotes={async v => { await updateAccountNotes(account.id, v); onRenamed(); }}
          date={account.snapped_at ? fmtDate(account.snapped_at) : null}
          extra={extra}
          badgeType={account.type === "investment" && isRetirementAccount(account.subtype, account.name) ? "retirement" : undefined}
          badgeLabel={account.type === "investment" && isRetirementAccount(account.subtype, account.name) ? "retirement" : undefined}
        />
      </div>
      <div className={`account-balance ${isLiab ? "liability" : ""}`}>
        {isLiab ? `-${fmt(account.current)}` : fmt(account.current)}
      </div>
    </div>
  );
}
