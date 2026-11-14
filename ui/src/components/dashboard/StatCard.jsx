import { fmt } from "../../utils/format.js";

// Category tile in the net worth hero grid.
export default function StatCard({ label, value, color, icon, negative }) {
  return (
    <div className="category-card">
      <div className="label">{icon} {label}</div>
      <div className="value" style={{ color, fontSize: 18 }}>
        {negative ? `-${fmt(value)}` : fmt(value)}
      </div>
    </div>
  );
}
