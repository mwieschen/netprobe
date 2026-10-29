import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  CaptureInterface,
  VoipAnalysis,
  VoipServer,
  VoipTest,
} from "../api";
import { useAuth } from "../auth";
import TimeSeriesChart from "../components/Chart";
import TimeRangePicker, { chartWindowProps, inWindow, loadQuery, TimeWindow } from "../components/TimeRange";
import { Badge, ErrorBox, Modal } from "../components/common";
import { fmtDateTime, fmtNum } from "../util";

interface TestOptions {
  username?: string;
  password?: string;
  capture_interface?: string;
  capture_duration_s?: number;
}

const PRESETS = ["1h", "6h", "12h", "24h", "7d", "30d", "90d", "365d", "all"];

function triState(v: boolean | null): string {
  if (v === null) return "–";
  return v ? "ok" : "Fehler";
}

export default function VoipMonitorPage() {
  const { isAdmin } = useAuth();
  const [servers, setServers] = useState<VoipServer[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [history, setHistory] = useState<VoipTest[]>([]);
  const [win, setWin] = useState<TimeWindow>({ range: "24h" });
  const [showAdd, setShowAdd] = useState(false);
  const [testFor, setTestFor] = useState<VoipServer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  function loadServers() {
    api.get<VoipServer[]>("/api/voip/servers").then((s) => {
      setServers(s);
      if (selected === null && s.length) setSelected(s[0].id);
    });
  }
  useEffect(loadServers, []);

  useEffect(() => {
    if (selected === null) return;
    api
      .get<VoipTest[]>(`/api/voip/servers/${selected}/history?${loadQuery(win)}`)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [selected, win]);

  function reload() {
    loadServers();
    if (selected !== null) {
      api
        .get<VoipTest[]>(`/api/voip/servers/${selected}/history?${loadQuery(win)}`)
        .then(setHistory)
        .catch(() => {});
    }
  }

  async function remove(id: number) {
    if (!confirm("3CX-Server wirklich löschen? Die Test-Historie wird mit entfernt.")) return;
    await api.del(`/api/voip/servers/${id}`);
    if (selected === id) setSelected(null);
    loadServers();
  }

  async function runTest(server: VoipServer, opts: TestOptions) {
    setBusy(server.id);
    setError(null);
    try {
      await api.post<VoipTest>(`/api/voip/servers/${server.id}/test`, opts);
      reload();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(null);
      setTestFor(null);
    }
  }

  const chartWin = chartWindowProps(win, setWin, selected);
  const ordered = [...history].reverse();
  const okOnly = ordered.filter((t) => t.sip_response_ms !== null || t.tls_handshake_ms !== null);
  const pt = (sel: (t: VoipTest) => number | null) =>
    okOnly.map((t) => [new Date(t.ts).getTime(), sel(t)] as [number, number | null]);
  const lossPt = ordered.map(
    (t) => [new Date(t.ts).getTime(), t.loss_pct] as [number, number | null],
  );
  const selectedServer = servers.find((s) => s.id === selected) ?? null;

  return (
    <div>
      <div className="topbar">
        <h1>VoIP / 3CX</h1>
        {isAdmin && (
          <button className="primary" onClick={() => setShowAdd(true)}>
            3CX-Server hinzufügen
          </button>
        )}
      </div>
      <ErrorBox error={error} />

      <div className="warn-box">
        Die Tests prüfen ausschließlich die <em>Erreichbarkeit</em> (SIP-Ports, SIP-OPTIONS-Ping,
        TLS-Handshake, WebRTC-/HTTPS-Ports, ICMP-Verlust) – es wird <strong>kein Anruf</strong>{" "}
        aufgebaut. SIP-Zugangsdaten für den optionalen Registrierungstest werden nur für den
        einzelnen Lauf verwendet und <strong>nicht gespeichert</strong>.
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Host</th>
                <th className="num">SIP</th>
                <th className="num">SIP-TLS</th>
                <th>Letzter Test</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {servers.map((s) => (
                <tr
                  key={s.id}
                  style={{ cursor: "pointer", background: selected === s.id ? "var(--bg)" : undefined }}
                  onClick={() => setSelected(s.id)}
                >
                  <td>{s.name}</td>
                  <td>{s.host}</td>
                  <td className="num">
                    {s.sip_port}/{s.sip_transport}
                  </td>
                  <td className="num">{s.sip_tls_port || "–"}</td>
                  <td>{s.last_test ? fmtDateTime(s.last_test.ts) : "–"}</td>
                  <td>
                    <Badge
                      ok={s.last_test ? s.last_test.ok : null}
                      label={!s.enabled ? "aus" : undefined}
                    />
                  </td>
                  <td className="num" onClick={(e) => e.stopPropagation()}>
                    {isAdmin && (
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        <button disabled={busy === s.id} onClick={() => setTestFor(s)}>
                          {busy === s.id ? "läuft…" : "Jetzt testen"}
                        </button>
                        <button
                          onClick={() =>
                            api
                              .patch(`/api/voip/servers/${s.id}`, { enabled: !s.enabled })
                              .then(loadServers)
                          }
                        >
                          {s.enabled ? "Deaktivieren" : "Aktivieren"}
                        </button>
                        <button className="danger" onClick={() => remove(s.id)}>
                          Löschen
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {!servers.length && (
                <tr>
                  <td colSpan={7} className="muted">
                    Kein 3CX-Server konfiguriert. Host = FQDN/IP der Anlage, z. B.
                    pbx.kunde.example.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selectedServer && (
        <>
          <div className="topbar">
            <h2>Verlauf: {selectedServer.name}</h2>
            <TimeRangePicker value={win} onChange={setWin} presets={PRESETS} />
          </div>

          {selectedServer.last_test && <LastTestCard test={selectedServer.last_test} />}
          {selectedServer.last_test && <AnalysisPanels test={selectedServer.last_test} />}

          <div className="card" style={{ marginBottom: 16 }}>
            <h2>Antwortzeiten (ms)</h2>
            <TimeSeriesChart
              {...chartWin}
              group="voip"
              yName="ms"
              series={[
                { name: "SIP-Antwort", data: pt((t) => t.sip_response_ms), color: "#38bdf8" },
                { name: "TLS-Handshake", data: pt((t) => t.tls_handshake_ms), color: "#a78bfa" },
                { name: "RTT ø", data: pt((t) => t.rtt_avg_ms), color: "#64748b" },
              ]}
            />
          </div>
          <div className="card" style={{ marginBottom: 16 }}>
            <h2>Paketverlust (%)</h2>
            <TimeSeriesChart
              {...chartWin}
              group="voip"
              yName="%"
              height={220}
              series={[{ name: "Loss", data: lossPt, color: "#ef4444" }]}
            />
          </div>

          <div className="card">
            <h2>Letzte Läufe</h2>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Zeit</th>
                    <th>Quelle</th>
                    <th>Gesamt</th>
                    <th>SIP UDP</th>
                    <th>SIP TCP</th>
                    <th>SIP TLS</th>
                    <th className="num">SIP-Antwort</th>
                    <th className="num">TLS</th>
                    <th className="num">Loss</th>
                    <th>Registrierung</th>
                  </tr>
                </thead>
                <tbody>
                  {history.filter((t) => inWindow(win, t.ts)).slice(0, 200).map((t) => (
                    <tr key={t.id}>
                      <td>{fmtDateTime(t.ts)}</td>
                      <td>{t.source === "scheduled" ? "Zeitplan" : "manuell"}</td>
                      <td>
                        <Badge ok={t.ok} />
                      </td>
                      <td>{triState(t.sip_udp_ok)}</td>
                      <td>{triState(t.sip_tcp_ok)}</td>
                      <td>
                        {triState(t.sip_tls_ok)}
                        {t.sip_tls_ok && t.tls_verified === false ? " (unverif.)" : ""}
                      </td>
                      <td className="num">
                        {fmtNum(t.sip_response_ms, 1, "ms")}
                        {t.sip_response_code ? ` (${t.sip_response_code})` : ""}
                      </td>
                      <td className="num">{fmtNum(t.tls_handshake_ms, 1, "ms")}</td>
                      <td className="num">{fmtNum(t.loss_pct, 0, "%")}</td>
                      <td>
                        {t.registration_ok === null
                          ? "–"
                          : t.registration_detail ?? (t.registration_ok ? "ok" : "Fehler")}
                      </td>
                    </tr>
                  ))}
                  {!history.length && (
                    <tr>
                      <td colSpan={10} className="muted">
                        Noch keine Läufe im gewählten Zeitraum.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <AnalyzeCapturePanel setError={setError} />

      {showAdd && (
        <ServerModal
          onClose={() => setShowAdd(false)}
          onSaved={() => {
            setShowAdd(false);
            loadServers();
          }}
          setError={setError}
        />
      )}
      {testFor && (
        <TestModal
          server={testFor}
          onClose={() => setTestFor(null)}
          onRun={(opts) => runTest(testFor, opts)}
        />
      )}
    </div>
  );
}

function LastTestCard({ test }: { test: VoipTest }) {
  const { isAdmin } = useAuth();
  const webrtc = test.webrtc_results
    .map((w) => `${w.port}: ${w.ok ? "ok" : "zu"}`)
    .join(", ");
  return (
    <div className="grid cols-4" style={{ marginBottom: 16 }}>
      <div className="card stat">
        <div className="label">Gesamturteil</div>
        <div className="value">
          <Badge ok={test.ok} />
        </div>
        <div className="sub">{fmtDateTime(test.ts)}</div>
      </div>
      <div className="card stat">
        <div className="label">SIP-Antwort</div>
        <div className="value">{fmtNum(test.sip_response_ms, 1, "ms")}</div>
        <div className="sub">{test.sip_response_code ? `SIP ${test.sip_response_code}` : "keine Antwort"}</div>
      </div>
      <div className="card stat">
        <div className="label">TLS-Handshake</div>
        <div className="value">{fmtNum(test.tls_handshake_ms, 1, "ms")}</div>
        <div className="sub">
          {test.tls_cert_expiry
            ? `Zert. bis ${fmtDateTime(test.tls_cert_expiry)}`
            : "kein TLS-Port"}
          {test.tls_verified === false ? " · unverifiziert" : ""}
        </div>
      </div>
      <div className="card stat">
        <div className="label">ICMP-Verlust</div>
        <div className="value">{fmtNum(test.loss_pct, 0, "%")}</div>
        <div className="sub">WebRTC {webrtc || "–"}</div>
      </div>
      <div className="card" style={{ gridColumn: "1 / -1" }}>
        <table>
          <tbody>
            <tr>
              <td>Server / 3CX-Version</td>
              <td>{test.server_header ?? "–"}</td>
            </tr>
            <tr>
              <td>Unterstützte Methoden</td>
              <td>{test.supported_methods ?? "–"}</td>
            </tr>
            <tr>
              <td>TLS</td>
              <td>
                {test.tls_version
                  ? `${test.tls_version} · ${test.tls_cipher ?? "?"}`
                  : "–"}
              </td>
            </tr>
            <tr>
              <td>Zertifikat</td>
              <td>
                {test.cert_subject
                  ? `${test.cert_subject}  ←  ${test.cert_issuer ?? "?"}`
                  : "–"}
              </td>
            </tr>
            {test.capture_url && isAdmin && (
              <tr>
                <td>Mitschnitt</td>
                <td>
                  <a href={test.capture_url} className="badge dim">
                    PCAP herunterladen
                  </a>{" "}
                  <span className="muted" style={{ fontSize: 12 }}>
                    (nur Admin – kann Gesprächsdaten enthalten)
                  </span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {test.error && (
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <span className="muted">{test.error}</span>
        </div>
      )}
    </div>
  );
}

function AnalysisPanels({ test }: { test: VoipTest }) {
  const sip = test.sip_analysis ?? {};
  const codecs = test.codec_analysis ?? {};
  const rtp = test.rtp_analysis ?? {};
  const hasSip = (sip.messages_captured ?? 0) > 0;
  const hasCodecs = (codecs.codecs_offered ?? []).length > 0;
  const hasRtp = (rtp.streams_detected ?? 0) > 0;
  if (!hasSip && !hasCodecs && !hasRtp) return null;

  return (
    <>
      {hasSip && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>SIP-Analyse (Mitschnitt)</h2>
          <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
            {Object.entries(sip.by_method ?? {}).map(([m, n]) => (
              <span key={m} className="badge dim">
                {m}: {n}
              </span>
            ))}
            {Object.entries(sip.by_status_class ?? {}).map(([c, n]) => (
              <span key={c} className={`badge ${c === "2xx" ? "ok" : "err"}`}>
                {c}: {n}
              </span>
            ))}
            <span className="badge dim">Retransmissions: {sip.retransmissions ?? 0}</span>
          </div>
          {(sip.errors ?? []).length > 0 && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th className="num">Code</th>
                    <th>Grund</th>
                    <th>Von → Nach</th>
                  </tr>
                </thead>
                <tbody>
                  {(sip.errors ?? []).map((e, i) => (
                    <tr key={i}>
                      <td className="num">{e.code}</td>
                      <td>{e.reason}</td>
                      <td className="muted">
                        {e.src} → {e.dst}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {hasCodecs && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Codecs (SDP)</h2>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
            {(codecs.codecs_offered ?? []).map((c) => (
              <span key={c} className="badge dim">
                {c}
              </span>
            ))}
            {codecs.secure && <span className="badge ok">SRTP</span>}
          </div>
          <p className="muted" style={{ marginTop: 8 }}>
            Primär: {codecs.primary_codec ?? "–"}
            {codecs.estimated_bandwidth_kbit
              ? ` · geschätzt ~${codecs.estimated_bandwidth_kbit} kbit/s`
              : ""}
            {codecs.ptime_ms ? ` · ptime ${codecs.ptime_ms} ms` : ""}
          </p>
        </div>
      )}

      {hasRtp && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>RTP-Streams</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>SSRC</th>
                  <th>Codec</th>
                  <th>Von → Nach</th>
                  <th className="num">Pakete</th>
                  <th className="num">Verlust</th>
                  <th className="num">Jitter ø</th>
                </tr>
              </thead>
              <tbody>
                {(rtp.streams ?? []).map((s, i) => (
                  <tr key={i}>
                    <td>{s.ssrc}</td>
                    <td>{s.codec}</td>
                    <td className="muted">
                      {s.src} → {s.dst}
                    </td>
                    <td className="num">{s.packets}</td>
                    <td className="num">
                      {s.lost} ({fmtNum(s.lost_pct, 1, "%")})
                    </td>
                    <td className="num">{fmtNum(s.mean_jitter_ms, 1, "ms")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

function AnalyzeCapturePanel({ setError }: { setError: (s: string | null) => void }) {
  const { isAdmin } = useAuth();
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<VoipAnalysis | null>(null);

  if (!isAdmin) return null;

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.post<VoipAnalysis>("/api/voip/analyze", { capture_path: path.trim() }));
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2>Vorhandene Capture analysieren</h2>
      <p className="muted">
        Analysiert eine bereits vorhandene PCAP im Capture-Verzeichnis auf SIP-Dialoge,
        SDP-/Codec-Angebote und RTP-Streams – z. B. einen echten Gesprächsmitschnitt.
      </p>
      <div className="row">
        <div className="field" style={{ flex: 3 }}>
          <label>Pfad (unterhalb des Capture-Verzeichnisses)</label>
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/var/lib/netprobe/captures/capture_12_...pcapng"
          />
        </div>
        <button className="primary" onClick={run} disabled={busy || !path.trim()}>
          {busy ? "Analysiere…" : "Analysieren"}
        </button>
      </div>
      {result && (
        <div style={{ marginTop: 12 }}>
          {result.error && <div className="error-box">{result.error}</div>}
          <AnalysisPanels
            test={
              {
                sip_analysis: result.sip,
                codec_analysis: result.codecs,
                rtp_analysis: result.rtp,
              } as VoipTest
            }
          />
        </div>
      )}
    </div>
  );
}

function ServerModal({
  onClose,
  onSaved,
  setError,
}: {
  onClose: () => void;
  onSaved: () => void;
  setError: (s: string) => void;
}) {
  const [form, setForm] = useState({
    name: "",
    host: "",
    sip_port: 5060,
    sip_tls_port: 5061,
    webrtc_ports: "443,5001",
    sip_transport: "udp",
    check_registration: false,
    interval_s: 300,
  });

  async function save() {
    try {
      await api.post("/api/voip/servers", form);
      onSaved();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <Modal title="3CX-Server hinzufügen" onClose={onClose}>
      <div className="field">
        <label>Name</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </div>
      <div className="field">
        <label>Host / IP (FQDN der 3CX-Anlage)</label>
        <input
          value={form.host}
          onChange={(e) => setForm({ ...form, host: e.target.value })}
          placeholder="pbx.kunde.example"
        />
      </div>
      <div className="row">
        <div className="field" style={{ flex: 1 }}>
          <label>SIP-Port</label>
          <input
            type="number"
            value={form.sip_port}
            onChange={(e) => setForm({ ...form, sip_port: +e.target.value })}
          />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>SIP-TLS-Port (0 = aus)</label>
          <input
            type="number"
            value={form.sip_tls_port}
            onChange={(e) => setForm({ ...form, sip_tls_port: +e.target.value })}
          />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>SIP-Transport</label>
          <select
            value={form.sip_transport}
            onChange={(e) => setForm({ ...form, sip_transport: e.target.value })}
          >
            <option value="udp">UDP</option>
            <option value="tcp">TCP</option>
          </select>
        </div>
      </div>
      <div className="row">
        <div className="field" style={{ flex: 2 }}>
          <label>WebRTC-/HTTPS-Ports (TCP, Kommaliste)</label>
          <input
            value={form.webrtc_ports}
            onChange={(e) => setForm({ ...form, webrtc_ports: e.target.value })}
          />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Intervall (s)</label>
          <input
            type="number"
            value={form.interval_s}
            onChange={(e) => setForm({ ...form, interval_s: +e.target.value })}
          />
        </div>
      </div>
      <div className="field">
        <label>
          <input
            type="checkbox"
            checked={form.check_registration}
            onChange={(e) => setForm({ ...form, check_registration: e.target.checked })}
          />{" "}
          Registrierungstest anbieten (SIP-Zugangsdaten pro Lauf, nicht gespeichert)
        </label>
      </div>
      <button className="primary" onClick={save} disabled={!form.name || !form.host}>
        Speichern
      </button>
    </Modal>
  );
}

function TestModal({
  server,
  onClose,
  onRun,
}: {
  server: VoipServer;
  onClose: () => void;
  onRun: (opts: TestOptions) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [iface, setIface] = useState("");
  const [duration, setDuration] = useState(10);
  const [interfaces, setInterfaces] = useState<CaptureInterface[]>([]);

  useEffect(() => {
    api.get<CaptureInterface[]>("/api/voip/interfaces").then(setInterfaces).catch(() => {});
  }, []);

  function start() {
    const opts: TestOptions = {};
    if (username && password) {
      opts.username = username;
      opts.password = password;
    }
    if (iface) {
      opts.capture_interface = iface;
      opts.capture_duration_s = duration;
    }
    onRun(opts);
  }

  return (
    <Modal title={`Test: ${server.name}`} onClose={onClose}>
      <div className="field">
        <label>PCAP-Mitschnitt (optional)</label>
        <select value={iface} onChange={(e) => setIface(e.target.value)}>
          <option value="">– kein Mitschnitt –</option>
          {interfaces.map((i) => (
            <option key={i.name} value={i.name}>
              {i.name}
              {i.description ? ` (${i.description})` : ""}
            </option>
          ))}
        </select>
      </div>
      {iface && (
        <div className="field">
          <label>Mitschnitt-Dauer (s)</label>
          <input
            type="number"
            value={duration}
            onChange={(e) => setDuration(+e.target.value)}
          />
          <span className="muted" style={{ fontSize: 12 }}>
            Der Mitschnitt erfasst SIP (5060/5061/5090) auf dem gewählten Interface. RTP/Codec-
            Daten erscheinen nur, wenn währenddessen echter Medienverkehr über die Box läuft.
          </span>
        </div>
      )}
      {server.check_registration && (
        <>
          <p className="muted">
            SIP-Zugangsdaten sind optional. Mit Angabe wird zusätzlich ein einzelner{" "}
            <code>REGISTER</code> ausgeführt. Die Daten werden nicht gespeichert.
          </p>
          <div className="field">
            <label>SIP-Benutzer / Nebenstelle</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label>SIP-Passwort</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="off"
            />
          </div>
        </>
      )}
      <div className="row">
        <button className="primary" onClick={start}>
          Test starten
        </button>
      </div>
    </Modal>
  );
}
