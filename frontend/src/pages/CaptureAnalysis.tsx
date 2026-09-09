import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  Capture,
  CaptureAnalysis,
  CaptureStats,
  Interpretation,
  IpConversation,
  KeyMetric,
  PacketList,
  Report,
  ReportPreview,
} from "../api";
import { useAuth } from "../auth";
import TimeSeriesChart from "../components/Chart";
import { ErrorBox, Modal } from "../components/common";
import { fmtBytes, fmtNum } from "../util";

const REPORT_SECTIONS: { key: string; label: string }[] = [
  { key: "summary", label: "Zusammenfassung (Key Metrics)" },
  { key: "ping", label: "Ping & WAN-Qualität" },
  { key: "speedtest", label: "Speedtest" },
  { key: "iperf3", label: "iperf3 (TCP/UDP)" },
  { key: "mtu", label: "MTU-Test" },
  { key: "passive", label: "Passive PCAP-Analyse" },
];

function KeyMetricTiles({ metrics }: { metrics: KeyMetric[] }) {
  return (
    <div className="grid cols-4" style={{ marginBottom: 12 }}>
      {metrics.map((m, i) => (
        <div
          key={i}
          className="card stat"
          style={
            m.level === "warn"
              ? { borderColor: "var(--warn)" }
              : m.level === "na"
                ? { opacity: 0.55 }
                : undefined
          }
        >
          <div className="label">{m.label}</div>
          <div className="value">{m.value}</div>
          <div className="sub">{m.sub}</div>
        </div>
      ))}
    </div>
  );
}

function Interpretations({ items }: { items: Interpretation[] }) {
  if (!items.length) return null;
  return (
    <div style={{ marginBottom: 12 }}>
      {items.map((it, i) => (
        <div key={i} className={it.level === "warn" ? "error-box" : "warn-box"} style={{ marginBottom: 6 }}>
          {it.level === "warn" ? "⚠ " : it.level === "ok" ? "✓ " : "ℹ "}
          {it.text}
        </div>
      ))}
    </div>
  );
}

type Tab = "packets" | "conversations" | "stats" | "overview";
const TAB_LABEL: Record<Tab, string> = {
  packets: "Pakete",
  conversations: "Conversations",
  stats: "Statistiken",
  overview: "Übersicht",
};

function fmtEpoch(epoch: string): string {
  const t = parseFloat(epoch);
  if (!isFinite(t)) return "–";
  return new Date(t * 1000).toLocaleTimeString("de-DE", { timeZone: "Europe/Berlin" });
}

export default function CaptureAnalysisPage() {
  const { id } = useParams();
  const { isAdmin } = useAuth();
  const capId = Number(id);
  const [capture, setCapture] = useState<Capture | null>(null);
  const [tab, setTab] = useState<Tab>("packets");
  const [error, setError] = useState<string | null>(null);
  const [showReport, setShowReport] = useState(false);
  const [report, setReport] = useState<Report | null>(null);

  useEffect(() => {
    api
      .get<Capture[]>("/api/captures")
      .then((cs) => setCapture(cs.find((c) => c.id === capId) ?? null))
      .catch((e: ApiError) => setError(e.detail));
  }, [capId]);

  if (!Number.isFinite(capId)) return <p className="muted">Ungültige Capture-ID.</p>;

  return (
    <div>
      <div className="topbar">
        <h1>Analyse – Capture #{capId}</h1>
        <Link to="/captures" className="badge dim">← Zurück zur Liste</Link>
      </div>
      {capture && (
        <p className="muted">
          {capture.iface} · {fmtBytes(capture.size_bytes)} · {capture.packet_count ?? "?"} Pakete ·{" "}
          <span className={`badge ${capture.status === "done" ? "ok" : "warn"}`}>{capture.status}</span>
        </p>
      )}

      <ErrorBox error={error} />

      <div className="row" style={{ justifyContent: "space-between", marginBottom: 16 }}>
        <div className="row">
          {(Object.keys(TAB_LABEL) as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "primary" : ""} onClick={() => setTab(t)}>
              {TAB_LABEL[t]}
            </button>
          ))}
        </div>
        <div className="row">
          {isAdmin && <button className="primary" onClick={() => setShowReport(true)}>Report erstellen</button>}
          <a className="badge dim" href={`/api/captures/${capId}/download`}>PCAPNG</a>
          <a className="badge dim" href={`/api/captures/${capId}/analysis.csv`}>CSV</a>
          <a className="badge dim" href={`/api/captures/${capId}/analysis.json`}>JSON</a>
        </div>
      </div>

      {report && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
            <div>
              <strong>Report #{report.id} erstellt.</strong>{" "}
              {report.error && <span className="muted">({report.error})</span>}
            </div>
            <div className="row">
              <a className="badge dim" href={report.html_url} target="_blank" rel="noreferrer">HTML öffnen</a>
              {report.pdf_url && <a className="badge dim" href={report.pdf_url}>PDF herunterladen</a>}
              <button onClick={() => setReport(null)}>Ausblenden</button>
            </div>
          </div>
          {report.key_metrics.length > 0 && <KeyMetricTiles metrics={report.key_metrics} />}
        </div>
      )}

      {tab === "packets" && <PacketsTab capId={capId} setError={setError} />}
      {tab === "conversations" && <ConversationsTab capId={capId} setError={setError} />}
      {tab === "stats" && <StatsTab capId={capId} setError={setError} />}
      {tab === "overview" && <OverviewTab capId={capId} setError={setError} />}

      {showReport && (
        <ReportModal
          capId={capId}
          onClose={() => setShowReport(false)}
          onCreated={(r) => {
            setReport(r);
            setShowReport(false);
          }}
          setError={setError}
        />
      )}
    </div>
  );
}

function PacketsTab({ capId, setError }: { capId: number; setError: (s: string | null) => void }) {
  const [filter, setFilter] = useState("");
  const [applied, setApplied] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<PacketList | null>(null);
  const [busy, setBusy] = useState(false);
  const limit = 100;

  useEffect(() => {
    setBusy(true);
    setError(null);
    api
      .get<PacketList>(
        `/api/captures/${capId}/packets?filter=${encodeURIComponent(applied)}&offset=${offset}&limit=${limit}`,
      )
      .then(setData)
      .catch((e: ApiError) => {
        setError(e.detail);
        setData(null);
      })
      .finally(() => setBusy(false));
  }, [capId, applied, offset, setError]);

  return (
    <div className="card">
      <form
        className="row"
        style={{ marginBottom: 12 }}
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
          setApplied(filter.trim());
        }}
      >
        <input
          style={{ flex: 1, minWidth: 240 }}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Display-Filter (tshark-Syntax), z. B. tcp.port == 443 && ip.addr == 10.0.0.1"
        />
        <button className="primary" type="submit" disabled={busy}>Filtern</button>
        {applied && (
          <button
            type="button"
            onClick={() => {
              setFilter("");
              setApplied("");
              setOffset(0);
            }}
          >
            Zurücksetzen
          </button>
        )}
      </form>

      {data && (
        <>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <span className="muted">
              {data.total} Treffer{data.total >= data.limit ? " (serverseitig begrenzt)" : ""}
              {data.total > limit
                ? ` · Zeige ${offset + 1}–${Math.min(offset + limit, data.total)}`
                : ""}
            </span>
            <div className="row">
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹ Zurück</button>
              <button disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>Weiter ›</button>
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>Zeit</th>
                  <th>Quelle</th>
                  <th>Ziel</th>
                  <th>Protokoll</th>
                  <th className="num">Länge</th>
                  <th>Info</th>
                </tr>
              </thead>
              <tbody>
                {data.packets.map((p, i) => (
                  <tr key={i}>
                    <td className="num">{p.number}</td>
                    <td>{fmtEpoch(p.time_epoch)}</td>
                    <td>{p.src || "–"}</td>
                    <td>{p.dst || "–"}</td>
                    <td>{p.protocol}</td>
                    <td className="num">{p.length}</td>
                    <td
                      className="muted"
                      style={{ maxWidth: 460, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={p.info}
                    >
                      {p.info}
                    </td>
                  </tr>
                ))}
                {!data.packets.length && (
                  <tr>
                    <td colSpan={7} className="muted">Keine Pakete für diesen Filter.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function ConversationsTab({ capId, setError }: { capId: number; setError: (s: string | null) => void }) {
  const [rows, setRows] = useState<IpConversation[] | null>(null);

  useEffect(() => {
    api
      .get<{ conversations: IpConversation[] }>(`/api/captures/${capId}/conversations`)
      .then((r) => setRows(r.conversations))
      .catch((e: ApiError) => setError(e.detail));
  }, [capId, setError]);

  return (
    <div className="card">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Endpunkt A</th>
              <th>Endpunkt B</th>
              <th className="num">Pakete</th>
              <th className="num">Bytes gesamt</th>
              <th className="num">A → B</th>
              <th className="num">B → A</th>
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((c, i) => (
              <tr key={i}>
                <td>{c.endpoint_a}</td>
                <td>{c.endpoint_b}</td>
                <td className="num">{c.frames_total}</td>
                <td className="num">{fmtBytes(c.bytes_total)}</td>
                <td className="num">{fmtBytes(c.bytes_ab)}</td>
                <td className="num">{fmtBytes(c.bytes_ba)}</td>
              </tr>
            ))}
            {rows && !rows.length && (
              <tr>
                <td colSpan={6} className="muted">Keine IP-Conversations.</td>
              </tr>
            )}
            {!rows && (
              <tr>
                <td colSpan={6} className="muted">Lädt…</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatsTab({ capId, setError }: { capId: number; setError: (s: string | null) => void }) {
  const [s, setS] = useState<CaptureStats | null>(null);

  useEffect(() => {
    api
      .get<CaptureStats>(`/api/captures/${capId}/stats`)
      .then(setS)
      .catch((e: ApiError) => setError(e.detail));
  }, [capId, setError]);

  if (!s) return <div className="card muted">Statistik wird berechnet…</div>;

  const ppsPt = s.series.map((b) => [b.t_epoch * 1000, b.packets_per_s] as [number, number | null]);
  const bpsPt = s.series.map((b) => [b.t_epoch * 1000, b.bits_per_s] as [number, number | null]);

  return (
    <>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="card stat"><div className="label">Pakete</div><div className="value">{s.total_packets}</div></div>
        <div className="card stat"><div className="label">Volumen</div><div className="value">{fmtBytes(s.total_bytes)}</div></div>
        <div className="card stat"><div className="label">Dauer</div><div className="value">{fmtNum(s.duration_s, 1, "s")}</div></div>
        <div className="card stat">
          <div className="label">ø Durchsatz</div>
          <div className="value">{fmtNum(s.avg_bps / 1e6, 2, "Mbit/s")}</div>
          <div className="sub">{fmtNum(s.avg_pps, 0, "P/s")} · Intervall {fmtNum(s.interval_s, 2, "s")}</div>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Pakete / s</h2>
        <TimeSeriesChart yName="P/s" height={240} series={[{ name: "Pakete/s", data: ppsPt, color: "#38bdf8" }]} />
      </div>
      <div className="card">
        <h2>Bit / s</h2>
        <TimeSeriesChart yName="bit/s" height={240} series={[{ name: "Bit/s", data: bpsPt, color: "#a78bfa" }]} />
      </div>
    </>
  );
}

function OverviewTab({ capId, setError }: { capId: number; setError: (s: string | null) => void }) {
  const [data, setData] = useState<CaptureAnalysis | null>(null);

  useEffect(() => {
    api
      .get<CaptureAnalysis>(`/api/captures/${capId}/analysis`)
      .then(setData)
      .catch((e: ApiError) => setError(e.detail));
  }, [capId, setError]);

  if (!data) return <div className="card muted">Analyse läuft…</div>;

  const d = data.frame_distribution;
  const t = data.tcp_health;

  return (
    <div style={{ fontSize: 13 }}>
      {data.warnings.map((w, i) => (
        <div key={i} className={w.level === "warn" ? "error-box" : "warn-box"}>
          <strong>{w.level === "warn" ? "⚠ Warnung" : "ℹ Hinweis"}:</strong> {w.message}
        </div>
      ))}

      {data.sampled && (
        <div className="warn-box">
          Grundanalyse auf Basis der ersten <strong>{data.packets_analyzed.toLocaleString("de-DE")}</strong> von{" "}
          {(data.packet_count ?? 0).toLocaleString("de-DE")} Paketen (Kopf-Sample). Prozentwerte
          sind repräsentativ, absolute Byte-Summen beziehen sich auf das Sample.
        </div>
      )}

      <OverviewSection title="Frame-Verteilung (Sicherungsschicht)">
        <div className="grid cols-4">
          <MiniStat label="Unicast" pct={d.unicast_pct} count={d.unicast} />
          <MiniStat label="Broadcast" pct={d.broadcast_pct} count={d.broadcast} warn={d.broadcast_pct > 10} />
          <MiniStat label="Multicast" pct={d.multicast_pct} count={d.multicast} warn={d.multicast_pct > 30} />
          <MiniStat label="Ohne Ethernet" pct={d.other_pct} count={d.other} />
        </div>
      </OverviewSection>

      <OverviewSection title="TCP-Gesundheit">
        <div className="grid cols-4">
          <MiniStat label="TCP-Pakete" pct={null} count={t.tcp_packets} />
          <MiniStat label="Retransmissions" pct={t.retransmission_pct} count={t.retransmissions} warn={t.retransmission_pct > 5} />
          <MiniStat label="Dup-ACKs" pct={t.dup_ack_pct} count={t.dup_acks} />
          <MiniStat label="RST-Flags" pct={null} count={t.resets} warn={t.resets > 50} />
        </div>
      </OverviewSection>

      <OverviewSection title="Top-Protokolle">
        <TwoMetricTable
          head={["Protokoll", "Pakete", "Bytes"]}
          rows={data.top_protocols.map((p) => [p.protocol, p.packets.toLocaleString("de-DE"), fmtBytes(p.bytes)])}
        />
      </OverviewSection>

      <OverviewSection title="Top-IP-Conversations">
        <TwoMetricTable
          head={["Endpunkt A", "Endpunkt B", "Pakete", "Bytes"]}
          rows={data.top_conversations.map((c) => [
            c.addr_a,
            c.addr_b,
            c.packets.toLocaleString("de-DE"),
            fmtBytes(c.bytes),
          ])}
        />
      </OverviewSection>

      <OverviewSection title="Protokollhierarchie (tshark)">
        {data.protocol_hierarchy.slice(0, 20).map((p, i) => (
          <div key={i} style={{ paddingLeft: p.level * 14 }}>
            {p.protocol} — {p.frames} Frames / {fmtBytes(p.bytes)}
          </div>
        ))}
      </OverviewSection>

      <OverviewSection title="DNS-Queries">
        {data.dns_queries.slice(0, 20).map((q, i) => <div key={i}>{q.name} ×{q.count}</div>)}
        {!data.dns_queries.length && <span className="muted">keine</span>}
      </OverviewSection>

      <OverviewSection title="TLS SNI">
        {data.tls_sni.slice(0, 20).map((sni, i) => <div key={i}>{sni.server_name} ×{sni.count}</div>)}
        {!data.tls_sni.length && <span className="muted">keine</span>}
      </OverviewSection>

      <OverviewSection title="Expert Infos">
        {data.expert_infos.slice(0, 20).map((e, i) => (
          <div key={i}>
            <span className={`badge ${e.severity === "Errors" ? "err" : e.severity === "Warnings" ? "warn" : "dim"}`}>
              {e.severity}
            </span>{" "}
            {e.protocol}: {e.summary} ×{e.count}
          </div>
        ))}
        {!data.expert_infos.length && <span className="muted">keine</span>}
      </OverviewSection>
    </div>
  );
}

function MiniStat({
  label,
  pct,
  count,
  warn,
}: {
  label: string;
  pct: number | null;
  count: number;
  warn?: boolean;
}) {
  return (
    <div className="card stat" style={warn ? { borderColor: "var(--warn)" } : undefined}>
      <div className="label">{label}</div>
      <div className="value">{pct === null ? count.toLocaleString("de-DE") : `${pct} %`}</div>
      {pct !== null && <div className="sub">{count.toLocaleString("de-DE")} Pakete</div>}
    </div>
  );
}

function TwoMetricTable({ head, rows }: { head: string[]; rows: string[][] }) {
  if (!rows.length) return <span className="muted">keine</span>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className={i >= head.length - 2 ? "num" : undefined}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} className={j >= r.length - 2 ? "num" : undefined}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OverviewSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <h2 style={{ fontSize: 14, marginBottom: 6 }}>{title}</h2>
      {children}
    </div>
  );
}

function ReportModal({
  capId,
  onClose,
  onCreated,
  setError,
}: {
  capId: number;
  onClose: () => void;
  onCreated: (r: Report) => void;
  setError: (s: string | null) => void;
}) {
  const [form, setForm] = useState({ customer: "", location: "", notes: "" });
  const [sections, setSections] = useState<string[]>(REPORT_SECTIONS.map((s) => s.key));
  const [busy, setBusy] = useState<"" | "preview" | "create">("");
  const [preview, setPreview] = useState<ReportPreview | null>(null);

  function toggle(key: string) {
    setSections((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));
    setPreview(null);
  }

  async function runPreview() {
    setBusy("preview");
    setError(null);
    try {
      setPreview(await api.post<ReportPreview>("/api/reports/preview", { capture_id: capId, sections }));
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy("");
    }
  }

  async function submit() {
    setBusy("create");
    setError(null);
    try {
      const r = await api.post<Report>("/api/reports", {
        capture_id: capId,
        customer: form.customer,
        location: form.location,
        notes: form.notes || null,
        sections,
      });
      onCreated(r);
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy("");
    }
  }

  return (
    <Modal title="Report erstellen" onClose={onClose}>
      <div className="field"><label>Kunde</label>
        <input value={form.customer} onChange={(e) => setForm({ ...form, customer: e.target.value })} /></div>
      <div className="field"><label>Standort</label>
        <input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></div>
      <div className="field"><label>Notizen</label>
        <textarea
          rows={3}
          style={{ font: "inherit", background: "var(--bg)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 8, padding: "7px 10px" }}
          value={form.notes}
          onChange={(e) => setForm({ ...form, notes: e.target.value })}
        />
      </div>
      <div className="field">
        <label>Abschnitte / Test-Kategorien</label>
        {REPORT_SECTIONS.map((s) => (
          <label key={s.key} className="row" style={{ gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={sections.includes(s.key)} onChange={() => toggle(s.key)} />
            {s.label}
          </label>
        ))}
      </div>

      {preview && (
        <div className="card" style={{ margin: "8px 0" }}>
          <div className="muted" style={{ marginBottom: 6 }}>Vorschau – Key Metrics</div>
          <KeyMetricTiles metrics={preview.key_metrics} />
          <Interpretations items={preview.interpretations} />
        </div>
      )}

      <p className="muted">
        Der Report wird als HTML gespeichert; ein PDF wird zusätzlich erzeugt, sofern WeasyPrint
        installiert ist. Andernfalls kann das HTML im Browser gedruckt werden.
      </p>
      <div className="row">
        <button onClick={runPreview} disabled={!!busy || !sections.length}>
          {busy === "preview" ? "Berechne…" : "Vorschau"}
        </button>
        <button className="primary" onClick={submit} disabled={!!busy || !sections.length}>
          {busy === "create" ? "Wird erstellt…" : "Report erstellen"}
        </button>
      </div>
    </Modal>
  );
}
