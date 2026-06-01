import { useState, useCallback, useEffect } from "react";
import { usePlaidLink } from "react-plaid-link";
import { createUpdateLinkToken, triggerSync } from "../api.js";

/**
 * "Reconnect" button for a Plaid institution whose login expired.
 * Launches Plaid Link in update mode (token from /api/plaid/link-token/update),
 * which repairs the existing item in place — same item_id, no duplicate.
 *
 * Update mode repairs the item server-side, so there is NO public token to
 * exchange; on success we just kick a sync and let the parent refresh.
 */
export default function PlaidReconnect({ itemId, onSuccess }) {
  const [linkToken, setLinkToken] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function fetchLinkToken() {
    setLoading(true);
    setError("");
    try {
      const { link_token } = await createUpdateLinkToken(itemId);
      setLinkToken(link_token);
    } catch {
      setError("Couldn't start reconnect. Try again.");
    } finally {
      setLoading(false);
    }
  }

  const onPlaidSuccess = useCallback(async () => {
    setLinkToken(null);
    try {
      await triggerSync();
    } catch {
      /* sync also runs on the 4×/day schedule — non-fatal */
    }
    onSuccess?.();
  }, [onSuccess]);

  const onExit = useCallback((err, metadata) => {
    setLinkToken(null);
    if (err) {
      // Surface Plaid's real reason instead of a blank "something went wrong".
      console.error("Plaid Link (reconnect) exit error:", err, metadata);
      const code = err.error_code || "UNKNOWN";
      const msg = err.display_message || err.error_message || "see browser console for detail";
      setError(`Plaid: ${code} — ${msg}`);
    }
  }, []);

  const { open, ready } = usePlaidLink({
    token: linkToken,
    onSuccess: onPlaidSuccess,
    onExit,
    onEvent: (eventName, metadata) => {
      if (eventName === "ERROR") console.error("Plaid Link (reconnect) ERROR event:", metadata);
    },
  });

  // Auto-open once we have a link token
  useEffect(() => {
    if (linkToken && ready) open();
  }, [linkToken, ready, open]);

  return (
    <>
      <button
        className="btn"
        style={{ fontSize: 12, padding: "5px 12px" }}
        onClick={fetchLinkToken}
        disabled={loading || !!linkToken}
        aria-label="Reconnect this institution's Plaid login"
      >
        {loading ? "…" : "Reconnect"}
      </button>
      {error && (
        <span style={{ color: "var(--red)", fontSize: 12, marginLeft: 8 }}>{error}</span>
      )}
    </>
  );
}
