import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  ApiError,
  Severity,
  SmtpSecurity,
  WachtlNotification,
  WachtlNotifySettings,
  WachtlRecipient,
  WachtlSite,
} from "../api";
import { useAuth } from "../auth";
import { ErrorBox, Modal } from "../components/common";
import { fmtDateTime } from "../util";

const SEVERITY_LABEL: Record<Severity, string> = { info: "alles (ab Info)", warning: "ab Warnung", critical: "nur kritisch" };
const DEFAULT_PORT: Record<SmtpSecurity, number> = { none: 25, starttls: 587, ssl: 465 };

type Form = Omit<WachtlNotifySettings, "password_set" | "password_error" | "ready_error"> & { smtp_password: string };

export default function ClientsNotifyPage() {
  const { isAdmin } = useAuth();
  const [cfg, setCfg] = useState<WachtlNotifySettings | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [recipients, setRecipients] = useState<WachtlRecipient[]>([]);
  const [sites, setSites] = useState<WachtlSite[]>([]);
  const [log, setLog] = useState<WachtlNotification[]>([]);
  const [testTo, setTestTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<WachtlRecipient | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  function applyCfg(c: WachtlNotifySettings) {
    setCfg(c);
    const { password_set: _p, password_error: _e, ready_error: _r, ...rest } = c;
    setForm({ ...rest, public_url: rest.public_url || window.location.origin, smtp_password: "" });
  }
  function load() {
    api.get<WachtlNotifySettings>("/api/clients/notify/settings").then(applyCfg).catch((e) => setError((e as ApiError).detail));
    api.get<WachtlRecipient[]>("/api/clients/notify/recipients").then(setRecipients).catch(() => setRecipients([]));
    api.get<WachtlSite[]>("/api/clients/sites").then(setSites).catch(() => setSites([]));
    api.get<WachtlNotification[]>("/api/clients/notify/log?limit=50").then(setLog).catch(() => setLog([]));
  }
  useEffect(load, []);

  async function save(extra: Partial<Form> = {}) {
    if (!form) return;
    setBusy(true);
    try {
      const { smtp_password, ...rest } = { ...form, ...extra };
      // Leeres Passwortfeld = gespeichertes Passwort unverändert lassen
      const body = smtp_password ? { ...rest, smtp_password } : rest;
      applyCfg(await api.put<WachtlNotifySettings>("/api/clients/notify/settings", body));
      setError(null);
      setInfo("Einstellungen gespeichert.");
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }
  async function clearPassword() {
    if (!confirm("Gespeichertes SMTP-Passwort löschen?")) return;
    try {
      applyCfg(await api.put<WachtlNotifySettings>("/api/clients/notify/settings", { smtp_password: "" }));
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  async function sendTest() {
    setBusy(true);
    try {
      await api.post("/api/clients/notify/test", { to: testTo });
      setError(null);
      setInfo(`Testmail an ${testTo} gesendet.`);
    } catch (err) {
      setError(`Testmail fehlgeschlagen: ${(err as ApiError).detail}`);
    } finally {
      setBusy(false);
      api.get<WachtlNotification[]>("/api/clients/notify/log?limit=50").then(setLog).catch(() => undefined);
    }
  }
  async function act(fn: () => Promise<unknown>, confirmText?: string) {
    if (confirmText && !confirm(confirmText)) return;
    try {
      await fn();
      setError(null);
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  if (!isAdmin) return <p className="muted">Nur für Administratoren.</p>;
  const siteName = (id: number | null) => (id === null ? "alle Standorte" : sites.find((s) => s.id === id)?.name ?? `#${id}`);
  const set = (patch: Partial<Form>) => form && setForm({ ...form, ...patch });

  return (
    <div>
      <div className="topbar">
        <h1><Link to="/clients">Clients</Link> / Benachrichtigung</h1>
        {cfg && (
          <label className="row" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={cfg.enabled} onChange={(e) => save({ enabled: e.target.checked })} />
            E-Mail-Benachrichtigung aktiv
          </label>
        )}
      </div>
      <ErrorBox error={error} />
      {info && <div className="info-box" onClick={() => setInfo(null)}>{info}</div>}
      {cfg?.enabled && cfg.ready_error && <div className="error-box">Versand nicht möglich: {cfg.ready_error}</div>}

      {form && cfg && (
        <div className="grid cols-2" style={{ marginBottom: 16 }}>
          <div className="card">
            <h2>SMTP-Server</h2>
            <div className="field"><label>Server</label>
              <input value={form.smtp_host} onChange={(e) => set({ smtp_host: e.target.value })} placeholder="mail.kunde.de" /></div>
            <div className="row">
              <div className="field" style={{ flex: 1 }}><label>Verschlüsselung</label>
                <select value={form.smtp_security} onChange={(e) => {
                  const sec = e.target.value as SmtpSecurity;
                  set({ smtp_security: sec, smtp_port: DEFAULT_PORT[sec] });
                }}>
                  <option value="starttls">STARTTLS (Port 587)</option>
                  <option value="ssl">SSL/TLS (Port 465)</option>
                  <option value="none">keine (Port 25, nur internes Relay)</option>
                </select></div>
              <div className="field" style={{ width: 110 }}><label>Port</label>
                <input type="number" min={1} max={65535} value={form.smtp_port} onChange={(e) => set({ smtp_port: +e.target.value })} /></div>
            </div>
            <div className="field"><label>Benutzer (leer = ohne Anmeldung)</label>
              <input value={form.smtp_username} autoComplete="off" onChange={(e) => set({ smtp_username: e.target.value })} /></div>
            <div className="field">
              <label>Passwort {cfg.password_set ? "(gespeichert – leer lassen = unverändert)" : ""}</label>
              <div className="row">
                <input type="password" autoComplete="new-password" style={{ flex: 1 }} value={form.smtp_password}
                  placeholder={cfg.password_set ? "••••••••" : ""} onChange={(e) => set({ smtp_password: e.target.value })} />
                {cfg.password_set && <button onClick={clearPassword}>Löschen</button>}
              </div>
              {cfg.password_error && <span style={{ color: "var(--err)", fontSize: 12 }}>{cfg.password_error}</span>}
              <span className="muted" style={{ fontSize: 12 }}>Wird verschlüsselt gespeichert und nie wieder angezeigt.</span>
            </div>
            <div className="field"><label>Absenderadresse</label>
              <input value={form.smtp_from} onChange={(e) => set({ smtp_from: e.target.value })} placeholder="netprobe@kunde.de" /></div>
            <label className="row" style={{ marginBottom: 6 }}>
              <input type="checkbox" checked={form.verify_tls} onChange={(e) => set({ verify_tls: e.target.checked })} />
              Zertifikat des SMTP-Servers prüfen
            </label>
            {!form.verify_tls && form.smtp_security !== "none" && (
              <div className="error-box" style={{ fontSize: 12 }}>
                Ohne Zertifikatsprüfung ist die Verbindung verschlüsselt, aber nicht vor Man-in-the-Middle geschützt.
                Nur für interne Mailserver mit selbstsigniertem Zertifikat verwenden.
              </div>
            )}
          </div>

          <div className="card">
            <h2>Wann wird gemeldet?</h2>
            <div className="field"><label>Störung melden ab (Minuten Dauer)</label>
              <input type="number" min={1} max={120} value={form.min_incident_minutes}
                onChange={(e) => set({ min_incident_minutes: +e.target.value })} /></div>
            <div className="field"><label>PC gilt als offline nach (Minuten ohne Kontakt)</label>
              <input type="number" min={2} max={1440} value={form.offline_after_min}
                onChange={(e) => set({ offline_after_min: +e.target.value })} /></div>
            <div className="field"><label>Höchstens Mails je Empfänger und Stunde</label>
              <input type="number" min={1} max={500} value={form.max_mails_per_hour}
                onChange={(e) => set({ max_mails_per_hour: +e.target.value })} /></div>
            <div className="field"><label>Adresse von netprobe (Link in der Mail)</label>
              <input value={form.public_url} onChange={(e) => set({ public_url: e.target.value })} /></div>
            <p className="muted" style={{ fontSize: 12 }}>
              Gemeldet werden neue Störungen (Gateway/LAN, WAN, einzelne Ziele, ganze Standorte), PCs offline,
              Ereignisse (Pfadwechsel, Bandbreite/MOS unter Schwellwert, Messfehler), Störungsmeldungen der Benutzer
              und Entwarnungen. Je Empfänger kommt höchstens eine Sammel-Mail pro Minute.
            </p>
            <button className="primary" onClick={() => save()} disabled={busy}>Speichern</button>
            <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "16px 0" }} />
            <div className="field"><label>Testmail senden an</label>
              <div className="row">
                <input style={{ flex: 1 }} value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder="it@kunde.de" />
                <button onClick={sendTest} disabled={busy || !testTo.trim()}>Senden</button>
              </div>
              <span className="muted" style={{ fontSize: 12 }}>Verwendet die gespeicherten Einstellungen – vorher speichern.</span>
            </div>
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="topbar" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Empfänger</h2>
          <button className="primary" onClick={() => setModal("new")}>Empfänger hinzufügen</button>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Name</th><th>E-Mail</th><th>Standort</th><th>Schwere</th><th>Erhält</th><th>Aktiv</th><th></th></tr>
            </thead>
            <tbody>
              {recipients.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td>{r.email}</td>
                  <td>{siteName(r.site_id)}</td>
                  <td>{SEVERITY_LABEL[r.min_severity]}</td>
                  <td style={{ fontSize: 12 }}>
                    {[r.notify_incidents && "Störungen", r.notify_recovery && "Entwarnungen", r.notify_events && "Ereignisse",
                      r.notify_reports && "Meldungen"].filter(Boolean).join(", ") || "–"}
                  </td>
                  <td>
                    <input type="checkbox" checked={r.enabled}
                      onChange={(e) => act(() => api.patch(`/api/clients/notify/recipients/${r.id}`, { enabled: e.target.checked }))} />
                  </td>
                  <td className="num">
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      <button onClick={() => setModal(r)}>Bearbeiten</button>
                      <button className="danger" onClick={() => act(() => api.del(`/api/clients/notify/recipients/${r.id}`),
                        `Empfänger ${r.email} löschen?`)}>Löschen</button>
                    </div>
                  </td>
                </tr>
              ))}
              {!recipients.length && <tr><td colSpan={7} className="muted">Noch keine Empfänger eingetragen.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>Versandprotokoll</h2>
        <div className="table-scroll">
          <table>
            <thead><tr><th>Zeit</th><th>Empfänger</th><th>Betreff</th><th className="num">Einträge</th><th>Status</th></tr></thead>
            <tbody>
              {log.map((n) => (
                <tr key={n.id}>
                  <td>{fmtDateTime(n.ts)}</td>
                  <td>{n.recipient}</td>
                  <td>{n.subject}</td>
                  <td className="num">{n.items}</td>
                  <td>{n.ok ? <span className="badge ok">gesendet</span> : <span className="badge err" title={n.error ?? ""}>{n.error ?? "Fehler"}</span>}</td>
                </tr>
              ))}
              {!log.length && <tr><td colSpan={5} className="muted">Noch nichts versendet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {modal && (
        <RecipientModal recipient={modal === "new" ? null : modal} sites={sites}
          onClose={() => { setModal(null); load(); }} setError={setError} />
      )}
    </div>
  );
}

function RecipientModal({ recipient, sites, onClose, setError }: {
  recipient: WachtlRecipient | null; sites: WachtlSite[]; onClose: () => void; setError: (s: string) => void;
}) {
  const [f, setF] = useState({
    name: recipient?.name ?? "", email: recipient?.email ?? "", site_id: recipient?.site_id?.toString() ?? "",
    min_severity: recipient?.min_severity ?? ("warning" as Severity), enabled: recipient?.enabled ?? true,
    notify_incidents: recipient?.notify_incidents ?? true, notify_recovery: recipient?.notify_recovery ?? true,
    notify_events: recipient?.notify_events ?? true, notify_reports: recipient?.notify_reports ?? true,
  });
  async function save() {
    const body = { ...f, site_id: f.site_id ? +f.site_id : null };
    try {
      if (recipient) await api.patch(`/api/clients/notify/recipients/${recipient.id}`, body);
      else await api.post("/api/clients/notify/recipients", body);
      onClose();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  const box = (key: "notify_incidents" | "notify_recovery" | "notify_events" | "notify_reports", label: string) => (
    <label className="row" style={{ marginBottom: 6 }}>
      <input type="checkbox" checked={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.checked })} /> {label}
    </label>
  );
  return (
    <Modal title={recipient ? "Empfänger bearbeiten" : "Empfänger hinzufügen"} onClose={onClose}>
      <div className="field"><label>Name</label>
        <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="IT-Bereitschaft" /></div>
      <div className="field"><label>E-Mail-Adresse</label>
        <input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} placeholder="it@kunde.de" /></div>
      <div className="field"><label>Standort</label>
        <select value={f.site_id} onChange={(e) => setF({ ...f, site_id: e.target.value })}>
          <option value="">alle Standorte</option>
          {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select></div>
      <div className="field"><label>Mindest-Schwere</label>
        <select value={f.min_severity} onChange={(e) => setF({ ...f, min_severity: e.target.value as Severity })}>
          {Object.entries(SEVERITY_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <span className="muted" style={{ fontSize: 12 }}>
          Kritisch: ganzer Standort ohne Gateway/Internet, MOS unter 3,1. Warnung: einzelne PCs, Ziele, offline,
          Schwellwerte. Info: Pfadwechsel, Messlücken einzelner PCs.
        </span>
      </div>
      <fieldset style={{ border: "1px solid var(--border)", borderRadius: 8, marginBottom: 12, padding: "8px 12px" }}>
        <legend className="muted" style={{ fontSize: 12 }}>Erhält</legend>
        {box("notify_incidents", "Störungen und PCs offline")}
        {box("notify_recovery", "Entwarnungen (Störung vorbei, PC wieder online)")}
        {box("notify_events", "Ereignisse (Pfadwechsel, Bandbreite/MOS unter Schwellwert, Messfehler)")}
        {box("notify_reports", "Störungsmeldungen der Benutzer")}
      </fieldset>
      <label className="row" style={{ marginBottom: 12 }}>
        <input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> aktiv
      </label>
      <button className="primary" onClick={save} disabled={!f.name.trim() || !f.email.trim()}>Speichern</button>
    </Modal>
  );
}
