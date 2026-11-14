import { useState, useEffect } from "react";
import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import { isAuthed, logout } from "./api.js";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Import from "./pages/Import.jsx";
import Settings from "./pages/Settings.jsx";
import "./App.css";

// ── Navigation structure ──────────────────────────────────────────────────────
const NAV = [
  { to: "/",         label: "Dashboard", icon: "◈", end: true },
  { to: "/import",   label: "Import",    icon: "📄" },
  { to: "/settings", label: "Settings",  icon: "⚙" },
];

// ── App shell ─────────────────────────────────────────────────────────────────
export default function App() {
  const [authed, setAuthed] = useState(isAuthed());

  // Hooks must all be called before any conditional return (Rules of Hooks).
  useEffect(() => {
    const handler = () => setAuthed(false);
    window.addEventListener("auth:logout", handler);
    return () => window.removeEventListener("auth:logout", handler);
  }, []);

  if (!authed) {
    return <Login onLogin={() => setAuthed(true)} />;
  }

  return (
    <div className="app-shell">
      {/* Desktop sidebar */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <img src="/favicon.png" alt="" style={{ width: 42, height: 42, objectFit: "contain" }} />
            <h1>Vaultic</h1>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}>
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <button onClick={() => { logout(); setAuthed(false); }}>Sign out</button>
        </div>
      </aside>

      {/* Main content */}
      <main className="main-content">
        <Routes>
          <Route path="/"             element={<Dashboard />} />
          <Route path="/import"       element={<Import />} />
          <Route path="/settings"     element={<Settings />} />
          {/* Legacy redirects for any bookmarked, linked or already-delivered
              push-notification URLs. /accounts was merged into the Dashboard (#86). */}
          <Route path="/accounts"     element={<Navigate to="/#accounts" replace />} />
          <Route path="/manual"       element={<Navigate to="/import" replace />} />
          <Route path="/pdf"          element={<Navigate to="/import" replace />} />
          <Route path="*"             element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      {/* Mobile bottom nav — key destinations only */}
      <nav className="mobile-nav">
        {NAV.map(({ to, label, icon, end }) => (
          <NavLink key={to} to={to} end={end} className="mobile-nav-item">
            <span className="mobile-nav-icon">{icon}</span>
            <span className="mobile-nav-label">{label}</span>
          </NavLink>
        ))}
      </nav>

    </div>
  );
}
