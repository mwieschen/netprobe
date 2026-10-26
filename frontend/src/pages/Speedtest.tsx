import { useEffect, useState } from "react";
import { api, ApiError, Speedtest } from "../api";
import { useAuth } from "../auth";
import TimeSeriesChart from "../components/Chart";
import TimeRangePicker, { TimeWindow, windowBounds, windowQuery, zoomWindow } from "../components/TimeRange";
import { ErrorBox } from "../components/common";
import { fmtDateTime, fmtNum } from "../util";

const PRESETS = ["24h", "7d", "30d", "90d", "365d", "all"];

export default function SpeedtestPage() {
  const { isAdmin } = useAuth();
  const [results, setResults] = useState<Speedtest[]>([]);
  const [win, setWin] = useState<TimeWindow>({ range: "24h" });
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.get<Speedtest[]>(`/api/speedtest/results?${windowQuery(win)}`).then(setResults).catch(() => {});
  }

  useEffect(load, [win]);

  async function runTest() {
    setRunning(true);
    setError(null);
    try {
      await api.post<Speedtest>("/api/speedtest/run", {});
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setRunning(false);
    }
  }

  const bounds = windowBounds(win);
  const ok = [...results].reverse().filter((r) => r.ok);
  const toPoint = (sel: (r: Speedtest) => number | null) =>
    ok.map((r) => [new Date(r.ts).getTime(), sel(r)] as [number, number | null]);

  return (
    <div>
      <div className="topbar">
        <h1>Speedtest</h1>
        <div className="row">
          <TimeRangePicker value={win} onChange={setWin} presets={PRESETS} />
          {isAdmin && (
            <button className="primary" onClick={runTest} disabled={running}>
              {running ? "Test läuft…" : "Test starten"}
            </button>
          )}
        </div>
      </div>
      <ErrorBox error={error} />

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Durchsatz (Mbps)</h2>
        <TimeSeriesChart
          xMin={bounds.min}
          xMax={bounds.max}
          zoomKey={windowQuery(win)}
          onZoomRange={(s, e) => setWin(zoomWindow(s, e))}
          yName="Mbps"
          series={[
            { name: "Download", data: toPoint((r) => r.download_mbps), color: "#38bdf8" },
            { name: "Upload", data: toPoint((r) => r.upload_mbps), color: "#22c55e" },
          ]}
        />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Latenz & Jitter (ms)</h2>
        <TimeSeriesChart
          xMin={bounds.min}
          xMax={bounds.max}
          zoomKey={windowQuery(win)}
          onZoomRange={(s, e) => setWin(zoomWindow(s, e))}
          yName="ms"
          series={[
            { name: "Ping", data: toPoint((r) => r.ping_ms), color: "#f59e0b" },
            { name: "Jitter", data: toPoint((r) => r.jitter_ms), color: "#a78bfa" },
          ]}
        />
      </div>

      <div className="card">
        <h2>Verlauf</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Zeit</th>
                <th>Status</th>
                <th className="num">Download</th>
                <th className="num">Upload</th>
                <th className="num">Ping</th>
                <th className="num">Jitter</th>
                <th className="num">Loss</th>
                <th>Server</th>
                <th>Quelle</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.id}>
                  <td>{fmtDateTime(r.ts)}</td>
                  <td>
                    {r.ok ? <span className="badge ok">OK</span> : <span className="badge err">{r.error ?? "Fehler"}</span>}
                  </td>
                  <td className="num">{fmtNum(r.download_mbps, 1)}</td>
                  <td className="num">{fmtNum(r.upload_mbps, 1)}</td>
                  <td className="num">{fmtNum(r.ping_ms, 1)}</td>
                  <td className="num">{fmtNum(r.jitter_ms, 1)}</td>
                  <td className="num">{fmtNum(r.packet_loss, 0, "%")}</td>
                  <td>{r.server_name ?? "–"}</td>
                  <td>{r.source === "scheduled" ? "geplant" : "manuell"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
