import { useState, useEffect } from "react";
import { getAccountAllocation } from "../../api.js";
import AssetCategoryDonut from "../dashboard/AssetCategoryDonut.jsx";
import I360AssetAllocation from "./I360AssetAllocation.jsx";

// ── Account Allocation (#181) ─────────────────────────────────────────────────
// Asset category donut and asset class bars for one Plaid investment account,
// from its latest holdings (categories as Parker classified the same funds).
export default function AccountAllocationPanel({ accountId }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    let alive = true;
    getAccountAllocation(accountId)
      .then(d => { if (alive) setData(d); })
      .catch(() => { if (alive) setData({ categories: [], asset_classes: [] }); });
    return () => { alive = false; };
  }, [accountId]);

  if (!data) return <div style={{ color: "var(--text2)", fontSize: 13, padding: "8px 0" }}>Loading…</div>;

  return (
    <div className="flex-wrap" style={{ display: "flex", gap: 20 }}>
      <div style={{ flex: "1 1 320px", minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Asset Category</div>
        <AssetCategoryDonut data={data.categories} />
      </div>
      <div style={{ flex: "1 1 260px", minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>Asset Class</div>
        <I360AssetAllocation data={data.asset_classes} />
      </div>
    </div>
  );
}
