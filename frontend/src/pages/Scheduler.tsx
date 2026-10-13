import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, SchedulerJob, SchedulerStatus } from "../api";
import { useAuth } from "../auth";
import { Badge, ErrorBox } from "../components/common";
import { fmtDateTime } from "../util";

const TYPE_LABEL: Record<SchedulerJob["type"], string> = {
  speedtest: "Speedtest",
  retransmission: "Retransmissions",
  wan: "WAN-Test",
  voip: "VoIP / 3CX",
  traceroute: "Traceroute",
  health: "System Health",
};

function fmtInterval(seconds: number): string {
  if (seconds <= 0) return "–";
  if (seconds % 86400 === 0) return `${seconds / 86400} d`;
  if (seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

export default function SchedulerPage() {
  const { isAdmin } = useAuth();
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function load() {
    api.get<SchedulerStatus>("/api/scheduler/status").then(setStatus).catch((e: ApiError) => setError(e.detail));
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  async function toggleGlobal() {
    if (!status) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.post<SchedulerStatus>(
        status.enabled ? "/api/scheduler/disable" : "/api/scheduler/enable",
      );
      setStatus(updated);
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  const jobs = status?.jobs ?? [];
  const activeCount = jobs.filter((j) => j.enabled && !j.paused).length;

  return (
    <div>
      <div className="topbar">
        <h1>Scheduler</h1>
        {isAdmin && status && (
          <button className={status.enabled ? "danger" : "primary"} disabled={busy} onClick={toggleGlobal}>
            {busy ? "…" : status.enabled ? "Automatisierung pausieren" : "Automatisierung aktivieren"}
          </button>
        )}
      </div>
      <p className="muted">
        Übersicht aller automatisch geplanten Tests. Intervalle werden auf der jeweiligen Feature-Seite
        (WAN-Tests, VoIP/3CX, Traceroute, System Health) bzw. unter <Link to="/settings">Einstellungen</Link>
        {" "}(Speedtest, Retransmissions) konfiguriert. Der Schalter hier pausiert/aktiviert alle automatischen
        Läufe global, sofort und ohne Neustart — manuelle Tests bleiben davon unberührt.
      </p>
      <ErrorBox error={error} />

      {status && (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <div className="card stat">
            <div className="label">Automatisierung</div>
            <div className="value">
              <Badge ok={status.enabled} label={status.enabled ? "aktiv" : "pausiert"} />
            </div>
          </div>
          <div className="card stat">
            <div className="label">Geplante Jobs</div>
            <div className="value">{jobs.length}</div>
          </div>
          <div className="card stat">
            <div className="label">Davon aktiv</div>
            <div className="value">{activeCount}</div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Typ</th>
                <th>Name</th>
                <th>Ziel</th>
                <th>Status</th>
                <th className="num">Intervall</th>
                <th>Nächster Lauf</th>
                <th>Letzter Lauf</th>
                <th>Ergebnis</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>{TYPE_LABEL[j.type]}</td>
                  <td>{j.label}</td>
                  <td className="muted">{j.target ?? "–"}</td>
                  <td>
                    {!j.enabled ? (
                      <Badge ok={null} label="deaktiviert" />
                    ) : j.paused ? (
                      <Badge ok={null} label="pausiert" />
                    ) : (
                      <Badge ok={true} label="aktiv" />
                    )}
                  </td>
                  <td className="num">{fmtInterval(j.interval_seconds)}</td>
                  <td>{j.next_run ? fmtDateTime(j.next_run) : "–"}</td>
                  <td>{j.last_run ? fmtDateTime(j.last_run) : "–"}</td>
                  <td>{j.last_run ? <Badge ok={j.last_ok} /> : "–"}</td>
                  <td className="num">
                    <Link className="badge dim" to={j.page}>
                      Öffnen
                    </Link>
                  </td>
                </tr>
              ))}
              {!jobs.length && (
                <tr>
                  <td colSpan={9} className="muted">
                    Lädt…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
