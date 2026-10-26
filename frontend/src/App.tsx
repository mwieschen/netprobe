import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import SpeedtestPage from "./pages/Speedtest";
import WanPage from "./pages/Wan";
import PingPage from "./pages/Ping";
import CapturesPage from "./pages/Captures";
import CaptureAnalysisPage from "./pages/CaptureAnalysis";
import CaptureJobsPage from "./pages/CaptureJobs";
import Iperf3Page from "./pages/Iperf3";
import MtuTestPage from "./pages/MtuTest";
import VoipMonitorPage from "./pages/VoipMonitor";
import TraceroutePage from "./pages/Traceroute";
import HealthChecksPage from "./pages/HealthChecks";
import NetworkDiagnosticsPage from "./pages/NetworkDiagnostics";
import RetransmissionsPage from "./pages/Retransmissions";
import SchedulerPage from "./pages/Scheduler";
import SettingsPage from "./pages/Settings";
import UsersPage from "./pages/Users";
import ClientsPage from "./pages/Clients";
import ClientDetailPage from "./pages/ClientDetail";
import ClientsNotifyPage from "./pages/ClientsNotify";
import DownloadsPage from "./pages/Downloads";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/diagnostics", label: "Netzwerk-Diagnose" },
  { to: "/clients", label: "Clients (Wachtl)" },
  { to: "/downloads", label: "Downloads" },
  { to: "/speedtest", label: "Speedtest" },
  { to: "/wan", label: "WAN-Tests" },
  { to: "/ping", label: "Dauerping" },
  { to: "/captures", label: "Packet Captures" },
  { to: "/capture-jobs", label: "Langzeit-Captures" },
  { to: "/iperf3", label: "iperf3" },
  { to: "/mtu-test", label: "MTU-Test" },
  { to: "/voip-monitor", label: "VoIP / 3CX" },
  { to: "/traceroute", label: "Traceroute" },
  { to: "/health-checks", label: "System Health" },
  { to: "/retransmissions", label: "Retransmissions" },
  { to: "/scheduler", label: "Scheduler" },
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
          <Route path="/diagnostics" element={<NetworkDiagnosticsPage />} />
          <Route path="/clients" element={<ClientsPage />} />
          <Route path="/clients/notify" element={<ClientsNotifyPage />} />
          <Route path="/downloads" element={<DownloadsPage />} />
          <Route path="/clients/:id" element={<ClientDetailPage />} />
          <Route path="/speedtest" element={<SpeedtestPage />} />
          <Route path="/wan" element={<WanPage />} />
          <Route path="/ping" element={<PingPage />} />
          <Route path="/captures" element={<CapturesPage />} />
          <Route path="/captures/:id" element={<CaptureAnalysisPage />} />
          <Route path="/capture-jobs" element={<CaptureJobsPage />} />
          <Route path="/iperf3" element={<Iperf3Page />} />
          <Route path="/mtu-test" element={<MtuTestPage />} />
          <Route path="/voip-monitor" element={<VoipMonitorPage />} />
          <Route path="/traceroute" element={<TraceroutePage />} />
          <Route path="/health-checks" element={<HealthChecksPage />} />
          <Route path="/retransmissions" element={<RetransmissionsPage />} />
          <Route path="/scheduler" element={<SchedulerPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
