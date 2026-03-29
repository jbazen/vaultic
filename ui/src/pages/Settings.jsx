import { useState, useEffect, useRef } from "react";
import {
  getMe, getUsers, createUser, deleteUser, changePassword,
  totpSetup, totpConfirm, disable2FA, getSecurityLog,
  subscribePush, unsubscribePush, getPushSubscription, sendTestPush,
  revokeAllSessions,
} from "../api.js";

// ── Helpers ──────────────────────────────────────────────────────────────────

function Section({ title, children }) {
  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 16, paddingBottom: 12,
        borderBottom: "1px solid var(--border)" }}>{title}</div>
      {children}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      {children}
    </div>
  );
}

function StatusMsg({ msg, type }) {
  if (!msg) return null;
  return (
    <p style={{ fontSize: 13, color: type === "error" ? "var(--red)" : "var(--green)",
      marginTop: 8, marginBottom: 0 }}>{msg}</p>
  );
}

// ── Change Password ───────────────────────────────────────────────────────────

function ChangePassword() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState(null);
  const [loading, setLoading] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (next !== confirm) { setMsg({ text: "Passwords don't match", type: "error" }); return; }
    if (next.length < 8) { setMsg({ text: "Password must be at least 8 characters", type: "error" }); return; }
    setLoading(true);
    try {
      await changePassword(cur, next);
      setMsg({ text: "Password updated", type: "ok" });
      setCur(""); setNext(""); setConfirm("");
    } catch (e) {
      setMsg({ text: e.message || "Failed", type: "error" });
    } finally { setLoading(false); }
  }

  return (
    <form onSubmit={submit}>
      <Field label="Current password">
        <input className="form-input" type="password" value={cur} onChange={e => setCur(e.target.value)} required />
      </Field>
      <Field label="New password">
        <input className="form-input" type="password" value={next} onChange={e => setNext(e.target.value)} required />
      </Field>
      <Field label="Confirm new password">
        <input className="form-input" type="password" value={confirm} onChange={e => setConfirm(e.target.value)} required />
      </Field>
      <StatusMsg msg={msg?.text} type={msg?.type} />
      <button className="btn btn-primary" type="submit" disabled={loading} style={{ marginTop: 8 }}>
        {loading ? "Saving…" : "Update password"}
      </button>
    </form>
  );
}

// ── 2FA (TOTP) ────────────────────────────────────────────────────────────────

function TwoFA({ me, onRefresh }) {
  const [step, setStep] = useState("idle"); // idle | scanning | done
  const [qrSvg, setQrSvg] = useState("");
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState(null);
  const [loading, setLoading] = useState(false);

  async function startSetup() {
    setLoading(true); setMsg(null);
    try {
      const svg = await totpSetup();
      setQrSvg(svg);
      setStep("scanning");
    } catch (err) {
      setMsg({ text: err.message, type: "error" });
    } finally { setLoading(false); }
  }

  async function confirmCode(e) {
    e.preventDefault();
    setLoading(true); setMsg(null);
    try {
      await totpConfirm(code);
      setStep("done");
      setMsg({ text: "Two-factor authentication enabled!", type: "ok" });
      onRefresh();
    } catch (err) {
      setMsg({ text: err.message, type: "error" });
    } finally { setLoading(false); }
  }

  async function handleDisable() {
    const pwd = prompt("Enter your password to disable two-factor authentication:");
    if (!pwd) return;
    setLoading(true);
    try {
      await disable2FA(pwd);
      onRefresh();
    } catch (err) {
      setMsg({ text: "Incorrect password", type: "error" });
    } finally { setLoading(false); }
  }

  if (me?.two_fa_enabled) {
    return (
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <span style={{ fontSize: 20 }}>✓</span>
          <span style={{ color: "var(--green)", fontWeight: 600, fontSize: 15 }}>
            Two-factor authentication is enabled
          </span>
        </div>
        <p style={{ color: "var(--text2)", fontSize: 13, marginBottom: 16 }}>
          You're using an authenticator app (Google Authenticator, Authy, etc.) for login verification.
        </p>
        <button className="btn btn-danger" onClick={handleDisable} disabled={loading}>
          Disable 2FA
        </button>
      </div>
    );
  }

  return (
    <div>
      {step === "idle" && (
        <>
          <p style={{ color: "var(--text2)", fontSize: 13, marginBottom: 16 }}>
            Use an authenticator app (Google Authenticator, Authy, or Microsoft Authenticator)
            to generate login codes. Free, offline, and more secure than SMS.
          </p>
          <StatusMsg msg={msg?.text} type={msg?.type} />
          <button className="btn btn-primary" onClick={startSetup} disabled={loading}>
            {loading ? "Generating…" : "Set up authenticator"}
          </button>
        </>
      )}

      {step === "scanning" && (
        <div>
          <p style={{ color: "var(--text2)", fontSize: 13, marginBottom: 16 }}>
            <strong style={{ color: "var(--text)" }}>Step 1:</strong> Scan this QR code with your authenticator app.
          </p>
          <div style={{
            background: "#fff", borderRadius: 8, padding: 16, display: "inline-block", marginBottom: 20,
            lineHeight: 0,
          }}>
            <div style={{ width: 200, height: 200 }} ref={el => {
              if (!el || !qrSvg) return;
              el.innerHTML = "";
              const doc = new DOMParser().parseFromString(qrSvg, "image/svg+xml");
              const svg = doc.querySelector("svg");
              if (svg) {
                svg.setAttribute("width", "200");
                svg.setAttribute("height", "200");
                el.appendChild(document.importNode(svg, true));
              }
            }} />
          </div>
          <p style={{ color: "var(--text2)", fontSize: 13, marginBottom: 12 }}>
            <strong style={{ color: "var(--text)" }}>Step 2:</strong> Enter the 6-digit code from your app to confirm.
          </p>
          <form onSubmit={confirmCode}>
            <Field label="Verification code">
              <input
                className="form-input"
                type="text"
                inputMode="numeric"
                maxLength={6}
                value={code}
                onChange={e => setCode(e.target.value.replace(/\D/g, ""))}
                placeholder="000000"
                autoFocus
                style={{ textAlign: "center", fontSize: "22px", letterSpacing: "0.4em", maxWidth: 180 }}
              />
            </Field>
            <StatusMsg msg={msg?.text} type={msg?.type} />
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button className="btn btn-primary" type="submit" disabled={loading || code.length < 6}>
                {loading ? "Verifying…" : "Activate 2FA"}
              </button>
              <button type="button" className="btn btn-secondary"
                onClick={() => { setStep("idle"); setCode(""); setMsg(null); setQrSvg(""); }}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {step === "done" && <StatusMsg msg={msg?.text} type={msg?.type} />}
    </div>
  );
}

// ── User Management ───────────────────────────────────────────────────────────

function UserManagement({ currentUser }) {
  const [users, setUsers] = useState([]);
  const [newUser, setNewUser] = useState("");
  const [newPass, setNewPass] = useState("");
  const [msg, setMsg] = useState(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    try { setUsers(await getUsers()); } catch {}
  }

  useEffect(() => { load(); }, []);

  async function handleCreate(e) {
    e.preventDefault();
    setLoading(true); setMsg(null);
    try {
      await createUser(newUser, newPass);
      setMsg({ text: `User "${newUser}" created`, type: "ok" });
      setNewUser(""); setNewPass("");
      load();
    } catch (err) {
      setMsg({ text: err.message || "Failed", type: "error" });
    } finally { setLoading(false); }
  }

  async function handleDelete(username) {
    if (!confirm(`Deactivate user "${username}"?`)) return;
    try {
      await deleteUser(username);
      load();
    } catch (err) {
      setMsg({ text: err.message || "Failed", type: "error" });
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        {users.map(u => (
          <div key={u.id} style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "10px 14px", background: "var(--bg3)", borderRadius: 8,
            border: "1px solid var(--border)", marginBottom: 8,
          }}>
            <div>
              <span style={{ fontWeight: 600, fontSize: 14 }}>{u.username}</span>
              {u.two_fa_enabled ? <span style={{ marginLeft: 8, fontSize: 11, color: "var(--green)" }}>2FA</span> : null}
              {!u.is_active && <span style={{ marginLeft: 8, fontSize: 11, color: "var(--red)" }}>inactive</span>}
            </div>
            {u.username !== currentUser && u.is_active && (
              <button className="btn btn-danger" style={{ fontSize: 12, padding: "4px 10px" }}
                onClick={() => handleDelete(u.username)}>
                Deactivate
              </button>
            )}
          </div>
        ))}
      </div>
      <form onSubmit={handleCreate} style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <div style={{ flex: 1 }}>
          <label className="form-label">Username</label>
          <input className="form-input" value={newUser} onChange={e => setNewUser(e.target.value)} required />
        </div>
        <div style={{ flex: 1 }}>
          <label className="form-label">Password</label>
          <input className="form-input" type="password" value={newPass}
            onChange={e => setNewPass(e.target.value)} required />
        </div>
        <button className="btn btn-primary" type="submit" disabled={loading}
          style={{ flexShrink: 0, alignSelf: "flex-end" }}>
          {loading ? "…" : "Add user"}
        </button>
      </form>
      <StatusMsg msg={msg?.text} type={msg?.type} />
    </div>
  );
}

// ── Security Log ──────────────────────────────────────────────────────────────

function SecurityLog() {
  const [lines, setLines] = useState([]);
  const [tailing, setTailing] = useState(true);
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const intervalRef = useRef(null);

  async function fetchLog() {
    try {
      const data = await getSecurityLog(1000);
      setLines(data.lines);
    } catch {}
  }

  useEffect(() => {
    setLoading(true);
    fetchLog().finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (tailing) {
      intervalRef.current = setInterval(fetchLog, 3000);
    } else {
      clearInterval(intervalRef.current);
    }
    return () => clearInterval(intervalRef.current);
  }, [tailing]);

  useEffect(() => {
    if (tailing && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [lines, tailing]);

  function getLineColor(line) {
    if (line.includes("LOGIN_SUCCESS") || line.includes("2FA_SUCCESS") || line.includes("TOKEN_ISSUED")) return "var(--green)";
    if (line.includes("FAILED") || line.includes("HTTP_4") || line.includes("HTTP_5") || line.includes("RATE_LIMITED")) return "var(--red)";
    if (line.includes("SYNC")) return "var(--yellow)";
    if (line.includes("SAGE_QUERY")) return "var(--purple)";
    if (line.includes("PLAID")) return "#60a5fa";
    if (line.includes("SERVER")) return "var(--text2)";
    return "var(--text)";
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontSize: 13, color: "var(--text2)" }}>
          {lines.length} entries
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className={`btn ${tailing ? "btn-primary" : "btn-secondary"}`}
            style={{ fontSize: 12, padding: "4px 12px" }}
            onClick={() => setTailing(v => !v)}
          >
            {tailing ? "⏸ Pause" : "▶ Live tail"}
          </button>
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: "4px 12px" }}
            onClick={fetchLog}>
            ↻ Refresh
          </button>
        </div>
      </div>
      <div style={{
        background: "#0a0d14",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "12px 14px",
        fontFamily: "monospace",
        fontSize: 12,
        lineHeight: 1.6,
        maxHeight: 480,
        overflowY: "auto",
      }}>
        {loading && lines.length === 0 && (
          <span style={{ color: "var(--text2)" }}>Loading…</span>
        )}
        {lines.map((line, i) => (
          <div key={i} style={{ color: getLineColor(line), whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {line}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Main Settings page ────────────────────────────────────────────────────────

// ── Push Notifications ────────────────────────────────────────────────────────

/**
 * Lets the user enable or disable Web Push notifications on this device.
 *
 * Flow:
 *   1. On mount, check if the browser is already subscribed via PushManager
 *   2. "Enable" button: requests OS permission then POSTs subscription to server
 *   3. "Disable" button: calls PushManager.unsubscribe() + notifies server
 *
 * Notifications are sent by the server after each Plaid sync when new
 * transactions are auto-categorized and waiting for approval.
 */
function PushNotifications() {
  const [supported,   setSupported]   = useState(false);
  const [subscribed,  setSubscribed]  = useState(false);
  const [permission,  setPermission]  = useState("default");
  const [loading,     setLoading]     = useState(true);
  const [msg,         setMsg]         = useState(null);

  useEffect(() => {
    // Push requires service workers, PushManager, and Notification API
    const ok = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
    setSupported(ok);
    setPermission(ok ? Notification.permission : "unsupported");

    if (ok) {
      getPushSubscription()
        .then(sub => setSubscribed(!!sub))
        .catch(() => setSubscribed(false))
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  async function handleEnable() {
    setLoading(true);
    setMsg(null);
    try {
      await subscribePush();
      setSubscribed(true);
      setPermission(Notification.permission);
      setMsg({ text: "Notifications enabled on this device.", type: "ok" });
    } catch (err) {
      setMsg({ text: err.message || "Failed to enable notifications.", type: "error" });
    } finally {
      setLoading(false);
    }
  }

  async function handleDisable() {
    setLoading(true);
    setMsg(null);
    try {
      await unsubscribePush();
      setSubscribed(false);
      setMsg({ text: "Notifications disabled on this device.", type: "ok" });
    } catch (err) {
      setMsg({ text: err.message || "Failed to disable notifications.", type: "error" });
    } finally {
      setLoading(false);
    }
  }

  if (!supported) {
    return (
      <p style={{ fontSize: 13, color: "var(--text2)" }}>
        Push notifications are not supported in this browser.
        Try Chrome or Edge on Android, or Safari 16.4+ on iOS.
      </p>
    );
  }

  return (
    <div>
      <p style={{ fontSize: 13, color: "var(--text2)", marginBottom: 16 }}>
        Get notified on this device when transactions are synced and ready for
        your review. Notifications are sent once per sync — no spam.
      </p>

      {/* Status badge */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <div style={{
          width: 10, height: 10, borderRadius: "50%",
          background: subscribed ? "var(--green)" : "var(--text2)",
          flexShrink: 0,
        }} />
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          {loading      ? "Checking…"
           : subscribed ? "Enabled on this device"
                        : "Disabled on this device"}
        </span>
      </div>

      {/* OS permission warning */}
      {permission === "denied" && (
        <p style={{ fontSize: 12, color: "var(--red)", marginBottom: 12 }}>
          Notifications are blocked in your browser settings. Open browser
          Settings → Site Permissions → Notifications and allow vaulticsage.com,
          then try again.
        </p>
      )}

      {/* Action buttons */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {subscribed ? (
          <button
            className="btn btn-secondary"
            onClick={handleDisable}
            disabled={loading}
            style={{ fontSize: 13 }}
          >
            Disable Notifications
          </button>
        ) : (
          <button
            className="btn btn-primary"
            onClick={handleEnable}
            disabled={loading || permission === "denied"}
            style={{ fontSize: 13 }}
          >
            Enable Notifications
          </button>
        )}

        {/* Test button — only shown when subscribed so there's something to send to */}
        {subscribed && (
          <button
            className="btn btn-secondary"
            disabled={loading}
            style={{ fontSize: 13 }}
            onClick={async () => {
              setLoading(true);
              setMsg(null);
              try {
                const res = await sendTestPush();
                setMsg({ text: `Test sent to ${res.sent} device(s).`, type: "ok" });
              } catch (err) {
                setMsg({ text: err.message || "Test failed.", type: "error" });
              } finally {
                setLoading(false);
              }
            }}
          >
            Send Test
          </button>
        )}
      </div>

      <StatusMsg msg={msg?.text} type={msg?.type === "error" ? "error" : "success"} />
    </div>
  );
}


function ActiveSessions() {
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  async function handleRevoke() {
    if (!window.confirm("Sign out all mobile devices? They will need to log in again.")) return;
    setLoading(true);
    try {
      await revokeAllSessions();
      setDone(true);
    } catch (e) {
      alert(e.message || "Failed to revoke sessions.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <p style={{ fontSize: 13, color: "var(--text2)", marginBottom: 14 }}>
        Signs out every device using a "Keep me signed in" session (mobile).
        Your current web session stays active.
      </p>
      {done
        ? <p style={{ color: "var(--green)", fontSize: 13 }}>All mobile sessions signed out.</p>
        : <button className="btn-danger" onClick={handleRevoke} disabled={loading}>
            {loading ? "Signing out…" : "Sign out all devices"}
          </button>
      }
    </div>
  );
}


export default function Settings() {
  const [me, setMe] = useState(null);

  async function loadMe() {
    try { setMe(await getMe()); } catch {}
  }

  useEffect(() => { loadMe(); }, []);

  return (
    <div>
      <div className="page-header">
        <h2>Settings</h2>
        <p>Account, security, and user management</p>
      </div>

      <Section title="Change Password">
        <ChangePassword />
      </Section>

      <Section title="Two-Factor Authentication">
        <TwoFA me={me} onRefresh={loadMe} />
      </Section>

      <Section title="User Management">
        <UserManagement currentUser={me?.username} />
      </Section>

      <Section title="Security Log">
        <SecurityLog />
      </Section>

      <Section title="Active Sessions">
        <ActiveSessions />
      </Section>

      <Section title="Push Notifications">
        <PushNotifications />
      </Section>

      <Section title="App Running Costs">
        <AppCosts />
      </Section>
    </div>
  );
}

const APP_COSTS = [
  { service: "Oracle Cloud (hosting)",    cost: "$0.00/mo",   note: "Always-free E2.1.Micro tier" },
  { service: "Cloudflare (DNS + SSL)",    cost: "$0.00/mo",   note: "Free plan + Origin Certificate" },
  { service: "vaulticsage.com (domain)",  cost: "$0.87/mo",   note: "$10.46/yr via Cloudflare Registrar" },
  { service: "Plaid (account linking)",   cost: "~$3–5/mo",   note: "Pay-as-you-go production; billed per product per connected item" },
  { service: "Claude Haiku (Sage AI)",    cost: "~$1–3/mo",   note: "Per-token pricing at personal use rates" },
  { service: "OpenAI TTS (Sage voice)",   cost: "~$0.50–2/mo",note: "tts-1, fable voice; requires credits at platform.openai.com/billing" },
  { service: "Tavily (web search)",       cost: "$0.00/mo",   note: "Free tier — 1,000 searches/month" },
  { service: "GitHub Actions (CI/CD)",    cost: "$0.00/mo",   note: "Free tier — 2,000 min/month" },
];

function AppCosts() {
  const totalLow  = 5.37;   // 0+0+0.87+3+1+0.50+0+0
  const totalHigh = 10.87;  // 0+0+0.87+5+3+2+0+0
  return (
    <div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {["Service", "Cost", "Notes"].map((h, i) => (
              <th key={h} scope="col" style={{
                textAlign: "left", padding: "6px 12px 10px",
                fontSize: 11, fontWeight: 600, color: "var(--text2)",
                textTransform: "uppercase", letterSpacing: "0.6px",
                paddingLeft: i === 0 ? 0 : 12,
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {APP_COSTS.map((row, i) => (
            <tr key={row.service} style={{ borderBottom: i < APP_COSTS.length - 1 ? "1px solid var(--border)" : "none" }}>
              <td style={{ padding: "10px 0",       fontWeight: 500 }}>{row.service}</td>
              <td style={{ padding: "10px 12px",    fontWeight: 700, color: "var(--accent)", whiteSpace: "nowrap" }}>{row.cost}</td>
              <td style={{ padding: "10px 0 10px 12px", color: "var(--text2)", fontSize: 12 }}>{row.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ marginTop: 16, padding: "10px 14px", background: "var(--surface2)", borderRadius: 8,
        display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Estimated monthly total</span>
        <span style={{ fontWeight: 700, fontSize: 16, color: "var(--accent)" }}>
          ${totalLow.toFixed(2)} – ${totalHigh.toFixed(2)}/mo
        </span>
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: "var(--text2)" }}>
        vs. Monarch Money $15/mo · Copilot $13/mo · YNAB $15/mo
      </div>
    </div>
  );
}
