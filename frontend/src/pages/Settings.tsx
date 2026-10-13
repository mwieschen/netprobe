import { useEffect, useState } from "react";
import { api, ApiError, AuditEntry } from "../api";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/common";
import { fmtDateTime } from "../util";

interface Settings {
  speedtest_enabled: boolean;
  speedtest_interval_min: number;
  speedtest_server_id: number | null;
  retrans_enabled: boolean;
  retrans_scheduled_interval_min: number;
  capture_max_duration_s: number;
  capture_max_size_mb: number;
  ping_raw_retention_days: number;
  timezone: string;
}

export default function SettingsPage() {
  const { isAdmin } = useAuth();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    api.get<Settings>("/api/settings").then(setSettings).catch((e: ApiError) => setError(e.detail));
    api.get<AuditEntry[]>("/api/audit?limit=100").then(setAudit).catch(() => {});
  }, [isAdmin]);

  if (!isAdmin) return <div className="warn-box">Nur Administratoren können Einstellungen ändern.</div>;
  if (!settings) return <ErrorBox error={error} />;

  async function save() {
    setError(null);
    try {
      const updated = await api.patch<Settings>("/api/settings", {
        speedtest_enabled: settings!.speedtest_enabled,
        speedtest_interval_min: settings!.speedtest_interval_min,
        speedtest_server_id: settings!.speedtest_server_id,
        retrans_enabled: settings!.retrans_enabled,
        retrans_scheduled_interval_min: settings!.retrans_scheduled_interval_min,
      });
      setSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <div>
      <h1>Einstellungen</h1>
      <ErrorBox error={error} />

      <div className="grid cols-2">
        <div className="card">
          <h2>Geplante Speedtests</h2>
          <div className="field">
            <label>
              <input type="checkbox" checked={settings.speedtest_enabled}
                onChange={(e) => setSettings({ ...settings, speedtest_enabled: e.target.checked })} /> Aktiviert
            </label>
          </div>
          <div className="field"><label>Intervall (Minuten, 0 = aus)</label>
            <input type="number" value={settings.speedtest_interval_min}
              onChange={(e) => setSettings({ ...settings, speedtest_interval_min: +e.target.value })} /></div>
          <div className="field"><label>Fester Server-ID (leer = Auto)</label>
            <input type="number" value={settings.speedtest_server_id ?? ""}
              onChange={(e) => setSettings({ ...settings, speedtest_server_id: e.target.value ? +e.target.value : null })} /></div>
          <button className="primary" onClick={save}>Speichern</button>
          {saved && <span className="badge ok" style={{ marginLeft: 10 }}>Gespeichert</span>}
        </div>

        <div className="card">
          <h2>Geplantes Retransmission-Monitoring</h2>
          <div className="field">
            <label>
              <input type="checkbox" checked={settings.retrans_enabled}
                onChange={(e) => setSettings({ ...settings, retrans_enabled: e.target.checked })} /> Aktiviert
            </label>
          </div>
          <div className="field"><label>Intervall (Minuten, 0 = aus)</label>
            <input type="number" value={settings.retrans_scheduled_interval_min}
              onChange={(e) => setSettings({ ...settings, retrans_scheduled_interval_min: +e.target.value })} /></div>
          <p className="muted">Läuft auf dem in <code>NETPROBE_RETRANS_DEFAULT_INTERFACE</code>
            konfigurierten Interface (Standard <code>auto</code> = Interface der Default-Route). Übersicht aller geplanten Tests: <a href="/scheduler">Scheduler</a>.</p>
          <button className="primary" onClick={save}>Speichern</button>
          {saved && <span className="badge ok" style={{ marginLeft: 10 }}>Gespeichert</span>}
        </div>

        <div className="card">
          <h2>System (schreibgeschützt)</h2>
          <table>
            <tbody>
              <tr><td>Zeitzone</td><td>{settings.timezone}</td></tr>
              <tr><td>Capture max. Dauer</td><td>{settings.capture_max_duration_s} s</td></tr>
              <tr><td>Capture max. Größe</td><td>{settings.capture_max_size_mb} MB</td></tr>
              <tr><td>Dauerping-Rohdaten</td><td>{settings.ping_raw_retention_days} Tage</td></tr>
            </tbody>
          </table>
          <p className="muted">Diese Werte werden über <code>/etc/netprobe/netprobe.env</code> gesetzt.</p>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Audit-Log</h2>
        <div className="table-scroll">
          <table>
            <thead><tr><th>Zeit</th><th>Benutzer</th><th>Aktion</th><th>Detail</th><th>IP</th></tr></thead>
            <tbody>
              {audit.map((a) => (
                <tr key={a.id}>
                  <td>{fmtDateTime(a.ts)}</td>
                  <td>{a.username ?? "–"}</td>
                  <td><span className="badge dim">{a.action}</span></td>
                  <td className="muted">{a.detail ?? "–"}</td>
                  <td>{a.ip ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
