import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  ApiError,
  WachtlCell,
  WachtlCheck,
  WachtlEvent,
  WachtlFinding,
  WachtlIncident,
  WachtlOverview,
  WachtlProbe,
  WachtlReport,
  WachtlRole,
  WachtlSettings,
  WachtlSite,
  WachtlToken,
  WachtlTokenCreated,
} from "../api";
import { useAuth } from "../auth";
import { ErrorBox, Modal } from "../components/common";
import { FindingList, IncidentTable, ReportTable } from "../components/WachtlEvents";
import { CheckModal, EventList } from "../components/WachtlChecks";
import { fmtDateTime, fmtNum } from "../util";

const TYPE_LABEL: Record<string, string> = {
  ping: "Ping", path: "Pfad", iperf: "Bandbreite intern", speedtest: "Bandbreite WAN", rtp: "Sprache",
};
const ROLE_LABEL: Record<WachtlRole, string> = { target: "Ziel", gateway: "Gateway", reference: "Internet-Referenz" };
const STATE_CLASS = { ok: "ok", warn: "warn", down: "err", stale: "dim" } as const;
const STATE_LABEL = { ok: "OK", warn: "auffällig", down: "gestört", stale: "keine Daten" } as const;

// Unbeaufsichtigte Installation (GPO/Intune) mit dem unveränderten Wachtl-Setup.
export function setupCommand(servers: string[], pin: string | null, token: string): string {
  const server = (servers.length ? servers : [`https://${window.location.hostname}`]).join(",");
  return `WachtlSetup-0.9.2.exe /VERYSILENT /SUPPRESSMSGBOXES /Server=${server} /Pin="${pin ?? "<PIN>"}" /Token=${token}`;
}

export default function ClientsPage() {
  const { isAdmin } = useAuth();
  const [cfg, setCfg] = useState<WachtlSettings | null>(null);
  const [overview, setOverview] = useState<WachtlOverview | null>(null);
  const [sites, setSites] = useState<WachtlSite[]>([]);
  const [checks, setChecks] = useState<WachtlCheck[]>([]);
  const [probes, setProbes] = useState<WachtlProbe[]>([]);
  const [tokens, setTokens] = useState<WachtlToken[]>([]);
  const [created, setCreated] = useState<WachtlTokenCreated | null>(null);
  const [modal, setModal] = useState<"site" | "check" | "token" | null>(null);
  const [incidents, setIncidents] = useState<WachtlIncident[]>([]);
  const [incRange, setIncRange] = useState("24h");
  const [reports, setReports] = useState<WachtlReport[]>([]);
  const [reportFilter, setReportFilter] = useState<"open" | "all">("open");
  const [findings, setFindings] = useState<WachtlFinding[]>([]);
  const [events, setEvents] = useState<WachtlEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  function loadEvents() {
    api.get<WachtlIncident[]>(`/api/clients/incidents?range=${incRange}`).then(setIncidents).catch(() => setIncidents([]));
    api.get<WachtlReport[]>(`/api/clients/reports?status=${reportFilter}&range=30d`).then(setReports).catch(() => setReports([]));
    api.get<WachtlEvent[]>("/api/clients/events?range=7d").then(setEvents).catch(() => setEvents([]));
    api.get<WachtlFinding[]>("/api/clients/findings?days=1").then((f) => setFindings(f.filter((x) => x.state === "active")))
      .catch(() => setFindings([]));
  }
  useEffect(loadEvents, [incRange, reportFilter]);

  function load() {
    api.get<WachtlSettings>("/api/clients/settings").then(setCfg).catch(() => setCfg(null));
    api.get<WachtlOverview>("/api/clients/overview").then(setOverview).catch(() => setOverview(null));
    api.get<WachtlSite[]>("/api/clients/sites").then(setSites);
    api.get<WachtlCheck[]>("/api/clients/checks").then(setChecks);
    api.get<WachtlProbe[]>("/api/clients/probes").then(setProbes);
    if (isAdmin) api.get<WachtlToken[]>("/api/clients/tokens").then(setTokens);
  }
  useEffect(load, [isAdmin]);
  useEffect(() => {
    const t = setInterval(() => {
      api.get<WachtlOverview>("/api/clients/overview").then(setOverview).catch(() => undefined);
      loadEvents();
    }, 30000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incRange, reportFilter]);

  async function run(fn: () => Promise<unknown>, confirmText?: string) {
    if (confirmText && !confirm(confirmText)) return;
    try {
      await fn();
      setError(null);
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  const siteName = (id: number | null) => (id === null ? "alle Standorte" : sites.find((s) => s.id === id)?.name ?? `#${id}`);

  return (
    <div>
      <div className="topbar">
        <h1>Clients (Wachtl)</h1>
        {isAdmin && <Link to="/clients/notify" style={{ marginLeft: "auto", marginRight: 16 }}>Benachrichtigung (E-Mail)</Link>}
        {isAdmin && cfg && (
          <label className="row" style={{ cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={cfg.enabled}
              onChange={(e) => run(() => api.put("/api/clients/settings", { enabled: e.target.checked }))}
            />
            Anbindung aktiv
          </label>
        )}
      </div>
      <ErrorBox error={error} />
      {info && <div className="info-box" onClick={() => setInfo(null)}>{info}</div>}
      {cfg && !cfg.enabled && (
        <div className="card" style={{ marginBottom: 16 }}>
          <p className="muted" style={{ margin: 0 }}>
            Die Probe-API (<code>/api/probe/v1</code>) ist abgeschaltet. Installierte Wachtl-Probes puffern ihre
            Messwerte bis zu 7 Tage und liefern sie nach dem Einschalten nach.
          </p>
        </div>
      )}

      {/* Standort-Matrix */}
      {overview?.sites.map((s) => (
        <div className="card" key={s.site_id} style={{ marginBottom: 16 }}>
          <h2>{s.name}</h2>
          {!s.probes.length ? (
            <p className="muted">Noch keine Probes an diesem Standort.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Probe</th>
                    {s.checks.map((c) => (
                      <th key={c.id} title={`${c.target} · ${ROLE_LABEL[c.role]}`}>{c.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {s.probes.map((p) => (
                    <tr key={p.probe_id}>
                      <td>
                        <Link to={`/clients/${p.probe_id}`}>{p.hostname}</Link>{" "}
                        <span className={`badge ${p.status !== "active" ? "dim" : p.online ? "ok" : "err"}`}>
                          {p.status !== "active" ? "deaktiviert" : p.online ? "online" : "offline"}
                        </span>
                      </td>
                      {p.cells.map((cell) => <MatrixCell key={cell.check_id} cell={cell} />)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            Bewertung über die letzten {Math.round((overview.window_s ?? 300) / 60)} Minuten.
          </p>
        </div>
      ))}
      {overview && !overview.sites.length && (
        <div className="card" style={{ marginBottom: 16 }}>
          <p className="muted" style={{ margin: 0 }}>Noch kein Standort angelegt.</p>
        </div>
      )}

      {/* Störungen mit Ursache */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="topbar" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Störungen</h2>
          <select value={incRange} onChange={(e) => setIncRange(e.target.value)} style={{ width: 170 }}>
            <option value="6h">letzte 6 Stunden</option>
            <option value="24h">letzte 24 Stunden</option>
            <option value="7d">letzte 7 Tage</option>
          </select>
        </div>
        <IncidentTable incidents={incidents} />
      </div>

      {/* Störungsmeldungen der Benutzer */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="topbar" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Störungsmeldungen</h2>
          <select value={reportFilter} onChange={(e) => setReportFilter(e.target.value as "open" | "all")} style={{ width: 170 }}>
            <option value="open">offene</option>
            <option value="all">alle (30 Tage)</option>
          </select>
        </div>
        <ReportTable reports={reports} isAdmin={isAdmin} onChanged={loadEvents} setError={setError} onInfo={setInfo} />
      </div>

      {findings.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Aktive Client-Hinweise</h2>
          <FindingList findings={findings} />
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Ereignisse (7 Tage) <span className="muted" style={{ fontSize: 13 }}>· Pfadwechsel, Bandbreite/MOS unter Schwellwert, Messfehler</span></h2>
        <EventList events={events.slice(0, 50)} />
      </div>

      {isAdmin && cfg && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Einrichtung</h2>
          <div className="field">
            <label>SPKI-Pin des Server-Zertifikats</label>
            {cfg.pin ? <div className="codeblock">{cfg.pin}</div> : <div className="error-box">{cfg.pin_error}</div>}
          </div>
          <div className="field">
            <label>Server-Adressen für die Probes</label>
            <div className="codeblock">
              {(cfg.server_urls.length ? cfg.server_urls : [`https://${window.location.hostname}`]).join(", ")}
            </div>
            {!cfg.server_urls.length && (
              <span className="muted" style={{ fontSize: 12 }}>
                Keine Vorgabe (NETPROBE_WACHTL_SERVER_URLS) – verwendet wird die Adresse dieser Seite.
              </span>
            )}
          </div>
          <div className="field">
            <label>Gegenstellen auf netprobe für Bandbreiten- und Sprachtests (Adresse für die Probes: {cfg.peer_host || "–"})</label>
            <div className="row">
              <span className={`badge ${cfg.iperf_running ? "ok" : "dim"}`}>
                iperf3-Server {cfg.iperf_running ? `aktiv (TCP ${cfg.iperf_port})` : "aus"}
              </span>
              <Link to="/iperf3" style={{ fontSize: 12 }}>iperf3-Server steuern</Link>
              <span className={`badge ${cfg.rtp_running ? "ok" : cfg.rtp_error ? "err" : "dim"}`}>
                RTP-Reflektor {cfg.rtp_running ? `aktiv (UDP ${cfg.rtp_port})` : cfg.rtp_error ?? "aus"}
              </span>
              <label className="row" style={{ cursor: "pointer" }}>
                <input type="checkbox" checked={cfg.rtp_enabled}
                  onChange={(e) => run(() => api.put("/api/clients/settings", { rtp_enabled: e.target.checked }))} />
                RTP-Reflektor an
              </label>
            </div>
            <span className="muted" style={{ fontSize: 12 }}>
              Firewall: TCP {cfg.iperf_port} bzw. UDP {cfg.rtp_port} von den Client-Netzen zu netprobe freigeben.
            </span>
          </div>
          <p className="muted" style={{ fontSize: 13 }}>
            Rollout: Standort anlegen, Token erzeugen und den angezeigten Befehl per Softwareverteilung ausführen
            (Wachtl-Setup ohne Vorbelegung). Checks ab {cfg.min_interval_s} s Intervall.
          </p>
        </div>
      )}

      {/* Probes */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Probes</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th><th>Standort</th><th>Status</th><th>Zuletzt gesehen</th><th>IP</th>
                <th>Version</th><th className="num">Puffer</th><th>Gegenstelle</th><th></th>
              </tr>
            </thead>
            <tbody>
              {probes.map((p) => (
                <tr key={p.id}>
                  <td><Link to={`/clients/${p.id}`}>{p.hostname}</Link></td>
                  <td>{p.site_name}</td>
                  <td>
                    <span className={`badge ${p.status !== "active" ? "dim" : p.online ? "ok" : "err"}`}>
                      {p.deregistered_at ? "abgemeldet" : p.status !== "active" ? "deaktiviert" : p.online ? "online" : "offline"}
                    </span>
                  </td>
                  <td>{p.last_seen ? fmtDateTime(p.last_seen) : "–"}</td>
                  <td>{p.last_ip ?? "–"}</td>
                  <td>{p.version ?? "–"}</td>
                  <td className="num">{p.buffer_items}</td>
                  <td>
                    <input type="checkbox" checked={p.peer_enabled} disabled={!isAdmin}
                      title="iperf3-Server und RTP-Reflektor auf diesem PC für Tests anderer PCs"
                      onChange={(e) => run(() => api.patch(`/api/clients/probes/${p.id}`, { peer_enabled: e.target.checked }))} />
                  </td>
                  <td className="num">
                    {isAdmin && (
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        {p.status === "active" ? (
                          <button onClick={() => run(() => api.patch(`/api/clients/probes/${p.id}`, { status: "disabled" }))}>
                            Deaktivieren
                          </button>
                        ) : !p.deregistered_at ? (
                          <button onClick={() => run(() => api.patch(`/api/clients/probes/${p.id}`, { status: "active" }))}>
                            Aktivieren
                          </button>
                        ) : null}
                        <button className="danger" onClick={() => run(() => api.del(`/api/clients/probes/${p.id}`),
                          `Probe ${p.hostname} mit allen Messwerten löschen?`)}>Löschen</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {!probes.length && <tr><td colSpan={9} className="muted">Noch keine Probe registriert.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* Checks */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="topbar" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Checks</h2>
          {isAdmin && <button className="primary" onClick={() => setModal("check")}>Check hinzufügen</button>}
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Name</th><th>Art</th><th>Ziel</th><th>Standort</th><th>Messung</th><th>Aktiv</th><th></th></tr>
            </thead>
            <tbody>
              {checks.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>{TYPE_LABEL[c.type]}{c.type === "ping" && <span className="muted"> · {ROLE_LABEL[c.role]}</span>}</td>
                  <td>{c.target}</td>
                  <td>{siteName(c.site_id)}</td>
                  <td>{c.summary}</td>
                  <td>
                    <input type="checkbox" checked={c.enabled} disabled={!isAdmin}
                      onChange={(e) => run(() => api.patch(`/api/clients/checks/${c.id}`, { enabled: e.target.checked }))} />
                  </td>
                  <td className="num">
                    {isAdmin && (
                      <button className="danger" onClick={() => run(() => api.del(`/api/clients/checks/${c.id}`),
                        `Check „${c.name}“ mit allen Messwerten löschen?`)}>Löschen</button>
                    )}
                  </td>
                </tr>
              ))}
              {!checks.length && <tr><td colSpan={7} className="muted">Keine Checks.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {isAdmin && (
        <div className="grid cols-2" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="topbar" style={{ marginBottom: 8 }}>
              <h2 style={{ margin: 0 }}>Standorte</h2>
              <button className="primary" onClick={() => setModal("site")}>Standort hinzufügen</button>
            </div>
            <table>
              <tbody>
                {sites.map((s) => (
                  <tr key={s.id}>
                    <td>{s.name}{s.description && <span className="muted"> · {s.description}</span>}</td>
                    <td className="num">
                      <button className="danger" onClick={() => run(() => api.del(`/api/clients/sites/${s.id}`),
                        `Standort „${s.name}“ löschen?`)}>Löschen</button>
                    </td>
                  </tr>
                ))}
                {!sites.length && <tr><td className="muted">Keine Standorte.</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="topbar" style={{ marginBottom: 8 }}>
              <h2 style={{ margin: 0 }}>Enrollment-Token</h2>
              <button className="primary" disabled={!sites.length} onClick={() => setModal("token")}>Token erzeugen</button>
            </div>
            <div className="table-scroll">
              <table>
                <thead><tr><th>Standort</th><th>Nutzung</th><th>Gültig bis</th><th></th></tr></thead>
                <tbody>
                  {tokens.map((t) => {
                    const expired = new Date(t.expires_at).getTime() < Date.now();
                    return (
                      <tr key={t.id}>
                        <td>{siteName(t.site_id)}{t.description && <span className="muted"> · {t.description}</span>}</td>
                        <td>{t.uses}/{t.max_uses}</td>
                        <td>
                          {t.revoked ? <span className="badge dim">widerrufen</span>
                            : expired ? <span className="badge dim">abgelaufen</span> : fmtDateTime(t.expires_at)}
                        </td>
                        <td className="num">
                          {!t.revoked && !expired && (
                            <button onClick={() => run(() => api.del(`/api/clients/tokens/${t.id}`), "Token widerrufen?")}>
                              Widerrufen
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {!tokens.length && <tr><td colSpan={4} className="muted">Keine Token.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {modal === "site" && <SiteModal onClose={() => { setModal(null); load(); }} setError={setError} />}
      {modal === "check" && cfg && (
        <CheckModal sites={sites} probes={probes} minInterval={cfg.min_interval_s}
          onClose={() => { setModal(null); load(); }} setError={setError} />
      )}
      {modal === "token" && (
        <TokenModal sites={sites} onCreated={(t) => { setModal(null); setCreated(t); load(); }}
          onClose={() => setModal(null)} setError={setError} />
      )}
      {created && cfg && (
        <Modal title="Token erzeugt" onClose={() => setCreated(null)}>
          <p className="muted">Das Token wird nur jetzt angezeigt. Für {siteName(created.site_id)}, {created.max_uses} Installationen.</p>
          <div className="field"><label>Token</label><div className="codeblock">{created.token}</div></div>
          <div className="field">
            <label>Unbeaufsichtigte Installation (als Administrator / Softwareverteilung)</label>
            <div className="codeblock">{setupCommand(cfg.server_urls, cfg.pin, created.token)}</div>
          </div>
          {!cfg.pin && <div className="error-box">{cfg.pin_error}</div>}
        </Modal>
      )}
    </div>
  );
}

function MatrixCell({ cell }: { cell: WachtlCell }) {
  return (
    <td>
      <span className={`badge ${STATE_CLASS[cell.state]}`} title={STATE_LABEL[cell.state]}>
        {cell.text ?? (cell.state === "stale" ? "–" : `${fmtNum(cell.rtt_avg, 1, "ms")} · ${fmtNum(cell.loss_pct, 1, "%")}`)}
      </span>
    </td>
  );
}

type SetError = (s: string) => void;

function SiteModal({ onClose, setError }: { onClose: () => void; setError: SetError }) {
  const [form, setForm] = useState({ name: "", description: "" });
  async function save() {
    try {
      await api.post("/api/clients/sites", { name: form.name, description: form.description || null });
      onClose();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  return (
    <Modal title="Standort hinzufügen" onClose={onClose}>
      <div className="field"><label>Name</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Zentrale" /></div>
      <div className="field"><label>Beschreibung (optional)</label>
        <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
      <button className="primary" onClick={save} disabled={!form.name.trim()}>Speichern</button>
    </Modal>
  );
}

function TokenModal({ sites, onCreated, onClose, setError }: {
  sites: WachtlSite[]; onCreated: (t: WachtlTokenCreated) => void; onClose: () => void; setError: SetError;
}) {
  const [form, setForm] = useState({ site_id: sites[0]?.id ?? 0, max_uses: 50, days: 30, description: "" });
  async function save() {
    try {
      const t = await api.post<WachtlTokenCreated>("/api/clients/tokens", { ...form, description: form.description || null });
      onCreated(t);
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  return (
    <Modal title="Enrollment-Token erzeugen" onClose={onClose}>
      <div className="field"><label>Standort</label>
        <select value={form.site_id} onChange={(e) => setForm({ ...form, site_id: +e.target.value })}>
          {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select></div>
      <div className="field"><label>Anzahl Installationen</label>
        <input type="number" min={1} max={10000} value={form.max_uses}
          onChange={(e) => setForm({ ...form, max_uses: +e.target.value })} /></div>
      <div className="field"><label>Gültigkeit (Tage)</label>
        <input type="number" min={1} max={365} value={form.days} onChange={(e) => setForm({ ...form, days: +e.target.value })} /></div>
      <div className="field"><label>Beschreibung (optional)</label>
        <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
      <button className="primary" onClick={save}>Erzeugen</button>
    </Modal>
  );
}
