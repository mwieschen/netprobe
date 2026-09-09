import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, Capture } from "../api";
import { useAuth } from "../auth";
import { Modal, ErrorBox } from "../components/common";
import { fmtDateTime, fmtBytes } from "../util";

interface Interface { index: number; name: string; description: string | null; }

export default function CapturesPage() {
  const { isAdmin } = useAuth();
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.get<Capture[]>("/api/captures").then(setCaptures);
  }
  useEffect(load, []);

  async function remove(id: number) {
    if (!confirm("Capture inklusive Datei löschen?")) return;
    await api.del(`/api/captures/${id}`);
    load();
  }

  return (
    <div>
      <div className="topbar">
        <h1>Packet Captures</h1>
        {isAdmin && <button className="primary" onClick={() => setShowModal(true)}>Capture starten</button>}
      </div>

      <div className="warn-box">
        <strong>Sicherheitshinweis:</strong> Packet Captures können <em>vertrauliche Daten</em> enthalten
        (z. B. Zugangsdaten, interne Hostnamen, unverschlüsselten Datenverkehr). Erstellen und teilen Sie
        Aufnahmen nur mit ausdrücklicher Berechtigung. Dateien werden außerhalb des Webroots gespeichert
        und sind ausschließlich angemeldeten Benutzern zugänglich. Löschen Sie nicht mehr benötigte Captures.
      </div>

      <ErrorBox error={error} />

      <div className="card">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Zeit</th><th>Interface</th><th>Status</th><th className="num">Dauer</th>
                <th className="num">Pakete</th><th className="num">Größe</th><th>Filter</th><th></th>
              </tr>
            </thead>
            <tbody>
              {captures.map((c) => (
                <tr key={c.id}>
                  <td>{fmtDateTime(c.ts)}</td>
                  <td>{c.iface}</td>
                  <td>
                    <span className={`badge ${c.status === "done" ? "ok" : c.status === "error" ? "err" : "warn"}`}>
                      {c.status === "error" ? c.error ?? "Fehler" : c.status}
                    </span>
                  </td>
                  <td className="num">{c.duration_s}s</td>
                  <td className="num">{c.packet_count ?? "–"}</td>
                  <td className="num">{fmtBytes(c.size_bytes)}</td>
                  <td className="muted">{c.bpf ?? "–"}</td>
                  <td className="num">
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      {c.status === "done" && <Link className="badge dim" to={`/captures/${c.id}`}>Analyse</Link>}
                      {c.status === "done" && <a className="badge dim" href={`/api/captures/${c.id}/download`}>PCAPNG</a>}
                      {isAdmin && <button className="danger" onClick={() => remove(c.id)}>Löschen</button>}
                    </div>
                  </td>
                </tr>
              ))}
              {!captures.length && <tr><td colSpan={8} className="muted">Keine Captures.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && <CaptureModal onClose={() => { setShowModal(false); load(); }} setError={setError} />}
    </div>
  );
}

function CaptureModal({ onClose, setError }: { onClose: () => void; setError: (s: string) => void }) {
  const [ifaces, setIfaces] = useState<Interface[]>([]);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ iface: "", duration_s: 60, max_filesize_mb: 100, max_packets: 0, ringbuffer: 0, bpf: "" });

  useEffect(() => {
    api.get<Interface[]>("/api/captures/interfaces").then((i) => {
      setIfaces(i);
      if (i.length) setForm((f) => ({ ...f, iface: i[0].name }));
    }).catch((e: ApiError) => setError(e.detail));
  }, []);

  async function start() {
    setBusy(true);
    try {
      await api.post("/api/captures", {
        iface: form.iface,
        duration_s: form.duration_s,
        max_filesize_mb: form.max_filesize_mb,
        max_packets: form.max_packets || null,
        ringbuffer: form.ringbuffer || null,
        bpf: form.bpf || null,
      });
      onClose();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Capture starten" onClose={onClose}>
      <div className="field"><label>Interface</label>
        <select value={form.iface} onChange={(e) => setForm({ ...form, iface: e.target.value })}>
          {ifaces.map((i) => <option key={i.name} value={i.name}>{i.name}{i.description ? ` (${i.description})` : ""}</option>)}
        </select>
      </div>
      <div className="row">
        <div className="field" style={{ flex: 1 }}><label>Dauer (s, max 900)</label>
          <input type="number" max={900} value={form.duration_s} onChange={(e) => setForm({ ...form, duration_s: +e.target.value })} /></div>
        <div className="field" style={{ flex: 1 }}><label>Max. Größe (MB, max 500)</label>
          <input type="number" max={500} value={form.max_filesize_mb} onChange={(e) => setForm({ ...form, max_filesize_mb: +e.target.value })} /></div>
      </div>
      <div className="row">
        <div className="field" style={{ flex: 1 }}><label>Max. Pakete (0 = ohne)</label>
          <input type="number" value={form.max_packets} onChange={(e) => setForm({ ...form, max_packets: +e.target.value })} /></div>
        <div className="field" style={{ flex: 1 }}><label>Ringbuffer-Dateien (0 = ohne)</label>
          <input type="number" value={form.ringbuffer} onChange={(e) => setForm({ ...form, ringbuffer: +e.target.value })} /></div>
      </div>
      <div className="field"><label>BPF-Capture-Filter (optional)</label>
        <input value={form.bpf} onChange={(e) => setForm({ ...form, bpf: e.target.value })} placeholder="tcp port 443" /></div>
      <p className="muted">Der Capture läuft synchron; das Fenster schließt nach Abschluss.</p>
      <button className="primary" onClick={start} disabled={busy || !form.iface}>
        {busy ? "Capture läuft…" : "Starten"}
      </button>
    </Modal>
  );
}
