import { useEffect, useState } from "react";
import { api, ApiError, Iperf3Result, Iperf3ServerStatus } from "../api";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/common";
import { fmtDateTime, fmtNum } from "../util";

export default function Iperf3Page() {
  const { isAdmin } = useAuth();
  const [results, setResults] = useState<Iperf3Result[]>([]);
  const [server, setServer] = useState<Iperf3ServerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    host: "",
    port: 5201,
    duration_s: 10,
    parallel: 1,
    protocol: "tcp" as "tcp" | "udp",
    reverse: false,
  });

  function loadResults() {
    api.get<Iperf3Result[]>("/api/iperf3/results?limit=50").then(setResults).catch(() => undefined);
  }
  function loadServer() {
    api.get<Iperf3ServerStatus>("/api/iperf3/server/status").then(setServer).catch(() => undefined);
  }
  useEffect(() => {
    loadResults();
    loadServer();
  }, []);

  async function runTest() {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/iperf3/client", {
        host: form.host,
        port: form.port,
        duration_s: form.duration_s,
        parallel: form.parallel,
        udp: form.protocol === "udp",
        reverse: form.reverse,
      });
      loadResults();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  async function toggleServer(enabled: boolean) {
    setError(null);
    try {
      const s = await api.post<Iperf3ServerStatus>("/api/iperf3/server", {
        enabled,
        port: server?.port ?? 5201,
      });
      setServer(s);
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  const last = results[0];

  return (
    <div>
      <div className="topbar">
        <h1>iperf3</h1>
      </div>
      <ErrorBox error={error} />

      {isAdmin && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <h2 style={{ marginBottom: 4 }}>iperf3-Server</h2>
              <span className="muted">
                {server
                  ? server.running
                    ? `läuft auf Port ${server.port}`
                    : `aus (Port ${server.port})`
                  : "…"}
              </span>
            </div>
            <div className="row">
              <input
                type="number"
                style={{ width: 90 }}
                value={server?.port ?? 5201}
                onChange={(e) => setServer((s) => ({ running: s?.running ?? false, port: +e.target.value }))}
              />
              {server?.running ? (
                <button className="danger" onClick={() => toggleServer(false)}>Stoppen</button>
              ) : (
                <button className="primary" onClick={() => toggleServer(true)}>Starten</button>
              )}
            </div>
          </div>
          <p className="muted" style={{ marginTop: 8, marginBottom: 0 }}>
            Der Server wird als Subprozess des netprobe-Dienstes ohne Root betrieben und nach einem
            Neustart automatisch wiederhergestellt. Firewall/Port ggf. freigeben.
          </p>
        </div>
      )}

      {isAdmin && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Client-Test</h2>
          <div className="row">
            <div className="field" style={{ flex: 2 }}>
              <label>Ziel (iperf3-Server)</label>
              <input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="10.0.0.2" />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Port</label>
              <input type="number" value={form.port} onChange={(e) => setForm({ ...form, port: +e.target.value })} />
            </div>
          </div>
          <div className="row">
            <div className="field" style={{ flex: 1 }}>
              <label>Dauer (s)</label>
              <input type="number" min={1} max={60} value={form.duration_s} onChange={(e) => setForm({ ...form, duration_s: +e.target.value })} />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Parallel-Streams</label>
              <input type="number" min={1} max={32} value={form.parallel} onChange={(e) => setForm({ ...form, parallel: +e.target.value })} />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Protokoll</label>
              <select value={form.protocol} onChange={(e) => setForm({ ...form, protocol: e.target.value as "tcp" | "udp" })}>
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
              </select>
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Richtung</label>
              <select value={form.reverse ? "down" : "up"} onChange={(e) => setForm({ ...form, reverse: e.target.value === "down" })}>
                <option value="up">Upload (→ Server)</option>
                <option value="down">Download (← Server)</option>
              </select>
            </div>
          </div>
          <button className="primary" onClick={runTest} disabled={busy || !form.host}>
            {busy ? "Test läuft…" : "Test starten"}
          </button>
        </div>
      )}

      {last && (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <div className="card stat"><div className="label">Senden</div><div className="value">{fmtNum(last.sent_mbps, 1, "Mbit/s")}</div></div>
          <div className="card stat"><div className="label">Empfangen</div><div className="value">{fmtNum(last.recv_mbps, 1, "Mbit/s")}</div></div>
          <div className="card stat"><div className="label">Jitter</div><div className="value">{fmtNum(last.jitter_ms, 2, "ms")}</div></div>
          <div className="card stat">
            <div className="label">{last.protocol === "udp" ? "Verlust" : "Retransmits"}</div>
            <div className="value">{last.protocol === "udp" ? fmtNum(last.lost_percent, 1, "%") : (last.retransmits ?? "–")}</div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Zeit</th><th>Ziel</th><th>Proto</th><th className="num">Dauer</th><th className="num">P</th>
                <th>Richtung</th><th className="num">Senden</th><th className="num">Empfangen</th>
                <th className="num">Jitter</th><th className="num">Verlust</th>
                <th className="num">Retr.</th><th className="num">OoO</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.id}>
                  <td>{fmtDateTime(r.ts)}</td>
                  <td>{r.host}:{r.port}</td>
                  <td>{r.protocol.toUpperCase()}</td>
                  <td className="num">{r.duration_s}s</td>
                  <td className="num">{r.parallel}</td>
                  <td>{r.reverse ? "↓ Download" : "↑ Upload"}</td>
                  <td className="num">{fmtNum(r.sent_mbps, 1)}</td>
                  <td className="num">{fmtNum(r.recv_mbps, 1)}</td>
                  <td className="num">{fmtNum(r.jitter_ms, 2)}</td>
                  <td className="num">{fmtNum(r.lost_percent, 1)}</td>
                  <td className="num">{r.retransmits ?? "–"}</td>
                  <td className="num">{r.out_of_order ?? "–"}</td>
                  <td>
                    <span className={`badge ${r.ok ? "ok" : "err"}`}>{r.ok ? "OK" : "Fehler"}</span>
                    {!r.ok && r.error && <div className="muted" style={{ fontSize: 11 }}>{r.error}</div>}
                  </td>
                </tr>
              ))}
              {!results.length && <tr><td colSpan={13} className="muted">Noch keine iperf3-Tests.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
