import { useEffect, useState } from "react";
import { api, DashboardHealth, Speedtest } from "../api";
import { StatCard, Badge } from "../components/common";
import { fmtDateTime, fmtNum, fmtBytes } from "../util";

interface Summary {
  latest_speedtest: Speedtest | null;
  wan_current: {
    target_id: number;
    name: string;
    host: string;
    enabled: boolean;
    ok: boolean | null;
    rtt_avg: number | null;
    jitter_ms: number | null;
    loss_pct: number | null;
    ts: string | null;
  }[];
  voip_current: {
    server_id: number;
    name: string;
    host: string;
    enabled: boolean;
    ok: boolean | null;
    sip_response_ms: number | null;
    loss_pct: number | null;
    ts: string | null;
  }[];
  active_ping_jobs: number;
  disruptions_24h: number;
}

export default function Dashboard() {
  const [health, setHealth] = useState<DashboardHealth | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);

  useEffect(() => {
    const load = () => {
      api.get<DashboardHealth>("/api/dashboard/health").then(setHealth).catch(() => {});
      api.get<Summary>("/api/dashboard/summary").then(setSummary).catch(() => {});
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const st = summary?.latest_speedtest;

  return (
    <div>
      <h1>Dashboard</h1>
      <p className="muted">Gesundheitsstatus und aktuelle Messwerte (Zeitzone Europe/Berlin).</p>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <StatCard
          label="Systemstatus"
          value={
            <Badge
              ok={health ? health.status === "ok" : null}
              label={health?.status === "ok" ? "OK" : health?.status ?? "…"}
            />
          }
          sub={`${summary?.active_ping_jobs ?? 0} aktive Dauerpings`}
        />
        <StatCard
          label="Download"
          value={st ? fmtNum(st.download_mbps, 1) : "–"}
          sub={st ? `↑ ${fmtNum(st.upload_mbps, 1)} Mbps` : "kein Speedtest"}
        />
        <StatCard
          label="Ping / Jitter"
          value={st ? fmtNum(st.ping_ms, 1, "ms") : "–"}
          sub={st ? `Jitter ${fmtNum(st.jitter_ms, 1, "ms")}` : ""}
        />
        <StatCard
          label="Störungen (24h)"
          value={summary?.disruptions_24h ?? 0}
          sub="WAN-Ausfälle/Timeouts"
        />
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>Aktuelle WAN-Werte</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Ziel</th>
                  <th>Status</th>
                  <th className="num">RTT ø</th>
                  <th className="num">Jitter</th>
                  <th className="num">Loss</th>
                </tr>
              </thead>
              <tbody>
                {summary?.wan_current.length ? (
                  summary.wan_current.map((w) => (
                    <tr key={w.target_id}>
                      <td>
                        {w.name}
                        <div className="muted" style={{ fontSize: 12 }}>{w.host}</div>
                      </td>
                      <td><Badge ok={w.ok} /></td>
                      <td className="num">{fmtNum(w.rtt_avg, 1, "ms")}</td>
                      <td className="num">{fmtNum(w.jitter_ms, 1, "ms")}</td>
                      <td className="num">{fmtNum(w.loss_pct, 0, "%")}</td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan={5} className="muted">Keine WAN-Ziele konfiguriert.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {summary?.voip_current.length ? (
          <div className="card">
            <h2>VoIP / 3CX</h2>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Anlage</th>
                    <th>Status</th>
                    <th className="num">SIP-Antwort</th>
                    <th className="num">Loss</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.voip_current.map((v) => (
                    <tr key={v.server_id}>
                      <td>
                        {v.name}
                        <div className="muted" style={{ fontSize: 12 }}>{v.host}</div>
                      </td>
                      <td><Badge ok={v.enabled ? v.ok : null} label={!v.enabled ? "aus" : undefined} /></td>
                      <td className="num">{fmtNum(v.sip_response_ms, 1, "ms")}</td>
                      <td className="num">{fmtNum(v.loss_pct, 0, "%")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        <div className="card">
          <h2>System & Speicher</h2>
          {health && (
            <table>
              <tbody>
                <tr><td>Speicher frei</td><td className="num">{fmtNum(health.disk_free_mb / 1024, 1, "GB")}</td></tr>
                <tr><td>Speicher belegt</td><td className="num">{fmtNum(health.disk_used_pct, 1, "%")}</td></tr>
                <tr><td>Datenbankgröße</td><td className="num">{fmtBytes(health.db_size_mb * 1024 * 1024)}</td></tr>
                <tr><td>Captures gesamt</td><td className="num">{fmtBytes(health.captures_size_mb * 1024 * 1024)}</td></tr>
              </tbody>
            </table>
          )}
          <h2 style={{ marginTop: 16 }}>Werkzeuge</h2>
          <div className="row">
            {health &&
              Object.entries(health.tools).map(([tool, ok]) => (
                <span key={tool} className={`badge ${ok ? "ok" : "warn"}`}>
                  {tool} {ok ? "✓" : "fehlt"}
                </span>
              ))}
          </div>
        </div>
      </div>

      {st && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Letzter Speedtest</h2>
          <div className="muted">
            {fmtDateTime(st.ts)} · Server: {st.server_name ?? "auto"} ·{" "}
            {st.ok ? <Badge ok={true} /> : <Badge ok={false} label={st.error ?? "Fehler"} />}
          </div>
        </div>
      )}
    </div>
  );
}
