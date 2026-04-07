/**
 * GroupSection — Collapsible budget group with drag-and-drop reordering, item list, and inline editing.
 */
import { useState, useRef } from "react";
import {
  updateBudgetGroup, deleteBudgetGroup,
  createBudgetItem, reorderItems,
} from "../../api.js";
import { fmt } from "../../utils/format.js";
import { getGroupColor, DragHandle } from "./budgetUtils.jsx";
import ItemRow from "./ItemRow.jsx";
import GroupTotalsRow from "./GroupTotalsRow.jsx";

export default function GroupSection({ group, month, colorIndex, onUpdate, onOpenItem,
                        dragHandleProps, isDragOver,
                        onDragStart, onDragOver, onDrop, onDragEnd,
                        dragGroupRef }) {
  const [collapsed, setCollapsed] = useState(false);
  const [showSpent, setShowSpent] = useState(false);  // toggles Remaining ↔ Spent column
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(group.name);
  const [addingItem, setAddingItem] = useState(false);
  const [newItemName, setNewItemName] = useState("");

  const isIncome = group.type === "income";
  const dotColor = isIncome ? "#22c55e" : getGroupColor(colorIndex);
  const remaining = group.total_planned - group.total_spent;

  async function saveName() {
    const trimmed = nameDraft.trim();
    if (!trimmed || trimmed === group.name) { setEditingName(false); return; }
    await updateBudgetGroup(group.id, { name: trimmed });
    setEditingName(false);
    onUpdate();
  }

  async function handleDelete() {
    if (!window.confirm(`Delete group "${group.name}" and all its items? This cannot be undone.`)) return;
    await deleteBudgetGroup(group.id);
    onUpdate();
  }

  async function addItem() {
    if (!newItemName.trim()) return;
    await createBudgetItem(group.id, newItemName.trim());
    setNewItemName("");
    setAddingItem(false);
    onUpdate();
  }

  // ── Item drag state (items only reorder within this group) ────────────────
  // dragItemRef is a ref (not state) so that setting it during dragstart does
  // NOT trigger a React re-render. Re-rendering mid-drag mutates the DOM and
  // causes the browser to cancel the drag operation — the same root cause that
  // broke group drag (fixed 2026-03-21). dragOverItemId stays as state because
  // it's only set during dragOver, when the drag is already live.
  const dragItemRef = useRef(null);
  const [dragOverItemId, setDragOverItemId] = useState(null);

  function handleItemDragStart(e, itemId) {
    dragItemRef.current = itemId;
    e.dataTransfer.effectAllowed = "move";
    e.stopPropagation(); // prevent triggering group drag
  }

  function handleItemDragOver(e, itemId) {
    // Use the ref (synchronous) to detect group drags — don't make item rows
    // valid drop targets during a group drag so the drop lands on the group div.
    if (dragGroupRef?.current) return;
    e.preventDefault();
    e.stopPropagation();
    if (dragItemRef.current && dragItemRef.current !== itemId) setDragOverItemId(itemId);
  }

  async function handleItemDrop(e, targetItemId) {
    // When a GROUP is being dragged, do not handle the drop here — let it
    // bubble up to the GroupSection outer div where handleGroupDrop is registered.
    if (dragGroupRef?.current || !dragItemRef.current) return;
    e.preventDefault();
    e.stopPropagation();
    const draggedId = dragItemRef.current;
    dragItemRef.current = null;
    setDragOverItemId(null);
    if (draggedId === targetItemId) return;
    // group.items is already visibility-filtered by Budget.jsx — use it directly.
    const ids = group.items.map(i => i.id);
    const fromIdx = ids.indexOf(draggedId);
    const toIdx   = ids.indexOf(targetItemId);
    if (fromIdx === -1 || toIdx === -1) return;
    ids.splice(fromIdx, 1);
    ids.splice(toIdx, 0, draggedId);
    await reorderItems(ids);
    onUpdate();
  }

  async function handleItemDropBottom(e) {
    if (dragGroupRef?.current || !dragItemRef.current) return;
    e.preventDefault();
    e.stopPropagation();
    const draggedId = dragItemRef.current;
    dragItemRef.current = null;
    setDragOverItemId(null);
    const ids = group.items.map(i => i.id).filter(id => id !== draggedId);
    ids.push(draggedId);
    await reorderItems(ids);
    onUpdate();
  }

  function handleItemDragEnd() {
    dragItemRef.current = null;
    setDragOverItemId(null);
  }

  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      className={isDragOver ? "drag-over-group" : ""}
      style={{
        marginBottom: 6, borderRadius: 8, overflow: "hidden",
        border: "1px solid var(--border)", background: "var(--bg2)",
      }}
    >
      {/* ── Group header ── */}
      <div
        className="budget-group-row"
        style={{
          display: "flex", alignItems: "center", gap: 6, padding: "11px 12px 11px 8px",
          cursor: "pointer", userSelect: "none",
        }}
        onClick={() => { if (!editingName) setCollapsed(c => !c); }}
      >
        {/* Drag handle — shown on row hover, grabs the whole group */}
        <DragHandle onMouseDown={dragHandleProps?.onMouseDown} />

        {/* Collapse chevron */}
        <span style={{ color: "var(--text2)", fontSize: 10, width: 10, flexShrink: 0 }}>
          {collapsed ? "▶" : "▼"}
        </span>

        {/* Color dot */}
        <div style={{ width: 10, height: 10, borderRadius: "50%", background: dotColor, flexShrink: 0 }} />

        {/* Group name or inline editor */}
        {editingName ? (
          <div style={{ flex: 1, display: "flex", gap: 8, alignItems: "center" }}
            onClick={e => e.stopPropagation()}>
            <input value={nameDraft} onChange={e => setNameDraft(e.target.value)}
              onBlur={saveName}
              onKeyDown={e => { if (e.key === "Enter") saveName(); if (e.key === "Escape") { setEditingName(false); setNameDraft(group.name); } }}
              autoFocus
              style={{
                flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
                borderRadius: 4, color: "var(--text)", fontSize: 14, fontWeight: 700,
                padding: "3px 8px",
              }}
            />
            <button onClick={handleDelete}
              style={{
                background: "var(--red)", border: "none", borderRadius: 6,
                color: "#fff", fontSize: 11, fontWeight: 600, padding: "4px 10px", cursor: "pointer",
              }}>
              Delete Group
            </button>
            <button onClick={e => { e.stopPropagation(); setEditingName(false); setNameDraft(group.name); }}
              aria-label="Cancel renaming"
              style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer", fontSize: 14, padding: 0 }}>
              ✕
            </button>
          </div>
        ) : (
          <span style={{ flex: 1, fontWeight: 700, fontSize: 14, color: "var(--text)" }}
            onClick={e => { e.stopPropagation(); setEditingName(true); setCollapsed(false); }}
            title="Click to rename">
            {group.name}
          </span>
        )}

        {/* Group totals summary in header (hidden while editing name) */}
        {!editingName && (
          <div style={{ fontSize: 11, color: "var(--text2)", flexShrink: 0, textAlign: "right" }}>
            <span style={{ fontWeight: 600, color: "var(--text)" }}>{fmt(group.total_spent)}</span>
            {" "}<span style={{ color: "var(--text2)" }}>of</span>{" "}
            <span>{fmt(group.total_planned)}</span>
          </div>
        )}
      </div>

      {/* ── Expanded content ── */}
      {!collapsed && (
        <>
          {/* Column headers */}
          <div style={{
            display: "grid", gridTemplateColumns: "20px 1fr 110px 90px 28px",
            gap: 8, padding: "5px 16px 5px 8px",
            background: "var(--bg3)", borderTop: "1px solid var(--border)",
          }}>
            <div />
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Item
            </div>
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "right" }}>
              Planned
            </div>
            {isIncome ? (
              <div style={{ fontSize: 10, fontWeight: 600, color: "#22c55e", textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "right" }}>
                Received
              </div>
            ) : (
              /* Clickable header toggles between Remaining and Spent */
              <button onClick={() => setShowSpent(s => !s)}
                style={{
                  background: "none", border: "none", cursor: "pointer", padding: 0,
                  fontSize: 10, fontWeight: 700, letterSpacing: "0.5px", textTransform: "uppercase",
                  color: showSpent ? "#22c55e" : "#3b82f6",
                  textAlign: "right", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 3,
                }}
                title="Click to toggle Remaining / Spent">
                {showSpent ? "Spent" : "Remaining"} ▾
              </button>
            )}
            <div />
          </div>

          {/* Item rows — visibility already handled by Budget.jsx (is_archived filter) */}
          {group.items.map(item => (
              <ItemRow key={item.id} item={item} month={month}
                groupType={group.type} showSpent={showSpent}
                onUpdate={onUpdate} onOpenItem={onOpenItem}
                isDragOver={dragOverItemId === item.id && dragItemRef.current !== item.id}
                dragHandleProps={{ onMouseDown: () => {} }}
                onDragStart={e => handleItemDragStart(e, item.id)}
                onDragOver={e => handleItemDragOver(e, item.id)}
                onDrop={e => handleItemDrop(e, item.id)}
                onDragEnd={handleItemDragEnd}
              />
            ))}

          {/* Bottom drop zone — allows dragging an item to the last position */}
          <div
            onDragOver={e => {
              if (dragGroupRef?.current || !dragItemRef.current) return;
              e.preventDefault();
              e.stopPropagation();
              setDragOverItemId("__bottom__");
            }}
            onDrop={handleItemDropBottom}
            onDragLeave={() => { if (dragOverItemId === "__bottom__") setDragOverItemId(null); }}
            style={{
              height: dragOverItemId === "__bottom__" ? 32 : 4,
              transition: "height 0.15s, background 0.15s",
              background: dragOverItemId === "__bottom__" ? "rgba(79,142,247,0.10)" : "transparent",
              borderTop: dragOverItemId === "__bottom__" ? "2px solid var(--accent)" : "2px solid transparent",
            }}
          />

          {/* Group totals row */}
          {group.items.length > 0 && (
            <GroupTotalsRow group={group} showSpent={showSpent} />
          )}

          {/* Add item link */}
          <div style={{ padding: "8px 16px" }}>
            {addingItem ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input value={newItemName} onChange={e => setNewItemName(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") addItem(); if (e.key === "Escape") setAddingItem(false); }}
                  autoFocus
                  placeholder={isIncome ? "Income source name…" : "Budget item name…"}
                  style={{
                    flex: 1, background: "var(--bg3)", border: "1px solid var(--accent)",
                    borderRadius: 6, color: "var(--text)", fontSize: 13, padding: "5px 10px",
                  }}
                />
                <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={addItem}>Add</button>
                <button className="btn btn-secondary" style={{ padding: "4px 12px", fontSize: 12 }} onClick={() => setAddingItem(false)}>Cancel</button>
              </div>
            ) : (
              <button onClick={() => setAddingItem(true)}
                style={{
                  background: "none", border: "none", cursor: "pointer", padding: 0,
                  fontSize: 12, fontWeight: 600,
                  color: isIncome ? "#22c55e" : "var(--accent)",
                }}>
                + {isIncome ? "Add Income" : "Add Item"}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
