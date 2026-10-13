import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  DiagnosticPreset,
  DiagnosticRun,
  DiagnosticTarget,
  DiagnosticTestSelection,
  RetransConfig,
} from "../api";
import { useAuth } from "../auth";
import { Badge, ErrorBox, Modal } from "../components/common";
import { fmtDateTime, fmtNum } from "../util";

interface Interface {
  index: number;
  name: string;
  description: string | null;
}

const TEST_LABELS: Record<keyof DiagnosticTestSelection, string> = {
  ping: "Ping (Latenz, Verlust, Jitter)",
  retrans: "TCP-Retransmissions",
  mtu: "MTU-Pfad-Test",
  broadcast: "Broadcast-Analyse",
  traceroute: "Traceroute",
  speedtest: "Speedtest",
};
const TEST_ORDER: (keyof DiagnosticTestSelection)[] = [
  "ping", "retrans", "mtu", "broadcast", "traceroute", "speedtest",
];
const PER_TARGET_TESTS: (keyof DiagnosticTestSelection)[] = ["ping", "retrans", "mtu", "traceroute"];

const ISSUE_LABELS: Record<string, string> = {
  packet_loss_high: "Hoher Paketverlust",
  jitter_high: "Hoher Jitter",
  latency_high: "Hohe Latenz",
  ping_failed: "Ping fehlgeschlagen",
  retrans_critical: "Kritische Retransmission-Rate",
  retrans_high: "Erhöhte Retransmission-Rate",
  mtu_low: "Niedrige Pfad-MTU",
  mtu_undetermined: "Pfad-MTU nicht ermittelbar",
};

const QUICK_PRESETS: DiagnosticTarget[] = [
  { name: "Cloudflare DNS", host: "1.1.1.1" },
  { name: "Google DNS", host: "8.8.8.8" },
  { name: "Quad9 DNS", host: "9.9.9.9" },
];

const DURATIONS = [
  { value: 30, label: "30 Sekunden (Quick)" },
  { value: 60, label: "60 Sekunden (Standard)" },
  { value: 120, label: "2 Minuten (Ausführlich)" },
  { value: 300, label: "5 Minuten (Detailliert)" },
];

function statusBadge(status: "OK" | "WARN" | "KRITISCH" | null) {
  if (status === "OK") return <Badge ok={true} label="OK" />;
  if (status === "WARN") return <Badge ok={false} label="WARN" />;
  if (status === "KRITISCH") return <span className="badge err">KRITISCH</span>;
  return <Badge ok={null} />;
}

export default function NetworkDiagnosticsPage() {
  const { isAdmin } = useAuth();
  const [ifaces, setIfaces] = useState<Interface[]>([]);
  const [targets, setTargets] = useState<DiagnosticTarget[]>([{ name: "", host: "" }]);
  const [tests, setTests] = useState<DiagnosticTestSelection>({
    ping: true, retrans: false, mtu: false, broadcast: false, traceroute: false, speedtest: false,
  });
  const [iface, setIface] = useState("");
  const [duration, setDuration] = useState(60);
  const [presets, setPresets] = useState<DiagnosticPreset[]>([]);
  const [runs, setRuns] = useState<DiagnosticRun[]>([]);
  const [selected, setSelected] = useState<DiagnosticRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSavePreset, setShowSavePreset] = useState(false);

  function loadPresets() {
    api.get<DiagnosticPreset[]>("/api/diagnostics/presets").then(setPresets).catch(() => {});
  }
  function loadRuns() {
    api.get<DiagnosticRun[]>("/api/diagnostics/runs?days=30").then(setRuns).catch(() => {});
  }
  useEffect(() => {
    // Vorbelegung: vom Backend ermitteltes Standard-Interface (Default-Route),
    // sonst das erste von dumpcap gelistete.
    Promise.all([
      api.get<Interface[]>("/api/captures/interfaces").catch(() => [] as Interface[]),
      api.get<RetransConfig>("/api/retransmission/config").catch(() => null),
    ]).then(([rows, cfg]) => {
      setIfaces(rows);
      const def = cfg?.default_interface;
      if (def && (!rows.length || rows.some((r) => r.name === def))) setIface(def);
      else if (rows.length) setIface(rows[0].name);
    });
    loadPresets();
    loadRuns();
  }, []);

  function addTarget(t: DiagnosticTarget) {
    if (targets.length >= 10) return;
    if (targets.some((x) => x.host === t.host)) return;
    const withoutEmpty = targets.filter((x) => x.host.trim());
    setTargets([...withoutEmpty, t]);
  }
  function updateTarget(i: number, patch: Partial<DiagnosticTarget>) {
    setTargets(targets.map((t, idx) => (idx === i ? { ...t, ...patch } : t)));
  }
  function removeTarget(i: number) {
    setTargets(targets.filter((_, idx) => idx !== i));
  }

  function applyPreset(p: DiagnosticPreset) {
    setTargets(p.targets.length ? p.targets : [{ name: "", host: "" }]);
    setTests(p.tests);
    setIface(p.interface);
    setDuration(p.duration_s);
  }

  async function deletePreset(id: number) {
    await api.del(`/api/diagnostics/presets/${id}`);
    loadPresets();
  }

  const cleanTargets = targets.filter((t) => t.host.trim());
  const needsTargets = PER_TARGET_TESTS.some((k) => tests[k]);
  const canStart = Object.values(tests).some(Boolean) && (!needsTargets || cleanTargets.length > 0);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const run = await api.post<DiagnosticRun>("/api/diagnostics/run", {
        targets: cleanTargets, tests, interface: iface, duration_s: duration,
      });
      setSelected(run);
      loadRuns();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  async function removeRun(id: number) {
    if (!confirm("Lauf wirklich löschen?")) return;
    await api.del(`/api/diagnostics/runs/${id}`);
    if (selected?.id === id) setSelected(null);
    loadRuns();
  }

  const results = selected?.results ?? null;

  return (
    <div>
      <div className="topbar">
        <h1>Netzwerk-Diagnose</h1>
      </div>
      <p className="muted">
        Mehrere Tests gebündelt zu mehreren Zielen ausführen und Probleme auf einen Blick erkennen.
        Jeder Sub-Test nutzt die bestehende, eigenständig getestete Implementierung (Ping/WAN,
        Retransmissions, MTU, Traceroute, Speedtest) – diese Seite fasst nur die Ergebnisse zusammen.
      </p>
      <ErrorBox error={error} />

      <div className="card">
        <h2>Ziele</h2>
        {targets.map((t, i) => (
          <div className="row" key={i} style={{ marginBottom: 6 }}>
            <input
              style={{ flex: 1 }}
              value={t.name ?? ""}
              placeholder="Name (z. B. Cloudflare)"
              onChange={(e) => updateTarget(i, { name: e.target.value })}
            />
            <input
              style={{ flex: 2 }}
              value={t.host}
              placeholder="IP oder Hostname"
              onChange={(e) => updateTarget(i, { host: e.target.value })}
            />
            <button onClick={() => removeTarget(i)}>✕</button>
          </div>
        ))}
        {targets.length < 10 && (
          <button onClick={() => setTargets([...targets, { name: "", host: "" }])}>
            + Ziel hinzufügen
          </button>
        )}
        <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 10 }}>
          {QUICK_PRESETS.map((p) => (
            <button key={p.host} className="badge dim" onClick={() => addTarget(p)}>
              {p.name} ({p.host})
            </button>
          ))}
        </div>
      </div>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <div className="card">
          <h2>Tests</h2>
          {TEST_ORDER.map((k) => (
            <div className="field" key={k}>
              <label>
                <input
                  type="checkbox"
                  checked={tests[k]}
                  onChange={(e) => setTests({ ...tests, [k]: e.target.checked })}
                />{" "}
                {TEST_LABELS[k]}
              </label>
            </div>
          ))}
        </div>

        <div className="card">
          <h2>Einstellungen</h2>
          <div className="field">
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
          <div className="field">
            <label>Dauer</label>
            <select value={duration} onChange={(e) => setDuration(+e.target.value)}>
              {DURATIONS.map((d) => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
          </div>

          {presets.length > 0 && (
            <div className="field">
              <label>Gespeicherte Presets</label>
              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                {presets.map((p) => (
                  <span key={p.id} className="row" style={{ gap: 2 }}>
                    <button className="badge dim" onClick={() => applyPreset(p)}>{p.name}</button>
                    {isAdmin && (
                      <button className="badge dim" onClick={() => deletePreset(p.id)} title="Löschen">
                        ✕
                      </button>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="row" style={{ marginTop: 10 }}>
            {isAdmin && (
              <button className="primary" onClick={start} disabled={busy || !canStart}>
                {busy ? `läuft (${duration}s)…` : "🚀 Alle Tests starten"}
              </button>
            )}
            {isAdmin && (
              <button onClick={() => setShowSavePreset(true)} disabled={!canStart}>
                Als Preset speichern
              </button>
            )}
          </div>
        </div>
      </div>

      {results && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="topbar">
            <h2>
              Ergebnisse {statusBadge(results.overall_status)}{" "}
              <span className="muted" style={{ fontSize: 13 }}>
                ({results.interface} · {results.duration_s}s · {fmtDateTime(selected!.ts)})
              </span>
            </h2>
          </div>

          {tests.retrans && results.retrans_error && (
            <div className="error-box">Retransmission-Test fehlgeschlagen: {results.retrans_error}</div>
          )}

          {results.targets.map((t) => (
            <div className="card" style={{ marginBottom: 12 }} key={t.host}>
              <div className="topbar">
                <h3>
                  {t.name} <span className="muted" style={{ fontSize: 13 }}>({t.host})</span>
                </h3>
                {statusBadge(t.status)}
              </div>
              {t.issues.length > 0 && (
                <p className="muted">{t.issues.map((i) => ISSUE_LABELS[i] ?? i).join(" · ")}</p>
              )}
              <div className="grid cols-4">
                {tests.ping && t.ping && (
                  <div className="card stat">
                    <div className="label">Ping</div>
                    <div className="value">{fmtNum(t.ping.rtt_avg, 1, "ms")}</div>
                    <div className="sub">
                      Verlust {fmtNum(t.ping.loss_pct, 0, "%")} · Jitter {fmtNum(t.ping.jitter_ms, 1, "ms")}
                    </div>
                  </div>
                )}
                {tests.retrans && (
                  <div className="card stat">
                    <div className="label">Retransmissions</div>
                    {t.retrans ? (
                      <>
                        <div className="value">{fmtNum(t.retrans.rate_percent, 2, "%")}</div>
                        <div className="sub">{t.retrans.packets} Pakete</div>
                      </>
                    ) : (
                      <div className="sub">nicht verfügbar</div>
                    )}
                  </div>
                )}
                {tests.mtu && t.mtu && (
                  <div className="card stat">
                    <div className="label">Pfad-MTU</div>
                    <div className="value">{t.mtu.path_mtu ?? "–"}</div>
                    <div className="sub">{t.mtu.ok ? "ermittelt" : t.mtu.error ?? "nicht ermittelt"}</div>
                  </div>
                )}
                {tests.traceroute && t.traceroute && (
                  <div className="card stat">
                    <div className="label">Traceroute</div>
                    <div className="value">{t.traceroute.total_hops} Hops</div>
                    <div className="sub">
                      {t.traceroute.dest_reached ? "Ziel erreicht" : "Ziel nicht erreicht"}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}

          {(tests.broadcast || tests.speedtest) && (
            <div className="grid cols-2">
              {tests.broadcast && results.broadcast && (
                <div className="card">
                  <h3>Broadcast-Analyse ({results.interface})</h3>
                  {results.broadcast.frame_distribution ? (
                    <table>
                      <tbody>
                        <tr><td>Broadcast-Anteil</td>
                          <td className="num">{fmtNum(results.broadcast.frame_distribution.broadcast_pct, 1, "%")}</td></tr>
                        <tr><td>Multicast-Anteil</td>
                          <td className="num">{fmtNum(results.broadcast.frame_distribution.multicast_pct, 1, "%")}</td></tr>
                      </tbody>
                    </table>
                  ) : (
                    <p className="error-box">{results.broadcast.error}</p>
                  )}
                  {results.broadcast.warnings.map((w, i) => (
                    <div className="warn-box" key={i}>{w.message}</div>
                  ))}
                </div>
              )}
              {tests.speedtest && results.speedtest && (
                <div className="card">
                  <h3>Speedtest</h3>
                  {results.speedtest.ok ? (
                    <table>
                      <tbody>
                        <tr><td>Download</td><td className="num">{fmtNum(results.speedtest.download_mbps, 1, "Mbit/s")}</td></tr>
                        <tr><td>Upload</td><td className="num">{fmtNum(results.speedtest.upload_mbps, 1, "Mbit/s")}</td></tr>
                        <tr><td>Ping</td><td className="num">{fmtNum(results.speedtest.ping_ms, 1, "ms")}</td></tr>
                      </tbody>
                    </table>
                  ) : (
                    <p className="error-box">{results.speedtest.error}</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Historie</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Zeit</th><th>Interface</th><th>Ziele</th><th>Status</th><th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr
                  key={r.id}
                  style={{ cursor: "pointer", background: selected?.id === r.id ? "var(--bg)" : undefined }}
                  onClick={() => setSelected(r)}
                >
                  <td>{fmtDateTime(r.ts)}</td>
                  <td>{r.interface}</td>
                  <td>{r.targets.map((t) => t.name ?? t.host).join(", ") || "–"}</td>
                  <td>{statusBadge(r.overall_status)}</td>
                  <td className="num" onClick={(e) => e.stopPropagation()}>
                    {isAdmin && (
                      <button className="danger" onClick={() => removeRun(r.id)}>Löschen</button>
                    )}
                  </td>
                </tr>
              ))}
              {!runs.length && (
                <tr><td colSpan={5} className="muted">Noch keine Diagnose-Läufe.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showSavePreset && (
        <SavePresetModal
          targets={cleanTargets} tests={tests} iface={iface} duration={duration}
          onClose={() => setShowSavePreset(false)}
          onSaved={() => { setShowSavePreset(false); loadPresets(); }}
          setError={setError}
        />
      )}
    </div>
  );
}

function SavePresetModal({
  targets, tests, iface, duration, onClose, onSaved, setError,
}: {
  targets: DiagnosticTarget[];
  tests: DiagnosticTestSelection;
  iface: string;
  duration: number;
  onClose: () => void;
  onSaved: () => void;
  setError: (s: string) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await api.post("/api/diagnostics/presets", {
        name, targets, tests, interface: iface, duration_s: duration,
      });
      onSaved();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Als Preset speichern" onClose={onClose}>
      <div className="field">
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="z. B. WAN-Check" />
      </div>
      <button className="primary" onClick={save} disabled={busy || !name}>
        Speichern
      </button>
    </Modal>
  );
}
