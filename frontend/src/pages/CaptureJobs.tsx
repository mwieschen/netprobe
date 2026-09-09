import { Fragment, useEffect, useState } from "react";
import { api, ApiError, CaptureJob, CaptureJobFile } from "../api";
import { useAuth } from "../auth";
import { ErrorBox, Modal } from "../components/common";
import { fmtBytes, fmtDateTime } from "../util";

interface Interface { index: number; name: string; description: string | null; }

const STATUS_CLASS: Record<string, string> = {
  running: "ok",
  stopped: "dim",
  error: "err",
};

export default function CaptureJobsPage() {
  const { isAdmin } = useAuth();
  const [jobs, setJobs] = useState<CaptureJob[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.get<CaptureJob[]>("/api/capture-jobs").then(setJobs).catch((e: ApiError) => setError(e.detail));
  }
  useEffect(load, []);
  useEffect(() => {
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  async function control(id: number, action: "start" | "stop") {
    try {
      await api.post(`/api/capture-jobs/${id}/${action}`);
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function remove(id: number) {
    if (!confirm("Job und alle rotierten PCAP-Dateien löschen?")) return;
    try {
      await api.del(`/api/capture-jobs/${id}`);
      if (expanded === id) setExpanded(null);
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <div>
      <div className="topbar">
        <h1>Langzeit-Captures</h1>
        {isAdmin && <button className="primary" onClick={() => setShowModal(true)}>Job anlegen</button>}
      </div>

      <div className="warn-box">
        <strong>Hinweis:</strong> Langzeit-Captures schreiben dauerhaft mit. <code>dumpcap</code> rotiert
        die Dateien im Ringpuffer (max. Größe × Anzahl). Planen Sie den Plattenbedarf ein und stoppen Sie
        nicht mehr benötigte Jobs. Aufnahmen können vertrauliche Daten enthalten.
      </div>

      <ErrorBox error={error} />

      <div className="card">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th><th>Interface</th><th>Filter</th><th>Ringpuffer</th>
                <th>Status</th><th>Angelegt</th><th></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <Fragment key={j.id}>
                  <tr style={{ cursor: "pointer" }} onClick={() => setExpanded(expanded === j.id ? null : j.id)}>
                    <td>{j.name || `#${j.id}`}</td>
                    <td>{j.iface}</td>
                    <td className="muted">{j.bpf ?? "–"}</td>
                    <td className="num">{j.max_file_size_mb} MB × {j.max_files}</td>
                    <td>
                      <span className={`badge ${STATUS_CLASS[j.status] ?? "dim"}`}>{j.status}</span>
                      {j.status === "error" && j.error && <div className="muted" style={{ fontSize: 11 }}>{j.error}</div>}
                    </td>
                    <td>{fmtDateTime(j.created_at)}</td>
                    <td className="num" onClick={(e) => e.stopPropagation()}>
                      {isAdmin && (
                        <div className="row" style={{ justifyContent: "flex-end" }}>
                          {j.status !== "running" && <button onClick={() => control(j.id, "start")}>Start</button>}
                          {j.status === "running" && <button onClick={() => control(j.id, "stop")}>Stop</button>}
                          <button className="danger" onClick={() => remove(j.id)}>Löschen</button>
                        </div>
                      )}
                    </td>
                  </tr>
                  {expanded === j.id && (
                    <tr>
                      <td colSpan={7} style={{ background: "var(--bg)" }}>
                        <JobFiles jobId={j.id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {!jobs.length && <tr><td colSpan={7} className="muted">Keine Langzeit-Capture-Jobs.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && <JobModal onClose={() => { setShowModal(false); load(); }} setError={setError} />}
    </div>
  );
}

function JobFiles({ jobId }: { jobId: number }) {
  const [files, setFiles] = useState<CaptureJobFile[] | null>(null);

  useEffect(() => {
    api.get<CaptureJobFile[]>(`/api/capture-jobs/${jobId}/captures`).then(setFiles).catch(() => setFiles([]));
  }, [jobId]);

  if (!files) return <span className="muted">Lädt…</span>;
  if (!files.length) return <span className="muted">Noch keine Dateien geschrieben.</span>;

  const total = files.reduce((s, f) => s + f.size_bytes, 0);
  return (
    <div>
      <div className="muted" style={{ marginBottom: 6 }}>{files.length} Dateien · {fmtBytes(total)} gesamt</div>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Datei</th><th className="num">Größe</th><th>Geändert</th><th></th></tr></thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.name}>
                <td>{f.name}</td>
                <td className="num">{fmtBytes(f.size_bytes)}</td>
                <td>{fmtDateTime(f.mtime)}</td>
                <td className="num">
                  <a className="badge dim" href={`/api/capture-jobs/${jobId}/captures/${encodeURIComponent(f.name)}/download`}>Download</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JobModal({ onClose, setError }: { onClose: () => void; setError: (s: string) => void }) {
  const [ifaces, setIfaces] = useState<Interface[]>([]);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ name: "", iface: "", bpf: "", max_file_size_mb: 100, max_files: 50 });

  useEffect(() => {
    api.get<Interface[]>("/api/captures/interfaces")
      .then((i) => {
        setIfaces(i);
        if (i.length) setForm((f) => ({ ...f, iface: i[0].name }));
      })
      .catch((e: ApiError) => setError(e.detail));
  }, []);

  async function save() {
    setBusy(true);
    try {
      await api.post("/api/capture-jobs", {
        name: form.name || undefined,
        iface: form.iface,
        bpf: form.bpf || null,
        max_file_size_mb: form.max_file_size_mb,
        max_files: form.max_files,
      });
      onClose();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  const totalMb = form.max_file_size_mb * form.max_files;

  return (
    <Modal title="Langzeit-Capture anlegen" onClose={onClose}>
      <div className="field"><label>Name (optional)</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="z. B. Uplink 24 h" /></div>
      <div className="field"><label>Interface</label>
        <select value={form.iface} onChange={(e) => setForm({ ...form, iface: e.target.value })}>
          {ifaces.map((i) => <option key={i.name} value={i.name}>{i.name}{i.description ? ` (${i.description})` : ""}</option>)}
        </select>
      </div>
      <div className="field"><label>BPF-Capture-Filter (optional)</label>
        <input value={form.bpf} onChange={(e) => setForm({ ...form, bpf: e.target.value })} placeholder="not port 22" /></div>
      <div className="row">
        <div className="field" style={{ flex: 1 }}><label>MB pro Datei</label>
          <input type="number" min={1} value={form.max_file_size_mb} onChange={(e) => setForm({ ...form, max_file_size_mb: +e.target.value })} /></div>
        <div className="field" style={{ flex: 1 }}><label>Anzahl Dateien (Ring)</label>
          <input type="number" min={1} value={form.max_files} onChange={(e) => setForm({ ...form, max_files: +e.target.value })} /></div>
      </div>
      <p className="muted">Maximaler Plattenbedarf: ca. {fmtBytes(totalMb * 1024 * 1024)}. Der Job startet sofort.</p>
      <button className="primary" onClick={save} disabled={busy || !form.iface}>
        {busy ? "Starte…" : "Anlegen & starten"}
      </button>
    </Modal>
  );
}
