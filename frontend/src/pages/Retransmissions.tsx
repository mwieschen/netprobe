import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  RetransConfig,
  RetransCorrelation,
  RetransTargetConfig,
  RetransTest,
  RetransTestDetail,
} from "../api";
import { useAuth } from "../auth";
import TimeSeriesChart from "../components/Chart";
import { Badge, ErrorBox, Modal } from "../components/common";
import { fmtDateTime, fmtNum } from "../util";

interface Interface {
  index: number;
  name: string;
  description: string | null;
}

interface TargetRow {
  host: string;
  port: number;
}

const CLASSIFICATION_LABEL: Record<string, string> = {
  OK: "Unauffällig",
  LOCAL_NETWORK_ISSUE: "Lokales Netzwerk",
  ISP_OR_WAN_LINK_ISSUE: "Provider / WAN-Anbindung",
  REMOTE_SERVER_ISSUE: "Entfernter Server",
  UPLINK_CONGESTION: "Upload-Überlastung",
  DOWNLINK_CONGESTION: "Download-Überlastung",
  UNKNOWN: "Uneindeutig",
};

const QUICK_PRESETS: TargetRow[] = [
  { host: "1.1.1.1", port: 443 },
  { host: "8.8.8.8", port: 443 },
  { host: "9.9.9.9", port: 443 },
];

export default function RetransmissionsPage() {
  const { isAdmin } = useAuth();
  const [tests, setTests] = useState<RetransTest[]>([]);
  const [config, setConfig] = useState<RetransConfig | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<RetransTestDetail | null>(null);
  const [correlation, setCorrelation] = useState<RetransCorrelation | null>(null);
  const [showStart, setShowStart] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  function load() {
    api.get<RetransTest[]>("/api/retransmission/tests?days=30").then((rows) => {
      setTests(rows);
      if (selected === null && rows.length) setSelected(rows[0].id);
    });
  }
  useEffect(load, []);
  useEffect(() => {
    api.get<RetransConfig>("/api/retransmission/config").then(setConfig).catch(() => {});
  }, []);

  useEffect(() => {
    if (selected === null) return;
    api.get<RetransTestDetail>(`/api/retransmission/tests/${selected}`).then(setDetail).catch(() => setDetail(null));
    api
      .get<RetransCorrelation>(`/api/retransmission/tests/${selected}/correlation`)
      .then(setCorrelation)
      .catch(() => setCorrelation(null));
  }, [selected]);

  async function remove(id: number) {
    if (!confirm("Test wirklich löschen?")) return;
    await api.del(`/api/retransmission/tests/${id}`);
    if (selected === id) setSelected(null);
    load();
  }

  async function exportReport(id: number) {
    setExporting(true);
    setError(null);
    try {
      const r = await api.post<{ report_id: number; html_url: string; pdf_url: string }>(
        `/api/retransmission/tests/${id}/report`,
        { customer: "", location: "", notes: null },
      );
      window.open(r.html_url, "_blank");
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setExporting(false);
    }
  }

  const rate = detail?.retransmission_rate_percent ?? null;
  const warnLevel =
    rate !== null && config ? (rate >= config.alert_threshold_pct ? "err" : rate >= config.warn_threshold_pct ? "warn" : "ok") : null;
  const isActive = detail?.test_mode === "active";

  const timelineSeries = detail
    ? [
        {
          name: "Retransmission-Rate",
          color: "#f87171",
          data: detail.timeline.map(
            (p) => [new Date(detail.ts).getTime() + p.t_offset_s * 1000, p.rate_percent] as [number, number | null],
          ),
        },
      ]
    : [];

  return (
    <div>
      <div className="topbar">
        <h1>TCP-Retransmissions</h1>
        {isAdmin && (
          <button className="primary" onClick={() => setShowStart(true)}>
            Test starten
          </button>
        )}
      </div>
      <p className="muted">
        Passive Kurz-Aufnahmen erkennen TCP-Retransmissions im laufenden Traffic. Aktive Tests senden
        gezielte TCP-Connect-Proben zu frei wählbaren Zielen. Beide ordnen die wahrscheinliche Ursache
        automatisch ein (lokales Netzwerk, Provider/WAN-Anbindung oder einzelner Server).
      </p>
      <ErrorBox error={error} />

      <div className="grid cols-2">
        <div className="card">
          <h2>Historie</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Zeit</th>
                  <th>Interface</th>
                  <th>Modus</th>
                  <th>Status</th>
                  <th className="num">Rate</th>
                  <th>Klassifikation</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {tests.map((t) => (
                  <tr
                    key={t.id}
                    style={{ cursor: "pointer", background: selected === t.id ? "var(--bg)" : undefined }}
                    onClick={() => setSelected(t.id)}
                  >
                    <td>{fmtDateTime(t.ts)}</td>
                    <td>{t.interface}</td>
                    <td>
                      <span className={`badge ${t.test_mode === "active" ? "warn" : "dim"}`}>
                        {t.test_mode === "active" ? "Aktiv" : "Passiv"}
                      </span>
                    </td>
                    <td>
                      <Badge
                        ok={t.status === "error" ? false : t.status === "done" ? true : null}
                        label={t.status === "running" ? "läuft…" : undefined}
                      />
                    </td>
                    <td className="num">{fmtNum(t.retransmission_rate_percent, 2, "%")}</td>
                    <td>{t.classification ? CLASSIFICATION_LABEL[t.classification] ?? t.classification : "–"}</td>
                    <td className="num" onClick={(e) => e.stopPropagation()}>
                      {isAdmin && (
                        <button className="danger" onClick={() => remove(t.id)}>
                          Löschen
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {!tests.length && (
                  <tr>
                    <td colSpan={7} className="muted">
                      Noch keine Tests. Klicken Sie auf "Test starten".
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="grid cols-4" style={{ alignContent: "flex-start" }}>
          <div className="card stat">
            <div className="label">Rate</div>
            <div className="value">
              {rate !== null ? (
                <span className={`badge ${warnLevel ?? "dim"}`}>{fmtNum(rate, 2, "%")}</span>
              ) : (
                "–"
              )}
            </div>
          </div>
          <div className="card stat">
            <div className="label">Retransmissions</div>
            <div className="value">{detail?.total_retransmissions ?? "–"}</div>
          </div>
          <div className="card stat">
            <div className="label">Pakete</div>
            <div className="value">{detail?.total_packets ?? "–"}</div>
          </div>
          <div className="card stat">
            <div className="label">Klassifikation</div>
            <div className="value" style={{ fontSize: 15 }}>
              {detail?.classification ? CLASSIFICATION_LABEL[detail.classification] ?? detail.classification : "–"}
            </div>
          </div>
        </div>
      </div>

      {detail && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="topbar">
            <h2>
              Test #{detail.id}{" "}
              <span className="muted" style={{ fontSize: 13 }}>
                ({detail.interface} · {detail.duration_s}s ·{" "}
                {isActive
                  ? `Aktiv, ${detail.concurrent ? "parallel" : "nacheinander"}`
                  : detail.bpf ?? "alle TCP-Pakete"}
                )
              </span>
            </h2>
            {isAdmin && (
              <button disabled={exporting} onClick={() => exportReport(detail.id)}>
                {exporting ? "erzeuge…" : "Report exportieren"}
              </button>
            )}
          </div>

          {detail.error && <div className="error-box">{detail.error}</div>}
          {detail.recommendation && <div className="warn-box">{detail.recommendation}</div>}

          {isActive && detail.configured_targets.length > 0 && (
            <p className="muted">
              Konfigurierte Ziele:{" "}
              {detail.configured_targets
                .map((t) => (t.host !== t.ip ? `${t.host} (${t.ip}):${t.port}` : `${t.ip}:${t.port}`))
                .join(", ")}
            </p>
          )}

          {detail.timeline.length > 0 && (
            <TimeSeriesChart yName="%" height={200} series={timelineSeries} />
          )}

          <h3 style={{ marginTop: 16 }}>{isActive ? "Getestete Ziele" : "Betroffene Ziele"}</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Ziel</th>
                  <th>Netz</th>
                  <th>Richtung</th>
                  <th className="num">Retransmissions</th>
                  <th className="num">Pakete</th>
                  <th className="num">Rate</th>
                  {isActive && (
                    <>
                      <th className="num">Verbindungen</th>
                      <th className="num">Fehler</th>
                      <th className="num">Latenz ø</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {detail.targets.map((t, i) => (
                  <tr key={i}>
                    <td>
                      {t.target_ip}
                      {t.target_port ? `:${t.target_port}` : ""}
                    </td>
                    <td>
                      <span className={`badge ${t.is_private ? "dim" : "warn"}`}>{t.is_private ? "LAN" : "WAN"}</span>
                    </td>
                    <td>{t.direction}</td>
                    <td className="num">{t.retransmissions}</td>
                    <td className="num">{t.packets}</td>
                    <td className="num">{fmtNum(t.rate_percent, 2, "%")}</td>
                    {isActive && (
                      <>
                        <td className="num">{t.connection_attempts ?? "–"}</td>
                        <td className="num">{t.connection_errors ?? "–"}</td>
                        <td className="num">{fmtNum(t.avg_connect_latency_ms, 1, "ms")}</td>
                      </>
                    )}
                  </tr>
                ))}
                {!detail.targets.length && (
                  <tr>
                    <td colSpan={isActive ? 9 : 6} className="muted">
                      Keine Retransmissions im Testfenster.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {correlation && (
            <>
              <h3 style={{ marginTop: 16 }}>Korrelation mit anderen Tests</h3>
              <p className="muted">
                Zeitfenster {fmtDateTime(correlation.window.start)} – {fmtDateTime(correlation.window.end)}:{" "}
                {correlation.correlated ? (
                  <Badge ok={false} label="auffällig" />
                ) : (
                  <Badge ok={true} label="unauffällig" />
                )}{" "}
                · {correlation.ping_failure_count} Ping-Verluste · {correlation.wan_failures.length} WAN-Ausfälle
              </p>
            </>
          )}
        </div>
      )}

      {showStart && config && (
        <StartModal
          config={config}
          onClose={() => setShowStart(false)}
          onStarted={(id) => {
            setShowStart(false);
            load();
            setSelected(id);
          }}
          setError={setError}
        />
      )}
    </div>
  );
}

function StartModal({
  config,
  onClose,
  onStarted,
  setError,
}: {
  config: RetransConfig;
  onClose: () => void;
  onStarted: (id: number) => void;
  setError: (s: string) => void;
}) {
  const [ifaces, setIfaces] = useState<Interface[]>([]);
  const [iface, setIface] = useState(config.default_interface);
  const [duration, setDuration] = useState(config.default_duration_s);
  const [bpf, setBpf] = useState("");
  const [mode, setMode] = useState<"passive" | "active">("passive");
  const [concurrent, setConcurrent] = useState(true);
  const [targets, setTargets] = useState<TargetRow[]>([]);
  const [saved, setSaved] = useState<RetransTargetConfig[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<Interface[]>("/api/captures/interfaces")
      .then((rows) => {
        setIfaces(rows);
        if (rows.length && !rows.some((r) => r.name === config.default_interface)) {
          setIface(rows[0].name);
        }
      })
      .catch(() => {});
  }, []);

  function loadSaved() {
    api.get<RetransTargetConfig[]>("/api/retransmission/targets").then(setSaved).catch(() => {});
  }
  useEffect(loadSaved, []);

  function addTarget(t: TargetRow) {
    if (targets.length >= config.max_active_targets) return;
    if (targets.some((x) => x.host === t.host && x.port === t.port)) return;
    setTargets([...targets, t]);
  }
  function updateTarget(i: number, patch: Partial<TargetRow>) {
    setTargets(targets.map((t, idx) => (idx === i ? { ...t, ...patch } : t)));
  }
  function removeTarget(i: number) {
    setTargets(targets.filter((_, idx) => idx !== i));
  }

  async function start() {
    setBusy(true);
    try {
      const test = await api.post<{ id: number }>("/api/retransmission/tests", {
        interface: iface,
        duration_s: duration,
        bpf: mode === "passive" ? bpf || null : null,
        mode,
        concurrent,
        targets: mode === "active" ? targets.map((t) => ({ ...t, protocol: "tcp" })) : [],
      });
      onStarted(test.id);
    } catch (err) {
      setError((err as ApiError).detail);
      onClose();
    } finally {
      setBusy(false);
    }
  }

  const canStart = iface && (mode === "passive" || targets.length > 0);

  return (
    <Modal title="Retransmission-Test starten" onClose={onClose}>
      <div className="row">
        <div className="field" style={{ flex: 1 }}>
          <label>Interface</label>
          {ifaces.length ? (
            <select value={iface} onChange={(e) => setIface(e.target.value)}>
              {ifaces.map((i) => (
                <option key={i.name} value={i.name}>
                  {i.name} {i.description ? `(${i.description})` : ""}
                </option>
              ))}
            </select>
          ) : (
            <input value={iface} onChange={(e) => setIface(e.target.value)} />
          )}
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Dauer (s)</label>
          <input
            type="number"
            value={duration}
            min={5}
            max={config.max_duration_s}
            onChange={(e) => setDuration(+e.target.value)}
          />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Modus</label>
          <select value={mode} onChange={(e) => setMode(e.target.value as "passive" | "active")}>
            <option value="passive">Passiv (laufender Traffic)</option>
            <option value="active">Aktiv (gezielte Ziele)</option>
          </select>
        </div>
      </div>

      {mode === "passive" && (
        <div className="field">
          <label>BPF-Filter (optional, Standard: "tcp")</label>
          <input value={bpf} onChange={(e) => setBpf(e.target.value)} placeholder="tcp" />
        </div>
      )}

      {mode === "active" && (
        <>
          <div className="field">
            <label>
              <input type="checkbox" checked={concurrent} onChange={(e) => setConcurrent(e.target.checked)} />{" "}
              Ziele parallel testen (sonst nacheinander, Dauer wird aufgeteilt)
            </label>
          </div>

          <label>Ziele (max. {config.max_active_targets})</label>
          {targets.map((t, i) => (
            <div className="row" key={i} style={{ marginBottom: 6 }}>
              <input
                style={{ flex: 2 }}
                value={t.host}
                placeholder="IP oder Hostname"
                onChange={(e) => updateTarget(i, { host: e.target.value })}
              />
              <input
                style={{ flex: 1 }}
                type="number"
                value={t.port}
                placeholder="Port"
                onChange={(e) => updateTarget(i, { port: +e.target.value })}
              />
              <button onClick={() => removeTarget(i)}>✕</button>
            </div>
          ))}
          {targets.length < config.max_active_targets && (
            <button onClick={() => addTarget({ host: "", port: 443 })}>+ Ziel hinzufügen</button>
          )}

          <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 10 }}>
            {QUICK_PRESETS.map((p) => (
              <button key={`${p.host}:${p.port}`} className="badge dim" onClick={() => addTarget(p)}>
                {p.host}:{p.port}
              </button>
            ))}
            {saved.map((s) => (
              <button
                key={s.id}
                className="badge dim"
                title={s.description ?? undefined}
                onClick={() => addTarget({ host: s.host, port: s.port })}
              >
                {s.name}
              </button>
            ))}
          </div>

          <SavedTargetManager saved={saved} onSaved={loadSaved} setError={setError} />
        </>
      )}

      <p className="muted" style={{ marginTop: 10 }}>
        Der Test blockiert diesen Dialog für die gewählte Dauer, während im Hintergrund{" "}
        {mode === "active" ? "Verbindungen getestet und" : ""} mitgeschnitten wird.
      </p>
      <button className="primary" onClick={start} disabled={busy || !canStart}>
        {busy ? `läuft (${duration}s)…` : "Test starten"}
      </button>
    </Modal>
  );
}

function SavedTargetManager({
  saved,
  onSaved,
  setError,
}: {
  saved: RetransTargetConfig[];
  onSaved: () => void;
  setError: (s: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState(443);
  const [description, setDescription] = useState("");

  async function save() {
    try {
      await api.post("/api/retransmission/targets", { name, host, port, description: description || null });
      setName("");
      setHost("");
      setPort(443);
      setDescription("");
      onSaved();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function remove(id: number) {
    await api.del(`/api/retransmission/targets/${id}`);
    onSaved();
  }

  if (!open) {
    return (
      <button style={{ marginTop: 10 }} onClick={() => setOpen(true)}>
        Ziel-Presets verwalten…
      </button>
    );
  }

  return (
    <div style={{ marginTop: 10 }}>
      {saved.length > 0 && (
        <table style={{ marginBottom: 8 }}>
          <tbody>
            {saved.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td className="muted">
                  {s.host}:{s.port}
                </td>
                <td className="muted">{s.usage_count}x genutzt</td>
                <td className="num">
                  <button className="danger" onClick={() => remove(s.id)}>
                    Löschen
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row" style={{ flexWrap: "wrap" }}>
        <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} style={{ flex: 2 }} />
        <input placeholder="Host" value={host} onChange={(e) => setHost(e.target.value)} style={{ flex: 2 }} />
        <input
          type="number"
          placeholder="Port"
          value={port}
          onChange={(e) => setPort(+e.target.value)}
          style={{ flex: 1 }}
        />
        <input
          placeholder="Beschreibung (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ flex: 3 }}
        />
        <button className="primary" onClick={save} disabled={!name || !host}>
          Speichern
        </button>
      </div>
    </div>
  );
}
