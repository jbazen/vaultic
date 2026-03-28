import { useState, useEffect, Component } from "react";
import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";

class ReviewErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(err) { return { error: err }; }
  render() {
    if (this.state.error) {
      return <div style={{ position:"fixed", inset:0, background:"var(--bg)", color:"var(--text)", padding:20, fontSize:13, overflow:"auto", zIndex:99 }}>
        <div style={{ color:"var(--red)", fontWeight:700, marginBottom:8, fontSize:16 }}>Something went wrong</div>
        <p>Please refresh the page to try again.</p>
      </div>;
    }
    return this.props.children;
  }
}
import { isAuthed, logout } from "./api.js";
import Review from "./pages/Review.jsx";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Accounts from "./pages/Accounts.jsx";
import Transactions from "./pages/Transactions.jsx";
import Import from "./pages/Import.jsx";
import Budget from "./pages/Budget.jsx";
import FundFinancials from "./pages/FundFinancials.jsx";
import Taxes from "./pages/Taxes.jsx";
import Documents from "./pages/Documents.jsx";
import Settings from "./pages/Settings.jsx";
import SageChat from "./components/SageChat.jsx";
import "./App.css";

// ── Navigation structure ──────────────────────────────────────────────────────
// Top-level items have a `to` property; group items have a `children` array.
const NAV = [
  { to: "/", label: "Dashboard", icon: "◈", end: true },
  {
    label: "Finance", icon: "⬡",
    children: [
      { to: "/accounts",     label: "Accounts" },
      { to: "/transactions", label: "Transactions" },
      { to: "/import",       label: "Import" },
    ],
  },
  {
    label: "Budget", icon: "≡",
    children: [
      { to: "/budget", label: "Monthly Budget" },
      { to: "/funds",  label: "Fund Financials" },
    ],
  },
  {
    label: "Taxes", icon: "⊞",
    children: [
      { to: "/taxes",     label: "Overview" },
      { to: "/documents", label: "Document Vault" },
    ],
  },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

// ── Collapsible nav group ─────────────────────────────────────────────────────
function NavGroup({ item }) {
  const location = useLocation();
  // Auto-expand if the current route is a child of this group
  const isActive = item.children.some(c => location.pathname.startsWith(c.to));
  const [open, setOpen] = useState(isActive);

  // Re-open if user navigates to a child route from elsewhere
  useEffect(() => { if (isActive) setOpen(true); }, [isActive]);

  return (
    <div>
      <button className="nav-group-header" onClick={() => setOpen(o => !o)}>
        <span className="nav-icon">{item.icon}</span>
        <span style={{ flex: 1 }}>{item.label}</span>
        <span className="nav-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="nav-group-children">
          {item.children.map(child => (
            <NavLink key={child.to} to={child.to} className="nav-child-link">
              {child.label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}

// ── App shell ─────────────────────────────────────────────────────────────────
export default function App() {
  const location = useLocation();
  const [authed, setAuthed] = useState(isAuthed());

  // Hooks must all be called before any conditional return (Rules of Hooks).
  useEffect(() => {
    const handler = () => setAuthed(false);
    window.addEventListener("auth:logout", handler);
    return () => window.removeEventListener("auth:logout", handler);
  }, []);

  // Render the Review page completely outside the auth shell so push notification
  // taps always land on a working page — it handles its own auth via device token.
  if (location.pathname === "/review") {
    return <ReviewErrorBoundary><Review /></ReviewErrorBoundary>;
  }

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
          {NAV.map((item) =>
            item.children ? (
              <NavGroup key={item.label} item={item} />
            ) : (
              <NavLink key={item.to} to={item.to} end={item.end}>
                <span className="nav-icon">{item.icon}</span>
                {item.label}
              </NavLink>
            )
          )}
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
          <Route path="/import"       element={<Import />} />
          <Route path="/budget"       element={<Budget />} />
          <Route path="/funds"        element={<FundFinancials />} />
          <Route path="/taxes"        element={<Taxes />} />
          <Route path="/documents"    element={<Documents />} />
          <Route path="/settings"     element={<Settings />} />
          {/* Legacy redirects for any bookmarked or linked old routes */}
          <Route path="/manual"       element={<Navigate to="/import" replace />} />
          <Route path="/pdf"          element={<Navigate to="/import" replace />} />
          <Route path="*"             element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      {/* Mobile bottom nav — key destinations only */}
      <nav className="mobile-nav">
        {[
          { to: "/",            label: "Dashboard", icon: "◈", end: true },
          { to: "/accounts",    label: "Accounts",  icon: "⬡" },
          { to: "/budget",      label: "Budget",    icon: "≡" },
          { to: "/import",      label: "Import",    icon: "📄" },
          { to: "/settings",    label: "Settings",  icon: "⚙" },
        ].map(({ to, label, icon, end }) => (
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
