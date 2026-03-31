/**
 * ItemRow — Single budget line item row with inline name editing, planned amount, and drag-to-reorder.
 */
import { useState } from "react";
import { updateBudgetItem, deleteBudgetItem, setBudgetAmount } from "../../api.js";
import { fmt } from "../../utils/format.js";
import AmountCell from "./AmountCell.jsx";
import { DragHandle } from "./budgetUtils.jsx";

export default function ItemRow({ item, month, groupType, showSpent, onUpdate, onOpenItem,
                   dragHandleProps, isDragOver,
                   onDragStart, onDragOver, onDrop, onDragEnd }) {
  const isIncome = groupType === "income";
  const remaining = item.planned - item.spent;
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft]     = useState(item.name);

  async function saveName() {
    const trimmed = nameDraft.trim();
    setEditingName(false);
    if (!trimmed || trimmed === item.name) return;
    await updateBudgetItem(item.id, trimmed);
    onUpdate();
  }

  function valueCell() {
    if (isIncome) {
      return (
        <span style={{ fontSize: 13, color: item.spent !== 0 ? "#22c55e" : "var(--text2)" }}>
          {item.spent !== 0 ? fmt(Math.abs(item.spent)) : "—"}
        </span>
      );
    }
    if (showSpent) {
      return (
        <span style={{ fontSize: 13, fontWeight: 600, color: item.spent !== 0 ? "#22c55e" : "var(--text2)" }}>
          {item.spent !== 0 ? fmt(Math.abs(item.spent)) : "—"}
        </span>
      );
    }
    if (item.planned === 0) {
      return <span style={{ fontSize: 13, color: "var(--text2)" }}>—</span>;
    }
    return (
      <span style={{ fontSize: 13, fontWeight: 600, color: remaining < 0 ? "var(--red)" : "#3b82f6" }}>
        {remaining < 0 ? `-${fmt(Math.abs(remaining))}` : fmt(remaining)}
      </span>
    );
  }

  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      className={`budget-item-row${isDragOver ? " drag-over-item" : ""}`}
      onClick={() => onOpenItem?.(item)}
      style={{
        display: "grid", gridTemplateColumns: "20px 1fr 110px 90px 28px",
        gap: 8, alignItems: "center",
        padding: "9px 16px 9px 8px",
        borderBottom: "1px solid var(--border)",
        cursor: "pointer",
      }}
    >
      {/* Drag handle */}
      <DragHandle onMouseDown={dragHandleProps?.onMouseDown} />

      {/* Item name — only the text span is click-to-rename; rest of the cell
          bubbles up to the row's onClick which opens the detail modal. */}
      <div style={{ minWidth: 0 }}>
        {editingName ? (
          <input
            value={nameDraft}
            onChange={e => setNameDraft(e.target.value)}
            onBlur={saveName}
            onClick={e => e.stopPropagation()}
            onKeyDown={e => { if (e.key === "Enter") saveName(); if (e.key === "Escape") { setEditingName(false); setNameDraft(item.name); } }}
            autoFocus
            style={{
              width: "100%", background: "var(--bg3)", border: "1px solid var(--accent)",
              borderRadius: 4, color: "var(--text)", fontSize: 13, padding: "2px 6px",
            }}
          />
        ) : (
          <span
            onClick={e => { e.stopPropagation(); setNameDraft(item.name); setEditingName(true); }}
            title="Click to rename"
            style={{ fontSize: 13, color: "var(--text)", cursor: "text",
              display: "inline-block", maxWidth: "100%",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {item.name}
          </span>
        )}
      </div>

      {/* Planned — click to edit; stop propagation so it doesn't open the modal */}
      <div onClick={e => e.stopPropagation()}>
        <AmountCell value={item.planned}
          onSave={v => setBudgetAmount(item.id, month, v).then(onUpdate)} />
      </div>

      <div style={{ textAlign: "right" }}>{valueCell()}</div>

      {/* Delete */}
      <button
        onClick={e => { e.stopPropagation(); if (window.confirm(`Delete "${item.name}"?`)) deleteBudgetItem(item.id).then(onUpdate); }}
        title="Delete item"
        aria-label="Delete item"
        style={{
          background: "none", border: "none", color: "var(--text2)",
          cursor: "pointer", fontSize: 13, padding: 0, opacity: 0.5,
          lineHeight: 1,
        }}
        onMouseEnter={e => e.currentTarget.style.opacity = 1}
        onMouseLeave={e => e.currentTarget.style.opacity = 0.5}>
        ✕
      </button>
    </div>
  );
}
