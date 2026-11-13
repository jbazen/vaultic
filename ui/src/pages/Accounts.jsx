import { useState, useEffect, useRef } from "react";
import {
  getAccounts, getPlaidItems, removePlaidItem, syncCoinbase, getManualEntries,
  deleteManualEntry, reorderInstitutions, getInstitutionOrder,
} from "../api.js";
import PlaidLink from "../components/PlaidLink.jsx";
import PlaidReconnect from "../components/PlaidReconnect.jsx";
import AccountRow from "../components/accounts/AccountRow.jsx";
import ManualInvestmentCard from "../components/accounts/ManualInvestmentCard.jsx";
import ManualSimpleRow from "../components/accounts/ManualSimpleRow.jsx";
import InsperityAccountCard from "../components/accounts/InsperityAccountCard.jsx";
import VoyaAccountCard from "../components/accounts/VoyaAccountCard.jsx";
import {
  SEC_INVESTED, SEC_INVESTED_OTHER, SEC_LIQUID, SEC_PROPERTY, SEC_LIABILITIES,
  isSyncStale,
} from "../components/accounts/accountsUtils.jsx";
import { fmtDate } from "../utils/format.js";

// ── Main Accounts Page ─────────────────────────────────────────────────────────

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const [coinbaseSyncing, setCoinbaseSyncing] = useState(false);
  const [coinbaseStatus, setCoinbaseStatus] = useState(null);
  const [manualEntries, setManualEntries] = useState([]);
  const [savedOrder, setSavedOrder] = useState([]);
  // Drag-and-drop ref (avoid setState during dragstart — see CLAUDE.md)
  const dragInstRef = useRef(null);

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

  async function handleRemove(itemId) {
    if (!confirm("Disconnect this institution? Account history will be preserved.")) return;
    setRemoveError("");
    try {
      await removePlaidItem(itemId);
      await load();
    } catch (e) {
      setRemoveError(e.message || "Failed to disconnect. Please try again.");
    }
  }

  const grouped = accounts.reduce((acc, a) => {
    const key = a.institution_name || "Manual";
    if (!acc[key]) acc[key] = [];
    acc[key].push(a);
    return acc;
  }, {});

  // ── Filtered manual entry collections (computed once) ──
  const investedImported = manualEntries
    .filter(e => e.category === "invested" && (e.account_number || e.exclude_from_net_worth))
    .sort((a, b) => (b.exclude_from_net_worth - a.exclude_from_net_worth) || a.name.localeCompare(b.name));
  const investedOther    = manualEntries.filter(e => e.category === "invested" && !e.account_number && !e.exclude_from_net_worth);
  const liquidEntries    = manualEntries.filter(e => e.category === "liquid");
  const propertyEntries  = manualEntries.filter(e => ["home_value", "car_value", "real_estate", "vehicles"].includes(e.category));
  const liabilityEntries = manualEntries.filter(e => e.category === "other_liability");

  // ── Build unified block list (institutions + manual sections, all draggable) ──
  const blocks = [];
  for (const [inst, accts] of Object.entries(grouped)) {
    blocks.push({ key: inst, kind: "institution", accts });
  }
  if (investedImported.length > 0) blocks.push({ key: SEC_INVESTED,       kind: "sec_invested" });
  if (investedOther.length > 0)    blocks.push({ key: SEC_INVESTED_OTHER, kind: "sec_invested_other" });
  if (liquidEntries.length > 0)    blocks.push({ key: SEC_LIQUID,         kind: "sec_liquid" });
  if (propertyEntries.length > 0)  blocks.push({ key: SEC_PROPERTY,       kind: "sec_property" });
  if (liabilityEntries.length > 0) blocks.push({ key: SEC_LIABILITIES,    kind: "sec_liabilities" });

  const orderMap = new Map(savedOrder.map((n, i) => [n, i]));
  const defaultIdx = new Map(blocks.map((b, i) => [b.key, i]));
  const orderedBlocks = [...blocks].sort((a, b) => {
    const oA = orderMap.get(a.key);
    const oB = orderMap.get(b.key);
    if (oA != null && oB != null) return oA - oB;
    if (oA != null) return -1;
    if (oB != null) return 1;
    if (a.kind === "institution" && b.kind !== "institution") return -1;
    if (a.kind !== "institution" && b.kind === "institution") return 1;
    if (a.kind === "institution" && b.kind === "institution") return a.key.localeCompare(b.key);
    return defaultIdx.get(a.key) - defaultIdx.get(b.key);
  });

  function handleInstDragStart(e, key) {
    // Never call setState during dragstart — React re-renders mutate the DOM,
    // which the browser interprets as the drag source disappearing and cancels
    // the operation. Use a ref + a body class instead (see CLAUDE.md).
    dragInstRef.current = key;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", key);
    document.body.classList.add("inst-drag-active");
  }

  function handleInstDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }

  async function handleInstDrop(e, targetKey) {
    e.preventDefault();
    const sourceKey = dragInstRef.current;
    dragInstRef.current = null;
    document.body.classList.remove("inst-drag-active");
    if (!sourceKey || sourceKey === targetKey) return;

    const keys = orderedBlocks.map(b => b.key);
    const fromIdx = keys.indexOf(sourceKey);
    const toIdx = keys.indexOf(targetKey);
    if (fromIdx < 0 || toIdx < 0) return;
    keys.splice(fromIdx, 1);
    keys.splice(toIdx, 0, sourceKey);

    try {
      await reorderInstitutions("accounts", keys);
      await load();
    } catch (_) { /* non-fatal */ }
  }

  function handleInstDragEnd() {
    dragInstRef.current = null;
    document.body.classList.remove("inst-drag-active");
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

      {removeError && (
        <div className="status-banner warn" role="alert" style={{ marginBottom: 16 }}>{removeError}</div>
      )}

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
        orderedBlocks.map(block => {
          let titleText = "";
          let subtitleText = null;
          let subtitleWarn = false;
          let rightButton = null;
          let bodyContent = null;

          if (block.kind === "institution") {
            const item = items.find(i => i.institution_name === block.key);
            titleText = block.key;
            // Real disconnect detection (issue #73): the "Reconnect" banner is
            // driven by Plaid's actual error_code, not by sync staleness. A merely
            // slow/late sync no longer falsely reads as "login expired"; it shows a
            // softer "hasn't synced recently" note instead.
            const loginExpired = item?.error_code === "ITEM_LOGIN_REQUIRED";
            const stale = item ? isSyncStale(item.last_synced_at) : false;
            if (loginExpired) {
              subtitleText = "⚠ Login expired — click Reconnect to resume syncing";
              subtitleWarn = true;
            } else if (item?.last_synced_at) {
              subtitleText = stale
                ? `Synced ${fmtDate(item.last_synced_at)} — hasn't synced recently`
                : `Synced ${fmtDate(item.last_synced_at)}`;
              subtitleWarn = false;
            }
            if (item) {
              rightButton = (
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <PlaidReconnect itemId={item.item_id} onSuccess={load} />
                  <button className="btn btn-danger" style={{ fontSize: 12, padding: "5px 12px" }}
                    onClick={() => handleRemove(item.item_id)}>
                    Disconnect
                  </button>
                </div>
              );
            }
            bodyContent = (
              <div className="account-list">
                {block.accts.map(a => <AccountRow key={a.id} account={a} onRenamed={load} />)}
              </div>
            );
          } else if (block.kind === "sec_invested") {
            titleText = "Investment Accounts (PDF Imported)";
            subtitleText = "Click any row to expand holdings. Use \"⊘ Exclude\" on consolidated portfolio summaries to avoid double-counting in net worth.";
            bodyContent = (
              <div className="account-list">
                {investedImported.map(entry => entry.account_number === "105001401K" ? (
                  <InsperityAccountCard key={entry.id} entry={entry}
                    onDelete={async (id) => { await deleteManualEntry(id); await load(); }}
                    onToggleExclude={load} onRenamed={load} />
                ) : entry.account_number === "861956" ? (
                  <VoyaAccountCard key={entry.id} entry={entry} />
                ) : (
                  <ManualInvestmentCard key={entry.id} entry={entry}
                    onDelete={async (id) => { await deleteManualEntry(id); await load(); }}
                    onToggleExclude={load} onRenamed={load} />
                ))}
              </div>
            );
          } else if (block.kind === "sec_invested_other") {
            titleText = "Other Investment Accounts";
            bodyContent = (
              <div className="account-list">
                {investedOther.map(entry => (
                  <ManualInvestmentCard key={entry.id} entry={entry}
                    onDelete={async (id) => { await deleteManualEntry(id); await load(); }}
                    onToggleExclude={load} onRenamed={load} />
                ))}
              </div>
            );
          } else if (block.kind === "sec_liquid") {
            titleText = "HSA / Cash Accounts";
            bodyContent = (
              <div className="account-list">
                {liquidEntries.map(entry => (
                  <ManualSimpleRow key={entry.id} entry={entry} badge="liquid" badgeClass="badge-depository"
                    onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                    onRenamed={load} />
                ))}
              </div>
            );
          } else if (block.kind === "sec_property") {
            titleText = "Property & Vehicles";
            bodyContent = (
              <div className="account-list">
                {propertyEntries.map(entry => (
                  <ManualSimpleRow key={entry.id} entry={entry}
                    badge={{ home_value: "Home", car_value: "Vehicle", real_estate: "Real Estate", vehicles: "Vehicle" }[entry.category]}
                    badgeClass="badge-investment"
                    onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                    onRenamed={load} />
                ))}
              </div>
            );
          } else if (block.kind === "sec_liabilities") {
            titleText = "Liabilities";
            bodyContent = (
              <div className="account-list">
                {liabilityEntries.map(entry => (
                  <ManualSimpleRow key={entry.id} entry={entry} badge="Liability" badgeClass="badge-credit" negative
                    onDelete={async () => { await deleteManualEntry(entry.id); await load(); }}
                    onRenamed={load} />
                ))}
              </div>
            );
          }

          return (
            <div
              className="card inst-drop-target"
              key={block.key}
              onDragOver={handleInstDragOver}
              onDrop={(e) => handleInstDrop(e, block.key)}
            >
              <div
                draggable
                onDragStart={(e) => handleInstDragStart(e, block.key)}
                onDragEnd={handleInstDragEnd}
                title="Drag to reorder"
                style={{
                  display: "flex", justifyContent: "space-between", alignItems: "flex-start",
                  marginBottom: 16, cursor: "grab",
                }}
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                  <span style={{ color: "var(--text2)", fontSize: 16, userSelect: "none", marginTop: 1 }}>⋮⋮</span>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 16 }}>{titleText}</div>
                    {subtitleText && (
                      <div style={{ fontSize: 12, color: subtitleWarn ? "var(--red)" : "var(--text2)", marginTop: 2 }}>{subtitleText}</div>
                    )}
                  </div>
                </div>
                {rightButton}
              </div>
              {bodyContent}
            </div>
          );
        })
      )}
    </div>
  );
}
