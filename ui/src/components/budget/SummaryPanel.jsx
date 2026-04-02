/**
 * SummaryPanel — Budget overview sidebar with donut chart, income/expense group breakdown, and balance indicator.
 */
import { useState } from "react";
import DonutChart from "./DonutChart.jsx";
import { getGroupColor } from "./budgetUtils.jsx";
import { fmt } from "../../utils/format.js";

export default function SummaryPanel({ groups, summary }) {
  // mode controls what the donut center + highlighted column show
  const [mode, setMode] = useState("planned");

  const expGroups = groups.filter(g => g.type !== "income");
  const incGroups = groups.filter(g => g.type === "income");

  // Map expense groups to their palette index (same as GroupSection)
  let expIdx = 0;
  const groupColors = {};
  groups.forEach(g => {
    if (g.type !== "income") groupColors[g.id] = expIdx++;
  });

  function modeBtn(m, label) {
    const active = mode === m;
    return (
      <button onClick={() => setMode(m)} style={{
        flex: 1, padding: "5px 0", fontSize: 10, fontWeight: active ? 700 : 500,
        background: "none", border: "none",
        borderBottom: `2px solid ${active ? "var(--accent)" : "transparent"}`,
        color: active ? "var(--accent)" : "var(--text2)",
        cursor: "pointer", letterSpacing: "0.8px", textTransform: "uppercase",
      }}>
        {label}
      </button>
    );
  }

  return (
    <div>
      {/* Mode selector */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: 16 }}>
        {modeBtn("planned", "Planned")}
        {modeBtn("spent", "Spent")}
        {modeBtn("remaining", "Left")}
      </div>

      {/* Donut chart */}
      <DonutChart groups={groups} mode={mode} summary={summary} />

      {/* Group breakdown table */}
      <div style={{ marginTop: 16 }}>
        {/* Table header */}
        <div style={{
          display: "grid", gridTemplateColumns: "1fr 72px 72px 72px",
          gap: 4, padding: "4px 0 6px",
          borderBottom: "1px solid var(--border)",
        }}>
          {["", "Planned", "Spent", "Left"].map((h, i) => (
            <div key={i} style={{
              fontSize: 9, fontWeight: 700, color: i > 0 && mode === ["", "planned", "spent", "remaining"][i] ? "var(--accent)" : "var(--text2)",
              textAlign: i === 0 ? "left" : "right",
              textTransform: "uppercase", letterSpacing: "0.6px",
            }}>{h}</div>
          ))}
        </div>

        {/* Expense groups */}
        {expGroups.map(g => {
          const remaining = g.total_planned - g.total_spent;
          const ci = groupColors[g.id] ?? 0;
          return (
            <div key={g.id} style={{
              display: "grid", gridTemplateColumns: "1fr 72px 72px 72px",
              gap: 4, padding: "5px 0",
              borderBottom: "1px solid var(--border)",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: getGroupColor(ci), flexShrink: 0 }} />
                <span style={{ fontSize: 11, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {g.name}
                </span>
              </div>
              <div style={{ fontSize: 11, textAlign: "right", color: "var(--text2)" }}>
                {g.total_planned > 0 ? fmt(g.total_planned) : "—"}
              </div>
              <div style={{ fontSize: 11, textAlign: "right", color: g.total_spent > 0 ? "var(--text)" : "var(--text2)" }}>
                {g.total_spent > 0 ? fmt(g.total_spent) : "—"}
              </div>
              <div style={{ fontSize: 11, textAlign: "right", fontWeight: 600,
                color: remaining < 0 ? "var(--red)" : remaining > 0 ? "#3b82f6" : "var(--text2)" }}>
                {g.total_planned > 0 ? fmt(Math.abs(remaining)) : "—"}
              </div>
            </div>
          );
        })}

        {/* Income groups */}
        {incGroups.map(g => (
          <div key={g.id} style={{
            display: "grid", gridTemplateColumns: "1fr 72px 72px 72px",
            gap: 4, padding: "5px 0",
            borderBottom: "1px solid var(--border)",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", flexShrink: 0 }} />
              <span style={{ fontSize: 11, color: "var(--text)" }}>{g.name}</span>
            </div>
            <div style={{ fontSize: 11, textAlign: "right", color: "var(--text2)" }}>
              {fmt(g.total_planned)}
            </div>
            <div style={{ fontSize: 11, textAlign: "right", color: g.total_spent !== 0 ? "#22c55e" : "var(--text2)" }}>
              {g.total_spent !== 0 ? fmt(Math.abs(g.total_spent)) : "—"}
            </div>
            <div style={{ fontSize: 11, textAlign: "right", color: "var(--text2)" }}>—</div>
          </div>
        ))}
      </div>
    </div>
  );
}
