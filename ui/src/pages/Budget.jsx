/**
 * Budget.jsx — Zero-based monthly budget page.
 *
 * Layout in Vaultic's dark theme:
 *   • Left column  — collapsible budget groups with inline editing
 *   • Right panel  — sticky Summary (donut chart) or Transactions panel
 *
 * Key interactions:
 *   • Month picker  — click the month label to open a year/month grid
 *   • Column toggle — each expense group's last column header (Remaining ▾)
 *                     toggles between "Remaining" (blue/red) and "Spent" (green)
 *   • Inline edit   — double-click any item name; single-click the planned amount
 *   • Group edit    — click the group title to rename or delete
 *   • Transactions  — "New" tab shows unassigned (with auto-suggested category
 *                     badges from budget_auto_rules); "Tracked" shows assigned
 */

import { useState, useEffect, useRef, useCallback } from "react";
import {
  getBudget,
  createBudgetGroup,
  reorderGroups,
} from "../api.js";
import { fmt } from "../utils/format.js";
import { monthLabel, prevMonth, nextMonth, currentMonth } from "../components/budget/budgetUtils.jsx";
import MonthPicker from "../components/budget/MonthPicker.jsx";
import SummaryPanel from "../components/budget/SummaryPanel.jsx";
import TransactionsPanel from "../components/budget/TransactionsPanel.jsx";
import GroupSection from "../components/budget/GroupSection.jsx";
import ItemDetailModal from "../components/budget/ItemDetailModal.jsx";
import CreateTransactionModal from "../components/budget/CreateTransactionModal.jsx";

// ── Main Budget page ──────────────────────────────────────────────────────────
export default function Budget() {
  const [month, setMonth] = useState(currentMonth);
  const [budget, setBudget] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [rightTab, setRightTab] = useState("summary"); // summary | transactions
  // Mobile: one panel visible at a time. "budget" = left groups panel,
  // "summary" / "transactions" = right panel tabs.
  const [mobileTab, setMobileTab] = useState("budget");
  const [budgetWindowWidth, setBudgetWindowWidth] = useState(window.innerWidth);
  useEffect(() => {
    const onResize = () => setBudgetWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const isMobile = budgetWindowWidth <= 768;
  const [showMonthPicker, setShowMonthPicker] = useState(false);
  const [addingGroup, setAddingGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupType, setNewGroupType] = useState("expense");
  const [activeItem, setActiveItem] = useState(null); // item clicked for detail modal
  const [showCreateTxn, setShowCreateTxn] = useState(false);

  // ── Drag-and-drop state ──────────────────────────────────────────────────
  // Groups and items use separate drag state so they don't interfere.
  // NOTE: dragGroupId (React state) is intentionally NOT used for group dragging.
  // Calling setState during dragstart triggers a re-render that adds overlay divs
  // mid-drag, which causes browsers to cancel the operation and snap groups back.
  // Instead we use:
  //   • dragGroupRef — synchronous ref, readable in all event handlers
  //   • document.body CSS class — toggled without React re-render to enable overlays
  //   • dragOverGroupId — only set in dragover/drop (after drag is already live)
  const [dragOverGroupId, setDragOverGroupId] = useState(null); // group being hovered over
  const dragGroupRef = useRef(null); // which group is currently being dragged

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try { setBudget(await getBudget(month)); }
    catch { setBudget(null); setLoadError(true); }
    finally { setLoading(false); }
  }, [month]);

  // Silent refresh — updates budget data in place without showing a loading
  // spinner or resetting scroll position. Used after transaction assignment
  // so spent/remaining totals update smoothly without the page jumping to top.
  const silentLoad = useCallback(async () => {
    try { setBudget(await getBudget(month)); }
    catch { /* ignore — stale data is fine for a background refresh */ }
  }, [month]);

  useEffect(() => { load(); }, [load]);

  async function handleAddGroup() {
    if (!newGroupName.trim()) return;
    await createBudgetGroup(newGroupName.trim(), newGroupType);
    setNewGroupName("");
    setAddingGroup(false);
    await load();
  }

  const summary = budget?.summary;
  const groups = budget?.groups ?? [];
  const hasGroups = groups.length > 0;  // true if any groups exist at all (for empty state)

  // Zero-based status
  const remainingToBudget = summary?.remaining_to_budget ?? 0;
  const fullyBudgeted = Math.abs(remainingToBudget) < 0.01;

  // Visibility filter — two modes depending on whether we're viewing the current
  // (or future) month vs a past month:
  //   • Past months: only show groups/items with actual activity (planned > 0 or
  //     spent != 0). This keeps historical views accurate — you only see the budget
  //     categories that were in use that month, not today's active categories.
  //   • Current/future: also show non-archived groups/items even if they have $0/$0.
  //     This ensures active budget categories like "Other Deposits" are always
  //     visible so the user can plan and assign transactions to them.
  // The is_archived flag (set by auto-migration) separates "active but empty this
  // month" from "old imported historical item that shouldn't clutter the view."
  const isCurrentOrFuture = month >= currentMonth();
  const visibleGroups = groups.filter(g => {
    const hasActivity = g.total_planned > 0 || g.total_spent !== 0;
    return hasActivity || (isCurrentOrFuture && !g.is_archived);
  }).map(g => {
    // Item-level filtering within visible groups — same logic
    const visibleItems = g.items.filter(i => {
      const hasActivity = i.planned > 0 || i.spent !== 0;
      return hasActivity || (isCurrentOrFuture && !i.is_archived);
    });
    return { ...g, items: visibleItems };
  });

  // Assign a stable color index to each expense group (income stays green)
  let expIdx = 0;
  const groupColorIdx = {};
  visibleGroups.forEach(g => { if (g.type !== "income") groupColorIdx[g.id] = expIdx++; });

  // ── Group drag handlers ────────────────────────────────────────────────────
  // IMPORTANT: handleGroupDragStart must NOT call any setState. Calling setState
  // during dragstart triggers an immediate React re-render that mutates the DOM
  // (adds overlay divs), which causes the browser to cancel the drag and snap the
  // group back to its original position. We use only:
  //   • dragGroupRef.current — synchronous ref, no re-render
  //   • document.body class  — CSS-only toggle, no re-render

  function handleGroupDragStart(e, groupId) {
    dragGroupRef.current = groupId;
    e.dataTransfer.effectAllowed = "move";
    document.body.classList.add("group-drag-active");
  }

  function handleGroupDragOver(e, groupId) {
    e.preventDefault();
    if (dragGroupRef.current && dragGroupRef.current !== groupId) {
      setDragOverGroupId(groupId);
    }
  }

  async function handleGroupDrop(e, targetGroupId) {
    e.preventDefault();
    const draggedId = dragGroupRef.current;
    dragGroupRef.current = null;
    document.body.classList.remove("group-drag-active");
    if (!draggedId || draggedId === targetGroupId) {
      setDragOverGroupId(null); return;
    }
    const ids = groups.map(g => g.id);
    const fromIdx = ids.indexOf(draggedId);
    const toIdx   = ids.indexOf(targetGroupId);
    ids.splice(fromIdx, 1);
    ids.splice(toIdx, 0, draggedId);
    setDragOverGroupId(null);
    await reorderGroups(ids);
    silentLoad();
  }

  function handleGroupDragEnd() {
    dragGroupRef.current = null;
    document.body.classList.remove("group-drag-active");
    setDragOverGroupId(null);
  }

  return (
    <div>
      {/* ── Month navigator ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 16, marginBottom: 10 }}>
        <button className="btn btn-secondary" style={{ padding: "6px 16px", fontSize: 18, lineHeight: 1 }}
          onClick={() => setMonth(prevMonth)} aria-label="Previous month">
          ‹
        </button>

        <div style={{ position: "relative" }}>
          <button onClick={() => setShowMonthPicker(p => !p)}
            style={{
              background: "none", border: "none", color: "var(--text)",
              fontSize: 22, fontWeight: 800, cursor: "pointer", padding: "4px 8px",
              display: "flex", alignItems: "center", gap: 6,
            }}>
            {monthLabel(month)}
            <span style={{ fontSize: 13, color: "var(--text2)" }}>▾</span>
          </button>
          {showMonthPicker && (
            <MonthPicker month={month} onChange={m => { setMonth(m); }}
              onClose={() => setShowMonthPicker(false)} />
          )}
        </div>

        <button className="btn btn-secondary" style={{ padding: "6px 16px", fontSize: 18, lineHeight: 1 }}
          onClick={() => setMonth(nextMonth)} aria-label="Next month">
          ›
        </button>

        <button
          onClick={() => setShowCreateTxn(true)}
          style={{
            padding: "6px 14px", borderRadius: 6, border: "none",
            background: "var(--accent)", color: "#fff",
            fontSize: 12, fontWeight: 600, cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          + New Transaction
        </button>
      </div>

      {/* ── Zero-based budget status banner ── */}
      {summary && (
        <div style={{ textAlign: "center", marginBottom: 20 }}>
          {fullyBudgeted ? (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 16px", borderRadius: 20, fontSize: 12, fontWeight: 600,
              background: "rgba(34,197,94,0.12)", color: "#22c55e",
            }}>
              ✓ The Budget is balanced
            </span>
          ) : remainingToBudget > 0 ? (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 16px", borderRadius: 20, fontSize: 12, fontWeight: 600,
              background: "rgba(245,158,11,0.12)", color: "#f59e0b",
            }}>
              {fmt(remainingToBudget)} left to budget
            </span>
          ) : (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 16px", borderRadius: 20, fontSize: 12, fontWeight: 600,
              background: "rgba(239,68,68,0.12)", color: "var(--red)",
            }}>
              {fmt(Math.abs(remainingToBudget))} over-budgeted
            </span>
          )}
        </div>
      )}

      {loading && (
        <div style={{ color: "var(--text2)", padding: "60px 0", textAlign: "center", fontSize: 14 }}>
          Loading…
        </div>
      )}

      {/* ── Load error — tap to retry ── */}
      {!loading && loadError && (
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{ fontSize: 14, color: "var(--text2)", marginBottom: 16 }}>
            Could not load budget data.
          </div>
          <button className="btn btn-primary" onClick={load}>
            Retry
          </button>
        </div>
      )}

      {/* ── Empty state ── */}
      {!loading && !loadError && !hasGroups && (
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{ fontSize: 40, marginBottom: 16 }}>📋</div>
          <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>No budget categories yet</div>
          <div style={{ color: "var(--text2)", fontSize: 14, marginBottom: 24 }}>
            Add your first group to get started with zero-based budgeting.
          </div>
          <button className="btn btn-primary" onClick={() => setAddingGroup(true)}>
            + Add First Group
          </button>
        </div>
      )}

      {/* ── Main layout ── */}
      {!loading && hasGroups && (
        <>
        {/* Mobile tab bar — switches between the groups panel and the right panel */}
        {isMobile && (
          <div style={{
            display: "flex", marginBottom: 12,
            background: "var(--bg2)", borderRadius: 8,
            border: "1px solid var(--border)", overflow: "hidden",
          }}>
            {[
              { key: "budget",       label: "Budget" },
              { key: "summary",      label: "Summary" },
              { key: "transactions", label: "Transactions" },
            ].map(({ key, label }) => (
              <button key={key}
                onClick={() => { setMobileTab(key); if (key !== "budget") setRightTab(key); }}
                style={{
                  flex: 1, padding: "10px 0", fontSize: 13,
                  fontWeight: mobileTab === key ? 700 : 400,
                  background: mobileTab === key ? "var(--accent)" : "none",
                  border: "none",
                  color: mobileTab === key ? "#fff" : "var(--text2)",
                  cursor: "pointer",
                }}>
                {label}
              </button>
            ))}
          </div>
        )}

        <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>

          {/* LEFT — budget groups list */}
          <div style={{ flex: 1, minWidth: 0, display: isMobile && mobileTab !== "budget" ? "none" : undefined }}>
            {visibleGroups.map(g => (
              /* GroupSection is draggable. Item rows inside it are also draggable,
                 but their drag handlers return early (without stopPropagation) when
                 dragGroupRef.current is set, so group-drag events bubble up to the
                 GroupSection outer div and fire its onDragOver / onDrop handlers.
                 No overlay div is needed — the real bug was the backend 422 from
                 route ordering, not a missing capture layer. */
              <GroupSection key={g.id} group={g} month={month}
                colorIndex={groupColorIdx[g.id] ?? 0}
                onUpdate={silentLoad}
                onOpenItem={setActiveItem}
                isDragOver={dragOverGroupId === g.id && dragGroupRef.current !== g.id}
                dragHandleProps={{ onMouseDown: () => {} }}
                dragGroupRef={dragGroupRef}
                onDragStart={e => handleGroupDragStart(e, g.id)}
                onDragOver={e => handleGroupDragOver(e, g.id)}
                onDrop={e => handleGroupDrop(e, g.id)}
                onDragEnd={handleGroupDragEnd}
              />
            ))}

            {/* Add group */}
            <div style={{ marginTop: 8 }}>
              {addingGroup ? (
                <div style={{
                  display: "flex", gap: 10, alignItems: "center",
                  padding: "12px 16px", background: "var(--bg2)",
                  border: "1px solid var(--border)", borderRadius: 8,
                }}>
                  <input value={newGroupName} onChange={e => setNewGroupName(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") handleAddGroup(); if (e.key === "Escape") setAddingGroup(false); }}
                    autoFocus placeholder="Group name…"
                    style={{
                      flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
                      borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "6px 12px",
                    }}
                  />
                  <select value={newGroupType} onChange={e => setNewGroupType(e.target.value)}
                    className="form-select" style={{ fontSize: 13, width: 110 }}>
                    <option value="expense">Expense</option>
                    <option value="income">Income</option>
                  </select>
                  <button className="btn btn-primary" style={{ padding: "6px 16px" }} onClick={handleAddGroup}>Add</button>
                  <button className="btn btn-secondary" style={{ padding: "6px 16px" }} onClick={() => setAddingGroup(false)}>Cancel</button>
                </div>
              ) : (
                <button onClick={() => setAddingGroup(true)}
                  style={{
                    width: "100%", padding: "10px", textAlign: "center",
                    background: "none", border: "1px dashed var(--border)",
                    borderRadius: 8, color: "var(--text2)", fontSize: 13,
                    fontWeight: 600, cursor: "pointer",
                  }}>
                  + Add Group
                </button>
              )}
            </div>
          </div>

          {/* RIGHT — Summary / Transactions panel */}
          <div style={{
            width: isMobile ? "100%" : 340,
            flexShrink: 0,
            display: isMobile && mobileTab === "budget" ? "none" : undefined,
            position: isMobile ? "static" : "sticky",
            top: 20,
            maxHeight: isMobile ? undefined : "calc(100vh - 100px)",
            overflowY: isMobile ? undefined : "auto",
          }}>
            <div className="card" style={{ padding: 16 }}>
              {/* Panel tab bar */}
              <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: 14 }}>
                {[
                  { key: "summary", label: "Summary" },
                  { key: "transactions", label: "Transactions" },
                ].map(({ key, label }) => (
                  <button key={key} onClick={() => setRightTab(key)}
                    style={{
                      flex: 1, padding: "8px 0", fontSize: 13,
                      fontWeight: rightTab === key ? 700 : 400,
                      background: "none", border: "none",
                      borderBottom: `2px solid ${rightTab === key ? "var(--accent)" : "transparent"}`,
                      color: rightTab === key ? "var(--text)" : "var(--text2)",
                      cursor: "pointer",
                    }}>
                    {label}
                  </button>
                ))}
              </div>

              {rightTab === "summary" ? (
                <SummaryPanel groups={visibleGroups} summary={summary} />
              ) : (
                <TransactionsPanel month={month} allGroups={visibleGroups} onBudgetUpdate={silentLoad} />
              )}
            </div>
          </div>
        </div>
        </>
      )}

      {/* Item detail modal — rendered at page root so it's never clipped by a parent overflow */}
      {activeItem && (
        <ItemDetailModal
          itemId={activeItem.id}
          itemName={activeItem.name}
          month={month}
          allGroups={visibleGroups}
          onClose={() => setActiveItem(null)}
          onUpdate={load}
        />
      )}

      {/* Create manual transaction modal */}
      {showCreateTxn && (
        <CreateTransactionModal
          month={month}
          allGroups={visibleGroups}
          onClose={() => setShowCreateTxn(false)}
          onSaved={load}
        />
      )}
    </div>
  );
}
