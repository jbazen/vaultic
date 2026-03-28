import { fmt } from "../../utils/format.js";

export default function GroupTotalsRow({ group, showSpent }) {
  const isIncome = group.type === "income";
  const remaining = group.total_planned - group.total_spent;

  return (
    <div style={{
      display: "grid", gridTemplateColumns: "1fr 110px 90px 28px",
      gap: 8, alignItems: "center",
      padding: "8px 16px",
      background: "var(--bg3)",
      borderTop: "1px solid var(--border)",
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        Total
      </div>
      <div style={{ textAlign: "right", fontSize: 13, fontWeight: 700, color: "var(--text)" }}>
        {fmt(group.total_planned)}
      </div>
      <div style={{ textAlign: "right", fontSize: 13, fontWeight: 700 }}>
        {isIncome ? (
          <span style={{ color: group.total_spent > 0 ? "#22c55e" : "var(--text2)" }}>
            {group.total_spent > 0 ? fmt(group.total_spent) : "—"}
          </span>
        ) : showSpent ? (
          <span style={{ color: group.total_spent > 0 ? "#22c55e" : "var(--text2)" }}>
            {group.total_spent > 0 ? fmt(group.total_spent) : "—"}
          </span>
        ) : (
          <span style={{ color: remaining < 0 ? "var(--red)" : "#3b82f6" }}>
            {remaining < 0 ? `-${fmt(Math.abs(remaining))}` : fmt(remaining)}
          </span>
        )}
      </div>
      <div />
    </div>
  );
}
