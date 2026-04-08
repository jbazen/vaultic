import { useState } from "react";
import {
  insperityStatus, insperityHoldings, insperityPrices,
  insperityTransactions, insperityPerformance,
  insperityContributions, insperityAllocations,
} from "../../api.js";
import { fmt, fmtDate, fmtPercent } from "../../utils/format.js";

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

/* ── Holdings + Prices tab ─────────────────────────────────────────────────── */

function HoldingsTab({ holdings, prices }) {
  if (!holdings?.length) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No holdings data.</div>;

  const priceMap = {};
  (prices || []).forEach(p => { priceMap[p.fund] = p; });

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", fontSize: 13 }}>
        <thead>
          <tr className="table-header-row">
            <th className="th-cell" scope="col">Fund</th>
            <th className="th-cell right" scope="col">Shares</th>
            <th className="th-cell right" scope="col">Price</th>
            <th className="th-cell right" scope="col">Balance</th>
            <th className="th-cell right" scope="col">%</th>
            <th className="th-cell right" scope="col">Prior Price</th>
            <th className="th-cell right" scope="col">Change</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h, i) => {
            const p = priceMap[h.fund];
            return (
              <tr key={i} className="tr-row">
                <td className="td-cell">{h.fund}</td>
                <td className="td-cell right">{h.shares?.toFixed(4)}</td>
                <td className="td-cell right">{fmt(h.price)}</td>
                <td className="td-cell right bold">{fmt(h.balance)}</td>
                <td className="td-cell right">{h.ratio_pct?.toFixed(1)}%</td>
                <td className="td-cell right dim">{p ? fmt(p.prior_price) : "–"}</td>
                <td className={`td-cell right ${p?.change_pct >= 0 ? "positive" : "negative"}`}>
                  {p ? `${p.change_pct >= 0 ? "+" : ""}${p.change_pct.toFixed(2)}%` : "–"}
                </td>
              </tr>
            );
          })}
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
            <th className="th-cell" scope="col">Description</th>
            <th className="th-cell" scope="col">Code</th>
            <th className="th-cell right" scope="col">Amount</th>
            <th className="th-cell right" scope="col">Shares</th>
            <th className="th-cell right" scope="col">Price</th>
          </tr>
        </thead>
        <tbody>
          {transactions.map((t, i) => (
            <tr key={i} className="tr-row">
              <td className="td-cell">{t.txn_date}</td>
              <td className="td-cell">{t.description}</td>
              <td className="td-cell dim">{t.code || "–"}</td>
              <td className="td-cell right">{fmt(t.amount)}</td>
              <td className="td-cell right">{t.shares?.toFixed(4)}</td>
              <td className="td-cell right">{fmt(t.price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Performance tab ───────────────────────────────────────────────────────── */

const PERIOD_LABELS = { "1_month": "1 Month", "3_month": "3 Month", ytd: "YTD" };

function PerformanceTab({ performance }) {
  if (!performance?.length) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No performance data.</div>;
  return (
    <table style={{ width: "100%", maxWidth: 400, fontSize: 13 }}>
      <thead>
        <tr className="table-header-row">
          <th className="th-cell" scope="col">Period</th>
          <th className="th-cell right" scope="col">Cumulative</th>
          <th className="th-cell right" scope="col">Annualized</th>
        </tr>
      </thead>
      <tbody>
        {performance.map((p, i) => (
          <tr key={i} className="tr-row">
            <td className="td-cell">{PERIOD_LABELS[p.period] || p.period}</td>
            <td className={`td-cell right ${p.cumulative >= 0 ? "positive" : "negative"}`}>
              {fmtPercent(p.cumulative)}
            </td>
            <td className={`td-cell right ${p.annualized >= 0 ? "positive" : "negative"}`}>
              {fmtPercent(p.annualized)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ── Contributions tab ─────────────────────────────────────────────────────── */

function ContributionsTab({ contributions }) {
  if (!contributions || !contributions.snapped_at) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No contributions data.</div>;
  const c = contributions;
  const grid = { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 24px", fontSize: 13, maxWidth: 500 };
  const lbl = { color: "var(--text2)" };
  const val = { fontWeight: 600, textAlign: "right" };
  return (
    <div style={grid}>
      <div style={lbl}>Pre-tax Rate</div>
      <div style={val}>{c.pretax_pct != null ? `${c.pretax_pct}%` : "–"}</div>
      <div style={lbl}>Roth Rate</div>
      <div style={val}>{c.roth_pct != null ? `${c.roth_pct}%` : "–"}</div>
      <div style={{ ...lbl, marginTop: 8, gridColumn: "1 / -1", borderTop: "1px solid var(--border)", paddingTop: 8 }} />
      <div style={lbl}>YTD Employee</div>
      <div style={val}>{fmt(c.ytd_employee)}</div>
      <div style={lbl}>YTD Employer</div>
      <div style={val}>{fmt(c.ytd_employer)}</div>
      <div style={{ ...lbl, marginTop: 8, gridColumn: "1 / -1", borderTop: "1px solid var(--border)", paddingTop: 8 }} />
      <div style={lbl}>Last Employee</div>
      <div style={val}>{fmt(c.last_ee_amount)} {c.last_ee_date ? `(${c.last_ee_date})` : ""}</div>
      <div style={lbl}>Last Employer</div>
      <div style={val}>{fmt(c.last_er_amount)} {c.last_er_date ? `(${c.last_er_date})` : ""}</div>
    </div>
  );
}

/* ── Allocations tab ───────────────────────────────────────────────────────── */

function AllocationsTab({ allocations }) {
  if (!allocations?.length) return <div style={{ color: "var(--text2)", fontSize: 13 }}>No allocation data.</div>;
  return (
    <table style={{ width: "100%", maxWidth: 500, fontSize: 13 }}>
      <thead>
        <tr className="table-header-row">
          <th className="th-cell" scope="col">Fund</th>
          <th className="th-cell" scope="col">Asset Class</th>
          <th className="th-cell right" scope="col">Target %</th>
        </tr>
      </thead>
      <tbody>
        {allocations.map((a, i) => (
          <tr key={i} className="tr-row">
            <td className="td-cell">{a.fund}</td>
            <td className="td-cell dim">{a.asset_class || "–"}</td>
            <td className="td-cell right bold">{a.target_pct?.toFixed(1)}%</td>
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
  { key: "contributions", label: "Contributions" },
  { key: "allocation", label: "Allocation" },
];

export default function InsperityAccountCard({ entry, onDelete, onToggleExclude, onRenamed }) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState("holdings");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState(null);
  const [holdings, setHoldings] = useState(null);
  const [prices, setPrices] = useState(null);
  const [transactions, setTransactions] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [contributions, setContributions] = useState(null);
  const [allocations, setAllocations] = useState(null);

  async function handleToggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && holdings === null) {
      setLoading(true);
      try {
        const [h, p, s] = await Promise.all([
          insperityHoldings(), insperityPrices(), insperityStatus(),
        ]);
        setHoldings(h);
        setPrices(p);
        setStatus(s);
      } finally { setLoading(false); }
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
      try { setTransactions(await insperityTransactions()); }
      finally { setLoading(false); }
    }
    if (tab === "performance" && performance === null) {
      setLoading(true);
      try { setPerformance(await insperityPerformance()); }
      finally { setLoading(false); }
    }
    if (tab === "contributions" && contributions === null) {
      setLoading(true);
      try { setContributions(await insperityContributions()); }
      finally { setLoading(false); }
    }
    if (tab === "allocation" && allocations === null) {
      setLoading(true);
      try { setAllocations(await insperityAllocations()); }
      finally { setLoading(false); }
    }
  }

  const vestedLabel = status?.synced && status.total_balance
    ? ` · Vested: ${fmt(status.total_balance)}` : "";
  const syncDate = status?.synced && status.last_sync
    ? fmtDate(status.last_sync) : (entry.entered_at ? fmtDate(entry.entered_at) : "");

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
            {syncDate && <span style={{ color: "var(--text2)", opacity: 0.6 }}>· Synced {syncDate}</span>}
            {vestedLabel && <span style={{ color: "var(--text2)", opacity: 0.6 }}>{vestedLabel}</span>}
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
            <HoldingsTab holdings={holdings} prices={prices} />
          ) : activeTab === "transactions" ? (
            <TransactionsTab transactions={transactions} />
          ) : activeTab === "performance" ? (
            <PerformanceTab performance={performance} />
          ) : activeTab === "contributions" ? (
            <ContributionsTab contributions={contributions} />
          ) : activeTab === "allocation" ? (
            <AllocationsTab allocations={allocations} />
          ) : null}
        </div>
      )}
    </div>
  );
}
