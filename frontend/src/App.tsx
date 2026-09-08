import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import SpeedtestPage from "./pages/Speedtest";
import WanPage from "./pages/Wan";
import PingPage from "./pages/Ping";
import CapturesPage from "./pages/Captures";
import SettingsPage from "./pages/Settings";
import UsersPage from "./pages/Users";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/speedtest", label: "Speedtest" },
  { to: "/wan", label: "WAN-Tests" },
  { to: "/ping", label: "Dauerping" },
  { to: "/captures", label: "Packet Captures" },
  { to: "/settings", label: "Einstellungen" },
  { to: "/users", label: "Benutzer" },
];

export default function App() {
  const { user, loading, logout } = useAuth();

  if (loading) return <div className="login-wrap">Lädt…</div>;
  if (!user) return <Login />;

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">netprobe</div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <div className="topbar">
          <div />
          <div className="row">
            <span className="muted">
              {user.username} ({user.role})
            </span>
            <button onClick={logout}>Abmelden</button>
          </div>
        </div>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/speedtest" element={<SpeedtestPage />} />
          <Route path="/wan" element={<WanPage />} />
          <Route path="/ping" element={<PingPage />} />
          <Route path="/captures" element={<CapturesPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
