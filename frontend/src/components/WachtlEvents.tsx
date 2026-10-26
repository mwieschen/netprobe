import { Link } from "react-router-dom";
import { api, ApiError, WachtlDiagnoseStarted, WachtlFinding, WachtlIncident, WachtlReport } from "../api";
import { fmtDateTime, fmtNum } from "../util";

const CAUSE_CLASS = { lan: "err", wan: "err", target: "warn", probe: "dim" } as const;
const SEVERITY_CLASS = { critical: "err", warning: "warn", info: "dim" } as const;
const SEVERITY_LABEL = { critical: "kritisch", warning: "Warnung", info: "Info" } as const;

function fmtDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  return `${h} h ${minutes % 60} min`;
}

export function IncidentTable({ incidents, showSite = true }: { incidents: WachtlIncident[]; showSite?: boolean }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Beginn</th><th>Dauer</th>{showSite && <th>Standort</th>}<th>Betroffen</th><th>Ursache</th>
            <th className="num">Verlust</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr key={`${i.scope}-${i.cause}-${i.probe_ids.join(",")}-${i.check_id}-${i.start}`}>
              <td>{fmtDateTime(i.start)}</td>
              <td>
                {fmtDuration(i.minutes)}
                {i.affected_minutes < i.minutes && (
                  <div className="muted" style={{ fontSize: 12 }}>davon {i.affected_minutes} min gestört</div>
                )}
                {i.ongoing && <> <span className="badge err">andauernd</span></>}
              </td>
              {showSite && <td>{i.site_name}</td>}
              <td>
                {i.scope === "site" ? <strong>ganzer Standort</strong> : (
                  <Link to={`/clients/${i.probe_ids[0]}`}>{i.probe_hostnames[0]}</Link>
                )}
                {i.check_names.length > 0 && <span className="muted"> · {i.check_names.join(", ")}</span>}
              </td>
              <td title={i.hint}>
                <span className={`badge ${CAUSE_CLASS[i.cause]}`}>{i.cause_label}</span>
                <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{i.hint}</div>
              </td>
              <td className="num">{fmtNum(i.max_loss_pct, 0, "%")}</td>
            </tr>
          ))}
          {!incidents.length && (
            <tr><td colSpan={showSite ? 6 : 5} className="muted">Keine Störungen in diesem Zeitraum.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function ReportTable({ reports, isAdmin, onChanged, setError, onInfo, showProbe = true }: {
  reports: WachtlReport[];
  isAdmin: boolean;
  onChanged: () => void;
  setError: (s: string | null) => void;
  onInfo: (s: string) => void;
  showProbe?: boolean;
}) {
  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      setError(null);
      onChanged();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  async function diagnose(r: WachtlReport) {
    await act(async () => {
      const res = await api.post<WachtlDiagnoseStarted>(`/api/clients/reports/${r.id}/diagnose`);
      onInfo(`Diagnose gestartet (${res.targets.join(", ")}). Das Ergebnis erscheint in etwa einer Minute.`);
    });
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Zeitpunkt</th>{showProbe && <th>PC</th>}<th>Art</th><th>Dauer</th><th>Beschreibung</th>
            <th>Diagnose</th><th>Status</th><th></th>
          </tr>
        </thead>
        <tbody>
          {reports.map((r) => (
            <tr key={r.id}>
              <td>{fmtDateTime(r.occurred_at)}</td>
              {showProbe && (
                <td>
                  <Link to={`/clients/${r.probe_id}`}>{r.probe_hostname || r.hostname}</Link>
                  <div className="muted" style={{ fontSize: 12 }}>{r.site_name}{r.reported_user ? ` · ${r.reported_user}` : ""}</div>
                </td>
              )}
              <td>{r.category_label}</td>
              <td>{r.duration_label}</td>
              <td style={{ maxWidth: 320, whiteSpace: "pre-wrap" }}>
                {r.description ?? <span className="muted">–</span>}
                {r.note && <div className="muted" style={{ fontSize: 12 }}>Notiz: {r.note}</div>}
              </td>
              <td>
                {r.diagnostic_run_id
                  ? <Link to={`/diagnostics?run=${r.diagnostic_run_id}`}>Lauf #{r.diagnostic_run_id}</Link>
                  : <span className="muted">–</span>}
              </td>
              <td>
                <span className={`badge ${r.status === "open" ? "warn" : "ok"}`}>{r.status === "open" ? "offen" : "erledigt"}</span>
                {r.done_by && <div className="muted" style={{ fontSize: 12 }}>{r.done_by}</div>}
              </td>
              <td className="num">
                {isAdmin && (
                  <div className="row" style={{ justifyContent: "flex-end" }}>
                    <button onClick={() => diagnose(r)}>Diagnose</button>
                    {r.status === "open" ? (
                      <button onClick={() => {
                        const note = prompt("Notiz (optional):") ?? undefined;
                        act(() => api.post(`/api/clients/reports/${r.id}/status`, { status: "done", note }));
                      }}>Erledigt</button>
                    ) : (
                      <button onClick={() => act(() => api.post(`/api/clients/reports/${r.id}/status`, { status: "open" }))}>
                        Wieder öffnen
                      </button>
                    )}
                  </div>
                )}
              </td>
            </tr>
          ))}
          {!reports.length && <tr><td colSpan={showProbe ? 8 : 7} className="muted">Keine Meldungen.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

export function FindingList({ findings, showProbe = true }: { findings: WachtlFinding[]; showProbe?: boolean }) {
  if (!findings.length) return <p className="muted">Keine Client-Hinweise.</p>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Erkannt</th>{showProbe && <th>PC</th>}<th>Hinweis</th><th>Lösungsvorschlag</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f) => (
            <tr key={f.id}>
              <td>{fmtDateTime(f.detected_at)}</td>
              {showProbe && <td><Link to={`/clients/${f.probe_id}`}>{f.probe_hostname}</Link></td>}
              <td>
                <span className={`badge ${SEVERITY_CLASS[f.severity]}`}>{SEVERITY_LABEL[f.severity]}</span>{" "}
                <strong>{f.title}</strong>
                {f.message && <div className="muted" style={{ fontSize: 12 }}>{f.message}</div>}
                {(f.adapter_description || f.adapter) && (
                  <div className="muted" style={{ fontSize: 12 }}>{f.adapter_description || f.adapter}</div>
                )}
              </td>
              <td style={{ maxWidth: 360 }}>{f.solution ?? <span className="muted">–</span>}</td>
              <td>
                <span className={`badge ${f.state === "active" ? "err" : "ok"}`}>{f.state === "active" ? "aktiv" : "behoben"}</span>
                {f.resolved_at && <div className="muted" style={{ fontSize: 12 }}>{fmtDateTime(f.resolved_at)}</div>}
                {f.details.resolution && <div className="muted" style={{ fontSize: 12 }}>{f.details.resolution}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
