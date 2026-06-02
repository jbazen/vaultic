/**
 * VoyaAccountCard — expandable detail card for the Voya 401K account.
 *
 * Mirrors InsperityAccountCard. Displays the balance in a collapsible header with
 * four lazy-loaded tabs sourced from /api/voya/* endpoints:
 *   Holdings (funds), Transactions (activity), Performance (personal rate of
 *   return + period balances), and Allocation (asset-class breakdown).
 *
 * Voya is local-sync only (account_number 861956). Data is captured from the
 * user's browser (Cloudflare blocks server-side fetch) and POSTed to /sync-local.
 *
 * Props:
 *   entry           — manual_entries row (id, name, value, entered_at, account_number)
 */
import { useState } from "react";
import {
  voyaHoldings, voyaTransactions, voyaPerformance, voyaAllocations,
} from "../../api.js";
import { fmt, fmtDate } from "../../utils/format.js";

const pct = v => (v == null ? "–" : `${v >= 0 ? "" : ""}${Number(v).toFixed(2)}%`);

function tabBtn(active) {
  return {
    background: active ? "var(--accent)" : "var(--bg)",
    color: active ? "#fff" : "var(--text2)",
    border: "1px solid var(--border)",
    borderRadius: 6, padding: "4px 14px",
    fontSize: 12, fontWeight: active ? 700 : 400,
    cursor: "pointer",
  };
}

/* ── Holdings tab ──────────────────────────────────────────────────────────── */

function HoldingsTab({ holdings }) {
  if (!holdings?.length) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No holdings data.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", fontSize: 13 }}>
        <thead>
          <tr className="table-header-row">
            <th className="th-cell" scope="col">Fund</th>
            <th className="th-cell right" scope="col">Units</th>
            <th className="th-cell right" scope="col">Price</th>
            <th className="th-cell right" scope="col">Balance</th>
            <th className="th-cell right" scope="col">%</th>
            <th className="th-cell right" scope="col">YTD</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h, i) => (
            <tr key={i} className="tr-row">
              <td className="td-cell">{h.fund_name}</td>
              <td className="td-cell right">{h.units?.toFixed(4)}</td>
              <td className="td-cell right">{fmt(h.unit_price)}</td>
              <td className="td-cell right bold">{fmt(h.balance)}</td>
              <td className="td-cell right">{h.pct_of_account?.toFixed(1)}%</td>
              <td className={`td-cell right ${h.ytd_pct >= 0 ? "positive" : "negative"}`}>{pct(h.ytd_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Transactions tab ──────────────────────────────────────────────────────── */

function TransactionsTab({ transactions }) {
  if (!transactions?.length) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No transaction data.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", fontSize: 13 }}>
        <thead>
          <tr className="table-header-row">
            <th className="th-cell" scope="col">Date</th>
            <th className="th-cell" scope="col">Activity</th>
            <th className="th-cell" scope="col">Fund</th>
            <th className="th-cell right" scope="col">Amount</th>
            <th className="th-cell right" scope="col">Units</th>
            <th className="th-cell right" scope="col">Price</th>
          </tr>
        </thead>
        <tbody>
          {transactions.map((t, i) => (
            <tr key={i} className="tr-row">
              <td className="td-cell">{t.trade_date}</td>
              <td className="td-cell">{t.activity}</td>
              <td className="td-cell dim">{t.fund_name || t.fund_id || "–"}</td>
              <td className="td-cell right">{t.amount != null ? fmt(t.amount) : "–"}</td>
              <td className="td-cell right">{t.units?.toFixed(4)}</td>
              <td className="td-cell right">{t.unit_price != null ? fmt(t.unit_price) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Performance tab ───────────────────────────────────────────────────────── */

function PerformanceTab({ performance }) {
  if (!performance || performance.personal_ror_ytd == null && performance.total_balance == null) {
    return <div style={{ color: "var(--text2)", fontSize: 13 }}>No performance data.</div>;
  }
  const p = performance;
  const grid = { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 24px", fontSize: 13, maxWidth: 460 };
  const lbl = { color: "var(--text2)" };
  const val = { fontWeight: 600, textAlign: "right" };
  const sep = { gridColumn: "1 / -1", borderTop: "1px solid var(--border)", marginTop: 6, paddingTop: 2 };
  return (
    <div style={grid}>
      <div style={lbl}>Personal Rate of Return (YTD)</div>
      <div style={{ ...val, color: p.personal_ror_ytd >= 0 ? "var(--green)" : "var(--red)" }}>{pct(p.personal_ror_ytd)}</div>
      <div style={lbl}>Total Balance</div>
      <div style={val}>{fmt(p.total_balance)}</div>
      {p.as_of && <><div style={lbl}>As of</div><div style={val}>{p.as_of}</div></>}
      {(p.balance_start != null || p.balance_end != null) && <div style={sep} />}
      {p.balance_start != null && <><div style={lbl}>Period Start Balance</div><div style={val}>{fmt(p.balance_start)}</div></>}
      {p.balance_end != null && <><div style={lbl}>Period End Balance</div><div style={val}>{fmt(p.balance_end)}</div></>}
      {p.growth != null && <>
        <div style={lbl}>Period Growth</div>
        <div style={{ ...val, color: p.growth >= 0 ? "var(--green)" : "var(--red)" }}>{p.growth >= 0 ? "+" : ""}{fmt(p.growth)}</div>
      </>}
    </div>
  );
}

/* ── Allocation tab ────────────────────────────────────────────────────────── */

function AllocationTab({ allocations }) {
  if (!allocations?.length) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No allocation data.</div>;
  return (
    <table style={{ width: "100%", maxWidth: 460, fontSize: 13 }}>
      <thead>
        <tr className="table-header-row">
          <th className="th-cell" scope="col">Asset Class</th>
          <th className="th-cell right" scope="col">%</th>
        </tr>
      </thead>
      <tbody>
        {allocations.map((a, i) => (
          <tr key={i} className="tr-row">
            <td className="td-cell" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: a.color || "var(--text2)", display: "inline-block", flexShrink: 0 }} />
              {a.asset_class}
            </td>
            <td className="td-cell right bold">{a.pct?.toFixed(0)}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ── Main Card ─────────────────────────────────────────────────────────────── */

const TABS = [
  { key: "holdings", label: "Holdings" },
  { key: "transactions", label: "Transactions" },
  { key: "performance", label: "Performance" },
  { key: "allocation", label: "Allocation" },
];

export default function VoyaAccountCard({ entry }) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState("holdings");
  const [loading, setLoading] = useState(false);
  const [holdings, setHoldings] = useState(null);
  const [transactions, setTransactions] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [allocations, setAllocations] = useState(null);

  async function handleToggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && holdings === null) {
      setLoading(true);
      try { setHoldings(await voyaHoldings()); }
      finally { setLoading(false); }
    }
  }

  function handleRowClick(e) {
    if (e.target.closest("button, input, a")) return;
    handleToggle();
  }

  async function handleTabChange(tab) {
    setActiveTab(tab);
    if (tab === "transactions" && transactions === null) {
      setLoading(true);
      try { setTransactions(await voyaTransactions()); }
      finally { setLoading(false); }
    }
    if (tab === "performance" && performance === null) {
      setLoading(true);
      try { setPerformance(await voyaPerformance()); }
      finally { setLoading(false); }
    }
    if (tab === "allocation" && allocations === null) {
      setLoading(true);
      try { setAllocations(await voyaAllocations()); }
      finally { setLoading(false); }
    }
  }

  return (
    <div className="account-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
      {/* Header */}
      <div onClick={handleRowClick}
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}>
        <div className="account-info">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ color: "var(--text2)", fontSize: 11, display: "inline-block",
              transition: "transform 0.15s", transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}>▶</span>
            <div className="account-name">{entry.name}</div>
          </div>
          <div className="account-meta" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0 8px" }}>
            <span className="badge badge-retirement">retirement</span>
            <span style={{ color: "var(--text2)" }}>401(k)</span>
            {entry.entered_at && <span style={{ color: "var(--text2)", opacity: 0.6 }}>· Synced {fmtDate(entry.entered_at)}</span>}
          </div>
        </div>
        <div className="account-balance">{fmt(entry.value)}</div>
      </div>

      {/* Expanded panel */}
      {expanded && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
            {TABS.map(t => (
              <button key={t.key} style={tabBtn(activeTab === t.key)}
                onClick={() => handleTabChange(t.key)}>{t.label}</button>
            ))}
          </div>
          {loading ? (
            <div style={{ color: "var(--text2)", fontSize: 13, padding: "8px 0" }}>Loading…</div>
          ) : activeTab === "holdings" ? (
            <HoldingsTab holdings={holdings} />
          ) : activeTab === "transactions" ? (
            <TransactionsTab transactions={transactions} />
          ) : activeTab === "performance" ? (
            <PerformanceTab performance={performance} />
          ) : activeTab === "allocation" ? (
            <AllocationTab allocations={allocations} />
          ) : null}
        </div>
      )}
    </div>
  );
}
