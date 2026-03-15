import { useState, useCallback } from "react";
import { usePlaidLink } from "react-plaid-link";
import { createLinkToken, exchangeToken } from "../api.js";

export default function PlaidLink({ onSuccess }) {
  const [linkToken, setLinkToken] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function fetchLinkToken() {
    setLoading(true);
    setError("");
    try {
      const { link_token } = await createLinkToken();
      setLinkToken(link_token);
    } catch (e) {
      setError("Failed to initialize Plaid. Check your API keys.");
    } finally {
      setLoading(false);
    }
  }

  const onPlaidSuccess = useCallback(async (publicToken, metadata) => {
    try {
      await exchangeToken(
        publicToken,
        metadata.institution?.institution_id,
        metadata.institution?.name,
      );
      setLinkToken(null);
      onSuccess?.();
    } catch (e) {
      setError("Failed to connect account. Please try again.");
    }
  }, [onSuccess]);

  const { open, ready } = usePlaidLink({
    token: linkToken,
    onSuccess: onPlaidSuccess,
    onExit: () => setLinkToken(null),
  });

  // Auto-open once we have a link token
  if (linkToken && ready) {
    open();
  }

  return (
    <div>
      <button
        className="btn btn-primary"
        onClick={fetchLinkToken}
        disabled={loading || !!linkToken}
      >
        {loading ? "Initializing…" : "+ Connect Account"}
      </button>
      {error && (
        <p style={{ color: "var(--red)", fontSize: "13px", marginTop: "8px" }}>{error}</p>
      )}
    </div>
  );
}
