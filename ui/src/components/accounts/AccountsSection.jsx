import { useState, useRef } from "react";
import { removePlaidItem, deleteManualEntry, reorderInstitutions } from "../../api.js";
import PlaidReconnect from "../PlaidReconnect.jsx";
import AccountRow from "./AccountRow.jsx";
import AccountBlockCard from "./AccountBlockCard.jsx";
import ManualInvestmentCard from "./ManualInvestmentCard.jsx";
import ManualSimpleRow from "./ManualSimpleRow.jsx";
import InsperityAccountCard from "./InsperityAccountCard.jsx";
import VoyaAccountCard from "./VoyaAccountCard.jsx";
import AllocationBar from "../AllocationBar.jsx";
import { institutionStatus, splitManualEntries, buildAccountBlocks, sortBlocks } from "./accountsUtils.jsx";

const PROPERTY_BADGES = { home_value: "Home", car_value: "Vehicle", real_estate: "Real Estate", vehicles: "Vehicle" };

// The draggable account list: one block per institution plus the manual sections.
// Owns drag-and-drop state, Disconnect handling and its error banner. Reorders
// are saved under `orderPage`; every successful change calls onChanged().
// The `id="accounts"` anchor is where /#accounts (push notifications) scrolls to.
export default function AccountsSection({
  accounts, manualEntries, plaidItems, savedOrder, orderPage, i360LastSync = null, onChanged,
}) {
  const [removeError, setRemoveError] = useState("");
  // Drag-and-drop ref (avoid setState during dragstart — see CLAUDE.md)
  const dragInstRef = useRef(null);

  const manual = splitManualEntries(manualEntries);
  const orderedBlocks = sortBlocks(buildAccountBlocks(accounts, manual), savedOrder);

  // Consolidated allocation across all imported investment holdings
  const allHoldings = manual.investedImported.flatMap(e => e.holdings || []);
  const allHoldingsTotal = allHoldings.reduce((sum, h) => sum + (h.value || 0), 0);
  const allocationByClass = allHoldings.reduce((acc, h) => {
    const cls = h.asset_class || "other";
    acc[cls] = (acc[cls] || 0) + (h.value || 0);
    return acc;
  }, {});

  async function handleRemove(itemId) {
    if (!confirm("Disconnect this institution? Account history will be preserved.")) return;
    setRemoveError("");
    try {
      await removePlaidItem(itemId);
      await onChanged();
    } catch (e) {
      setRemoveError(e.message || "Failed to disconnect. Please try again.");
    }
  }

  async function handleDeleteEntry(id) {
    await deleteManualEntry(id);
    await onChanged();
  }

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
      await reorderInstitutions(orderPage, keys);
      await onChanged();
    } catch { /* non-fatal */ }
  }

  function handleInstDragEnd() {
    dragInstRef.current = null;
    document.body.classList.remove("inst-drag-active");
  }

  function renderManualCards(entries) {
    return entries.map(entry => (
      <ManualInvestmentCard key={entry.id} entry={entry}
        onDelete={handleDeleteEntry} onToggleExclude={onChanged} onRenamed={onChanged} />
    ));
  }

  function renderBlock(block) {
    const card = { blockKey: block.key, onDragStart: handleInstDragStart, onDragOver: handleInstDragOver,
      onDrop: handleInstDrop, onDragEnd: handleInstDragEnd };

    if (block.kind === "institution") {
      const item = plaidItems.find(i => i.institution_name === block.key);
      const isI360 = block.accts.some(x => x.source === "investor360");
      const { loginExpired, subtitle } = institutionStatus(item, isI360, i360LastSync);
      const actions = item && (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <PlaidReconnect itemId={item.item_id} onSuccess={onChanged} />
          <button className="btn btn-danger" style={{ fontSize: 12, padding: "5px 12px" }}
            onClick={() => handleRemove(item.item_id)}>
            Disconnect
          </button>
        </div>
      );
      return (
        <AccountBlockCard key={block.key} {...card} title={block.key}
          subtitle={subtitle} warn={loginExpired} actions={actions}>
          <div className="account-list">
            {block.accts.map(a => <AccountRow key={a.id} account={a} onRenamed={onChanged} />)}
          </div>
        </AccountBlockCard>
      );
    }

    if (block.kind === "sec_invested") {
      return (
        <AccountBlockCard key={block.key} {...card} title="Investment Accounts (Imported)"
          subtitle={'Click any row to expand holdings. Use "⊘ Exclude" on consolidated portfolio summaries to avoid double-counting in net worth.'}>
          {allHoldingsTotal > 0 && (
            <div style={{ marginBottom: 14 }}>
              <div className="section-label" style={{ marginBottom: 8 }}>Portfolio Allocation</div>
              <AllocationBar allocation={allocationByClass} total={allHoldingsTotal} />
            </div>
          )}
          <div className="account-list">
            {manual.investedImported.map(entry => entry.account_number === "105001401K" ? (
              <InsperityAccountCard key={entry.id} entry={entry}
                onDelete={handleDeleteEntry} onToggleExclude={onChanged} onRenamed={onChanged} />
            ) : entry.account_number === "861956" ? (
              <VoyaAccountCard key={entry.id} entry={entry} />
            ) : (
              <ManualInvestmentCard key={entry.id} entry={entry}
                onDelete={handleDeleteEntry} onToggleExclude={onChanged} onRenamed={onChanged} />
            ))}
          </div>
        </AccountBlockCard>
      );
    }

    if (block.kind === "sec_invested_other") {
      return (
        <AccountBlockCard key={block.key} {...card} title="Other Investment Accounts">
          <div className="account-list">{renderManualCards(manual.investedOther)}</div>
        </AccountBlockCard>
      );
    }

    const simple = {
      sec_liquid:       { title: "HSA / Cash Accounts",  entries: manual.liquidEntries,    badge: () => "liquid",   badgeClass: "badge-depository" },
      sec_property:     { title: "Property & Vehicles",  entries: manual.propertyEntries,  badge: e => PROPERTY_BADGES[e.category], badgeClass: "badge-investment" },
      sec_other_assets: { title: "Other Assets",        entries: manual.otherAssets,      badge: () => "asset",    badgeClass: "badge-depository" },
      sec_liabilities:  { title: "Liabilities",          entries: manual.liabilityEntries, badge: () => "Liability", badgeClass: "badge-credit", negative: true },
    }[block.kind];
    return (
      <AccountBlockCard key={block.key} {...card} title={simple.title}>
        <div className="account-list">
          {simple.entries.map(entry => (
            <ManualSimpleRow key={entry.id} entry={entry} badge={simple.badge(entry)}
              badgeClass={simple.badgeClass} negative={simple.negative}
              onDelete={() => handleDeleteEntry(entry.id)} onRenamed={onChanged} />
          ))}
        </div>
      </AccountBlockCard>
    );
  }

  return (
    <section id="accounts" aria-label="Accounts">
      <h3 style={{ fontSize: 18, fontWeight: 700, margin: "4px 0 12px" }}>Accounts</h3>
      {removeError && (
        <div className="status-banner warn" role="alert" style={{ marginBottom: 16 }}>{removeError}</div>
      )}
      {orderedBlocks.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "24px 0", color: "var(--text2)" }}>
          No accounts connected. Click <strong>+ Connect Account</strong> above.
        </div>
      ) : orderedBlocks.map(renderBlock)}
    </section>
  );
}
