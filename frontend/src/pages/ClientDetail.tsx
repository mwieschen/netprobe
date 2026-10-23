import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, WachtlCheck, WachtlProbe, WachtlSeriesPoint } from "../api";
import TimeSeriesChart from "../components/Chart";
import { ErrorBox } from "../components/common";
import TimeRangePicker, { TimeWindow, windowBounds, windowQuery, zoomWindow } from "../components/TimeRange";
import { fmtDateTime, fmtNum } from "../util";

const PRESETS = ["1h", "6h", "24h", "7d", "30d", "90d", "365d", "all"];

function fmtUptime(s: number): string {
  if (!s) return "–";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  return d ? `${d} d ${h} h` : `${h} h ${Math.floor((s % 3600) / 60)} min`;
}

export default function ClientDetailPage() {
  const id = Number(useParams().id);
  const [probe, setProbe] = useState<WachtlProbe | null>(null);
  const [checks, setChecks] = useState<WachtlCheck[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [points, setPoints] = useState<WachtlSeriesPoint[]>([]);
  const [win, setWin] = useState<TimeWindow>({ range: "24h" });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<WachtlProbe>(`/api/clients/probes/${id}`).then((p) => {
      setProbe(p);
      api.get<WachtlCheck[]>("/api/clients/checks").then((all) => {
        const mine = all.filter((c) => c.site_id === null || c.site_id === p.site_id);
        setChecks(mine);
        if (mine.length) setSelected((s) => s ?? mine[0].id);
      });
    }).catch(() => setError("Probe nicht gefunden"));
  }, [id]);

  useEffect(() => {
    if (selected === null) return;
    api.get<WachtlSeriesPoint[]>(`/api/clients/probes/${id}/checks/${selected}/series?${windowQuery(win)}`)
      .then(setPoints).catch(() => setPoints([]));
  }, [id, selected, win]);

  // Kennzahlen über den gewählten Zeitraum aus den (gebündelten) Punkten
  const count = points.reduce((a, p) => a + p.count, 0);
  const loss = points.reduce((a, p) => a + p.loss, 0);
  const rttMins = points.map((p) => p.rtt_min).filter((v): v is number => v !== null);
  const rttMaxs = points.map((p) => p.rtt_max).filter((v): v is number => v !== null);
  const okWeighted = points.filter((p) => p.rtt_avg !== null && p.count > p.loss);
  const okN = okWeighted.reduce((a, p) => a + (p.count - p.loss), 0);
  const rttAvg = okN ? okWeighted.reduce((a, p) => a + (p.rtt_avg as number) * (p.count - p.loss), 0) / okN : null;

  const t = (p: WachtlSeriesPoint) => new Date(p.ts).getTime();
  const bounds = windowBounds(win);

  return (
    <div>
      <div className="topbar">
        <h1>
          <Link to="/clients">Clients</Link> / {probe?.hostname ?? "…"}
        </h1>
      </div>
      <ErrorBox error={error} />

      {probe && (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <div className="card stat">
            <div className="label">Status</div>
            <div className="value">
              <span className={`badge ${probe.status !== "active" ? "dim" : probe.online ? "ok" : "err"}`}>
                {probe.deregistered_at ? "abgemeldet" : probe.status !== "active" ? "deaktiviert" : probe.online ? "online" : "offline"}
              </span>
            </div>
            <div className="sub">{probe.last_seen ? `zuletzt ${fmtDateTime(probe.last_seen)}` : "noch nie gesehen"}</div>
          </div>
          <div className="card stat">
            <div className="label">Standort</div>
            <div className="value" style={{ fontSize: 18 }}>{probe.site_name}</div>
            <div className="sub">{probe.addresses.join(", ") || probe.last_ip || "–"}</div>
          </div>
          <div className="card stat">
            <div className="label">System</div>
            <div className="value" style={{ fontSize: 18 }}>Wachtl {probe.version ?? "–"}</div>
            <div className="sub">{probe.os ?? "–"}</div>
          </div>
          <div className="card stat">
            <div className="label">Laufzeit / Puffer</div>
            <div className="value" style={{ fontSize: 18 }}>{fmtUptime(probe.uptime_s)}</div>
            <div className="sub">{probe.buffer_items} Werte noch nicht übertragen</div>
          </div>
        </div>
      )}

      <div className="topbar">
        <div className="row">
          <select value={selected ?? ""} onChange={(e) => setSelected(+e.target.value)}>
            {checks.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.target})</option>)}
          </select>
        </div>
        <TimeRangePicker value={win} onChange={setWin} presets={PRESETS} />
      </div>
      {!checks.length && <p className="muted">Dieser Probe sind keine Checks zugewiesen.</p>}

      {selected !== null && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <div className="card stat"><div className="label">RTT min</div><div className="value">{fmtNum(rttMins.length ? Math.min(...rttMins) : null, 1, "ms")}</div></div>
            <div className="card stat"><div className="label">RTT ø</div><div className="value">{fmtNum(rttAvg, 1, "ms")}</div></div>
            <div className="card stat"><div className="label">RTT max</div><div className="value">{fmtNum(rttMaxs.length ? Math.max(...rttMaxs) : null, 1, "ms")}</div></div>
            <div className="card stat">
              <div className="label">Paketverlust</div>
              <div className="value">{fmtNum(count ? (loss * 100) / count : null, 2, "%")}</div>
              <div className="sub">{loss}/{count}</div>
            </div>
          </div>
          <div className="card">
            <h2>RTT und Verlust <span className="muted" style={{ fontSize: 13 }}>· ø / max je Intervall</span></h2>
            {!points.length && <p className="muted">Keine Messwerte in diesem Zeitraum.</p>}
            <TimeSeriesChart
              yName="ms"
              y2Name="Verlust %"
              xMin={bounds.min}
              xMax={bounds.max}
              zoomKey={`${selected}|${windowQuery(win)}`}
              onZoomRange={(s, e) => setWin(zoomWindow(s, e))}
              series={[
                { name: "RTT ø", data: points.map((p) => [t(p), p.rtt_avg]), color: "#38bdf8" },
                { name: "RTT max", data: points.map((p) => [t(p), p.rtt_max]), color: "#64748b" },
                { name: "Jitter", data: points.map((p) => [t(p), p.jitter_ms]), color: "#a78bfa" },
                {
                  name: "Verlust %",
                  data: points.filter((p) => p.loss > 0).map((p) => [t(p), Math.round((p.loss / p.count) * 1000) / 10]),
                  color: "#ef4444",
                  type: "bar",
                  yAxisIndex: 1,
                },
              ]}
            />
          </div>
        </>
      )}
    </div>
  );
}
