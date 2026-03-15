import { useState, useEffect, useRef } from "react";
import {
  getMe, getUsers, createUser, deleteUser, changePassword,
  totpSetup, totpConfirm, disable2FA, getSecurityLog,
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
    if (!confirm("Disable two-factor authentication? You will no longer need a code to log in.")) return;
    setLoading(true);
    try {
      await disable2FA();
      onRefresh();
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
            <div style={{ width: 200, height: 200 }}
              dangerouslySetInnerHTML={{ __html: qrSvg.replace(/width="[^"]*"/, 'width="200"').replace(/height="[^"]*"/, 'height="200"') }}
            />
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
    </div>
  );
}
