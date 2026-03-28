/**
 * DonutChart — SVG donut visualization of expense group totals (planned, spent, or remaining).
 */
import { getGroupColor } from "./budgetUtils.jsx";
import { fmt } from "../../utils/format.js";

export default function DonutChart({ groups, mode, summary }) {
  const size = 200;
  const cx = 100, cy = 100;
  const r = 72;
  const strokeW = 32;
  const circ = 2 * Math.PI * r;

  // Only expense groups contribute to the donut segments
  const expGroups = groups.filter(g => g.type !== "income");

  // Pick the value for each segment based on the current summary mode
  function segVal(g) {
    if (mode === "spent") return g.total_spent;
    if (mode === "remaining") return Math.max(0, g.total_planned - g.total_spent);
    return g.total_planned; // "planned" (default)
  }

  const total = expGroups.reduce((sum, g) => sum + segVal(g), 0);

  // Build arc segments — each one continues where the last left off
  let cumLen = 0;
  const segments = expGroups.map((g, i) => {
    const val = segVal(g);
    const len = total > 0 ? (val / total) * circ : 0;
    const seg = { g, len, offset: cumLen, color: getGroupColor(i) };
    cumLen += len;
    return seg;
  });

  // Center label depends on mode
  let centerLabel, centerValue;
  if (mode === "spent") {
    centerLabel = "SPENT";
    centerValue = summary.total_expense_spent;
  } else if (mode === "remaining") {
    centerLabel = "LEFT";
    centerValue = summary.total_income_planned - summary.total_expense_spent;
  } else {
    centerLabel = "INCOME";
    centerValue = summary.total_income_planned;
  }

  return (
    <div style={{ position: "relative", width: size, height: size, margin: "0 auto" }}>
      {/* Rotate -90° so the first segment starts at 12 o'clock */}
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        {/* Gray background track */}
        <circle cx={cx} cy={cy} r={r} fill="none"
          stroke="var(--bg3)" strokeWidth={strokeW} />
        {/* Colored segments */}
        {segments.map((seg, i) =>
          seg.len > 0 && (
            <circle key={i} cx={cx} cy={cy} r={r}
              fill="none" stroke={seg.color} strokeWidth={strokeW}
              strokeDasharray={`${seg.len} ${circ}`}
              strokeDashoffset={-seg.offset}
            />
          )
        )}
      </svg>

      {/* Center text overlay — not rotated */}
      <div style={{
        position: "absolute", top: "50%", left: "50%",
        transform: "translate(-50%, -50%)",
        textAlign: "center", pointerEvents: "none",
      }}>
        <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text2)", letterSpacing: "1px" }}>
          {centerLabel}
        </div>
        <div style={{ fontSize: 18, fontWeight: 800, color: "var(--text)", lineHeight: 1.2, marginTop: 2 }}>
          {fmt(centerValue)}
        </div>
      </div>
    </div>
  );
}
