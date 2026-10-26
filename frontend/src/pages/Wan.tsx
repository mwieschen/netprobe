import { useEffect, useState } from "react";
import { api, ApiError, WanResult, WanTarget } from "../api";
import { useAuth } from "../auth";
import TimeSeriesChart from "../components/Chart";
import TimeRangePicker, { TimeWindow, windowBounds, windowQuery, zoomWindow } from "../components/TimeRange";
import { Badge, ErrorBox, Modal } from "../components/common";

const PRESETS = ["24h", "7d", "30d", "90d", "365d", "all"];

export default function WanPage() {
  const { isAdmin } = useAuth();
  const [targets, setTargets] = useState<WanTarget[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [results, setResults] = useState<WanResult[]>([]);
  const [win, setWin] = useState<TimeWindow>({ range: "24h" });
  const [showModal, setShowModal] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loadTargets() {
    api.get<WanTarget[]>("/api/wan/targets").then((t) => {
      setTargets(t);
      if (selected === null && t.length) setSelected(t[0].id);
    });
  }
  useEffect(loadTargets, []);

  useEffect(() => {
    if (selected === null) return;
    api.get<WanResult[]>(`/api/wan/targets/${selected}/results?${windowQuery(win)}`).then(setResults);
  }, [selected, win]);

  async function remove(id: number) {
    if (!confirm("Ziel wirklich löschen?")) return;
    await api.del(`/api/wan/targets/${id}`);
    if (selected === id) setSelected(null);
    loadTargets();
  }

  async function runNow(id: number) {
    try {
      await api.post(`/api/wan/targets/${id}/run`);
      if (selected === id) {
        api.get<WanResult[]>(`/api/wan/targets/${id}/results?${windowQuery(win)}`).then(setResults);
      }
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  const bounds = windowBounds(win);
  const ordered = [...results].reverse();
  const pt = (sel: (r: WanResult) => number | null) =>
    ordered.filter((r) => r.ok).map((r) => [new Date(r.ts).getTime(), sel(r)] as [number, number | null]);
  const lossPt = ordered.map((r) => [new Date(r.ts).getTime(), r.loss_pct] as [number, number | null]);

  return (
    <div>
      <div className="topbar">
        <h1>WAN-Tests</h1>
        {isAdmin && <button className="primary" onClick={() => setShowModal(true)}>Ziel hinzufügen</button>}
      </div>
      <ErrorBox error={error} />

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th><th>Host</th><th className="num">Intervall</th>
                <th className="num">Pakete</th><th>Status</th><th></th>
              </tr>
            </thead>
            <tbody>
              {targets.map((t) => (
                <tr key={t.id} style={{ cursor: "pointer", background: selected === t.id ? "var(--bg)" : undefined }}
                    onClick={() => setSelected(t.id)}>
                  <td>{t.name}</td>
                  <td>{t.host}</td>
                  <td className="num">{t.interval_s}s</td>
                  <td className="num">{t.count}</td>
                  <td><Badge ok={t.enabled} label={t.enabled ? "aktiv" : "aus"} /></td>
                  <td className="num" onClick={(e) => e.stopPropagation()}>
                    {isAdmin && (
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        <button onClick={() => runNow(t.id)}>Jetzt testen</button>
                        <button onClick={() => api.patch(`/api/wan/targets/${t.id}`, { enabled: !t.enabled }).then(loadTargets)}>
                          {t.enabled ? "Deaktivieren" : "Aktivieren"}
                        </button>
                        <button className="danger" onClick={() => remove(t.id)}>Löschen</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {!targets.length && <tr><td colSpan={6} className="muted">Keine Ziele. Beispiele: 1.1.1.1, 8.8.8.8, Provider-Gateway.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {selected !== null && (
        <>
          <div className="topbar">
            <h2>Zeitreihen: {targets.find((t) => t.id === selected)?.name}</h2>
            <TimeRangePicker value={win} onChange={setWin} presets={PRESETS} />
          </div>
          <div className="card" style={{ marginBottom: 16 }}>
            <h2>Latenz (ms) & Jitter</h2>
            <TimeSeriesChart
              xMin={bounds.min}
              xMax={bounds.max}
              zoomKey={`${selected}|${windowQuery(win)}`}
              onZoomRange={(s, e) => setWin(zoomWindow(s, e))}
              yName="ms"
              series={[
                { name: "RTT ø", data: pt((r) => r.rtt_avg), color: "#38bdf8" },
                { name: "RTT max", data: pt((r) => r.rtt_max), color: "#64748b" },
                { name: "Jitter", data: pt((r) => r.jitter_ms), color: "#a78bfa" },
              ]}
            />
          </div>
          <div className="card">
            <h2>Paketverlust (%)</h2>
            <TimeSeriesChart
              xMin={bounds.min}
              xMax={bounds.max}
              zoomKey={`${selected}|${windowQuery(win)}`}
              onZoomRange={(s, e) => setWin(zoomWindow(s, e))}
              yName="%"
              series={[{ name: "Loss", data: lossPt, color: "#ef4444", type: "line" }]}
              height={220}
            />
          </div>
        </>
      )}

      {showModal && <TargetModal onClose={() => { setShowModal(false); loadTargets(); }} setError={setError} />}
    </div>
  );
}

function TargetModal({ onClose, setError }: { onClose: () => void; setError: (s: string) => void }) {
  const [form, setForm] = useState({ name: "", host: "", interval_s: 60, count: 10, timeout_ms: 1000 });
  async function save() {
    try {
      await api.post("/api/wan/targets", form);
      onClose();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  return (
    <Modal title="WAN-Ziel hinzufügen" onClose={onClose}>
      <div className="field"><label>Name</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
      <div className="field"><label>Host / IP</label>
        <input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="1.1.1.1" /></div>
      <div className="row">
        <div className="field" style={{ flex: 1 }}><label>Intervall (s)</label>
          <input type="number" value={form.interval_s} onChange={(e) => setForm({ ...form, interval_s: +e.target.value })} /></div>
        <div className="field" style={{ flex: 1 }}><label>Pakete</label>
          <input type="number" value={form.count} onChange={(e) => setForm({ ...form, count: +e.target.value })} /></div>
        <div className="field" style={{ flex: 1 }}><label>Timeout (ms)</label>
          <input type="number" value={form.timeout_ms} onChange={(e) => setForm({ ...form, timeout_ms: +e.target.value })} /></div>
      </div>
      <button className="primary" onClick={save}>Speichern</button>
    </Modal>
  );
}
