import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  WachtlBandwidthRun,
  WachtlCheck,
  WachtlCheckType,
  WachtlCheckTypeKey,
  WachtlEvent,
  WachtlField,
  WachtlPathView,
  WachtlProbe,
  WachtlRole,
  WachtlRtpRun,
  WachtlSite,
} from "../api";
import TimeSeriesChart from "./Chart";
import type { ChartWindowProps } from "./TimeRange";
import { Modal } from "./common";
import { fmtDateTime, fmtNum } from "../util";

type Values = Record<string, string | number | boolean>;

const ROLE_LABEL: Record<WachtlRole, string> = { target: "Ziel", gateway: "Gateway", reference: "Internet-Referenz" };

function visible(f: WachtlField, values: Values): boolean {
  return !f.show_if || f.show_if[1].includes(String(values[f.show_if[0]]));
}

// Formular für alle Check-Typen – Felder kommen aus /api/clients/check-types (eine Quelle im Backend).
export function CheckModal({ sites, probes, minInterval, onClose, setError }: {
  sites: WachtlSite[];
  probes: WachtlProbe[];
  minInterval: number;
  onClose: () => void;
  setError: (s: string) => void;
}) {
  const [types, setTypes] = useState<WachtlCheckType[]>([]);
  const [type, setType] = useState<WachtlCheckTypeKey>("ping");
  const [base, setBase] = useState({ name: "", target: "", role: "target" as WachtlRole, interval_s: minInterval,
    timeout_ms: 1000, site_id: "" });
  const [values, setValues] = useState<Values>({});
  useEffect(() => {
    api.get<WachtlCheckType[]>("/api/clients/check-types").then(setTypes).catch(() => setTypes([]));
  }, []);
  const ct = types.find((t) => t.key === type);
  useEffect(() => {
    if (ct) setValues(Object.fromEntries(ct.fields.map((f) => [f.key, f.default])));
  }, [ct]);

  async function save() {
    if (!ct) return;
    const params = Object.fromEntries(ct.fields.filter((f) => visible(f, values)).map((f) => [f.key, values[f.key]]));
    try {
      await api.post("/api/clients/checks", {
        name: base.name, type, target: ct.uses_target ? base.target : "", role: base.role,
        interval_s: base.interval_s, timeout_ms: base.timeout_ms,
        site_id: base.site_id ? +base.site_id : null, params,
      });
      onClose();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  const groups = ct ? [...new Set(ct.fields.map((f) => f.group))] : [];
  return (
    <Modal title="Check hinzufügen" onClose={onClose}>
      <div className="field"><label>Art</label>
        <select value={type} onChange={(e) => setType(e.target.value as WachtlCheckTypeKey)}>
          {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        {ct && <span className="muted" style={{ fontSize: 12 }}>{ct.description}</span>}
      </div>
      <div className="field"><label>Name</label>
        <input value={base.name} onChange={(e) => setBase({ ...base, name: e.target.value })} /></div>
      {ct?.uses_target && (
        <div className="field">
          <label>Ziel{type === "ping" ? " (IP, Hostname oder „gateway“ = Default-Gateway des PCs)" : " (IP oder Hostname)"}</label>
          <input value={base.target} onChange={(e) => setBase({ ...base, target: e.target.value })} placeholder="1.1.1.1" />
        </div>
      )}
      {type === "ping" && (
        <div className="field"><label>Rolle</label>
          <select value={base.role} onChange={(e) => setBase({ ...base, role: e.target.value as WachtlRole })}>
            {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></div>
      )}
      <div className="field"><label>Standort</label>
        <select value={base.site_id} onChange={(e) => setBase({ ...base, site_id: e.target.value })}>
          <option value="">alle Standorte</option>
          {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select></div>
      {ct?.uses_interval && (
        <div className="field"><label>{type === "path" ? "Hop-Pings alle" : "Intervall"} (Sekunden, min. {minInterval})</label>
          <input type="number" min={minInterval} value={base.interval_s}
            onChange={(e) => setBase({ ...base, interval_s: +e.target.value })} /></div>
      )}
      {groups.map((g) => {
        const fields = ct!.fields.filter((f) => f.group === g && visible(f, values));
        if (!fields.length) return null;
        return (
          <fieldset key={g} style={{ border: "1px solid var(--border)", borderRadius: 8, marginBottom: 12, padding: "8px 12px" }}>
            <legend className="muted" style={{ fontSize: 12 }}>{g}</legend>
            {fields.map((f) => (
              <FieldInput key={f.key} field={f} value={values[f.key]} probes={probes}
                onChange={(v) => setValues({ ...values, [f.key]: v })} />
            ))}
          </fieldset>
        );
      })}
      <button className="primary" onClick={save} disabled={!base.name.trim() || (ct?.uses_target && !base.target.trim())}>
        Speichern
      </button>
    </Modal>
  );
}

function FieldInput({ field: f, value, probes, onChange }: {
  field: WachtlField;
  value: string | number | boolean | undefined;
  probes: WachtlProbe[];
  onChange: (v: string | number | boolean) => void;
}) {
  const label = `${f.label}${f.unit ? ` (${f.unit})` : ""}`;
  let input;
  if (f.kind === "bool") {
    return (
      <label className="row" style={{ marginBottom: 10 }}>
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {f.label}
      </label>
    );
  } else if (f.key === "peer_probe_id") {
    input = (
      <select value={String(value ?? 0)} onChange={(e) => onChange(+e.target.value)}>
        <option value="0">– Probe wählen –</option>
        {probes.map((p) => <option key={p.id} value={p.id}>{p.hostname} ({p.site_name}){p.peer_enabled ? "" : " – nicht freigegeben"}</option>)}
      </select>
    );
  } else if (f.kind === "choice") {
    input = (
      <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
        {f.choices.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
    );
  } else if (f.kind === "int" || f.kind === "float") {
    input = (
      <input type="number" step={f.kind === "float" ? "any" : 1} min={f.min ?? undefined} max={f.max ?? undefined}
        value={String(value ?? "")} onChange={(e) => onChange(e.target.value === "" ? "" : +e.target.value)} />
    );
  } else {
    input = <input value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}
      placeholder={f.kind === "time" ? "HH:MM" : undefined} />;
  }
  return (
    <div className="field">
      <label>{label}</label>
      {input}
      {f.help && <span className="muted" style={{ fontSize: 12 }}>{f.help}</span>}
    </div>
  );
}

// --- Ansichten auf der PC-Seite ---------------------------------------------
export function RunNowButton({ probeId, check, onInfo, setError }: {
  probeId: number; check: WachtlCheck; onInfo: (s: string) => void; setError: (s: string | null) => void;
}) {
  async function run() {
    try {
      await api.post(`/api/clients/probes/${probeId}/checks/${check.id}/run`);
      setError(null);
      onInfo(check.type === "path"
        ? "Auftrag gesendet: Pfad wird mit dem nächsten Heartbeat (≤ 30 s) neu ermittelt."
        : "Auftrag gesendet: Die Messung startet mit dem nächsten Heartbeat (≤ 30 s); Ergebnis danach hier.");
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  return <button onClick={run}>{check.type === "path" ? "Pfad neu ermitteln" : "Jetzt messen"}</button>;
}

const VERDICT_CLASS: Record<string, string> = {
  ok: "ok", no_data: "dim", icmp_limit: "dim", loss: "err", loss_continues: "warn", latency: "warn",
};

export function PathPanel({ view }: { view: WachtlPathView | null }) {
  if (!view) return null;
  return (
    <>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Hops (MTR) <span className="muted" style={{ fontSize: 13 }}>· Hop-Pings im gewählten Zeitraum</span></h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th className="num">Hop</th><th>Router</th><th className="num">Verlust</th><th className="num">RTT ø</th>
                <th className="num">min</th><th className="num">max</th><th className="num">Jitter</th><th>Bewertung</th></tr>
            </thead>
            <tbody>
              {view.hops.map((h) => (
                <tr key={h.hop}>
                  <td className="num">{h.hop}</td>
                  <td>{h.ip}{h.alt_ips.length > 0 && <span className="muted" style={{ fontSize: 12 }}> (auch {h.alt_ips.join(", ")})</span>}</td>
                  <td className="num">{fmtNum(h.loss_pct, 1, "%")}</td>
                  <td className="num">{fmtNum(h.rtt_avg, 1, "ms")}</td>
                  <td className="num">{fmtNum(h.rtt_min, 1)}</td>
                  <td className="num">{fmtNum(h.rtt_max, 1)}</td>
                  <td className="num">{fmtNum(h.jitter_ms, 1)}</td>
                  <td><span className={`badge ${VERDICT_CLASS[h.verdict]}`}>{h.verdict_label}</span></td>
                </tr>
              ))}
              {!view.hops.length && <tr><td colSpan={8} className="muted">Keine Hop-Pings in diesem Zeitraum.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
          Verlust nur an einem Hop, während spätere Hops ihn nicht zeigen, ist meist ICMP-Drosselung des Routers – kein
          echtes Problem. Echter Verlust beginnt an einem Hop und bleibt bis zum Ziel.
        </p>
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Pfad</h2>
        {view.current ? (
          <p className="muted" style={{ marginTop: 0 }}>
            Zuletzt ermittelt {fmtDateTime(view.current.ts)} · {view.current.reached ? "Ziel erreicht" : "Ziel nicht erreicht"}
            {view.current.reason ? ` · Anlass: ${view.current.reason}` : ""}
          </p>
        ) : <p className="muted">Noch kein Pfad ermittelt.</p>}
        <div className="table-scroll">
          <table>
            <thead><tr><th>Seit</th><th>Bis</th><th>Pfad</th><th>Ziel</th></tr></thead>
            <tbody>
              {view.history.map((r) => (
                <tr key={r.id}>
                  <td>{fmtDateTime(r.first_ts)}{r.changed && <> <span className="badge warn">Wechsel</span></>}</td>
                  <td>{fmtDateTime(r.ts)}</td>
                  <td style={{ fontSize: 12 }}>{r.hops.map((h) => h.ip ?? "*").join(" → ")}</td>
                  <td><span className={`badge ${r.reached ? "ok" : "err"}`}>{r.reached ? "erreicht" : "nicht erreicht"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// chart: Zeitfenster-Props (Zoom/Verschieben); visible: Zeile im sichtbaren Fenster (ohne Ladepuffer).
export function BandwidthPanel({ runs, chart, visible }: {
  runs: WachtlBandwidthRun[]; chart: ChartWindowProps; visible: (ts: string) => boolean;
}) {
  const ok = runs.filter((r) => r.mbps !== null && !r.error);
  const series = (dir: "down" | "up") =>
    ok.filter((r) => r.direction === dir).map((r) => [new Date(r.ts).getTime(), r.mbps] as [number, number | null]).reverse();
  return (
    <>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Durchsatz (Mbit/s)</h2>
        {!runs.length && <p className="muted">Keine Messungen in diesem Zeitraum.</p>}
        <TimeSeriesChart yName="Mbit/s" {...chart} series={[
          { name: "Download", data: series("down"), color: "#38bdf8", type: "line" },
          { name: "Upload", data: series("up"), color: "#a78bfa", type: "line" },
        ]} />
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Messungen</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Zeit</th><th>Richtung</th><th className="num">Mbit/s</th><th className="num">Latenz</th>
                <th className="num">Jitter</th><th className="num">Verlust</th><th className="num">Retransmits</th><th>Anlass</th><th>Fehler</th></tr>
            </thead>
            <tbody>
              {runs.filter((r) => visible(r.ts)).slice(0, 200).map((r) => (
                <tr key={r.id}>
                  <td>{fmtDateTime(r.ts)}</td>
                  <td>{r.direction === "down" ? "↓ Download" : "↑ Upload"}</td>
                  <td className="num">{fmtNum(r.mbps, 1)}</td>
                  <td className="num">{fmtNum(r.latency_ms, 1, "ms")}</td>
                  <td className="num">{fmtNum(r.jitter_ms, 1, "ms")}</td>
                  <td className="num">{fmtNum(r.loss_pct, 1, "%")}</td>
                  <td className="num">{r.retransmits ?? "–"}</td>
                  <td>{r.trigger === "manual" ? "manuell" : "Zeitplan"}</td>
                  <td>{r.error ? <span className="badge err" title={r.error}>Fehler</span> : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

export function RtpPanel({ runs, chart, visible }: {
  runs: WachtlRtpRun[]; chart: ChartWindowProps; visible: (ts: string) => boolean;
}) {
  const t = (r: WachtlRtpRun) => new Date(r.ts).getTime();
  const asc = [...runs].reverse();
  return (
    <>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Sprachqualität (MOS)</h2>
        {!runs.length && <p className="muted">Keine Sprachtests in diesem Zeitraum.</p>}
        <TimeSeriesChart yName="MOS" y2Name="Verlust %" {...chart} series={[
          { name: "MOS", data: asc.map((r) => [t(r), r.mos]), color: "#22c55e" },
          { name: "Verlust %", data: asc.filter((r) => (r.loss_pct ?? 0) > 0).map((r) => [t(r), r.loss_pct]),
            color: "#ef4444", type: "bar", yAxisIndex: 1 },
        ]} />
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Sprachtests</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Zeit</th><th className="num">MOS</th><th className="num">Laufzeit</th><th className="num">Jitter ↑/↓</th>
                <th className="num">Verlust ↑/↓</th><th>Codec</th><th>Anlass</th><th>Fehler</th></tr>
            </thead>
            <tbody>
              {runs.filter((r) => visible(r.ts)).slice(0, 200).map((r) => (
                <tr key={r.id}>
                  <td>{fmtDateTime(r.ts)}</td>
                  <td className="num">{fmtNum(r.mos, 2)}</td>
                  <td className="num">{fmtNum(r.rtt_ms, 0, "ms")}</td>
                  <td className="num">{r.mode === "wachtl" ? `${fmtNum(r.up_jitter_ms, 1)} / ${fmtNum(r.down_jitter_ms, 1)}` : fmtNum(r.jitter_ms, 1)}</td>
                  <td className="num">{r.mode === "wachtl" ? `${fmtNum(r.up_loss_pct, 1)} / ${fmtNum(r.down_loss_pct, 1)} %` : fmtNum(r.loss_pct, 1, "%")}</td>
                  <td>{r.codec ?? "–"}{r.ptime_ms ? `, ${r.ptime_ms} ms` : ""}</td>
                  <td>{r.trigger === "manual" ? "manuell" : "Zeitplan"}</td>
                  <td>{r.error ? <span className="badge err" title={r.error}>Fehler</span> : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

const SEV_CLASS = { critical: "err", warning: "warn", info: "dim" } as const;

export function EventList({ events, showProbe = true }: { events: WachtlEvent[]; showProbe?: boolean }) {
  if (!events.length) return <p className="muted">Keine Ereignisse.</p>;
  return (
    <div className="table-scroll">
      <table>
        <thead><tr><th>Zeit</th>{showProbe && <th>Standort</th>}<th>Ereignis</th><th>Details</th></tr></thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{fmtDateTime(e.ts)}</td>
              {showProbe && <td>{e.site_name}</td>}
              <td><span className={`badge ${SEV_CLASS[e.severity]}`}>{e.severity === "critical" ? "kritisch" : e.severity === "warning" ? "Warnung" : "Info"}</span> {e.title}</td>
              <td className="muted" style={{ fontSize: 12 }}>{e.detail ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
