// Pure-display meta-line label that renders a manual entry's account number when
// present. Reused by the generic manual-entry rows in the Dashboard's account list
// (ManualSimpleRow, ManualInvestmentCard).
// Insperity/Voya have bespoke cards that render the account number themselves.
export default function AccountNumberMeta({ accountNumber }) {
  if (!accountNumber) return null;
  return (
    <span style={{ color: "var(--text2)", opacity: 0.6 }} title="Account number">
      · #{accountNumber}
    </span>
  );
}
