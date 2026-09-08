import { useEffect, useState } from "react";
import { api, ApiError, Capture } from "../api";
import { useAuth } from "../auth";
import { Modal, ErrorBox } from "../components/common";
import { fmtDateTime, fmtBytes } from "../util";

interface Interface { index: number; name: string; description: string | null; }

interface Analysis {
  packet_count: number | null;
  protocol_hierarchy: { level: number; protocol: string; frames: number; bytes: number }[];
  top_endpoints: { address: string; packets: number; bytes: number }[];
  tcp_conversations: { endpoint_a: string; endpoint_b: string; bytes_total: number }[];
  udp_conversations: { endpoint_a: string; endpoint_b: string; bytes_total: number }[];
  dns_queries: { name: string; count: number }[];
  tls_sni: { server_name: string; count: number }[];
  tcp_retransmissions: number;
  tcp_resets: number;
  expert_infos: { severity: string; protocol: string; summary: string; count: number }[];
}

export default function CapturesPage() {
  const { isAdmin } = useAuth();
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [detail, setDetail] = useState<Capture | null>(null);
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
                      {c.status === "done" && <button onClick={() => setDetail(c)}>Analyse</button>}
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
      {detail && <AnalysisModal capture={detail} onClose={() => setDetail(null)} />}
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

function AnalysisModal({ capture, onClose }: { capture: Capture; onClose: () => void }) {
  const [data, setData] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [filterResult, setFilterResult] = useState<any>(null);

  useEffect(() => {
    api.get<Analysis>(`/api/captures/${capture.id}/analysis`).then(setData).catch((e: ApiError) => setError(e.detail));
  }, [capture.id]);

  async function applyFilter() {
    try {
      const r = await api.post(`/api/captures/${capture.id}/filter`, { filter, offset: 0, limit: 100 });
      setFilterResult(r);
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <Modal title={`Analyse – Capture #${capture.id}`} onClose={onClose}>
      <ErrorBox error={error} />
      <div className="row" style={{ marginBottom: 12 }}>
        <a className="badge dim" href={`/api/captures/${capture.id}/analysis.json`}>JSON-Export</a>
        <a className="badge dim" href={`/api/captures/${capture.id}/analysis.csv`}>CSV-Export</a>
      </div>
      {!data && !error && <p className="muted">Analyse läuft…</p>}
      {data && (
        <div style={{ fontSize: 13 }}>
          <div className="grid cols-3" style={{ marginBottom: 12 }}>
            <div className="card stat"><div className="label">Pakete</div><div className="value">{data.packet_count ?? "–"}</div></div>
            <div className="card stat"><div className="label">Retransmissions</div><div className="value">{data.tcp_retransmissions}</div></div>
            <div className="card stat"><div className="label">TCP Resets</div><div className="value">{data.tcp_resets}</div></div>
          </div>

          <Section title="Protokollhierarchie">
            {data.protocol_hierarchy.slice(0, 15).map((p, i) => (
              <div key={i} style={{ paddingLeft: p.level * 14 }}>{p.protocol} — {p.frames} Frames / {fmtBytes(p.bytes)}</div>
            ))}
          </Section>

          <Section title="Top IP-Endpunkte">
            {data.top_endpoints.slice(0, 10).map((e, i) => <div key={i}>{e.address} — {fmtBytes(e.bytes)} ({e.packets} Pakete)</div>)}
          </Section>

          <Section title="TCP-Conversations">
            {data.tcp_conversations.slice(0, 10).map((c, i) => <div key={i}>{c.endpoint_a} ↔ {c.endpoint_b} — {fmtBytes(c.bytes_total)}</div>)}
          </Section>

          <Section title="UDP-Conversations">
            {data.udp_conversations.slice(0, 10).map((c, i) => <div key={i}>{c.endpoint_a} ↔ {c.endpoint_b} — {fmtBytes(c.bytes_total)}</div>)}
          </Section>

          <Section title="DNS-Queries">
            {data.dns_queries.slice(0, 15).map((q, i) => <div key={i}>{q.name} ×{q.count}</div>)}
            {!data.dns_queries.length && <span className="muted">keine</span>}
          </Section>

          <Section title="TLS SNI">
            {data.tls_sni.slice(0, 15).map((s, i) => <div key={i}>{s.server_name} ×{s.count}</div>)}
            {!data.tls_sni.length && <span className="muted">keine</span>}
          </Section>

          <Section title="Expert Infos">
            {data.expert_infos.slice(0, 15).map((e, i) => (
              <div key={i}><span className={`badge ${e.severity === "Errors" ? "err" : e.severity === "Warnings" ? "warn" : "dim"}`}>{e.severity}</span> {e.protocol}: {e.summary} ×{e.count}</div>
            ))}
            {!data.expert_infos.length && <span className="muted">keine</span>}
          </Section>

          <Section title="Display-Filter (max. 1000 Pakete)">
            <div className="row">
              <input style={{ flex: 1 }} value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="z. B. tcp.port == 443" />
              <button onClick={applyFilter}>Anwenden</button>
            </div>
            {filterResult && (
              <div className="table-scroll" style={{ marginTop: 8 }}>
                <div className="muted">{filterResult.total} Treffer (zeige {filterResult.packets.length})</div>
                <table>
                  <thead><tr><th>#</th><th>Quelle</th><th>Ziel</th><th>Protokoll</th><th>Info</th></tr></thead>
                  <tbody>
                    {filterResult.packets.map((p: any, i: number) => (
                      <tr key={i}><td>{p.number}</td><td>{p.src}</td><td>{p.dst}</td><td>{p.protocol}</td><td>{p.info}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </div>
      )}
    </Modal>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <h2 style={{ fontSize: 14, marginBottom: 6 }}>{title}</h2>
      {children}
    </div>
  );
}
