import { useEffect, useRef, useState } from "react";
import { api, ApiError, PingJob, PingSample } from "../api";
import { useAuth } from "../auth";
import TimeSeriesChart from "../components/Chart";
import { ErrorBox, Modal, RangeSelect } from "../components/common";
import { fmtNum } from "../util";

const RANGES = [
  { value: "5m", label: "5 Minuten" },
  { value: "1h", label: "1 Stunde" },
  { value: "6h", label: "6 Stunden" },
  { value: "24h", label: "24 Stunden" },
  { value: "7d", label: "7 Tage" },
];

interface Stats { count: number; loss: number; loss_pct: number; rtt_min: number | null; rtt_avg: number | null; rtt_max: number | null; }

export default function PingPage() {
  const { isAdmin } = useAuth();
  const [jobs, setJobs] = useState<PingJob[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [samples, setSamples] = useState<PingSample[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [range, setRange] = useState("1h");
  const [live, setLive] = useState<Record<number, PingSample[]>>({});
  const [showModal, setShowModal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  function loadJobs() {
    api.get<PingJob[]>("/api/ping/jobs").then((j) => {
      setJobs(j);
      if (selected === null && j.length) setSelected(j[0].id);
    });
  }
  useEffect(loadJobs, []);

  function loadSamples() {
    if (selected === null) return;
    api.get<PingSample[]>(`/api/ping/jobs/${selected}/samples?range=${range}`).then((s) => setSamples(s.reverse()));
    api.get<Stats>(`/api/ping/jobs/${selected}/stats?range=${range}`).then(setStats);
  }
  useEffect(loadSamples, [selected, range]);

  // Live-Updates via Server-Sent Events
  useEffect(() => {
    const es = new EventSource("/api/ping/stream", { withCredentials: true });
    esRef.current = es;
    es.addEventListener("ping", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setLive((prev) => {
        const arr = [...(prev[d.job_id] ?? []), { ts: d.ts, rtt_ms: d.rtt_ms, ok: d.ok }];
        return { ...prev, [d.job_id]: arr.slice(-300) };
      });
    });
    return () => es.close();
  }, []);

  async function control(id: number, action: string) {
    try {
      if (action === "delete") {
        if (!confirm("Job löschen?")) return;
        await api.del(`/api/ping/jobs/${id}`);
        if (selected === id) setSelected(null);
      } else {
        await api.post(`/api/ping/jobs/${id}/${action}`);
      }
      loadJobs();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  // Kombiniere historische Samples mit Live-Daten des ausgewählten Jobs
  const liveForSel = selected !== null ? live[selected] ?? [] : [];
  const combined = [...samples, ...liveForSel];
  const rttPt = combined.map((s) => [new Date(s.ts).getTime(), s.ok ? s.rtt_ms : null] as [number, number | null]);
  const lossPt = combined
    .filter((s) => !s.ok)
    .map((s) => [new Date(s.ts).getTime(), 0] as [number, number | null]);

  return (
    <div>
      <div className="topbar">
        <h1>Dauerping</h1>
        {isAdmin && <button className="primary" onClick={() => setShowModal(true)}>Job hinzufügen</button>}
      </div>
      <ErrorBox error={error} />

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Name</th><th>Host</th><th className="num">Intervall</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} style={{ cursor: "pointer", background: selected === j.id ? "var(--bg)" : undefined }}
                    onClick={() => setSelected(j.id)}>
                  <td>{j.name}</td>
                  <td>{j.host}</td>
                  <td className="num">{j.interval_s}s</td>
                  <td>
                    <span className={`badge ${j.status === "running" ? "ok" : j.status === "paused" ? "warn" : "dim"}`}>
                      {j.status}
                    </span>
                  </td>
                  <td className="num" onClick={(e) => e.stopPropagation()}>
                    {isAdmin && (
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        {j.status !== "running" && <button onClick={() => control(j.id, "start")}>Start</button>}
                        {j.status === "running" && <button onClick={() => control(j.id, "pause")}>Pause</button>}
                        {j.status === "running" && <button onClick={() => control(j.id, "stop")}>Stop</button>}
                        <button className="danger" onClick={() => control(j.id, "delete")}>Löschen</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {!jobs.length && <tr><td colSpan={5} className="muted">Keine Jobs.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {selected !== null && (
        <>
          <div className="topbar">
            <h2>{jobs.find((j) => j.id === selected)?.name}</h2>
            <RangeSelect value={range} onChange={setRange} options={RANGES} />
          </div>
          {stats && (
            <div className="grid cols-4" style={{ marginBottom: 16 }}>
              <div className="card stat"><div className="label">RTT min</div><div className="value">{fmtNum(stats.rtt_min, 1, "ms")}</div></div>
              <div className="card stat"><div className="label">RTT ø</div><div className="value">{fmtNum(stats.rtt_avg, 1, "ms")}</div></div>
              <div className="card stat"><div className="label">RTT max</div><div className="value">{fmtNum(stats.rtt_max, 1, "ms")}</div></div>
              <div className="card stat"><div className="label">Paketverlust</div><div className="value">{fmtNum(stats.loss_pct, 1, "%")}</div><div className="sub">{stats.loss}/{stats.count}</div></div>
            </div>
          )}
          <div className="card">
            <h2>RTT (ms) – Live</h2>
            <TimeSeriesChart
              yName="ms"
              series={[
                { name: "RTT", data: rttPt, color: "#38bdf8" },
                { name: "Timeout", data: lossPt, color: "#ef4444", type: "scatter" },
              ]}
            />
          </div>
        </>
      )}

      {showModal && <JobModal onClose={() => { setShowModal(false); loadJobs(); }} setError={setError} />}
    </div>
  );
}

function JobModal({ onClose, setError }: { onClose: () => void; setError: (s: string) => void }) {
  const [form, setForm] = useState({ name: "", host: "", interval_s: 1 });
  async function save() {
    try {
      await api.post("/api/ping/jobs", form);
      onClose();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  return (
    <Modal title="Dauerping-Job hinzufügen" onClose={onClose}>
      <div className="field"><label>Name</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
      <div className="field"><label>Host / IP</label>
        <input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="8.8.8.8" /></div>
      <div className="field"><label>Intervall (Sekunden, min. 1)</label>
        <input type="number" min={1} value={form.interval_s} onChange={(e) => setForm({ ...form, interval_s: +e.target.value })} /></div>
      <button className="primary" onClick={save}>Speichern</button>
    </Modal>
  );
}
