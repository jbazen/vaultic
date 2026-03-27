import { useState } from "react";
import { login, verify2FA } from "../api.js";

export default function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // 2FA state
  const [needs2FA, setNeeds2FA] = useState(false);
  const [pendingToken, setPendingToken] = useState("");
  const [code, setCode] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await login(username, password);
      if (result.requires_2fa) {
        setPendingToken(result.pending_token);
        setNeeds2FA(true);
      } else {
        onLogin();
      }
    } catch {
      setError("Invalid credentials");
    } finally {
      setLoading(false);
    }
  }

  async function handle2FA(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await verify2FA(pendingToken, code);
      onLogin();
    } catch (err) {
      setError(err.message || "Invalid or expired code");
    } finally {
      setLoading(false);
    }
  }

  const boxStyle = {
    background: "var(--bg2)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    padding: "40px",
    width: "100%",
    maxWidth: "380px",
  };

  return (
    <div style={{
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      background: "var(--bg)",
    }}>
      <div style={boxStyle}>
        <div style={{ marginBottom: "32px", textAlign: "center" }}>
          <h1 style={{ fontSize: "28px", fontWeight: 800, color: "var(--text)" }}>Vaultic</h1>
          <p style={{ color: "var(--text2)", fontSize: "14px", marginTop: "4px" }}>
            {needs2FA ? "Two-factor verification" : "Your financial command center"}
          </p>
        </div>

        {!needs2FA ? (
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label">Username</label>
              <input className="form-input" type="text" placeholder="Username" value={username}
                onChange={e => setUsername(e.target.value)} autoFocus required />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <div style={{ position: "relative" }}>
                <input className="form-input" type={showPassword ? "text" : "password"} placeholder="Password" value={password}
                  onChange={e => setPassword(e.target.value)} required style={{ paddingRight: "40px" }} />
                <button type="button" onClick={() => setShowPassword(s => !s)}
                  style={{ position: "absolute", right: "10px", top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "var(--text2)", fontSize: "14px", padding: 0 }}>
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
            </div>
            {error && <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "12px" }}>{error}</p>}
            <button className="btn btn-primary" type="submit" disabled={loading}
              style={{ width: "100%", justifyContent: "center", marginTop: "4px" }}>
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        ) : (
          <form onSubmit={handle2FA}>
            <p style={{ color: "var(--text2)", fontSize: "13px", marginBottom: "20px", textAlign: "center" }}>
              Enter the 6-digit code from your authenticator app.
            </p>
            <div className="form-group">
              <label className="form-label">Verification Code</label>
              <input
                className="form-input"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={code}
                onChange={e => setCode(e.target.value.replace(/\D/g, ""))}
                placeholder="000000"
                autoFocus
                required
                style={{ textAlign: "center", fontSize: "24px", letterSpacing: "0.3em" }}
              />
            </div>
            {error && <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "12px" }}>{error}</p>}
            <button className="btn btn-primary" type="submit" disabled={loading || code.length < 6}
              style={{ width: "100%", justifyContent: "center", marginTop: "4px" }}>
              {loading ? "Verifying…" : "Verify"}
            </button>
            <button type="button" onClick={() => { setNeeds2FA(false); setCode(""); setError(""); }}
              style={{ background: "none", border: "none", color: "var(--text2)", fontSize: "13px",
                cursor: "pointer", width: "100%", textAlign: "center", marginTop: "12px" }}>
              ← Back to login
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
