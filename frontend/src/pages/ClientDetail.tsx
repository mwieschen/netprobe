import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  WachtlBandwidthRun,
  WachtlCheck,
  WachtlDiagnoseStarted,
  WachtlEvent,
  WachtlFinding,
  WachtlIncident,
  WachtlPathView,
  WachtlProbe,
  WachtlReport,
  WachtlRtpRun,
  WachtlSeriesPoint,
} from "../api";
import { useAuth } from "../auth";
import TimeSeriesChart from "../components/Chart";
import { ErrorBox } from "../components/common";
import { FindingList, IncidentTable, ReportTable } from "../components/WachtlEvents";
import { BandwidthPanel, EventList, PathPanel, RtpPanel, RunNowButton } from "../components/WachtlChecks";
import TimeRangePicker, {
  chartWindowProps, inWindow, loadQuery, TimeWindow, windowQuery,
} from "../components/TimeRange";
import { fmtDateTime, fmtNum } from "../util";

const PRESETS = ["15m", "1h", "3h", "6h", "12h", "24h", "7d", "30d", "90d", "365d", "all"];

function fmtUptime(s: number): string {
  if (!s) return "–";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  return d ? `${d} d ${h} h` : `${h} h ${Math.floor((s % 3600) / 60)} min`;
}

export default function ClientDetailPage() {
  const { isAdmin } = useAuth();
  const id = Number(useParams().id);
  const [probe, setProbe] = useState<WachtlProbe | null>(null);
  const [checks, setChecks] = useState<WachtlCheck[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [points, setPoints] = useState<WachtlSeriesPoint[]>([]);
  const [win, setWin] = useState<TimeWindow>({ range: "24h" });
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [reports, setReports] = useState<WachtlReport[]>([]);
  const [findings, setFindings] = useState<WachtlFinding[]>([]);
  const [incidents, setIncidents] = useState<WachtlIncident[]>([]);
  const [events, setEvents] = useState<WachtlEvent[]>([]);
  const [pathView, setPathView] = useState<WachtlPathView | null>(null);
  const [bwRuns, setBwRuns] = useState<WachtlBandwidthRun[]>([]);
  const [rtpRuns, setRtpRuns] = useState<WachtlRtpRun[]>([]);

  function loadEvents() {
    api.get<WachtlReport[]>(`/api/clients/reports?probe_id=${id}&range=90d`).then(setReports).catch(() => setReports([]));
    api.get<WachtlFinding[]>(`/api/clients/findings?probe_id=${id}&days=30`).then(setFindings).catch(() => setFindings([]));
    api.get<WachtlEvent[]>(`/api/clients/events?probe_id=${id}&range=30d`).then(setEvents).catch(() => setEvents([]));
  }
  useEffect(loadEvents, [id]);
  useEffect(() => {
    if (!probe) return;
    api.get<WachtlIncident[]>(`/api/clients/incidents?range=7d&site_id=${probe.site_id}`)
      .then((all) => setIncidents(all.filter((i) => i.probe_ids.includes(id))))
      .catch(() => setIncidents([]));
  }, [probe, id]);

  async function diagnose() {
    try {
      const res = await api.post<WachtlDiagnoseStarted>(`/api/clients/probes/${id}/diagnose`);
      setInfo(`Diagnose gestartet (${res.targets.join(", ")}). Ergebnis in etwa einer Minute unter Netzwerk-Diagnose.`);
      setError(null);
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

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

  const check = checks.find((c) => c.id === selected) ?? null;
  useEffect(() => {
    if (!check) return;
    const base = `/api/clients/probes/${id}/checks/${check.id}`;
    const q = windowQuery(win);
    if (check.type === "ping") {
      api.get<WachtlSeriesPoint[]>(`${base}/series?${loadQuery(win)}&points=4500`).then(setPoints).catch(() => setPoints([]));
    } else if (check.type === "path") {
      api.get<WachtlPathView>(`${base}/path?${q}`).then(setPathView).catch(() => setPathView(null));
    } else if (check.type === "rtp") {
      api.get<WachtlRtpRun[]>(`${base}/rtp?${loadQuery(win)}`).then(setRtpRuns).catch(() => setRtpRuns([]));
    } else {
      api.get<WachtlBandwidthRun[]>(`${base}/bandwidth?${loadQuery(win)}`).then(setBwRuns).catch(() => setBwRuns([]));
    }
  }, [id, check, win]);

  // Kennzahlen über den gewählten Zeitraum aus den (gebündelten) Punkten – ohne Ladepuffer
  const visible = (ts: string) => inWindow(win, ts);
  const chartWin = chartWindowProps(win, setWin, selected);
  const shown = points.filter((p) => visible(p.ts));
  const count = shown.reduce((a, p) => a + p.count, 0);
  const loss = shown.reduce((a, p) => a + p.loss, 0);
  const rttMins = shown.map((p) => p.rtt_min).filter((v): v is number => v !== null);
  const rttMaxs = shown.map((p) => p.rtt_max).filter((v): v is number => v !== null);
  const okWeighted = shown.filter((p) => p.rtt_avg !== null && p.count > p.loss);
  const okN = okWeighted.reduce((a, p) => a + (p.count - p.loss), 0);
  const rttAvg = okN ? okWeighted.reduce((a, p) => a + (p.rtt_avg as number) * (p.count - p.loss), 0) / okN : null;

  const t = (p: WachtlSeriesPoint) => new Date(p.ts).getTime();

  return (
    <div>
      <div className="topbar">
        <h1>
          <Link to="/clients">Clients</Link> / {probe?.hostname ?? "…"}
        </h1>
        {isAdmin && probe && <button className="primary" onClick={diagnose}>Diagnose starten</button>}
      </div>
      <ErrorBox error={error} />
      {info && <div className="info-box" onClick={() => setInfo(null)}>{info}</div>}

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
          {check && <span className="muted" style={{ fontSize: 13 }}>{check.summary}</span>}
          {isAdmin && probe && check && check.type !== "ping" && (
            <RunNowButton probeId={probe.id} check={check} onInfo={setInfo} setError={setError} />
          )}
        </div>
        <TimeRangePicker value={win} onChange={setWin} presets={PRESETS} />
      </div>
      {!checks.length && <p className="muted">Dieser Probe sind keine Checks zugewiesen.</p>}

      {check?.type === "path" && <PathPanel view={pathView} />}
      {(check?.type === "iperf" || check?.type === "speedtest") && <BandwidthPanel runs={bwRuns} chart={chartWin} visible={visible} />}
      {check?.type === "rtp" && <RtpPanel runs={rtpRuns} chart={chartWin} visible={visible} />}

      {check?.type === "ping" && (
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
              {...chartWin}
              markers={reports.map((r) => ({ ts: new Date(r.occurred_at).getTime(), label: `Meldung: ${r.category_label}` }))}
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

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Störungen (7 Tage)</h2>
        <IncidentTable incidents={incidents} showSite={false} />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Störungsmeldungen</h2>
        <ReportTable reports={reports} isAdmin={isAdmin} onChanged={loadEvents} setError={setError} onInfo={setInfo}
          showProbe={false} />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Client-Hinweise (30 Tage)</h2>
        <FindingList findings={findings} showProbe={false} />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Ereignisse (30 Tage)</h2>
        <EventList events={events} showProbe={false} />
      </div>
    </div>
  );
}
