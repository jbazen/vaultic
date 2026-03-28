/**
 * MonthPicker — Dropdown calendar for selecting a budget month and year.
 */
import { useState, useEffect, useRef } from "react";
import { MONTH_NAMES } from "./budgetUtils.jsx";

export default function MonthPicker({ month, onChange, onClose }) {
  const [pickerYear, setPickerYear] = useState(parseInt(month.split("-")[0]));
  const ref = useRef(null);

  // Close on outside click
  useEffect(() => {
    function handle(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [onClose]);

  return (
    <div ref={ref} style={{
      position: "absolute", top: "calc(100% + 8px)", left: "50%",
      transform: "translateX(-50%)", zIndex: 300,
      background: "var(--bg2)", border: "1px solid var(--border)",
      borderRadius: 12, padding: 16, boxShadow: "0 12px 32px rgba(0,0,0,0.5)",
      minWidth: 260,
    }}>
      {/* Year navigation */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <button onClick={() => setPickerYear(y => y - 1)} aria-label="Previous year"
          style={{ background: "none", border: "none", color: "var(--text)", cursor: "pointer", fontSize: 20, padding: "0 8px", lineHeight: 1 }}>
          ‹
        </button>
        <span style={{ fontWeight: 700, fontSize: 15, color: "var(--text)" }}>{pickerYear}</span>
        <button onClick={() => setPickerYear(y => y + 1)} aria-label="Next year"
          style={{ background: "none", border: "none", color: "var(--text)", cursor: "pointer", fontSize: 20, padding: "0 8px", lineHeight: 1 }}>
          ›
        </button>
      </div>

      {/* Month grid — 4 columns × 3 rows */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
        {MONTH_NAMES.map((name, i) => {
          const m = `${pickerYear}-${String(i + 1).padStart(2, "0")}`;
          const selected = m === month;
          return (
            <button key={name} onClick={() => { onChange(m); onClose(); }}
              style={{
                padding: "8px 0", borderRadius: 6, fontSize: 13,
                fontWeight: selected ? 700 : 400,
                background: selected ? "var(--accent)" : "transparent",
                color: selected ? "#fff" : "var(--text)",
                border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                cursor: "pointer",
              }}>
              {name}
            </button>
          );
        })}
      </div>
    </div>
  );
}
