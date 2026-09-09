import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  Traceroute,
  TracerouteCompare,
  TracerouteHop,
  TracerouteListItem,
  TracerouteTarget,
} from "../api";
import { useAuth } from "../auth";
import MapView, { MapPoint } from "../components/MapView";
import { Badge, ErrorBox, Modal } from "../components/common";
import { fmtDateTime, fmtNum } from "../util";

export default function TraceroutePage() {
  const { isAdmin } = useAuth();
  const [targets, setTargets] = useState<TracerouteTarget[]>([]);
  const [history, setHistory] = useState<TracerouteListItem[]>([]);
  const [current, setCurrent] = useState<Traceroute | null>(null);
  const [compareWith, setCompareWith] = useState<number | "">("");
  const [compare, setCompare] = useState<TracerouteCompare | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    host: "",
    mode: "icmp" as "icmp" | "udp" | "tcp",
    tcp_port: 443,
    queries: 3,
    max_hops: 30,
    resolve_geo: true,
    measure_mtu: false,
  });

  function loadTargets() {
    api.get<TracerouteTarget[]>("/api/traceroute/targets").then(setTargets);
  }
  function loadHistory() {
    api.get<TracerouteListItem[]>("/api/traceroute/history?range=30d&limit=100").then(setHistory);
  }
  useEffect(() => {
    loadTargets();
    loadHistory();
  }, []);

  async function runAdhoc() {
    setBusy(true);
    setError(null);
    setCompare(null);
    try {
      const tr = await api.post<Traceroute>("/api/traceroute/run", form);
      setCurrent(tr);
      loadHistory();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  async function runTarget(id: number) {
    setBusy(true);
    setError(null);
    setCompare(null);
    try {
      const tr = await api.post<Traceroute>(`/api/traceroute/targets/${id}/run`);
      setCurrent(tr);
      loadHistory();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  async function openRun(id: number) {
    setCompare(null);
    const tr = await api.get<Traceroute>(`/api/traceroute/${id}`);
    setCurrent(tr);
  }

  async function removeTarget(id: number) {
    if (!confirm("Ziel wirklich löschen?")) return;
    await api.del(`/api/traceroute/targets/${id}`);
    loadTargets();
  }

  async function runCompare() {
    if (!current || compareWith === "") return;
    try {
      const c = await api.get<TracerouteCompare>(
        `/api/traceroute/compare?a=${compareWith}&b=${current.id}`,
      );
      setCompare(c);
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  const mapPoints: MapPoint[] = useMemo(() => {
    if (!current) return [];
    return current.hops
      .filter((h): h is TracerouteHop & { lat: number; lon: number } => h.lat !== null && h.lon !== null)
      .map((h) => ({
        lat: h.lat,
        lon: h.lon,
        hop: h.hop_number,
        label: h.hostname ?? h.ip ?? "?",
      }));
  }, [current]);

  const maxLat = useMemo(
    () => Math.max(1, ...(current?.hops ?? []).map((h) => h.latency_avg_ms ?? 0)),
    [current],
  );

  return (
    <div>
      <div className="topbar">
        <h1>Traceroute</h1>
        {isAdmin && (
          <button className="primary" onClick={() => setShowAdd(true)}>
            Ziel speichern
          </button>
        )}
      </div>
      <ErrorBox error={error} />

      {isAdmin && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Sofort ausführen</h2>
          <div className="row">
            <div className="field" style={{ flex: 2 }}>
              <label>Ziel (Host / IP)</label>
              <input
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                placeholder="8.8.8.8"
              />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Modus</label>
              <select
                value={form.mode}
                onChange={(e) => setForm({ ...form, mode: e.target.value as typeof form.mode })}
              >
                <option value="icmp">ICMP</option>
                <option value="udp">UDP</option>
                <option value="tcp">TCP</option>
              </select>
            </div>
            {form.mode === "tcp" && (
              <div className="field" style={{ flex: 1 }}>
                <label>TCP-Port</label>
                <input
                  type="number"
                  value={form.tcp_port}
                  onChange={(e) => setForm({ ...form, tcp_port: +e.target.value })}
                />
              </div>
            )}
            <div className="field" style={{ flex: 1 }}>
              <label>Queries</label>
              <input
                type="number"
                value={form.queries}
                onChange={(e) => setForm({ ...form, queries: +e.target.value })}
              />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Max. Hops</label>
              <input
                type="number"
                value={form.max_hops}
                onChange={(e) => setForm({ ...form, max_hops: +e.target.value })}
              />
            </div>
          </div>
          <div className="row">
            <label>
              <input
                type="checkbox"
                checked={form.resolve_geo}
                onChange={(e) => setForm({ ...form, resolve_geo: e.target.checked })}
              />{" "}
              Geo/ASN auflösen
            </label>
            <label>
              <input
                type="checkbox"
                checked={form.measure_mtu}
                onChange={(e) => setForm({ ...form, measure_mtu: e.target.checked })}
              />{" "}
              Pfad-MTU zum Ziel messen
            </label>
            <button className="primary" onClick={runAdhoc} disabled={busy || !form.host}>
              {busy ? "läuft…" : "Traceroute starten"}
            </button>
          </div>
        </div>
      )}

      {targets.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Gespeicherte Ziele</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Host</th>
                  <th>Modus</th>
                  <th className="num">Intervall</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {targets.map((t) => (
                  <tr key={t.id}>
                    <td>{t.name}</td>
                    <td>{t.host}</td>
                    <td>{t.mode}</td>
                    <td className="num">{t.interval_s}s</td>
                    <td>
                      <Badge ok={t.enabled} label={t.enabled ? "aktiv" : "aus"} />
                    </td>
                    <td className="num">
                      {isAdmin && (
                        <div className="row" style={{ justifyContent: "flex-end" }}>
                          <button disabled={busy} onClick={() => runTarget(t.id)}>
                            Jetzt
                          </button>
                          <button
                            onClick={() =>
                              api
                                .patch(`/api/traceroute/targets/${t.id}`, { enabled: !t.enabled })
                                .then(loadTargets)
                            }
                          >
                            {t.enabled ? "Deaktivieren" : "Aktivieren"}
                          </button>
                          <button className="danger" onClick={() => removeTarget(t.id)}>
                            Löschen
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {current && (
        <>
          <div className="topbar">
            <h2>
              Lauf #{current.id} → {current.target}
            </h2>
            <div className="row">
              <span className="muted">
                {fmtDateTime(current.ts)} · {current.engine} · {current.mode} ·{" "}
                {current.dest_reached ? (
                  <Badge ok={true} label="Ziel erreicht" />
                ) : (
                  <Badge ok={false} label="Ziel nicht erreicht" />
                )}
              </span>
            </div>
          </div>

          {current.error && <div className="error-box">{current.error}</div>}

          {mapPoints.length > 0 && (
            <div className="card" style={{ marginBottom: 16 }}>
              <MapView points={mapPoints} />
            </div>
          )}

          <div className="card" style={{ marginBottom: 16 }}>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th className="num">#</th>
                    <th>IP</th>
                    <th>Hostname</th>
                    <th>Land / Stadt</th>
                    <th>ASN / ISP</th>
                    <th className="num">Latenz min/ø/max</th>
                    <th>Latenz</th>
                    <th className="num">Loss</th>
                    <th className="num">MTU</th>
                  </tr>
                </thead>
                <tbody>
                  {current.hops.map((h) => (
                    <tr key={h.hop_number}>
                      <td className="num">{h.hop_number}</td>
                      <td>{h.ip ?? "*"}</td>
                      <td>{h.hostname ?? "–"}</td>
                      <td>{[h.country, h.city].filter(Boolean).join(" / ") || "–"}</td>
                      <td>{[h.asn, h.isp].filter(Boolean).join(" · ") || "–"}</td>
                      <td className="num">
                        {fmtNum(h.latency_min_ms, 1)} / {fmtNum(h.latency_avg_ms, 1)} /{" "}
                        {fmtNum(h.latency_max_ms, 1)}
                      </td>
                      <td style={{ minWidth: 120 }}>
                        <div
                          style={{
                            height: 8,
                            borderRadius: 4,
                            background: (h.loss_pct ?? 0) >= 100 ? "#ef4444" : "#38bdf8",
                            width: `${Math.round(((h.latency_avg_ms ?? 0) / maxLat) * 100)}%`,
                          }}
                        />
                      </td>
                      <td className="num">{fmtNum(h.loss_pct, 0, "%")}</td>
                      <td className="num">{h.mtu ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <h2>Mit früherem Lauf vergleichen</h2>
            <div className="row">
              <select
                value={compareWith}
                onChange={(e) => setCompareWith(e.target.value ? +e.target.value : "")}
              >
                <option value="">– Lauf wählen –</option>
                {history
                  .filter((h) => h.id !== current.id && h.target === current.target)
                  .map((h) => (
                    <option key={h.id} value={h.id}>
                      #{h.id} · {fmtDateTime(h.ts)}
                    </option>
                  ))}
              </select>
              <button onClick={runCompare} disabled={compareWith === ""}>
                Vergleichen
              </button>
            </div>

            {compare && (
              <div className="table-scroll" style={{ marginTop: 12 }}>
                <p className="muted">
                  {compare.path_changed
                    ? "Der Pfad hat sich zwischen den Läufen geändert."
                    : "Der Pfad ist unverändert."}
                </p>
                <table>
                  <thead>
                    <tr>
                      <th className="num">#</th>
                      <th>#{compare.a.id} IP</th>
                      <th>#{compare.b.id} IP</th>
                      <th className="num">Latenz #{compare.a.id}</th>
                      <th className="num">Latenz #{compare.b.id}</th>
                      <th className="num">Δ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compare.rows.map((r) => (
                      <tr
                        key={r.hop_number}
                        style={{ background: r.changed ? "rgba(239,68,68,.12)" : undefined }}
                      >
                        <td className="num">{r.hop_number}</td>
                        <td>{r.ip_a ?? "*"}</td>
                        <td>{r.ip_b ?? "*"}</td>
                        <td className="num">{fmtNum(r.latency_avg_a, 1, "ms")}</td>
                        <td className="num">{fmtNum(r.latency_avg_b, 1, "ms")}</td>
                        <td className="num">{r.delta_ms === null ? "–" : fmtNum(r.delta_ms, 1, "ms")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      <div className="card">
        <h2>Verlauf</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Zeit</th>
                <th>Ziel</th>
                <th>Quelle</th>
                <th>Engine</th>
                <th className="num">Hops</th>
                <th>Ziel erreicht</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td>{fmtDateTime(h.ts)}</td>
                  <td>{h.target}</td>
                  <td>{h.source === "scheduled" ? "Zeitplan" : "manuell"}</td>
                  <td>{h.engine}</td>
                  <td className="num">{h.total_hops}</td>
                  <td>
                    <Badge ok={h.completed ? h.dest_reached : null} />
                  </td>
                  <td className="num">
                    <button onClick={() => openRun(h.id)}>Anzeigen</button>
                  </td>
                </tr>
              ))}
              {!history.length && (
                <tr>
                  <td colSpan={7} className="muted">
                    Noch keine Läufe.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showAdd && (
        <TargetModal
          onClose={() => setShowAdd(false)}
          onSaved={() => {
            setShowAdd(false);
            loadTargets();
          }}
          setError={setError}
        />
      )}
    </div>
  );
}

function TargetModal({
  onClose,
  onSaved,
  setError,
}: {
  onClose: () => void;
  onSaved: () => void;
  setError: (s: string) => void;
}) {
  const [form, setForm] = useState({
    name: "",
    host: "",
    mode: "icmp",
    tcp_port: 443,
    max_hops: 30,
    queries: 3,
    resolve_geo: true,
    interval_s: 900,
  });

  async function save() {
    try {
      await api.post("/api/traceroute/targets", form);
      onSaved();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <Modal title="Traceroute-Ziel speichern" onClose={onClose}>
      <div className="field">
        <label>Name</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </div>
      <div className="field">
        <label>Host / IP</label>
        <input
          value={form.host}
          onChange={(e) => setForm({ ...form, host: e.target.value })}
          placeholder="8.8.8.8"
        />
      </div>
      <div className="row">
        <div className="field" style={{ flex: 1 }}>
          <label>Modus</label>
          <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
            <option value="icmp">ICMP</option>
            <option value="udp">UDP</option>
            <option value="tcp">TCP</option>
          </select>
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Intervall (s)</label>
          <input
            type="number"
            value={form.interval_s}
            onChange={(e) => setForm({ ...form, interval_s: +e.target.value })}
          />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Max. Hops</label>
          <input
            type="number"
            value={form.max_hops}
            onChange={(e) => setForm({ ...form, max_hops: +e.target.value })}
          />
        </div>
      </div>
      <div className="field">
        <label>
          <input
            type="checkbox"
            checked={form.resolve_geo}
            onChange={(e) => setForm({ ...form, resolve_geo: e.target.checked })}
          />{" "}
          Geo/ASN auflösen (nutzt ip-api.com, falls keine lokale MaxMind-DB)
        </label>
      </div>
      <button className="primary" onClick={save} disabled={!form.name || !form.host}>
        Speichern
      </button>
    </Modal>
  );
}
