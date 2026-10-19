import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  HealthCheckResult,
  HealthCheckType,
  HealthProbe,
} from "../api";
import { useAuth } from "../auth";
import TimeSeriesChart from "../components/Chart";
import TimeRangePicker, { TimeWindow, windowBounds, windowQuery, zoomWindow } from "../components/TimeRange";
import { Badge, ErrorBox, Modal } from "../components/common";
import { fmtDateTime, fmtNum } from "../util";

const PRESETS = ["24h", "7d", "30d", "90d", "365d", "all"];

const TYPE_LABEL: Record<HealthCheckType, string> = {
  dns: "DNS",
  http: "HTTP(S)",
  smtp: "SMTP",
  imap: "IMAP",
  ntp: "NTP",
};

const TYPE_ORDER: HealthCheckType[] = ["dns", "http", "smtp", "imap", "ntp"];

export default function HealthChecksPage() {
  const { isAdmin } = useAuth();
  const [probes, setProbes] = useState<HealthProbe[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [history, setHistory] = useState<HealthCheckResult[]>([]);
  const [win, setWin] = useState<TimeWindow>({ range: "7d" });
  const [showAdd, setShowAdd] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  function load() {
    api.get<HealthProbe[]>("/api/health-checks").then((p) => {
      setProbes(p);
      if (selected === null && p.length) setSelected(p[0].id);
    });
  }
  useEffect(load, []);

  useEffect(() => {
    if (selected === null) return;
    api
      .get<HealthCheckResult[]>(`/api/health-checks/${selected}/history?${windowQuery(win)}`)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [selected, win]);

  async function run(id: number) {
    setBusy(id);
    setError(null);
    try {
      await api.post(`/api/health-checks/${id}/run`);
      load();
      if (selected === id) {
        api
          .get<HealthCheckResult[]>(`/api/health-checks/${id}/history?${windowQuery(win)}`)
          .then(setHistory);
      }
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(null);
    }
  }

  async function remove(id: number) {
    if (!confirm("Probe wirklich löschen?")) return;
    await api.del(`/api/health-checks/${id}`);
    if (selected === id) setSelected(null);
    load();
  }

  const counts = useMemo(() => {
    let ok = 0;
    let fail = 0;
    let pending = 0;
    for (const p of probes) {
      if (!p.last_result) pending++;
      else if (p.last_result.ok) ok++;
      else fail++;
    }
    return { ok, fail, pending };
  }, [probes]);

  const grouped = useMemo(() => {
    const m = new Map<HealthCheckType, HealthProbe[]>();
    for (const t of TYPE_ORDER) m.set(t, []);
    for (const p of probes) m.get(p.check_type)?.push(p);
    return m;
  }, [probes]);

  const selectedProbe = probes.find((p) => p.id === selected) ?? null;
  const bounds = windowBounds(win);
  const ordered = [...history].reverse();
  const rtPt = ordered.map(
    (r) => [new Date(r.ts).getTime(), r.response_ms] as [number, number | null],
  );

  return (
    <div>
      <div className="topbar">
        <h1>System Health</h1>
        {isAdmin && (
          <button className="primary" onClick={() => setShowAdd(true)}>
            Prüfung hinzufügen
          </button>
        )}
      </div>
      <ErrorBox error={error} />

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="card stat">
          <div className="label">OK</div>
          <div className="value" style={{ color: "#22c55e" }}>
            {counts.ok}
          </div>
        </div>
        <div className="card stat">
          <div className="label">Fehler</div>
          <div className="value" style={{ color: counts.fail ? "#ef4444" : undefined }}>
            {counts.fail}
          </div>
        </div>
        <div className="card stat">
          <div className="label">Ausstehend</div>
          <div className="value">{counts.pending}</div>
        </div>
        <div className="card stat">
          <div className="label">Prüfungen</div>
          <div className="value">{probes.length}</div>
        </div>
      </div>

      {TYPE_ORDER.filter((t) => (grouped.get(t) ?? []).length > 0).map((t) => (
        <div className="card" style={{ marginBottom: 16 }} key={t}>
          <h2>{TYPE_LABEL[t]}</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Ziel</th>
                  <th>Status</th>
                  <th className="num">Antwort</th>
                  <th>Letzter Lauf</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {(grouped.get(t) ?? []).map((p) => (
                  <tr
                    key={p.id}
                    style={{ cursor: "pointer", background: selected === p.id ? "var(--bg)" : undefined }}
                    onClick={() => setSelected(p.id)}
                  >
                    <td>{p.name}</td>
                    <td>{p.target}</td>
                    <td>
                      <Badge
                        ok={p.last_result ? p.last_result.ok : null}
                        label={!p.enabled ? "aus" : p.last_result?.status ?? undefined}
                      />
                    </td>
                    <td className="num">{fmtNum(p.last_result?.response_ms ?? null, 0, "ms")}</td>
                    <td>{p.last_result ? fmtDateTime(p.last_result.ts) : "–"}</td>
                    <td className="num" onClick={(e) => e.stopPropagation()}>
                      {isAdmin && (
                        <div className="row" style={{ justifyContent: "flex-end" }}>
                          <button disabled={busy === p.id} onClick={() => run(p.id)}>
                            {busy === p.id ? "läuft…" : "Jetzt"}
                          </button>
                          <button
                            onClick={() =>
                              api
                                .patch(`/api/health-checks/${p.id}`, { enabled: !p.enabled })
                                .then(load)
                            }
                          >
                            {p.enabled ? "Deaktivieren" : "Aktivieren"}
                          </button>
                          <button className="danger" onClick={() => remove(p.id)}>
                            Löschen
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {!probes.length && (
        <div className="card">
          <p className="muted">
            Noch keine Prüfungen. Beispiele: DNS-Auflösung gegen 1.1.1.1/8.8.8.8, HTTPS-Endpoint
            mit Zertifikatsprüfung, SMTP-STARTTLS des Mailservers, NTP-Abweichung.
          </p>
        </div>
      )}

      {selectedProbe && (
        <div className="card">
          <h2>
            Verlauf: {selectedProbe.name}{" "}
            <span className="muted" style={{ fontSize: 13 }}>
              ({TYPE_LABEL[selectedProbe.check_type]} · {selectedProbe.target})
            </span>
          </h2>
          <div style={{ marginBottom: 8 }}>
            <TimeRangePicker value={win} onChange={setWin} presets={PRESETS} />
          </div>
          {selectedProbe.last_result?.error && (
            <div className="error-box">{selectedProbe.last_result.error}</div>
          )}
          <TimeSeriesChart
            xMin={bounds.min}
            xMax={bounds.max}
            zoomKey={`${selected}|${windowQuery(win)}`}
            onZoomRange={(s, e) => setWin(zoomWindow(s, e))}
            yName="ms"
            height={220}
            series={[{ name: "Antwortzeit", data: rtPt, color: "#38bdf8" }]}
          />
          <div className="table-scroll" style={{ marginTop: 12 }}>
            <table>
              <thead>
                <tr>
                  <th>Zeit</th>
                  <th>Quelle</th>
                  <th>OK</th>
                  <th className="num">Antwort</th>
                  <th>Status</th>
                  <th>Fehler</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 200).map((r) => (
                  <tr key={r.id}>
                    <td>{fmtDateTime(r.ts)}</td>
                    <td>{r.source === "scheduled" ? "Zeitplan" : "manuell"}</td>
                    <td>
                      <Badge ok={r.ok} />
                    </td>
                    <td className="num">{fmtNum(r.response_ms, 0, "ms")}</td>
                    <td>{r.status ?? "–"}</td>
                    <td className="muted">{r.error ?? ""}</td>
                  </tr>
                ))}
                {!history.length && (
                  <tr>
                    <td colSpan={6} className="muted">
                      Noch keine Läufe.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showAdd && (
        <ProbeModal
          onClose={() => setShowAdd(false)}
          onSaved={() => {
            setShowAdd(false);
            load();
          }}
          setError={setError}
        />
      )}
    </div>
  );
}

function ProbeModal({
  onClose,
  onSaved,
  setError,
}: {
  onClose: () => void;
  onSaved: () => void;
  setError: (s: string) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<HealthCheckType>("dns");
  const [target, setTarget] = useState("");
  const [interval_s, setInterval] = useState(300);
  const [params, setParams] = useState<Record<string, unknown>>({ record_type: "A", servers: "1.1.1.1,8.8.8.8" });

  function setType2(t: HealthCheckType) {
    setType(t);
    setParams(
      t === "dns"
        ? { record_type: "A", servers: "1.1.1.1,8.8.8.8", expected: "" }
        : t === "http"
          ? { expect_substring: "", verify_tls: true }
          : t === "ntp"
            ? { max_offset_ms: 1000 }
            : { port: t === "smtp" ? 25 : 143, starttls: true },
    );
  }

  async function save() {
    const p: Record<string, unknown> = { ...params };
    if (type === "dns" && typeof p.servers === "string") {
      p.servers = (p.servers as string)
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    try {
      await api.post("/api/health-checks", {
        name,
        check_type: type,
        target,
        params: p,
        interval_s,
      });
      onSaved();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <Modal title="Prüfung hinzufügen" onClose={onClose}>
      <div className="row">
        <div className="field" style={{ flex: 2 }}>
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Typ</label>
          <select value={type} onChange={(e) => setType2(e.target.value as HealthCheckType)}>
            {TYPE_ORDER.map((t) => (
              <option key={t} value={t}>
                {TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Intervall (s)</label>
          <input
            type="number"
            value={interval_s}
            onChange={(e) => setInterval(+e.target.value)}
          />
        </div>
      </div>
      <div className="field">
        <label>
          {type === "dns"
            ? "Domain"
            : type === "http"
              ? "URL oder Host"
              : "Host"}
        </label>
        <input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder={type === "http" ? "https://mail.kunde.example" : "mail.kunde.example"}
        />
      </div>

      {type === "dns" && (
        <div className="row">
          <div className="field" style={{ flex: 1 }}>
            <label>Record</label>
            <select
              value={String(params.record_type ?? "A")}
              onChange={(e) => setParams({ ...params, record_type: e.target.value })}
            >
              {["A", "AAAA", "MX", "TXT", "CNAME", "NS"].map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ flex: 2 }}>
            <label>DNS-Server (Kommaliste, leer = System)</label>
            <input
              value={String(params.servers ?? "")}
              onChange={(e) => setParams({ ...params, servers: e.target.value })}
            />
          </div>
          <div className="field" style={{ flex: 2 }}>
            <label>Erwarteter Wert (optional)</label>
            <input
              value={String(params.expected ?? "")}
              onChange={(e) => setParams({ ...params, expected: e.target.value })}
            />
          </div>
        </div>
      )}

      {type === "http" && (
        <div className="row">
          <div className="field" style={{ flex: 1 }}>
            <label>Status (leer = 2xx/3xx)</label>
            <input
              type="number"
              value={params.expect_status ? String(params.expect_status) : ""}
              onChange={(e) =>
                setParams({ ...params, expect_status: e.target.value ? +e.target.value : undefined })
              }
            />
          </div>
          <div className="field" style={{ flex: 2 }}>
            <label>Text im Body (optional)</label>
            <input
              value={String(params.expect_substring ?? "")}
              onChange={(e) => setParams({ ...params, expect_substring: e.target.value })}
            />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>
              <input
                type="checkbox"
                checked={Boolean(params.verify_tls)}
                onChange={(e) => setParams({ ...params, verify_tls: e.target.checked })}
              />{" "}
              TLS prüfen
            </label>
          </div>
        </div>
      )}

      {(type === "smtp" || type === "imap") && (
        <div className="row">
          <div className="field" style={{ flex: 1 }}>
            <label>Port</label>
            <input
              type="number"
              value={String(params.port ?? (type === "smtp" ? 25 : 143))}
              onChange={(e) => setParams({ ...params, port: +e.target.value })}
            />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>
              <input
                type="checkbox"
                checked={Boolean(params.starttls)}
                onChange={(e) => setParams({ ...params, starttls: e.target.checked })}
              />{" "}
              STARTTLS aushandeln
            </label>
          </div>
        </div>
      )}

      {type === "ntp" && (
        <div className="field">
          <label>Max. Abweichung (ms)</label>
          <input
            type="number"
            value={String(params.max_offset_ms ?? 1000)}
            onChange={(e) => setParams({ ...params, max_offset_ms: +e.target.value })}
          />
        </div>
      )}

      <button className="primary" onClick={save} disabled={!name || !target}>
        Speichern
      </button>
    </Modal>
  );
}
