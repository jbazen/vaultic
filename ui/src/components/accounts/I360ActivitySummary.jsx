import { fmt, fmtDate } from "../../utils/format.js";

function Row({ label, value, color, bold }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
      <span style={{ color: "var(--text2)", fontSize: 13 }}>{label}</span>
      <span style={{ color: color || "var(--text)", fontSize: 13, fontWeight: bold ? 700 : 400 }}>
        {value != null ? fmt(value) : "—"}
      </span>
    </div>
  );
}

export default function I360ActivitySummary({ data }) {
  if (!data) {
    return <div style={{ color: "var(--text2)", fontSize: 13, padding: "12px 0" }}>No activity summary available.</div>;
  }

  const netChange = data.net_change;
  const netColor = netChange > 0 ? "var(--green)" : netChange < 0 ? "var(--red)" : "var(--text)";

  return (
    <div>
      <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 12 }}>
        {fmtDate(data.start_date)} — {fmtDate(data.end_date)}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 32px" }}>
        <div>
          <Row label="Beginning Balance" value={data.beginning_balance} />
          <Row label="Net Contributions / Withdrawals" value={data.net_contributions_withdrawals} />
          <Row label="Change in Value" value={data.positions_change_in_value} />
          <Row label="Interest" value={data.interest} />
          <Row label="Capital Gains" value={data.cap_gains} />
        </div>
        <div>
          <Row label="Management Fee" value={data.management_fee} color={data.management_fee < 0 ? "var(--red)" : undefined} />
          <Row label="Fees Paid" value={data.management_fees_paid} color={data.management_fees_paid < 0 ? "var(--red)" : undefined} />
          <Row label="12b-1 Credits" value={data.credits_12b1} />
          <Row label="Net Change" value={netChange} color={netColor} bold />
          <Row label="Ending Balance" value={data.ending_balance} bold />
        </div>
      </div>
      {data.total_gain_loss_after_fee != null && (
        <div style={{ marginTop: 12, padding: "8px 12px", borderRadius: 6, background: "var(--bg2)", display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--text2)", fontSize: 13 }}>Total Gain/Loss (After Fees)</span>
          <span style={{
            fontWeight: 700, fontSize: 14,
            color: data.total_gain_loss_after_fee > 0 ? "var(--green)" : data.total_gain_loss_after_fee < 0 ? "var(--red)" : "var(--text)",
          }}>
            {data.total_gain_loss_after_fee > 0 ? "+" : ""}{fmt(data.total_gain_loss_after_fee)}
          </span>
        </div>
      )}
    </div>
  );
}
