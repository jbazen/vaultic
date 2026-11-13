import { useState, useEffect } from "react";
import {
  getAccounts, getPlaidItems, syncCoinbase, getManualEntries, getInstitutionOrder,
} from "../api.js";
import PlaidLink from "../components/PlaidLink.jsx";
import AccountsSection from "../components/accounts/AccountsSection.jsx";

// ── Main Accounts Page ─────────────────────────────────────────────────────────

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const [coinbaseStatus, setCoinbaseStatus] = useState(null);
  const [manualEntries, setManualEntries] = useState([]);
  const [savedOrder, setSavedOrder] = useState([]);

  async function load() {
    setLoadError(false);
    try {
      const [accts, its, manual, order] = await Promise.all([
        getAccounts(),
        getPlaidItems(),
        getManualEntries(),
        getInstitutionOrder("accounts").catch(() => ({ names: [] })),
      ]);
      setAccounts(accts);
      setItems(its);
      setManualEntries(manual);
      setSavedOrder(order?.names || []);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleCoinbaseSync() {
    setCoinbaseSyncing(true);
    setCoinbaseStatus(null);
    try {
      const result = await syncCoinbase();
      if (result.skipped) {
        setCoinbaseStatus({ ok: false, msg: "Coinbase API keys not configured in .env" });
      } else {
        setCoinbaseStatus({ ok: true, msg: `Synced ${result.synced} holdings` });
        await load();
      }
    } catch (err) {
      setCoinbaseStatus({ ok: false, msg: err.message });
    } finally {
      setCoinbaseSyncing(false);
    }
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2>Accounts</h2>
          <p>All connected institutions and balances</p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
            <button className="btn btn-secondary" onClick={handleCoinbaseSync} disabled={coinbaseSyncing} style={{ fontSize: 13 }}>
              {coinbaseSyncing ? "Syncing…" : "⟳ Sync Coinbase"}
            </button>
            {coinbaseStatus && (
              <span style={{ fontSize: 12, color: coinbaseStatus.ok ? "var(--accent)" : "var(--red)" }}>
                {coinbaseStatus.msg}
              </span>
            )}
          </div>
          <PlaidLink onSuccess={load} />
        </div>
      </div>

      {loading ? (
        <div style={{ color: "var(--text2)" }}>Loading…</div>
      ) : loadError && accounts.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{ fontSize: 14, color: "var(--text2)", marginBottom: 16 }}>
            Could not load accounts.
          </div>
          <button className="btn btn-primary" onClick={() => { setLoading(true); load(); }}>
            Retry
          </button>
        </div>
      ) : accounts.length === 0 ? (
        <div className="card empty-state">
          <p>No accounts connected yet.</p>
          <p style={{ fontSize: "13px" }}>Click "Connect Account" to link your first institution via Plaid.</p>
        </div>
      ) : (
        <AccountsSection accounts={accounts} manualEntries={manualEntries} plaidItems={items}
          savedOrder={savedOrder} orderPage="accounts" onChanged={load} />
      )}
    </div>
  );
}
