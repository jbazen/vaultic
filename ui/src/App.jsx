import { useState, useEffect } from "react";
import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import { isAuthed, logout } from "./api.js";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Accounts from "./pages/Accounts.jsx";
import Transactions from "./pages/Transactions.jsx";
import Manual from "./pages/Manual.jsx";
import Settings from "./pages/Settings.jsx";
import PDFImport from "./pages/PDFImport.jsx";
import SageChat from "./components/SageChat.jsx";
import "./App.css";

const NAV = [
  { to: "/",            label: "Dashboard",    icon: "◈", end: true },
  { to: "/accounts",    label: "Accounts",     icon: "⬡" },
  { to: "/transactions",label: "Transactions", icon: "≡" },
  { to: "/manual",      label: "Manual",       icon: "✎" },
  { to: "/pdf",         label: "PDF Import",   icon: "📄" },
  { to: "/settings",    label: "Settings",     icon: "⚙" },
];

export default function App() {
  const [authed, setAuthed] = useState(isAuthed());

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
          <span>Powered by Sage</span>
        </div>
        <nav className="sidebar-nav">
          {NAV.map(({ to, label, icon, end }) => (
            <NavLink key={to} to={to} end={end}>
              <span className="nav-icon">{icon}</span>
              {label}
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
          <Route path="/accounts"     element={<Accounts />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/manual"       element={<Manual />} />
          <Route path="/pdf"          element={<PDFImport />} />
          <Route path="/settings"     element={<Settings />} />
          <Route path="*"             element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      {/* Mobile bottom nav */}
      <nav className="mobile-nav">
        {NAV.map(({ to, label, icon, end }) => (
          <NavLink key={to} to={to} end={end} className="mobile-nav-item">
            <span className="mobile-nav-icon">{icon}</span>
            <span className="mobile-nav-label">{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Sage — always present, accessible from any page */}
      <SageChat />
    </div>
  );
}
