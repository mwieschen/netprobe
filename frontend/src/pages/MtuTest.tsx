import { useState } from "react";
import { api, ApiError, MtuTestResult } from "../api";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/common";

export default function MtuTestPage() {
  const { isAdmin } = useAuth();
  const [form, setForm] = useState({ target: "", min_mtu: 576, max_mtu: 1500 });
  const [result, setResult] = useState<MtuTestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.post<MtuTestResult>("/api/mtu-test", form);
      setResult(r);
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="topbar">
        <h1>MTU-Test</h1>
      </div>
      <ErrorBox error={error} />

      <div className="warn-box">
        Der Test sendet ICMP-Echo-Pakete mit gesetztem <em>Don't-Fragment</em>-Bit und variabler
        Größe (<code>ping -M do</code>) und ermittelt per binärer Suche die größte durchgehende
        Paketgröße. Filtert ein Router ICMP, kann das Ergebnis zu niedrig ausfallen.
      </div>

      {isAdmin && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row">
            <div className="field" style={{ flex: 2 }}>
              <label>Ziel (Host / IP)</label>
              <input value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} placeholder="1.1.1.1" />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Min. MTU</label>
              <input type="number" value={form.min_mtu} onChange={(e) => setForm({ ...form, min_mtu: +e.target.value })} />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Max. MTU</label>
              <input type="number" value={form.max_mtu} onChange={(e) => setForm({ ...form, max_mtu: +e.target.value })} />
            </div>
          </div>
          <button className="primary" onClick={run} disabled={busy || !form.target}>
            {busy ? "Suche läuft…" : "Pfad-MTU ermitteln"}
          </button>
        </div>
      )}

      {result && (
        <>
          <div className="grid cols-3" style={{ marginBottom: 16 }}>
            <div className="card stat">
              <div className="label">Pfad-MTU zu {result.target}</div>
              <div className="value">{result.ok ? `${result.path_mtu} Byte` : "–"}</div>
            </div>
            <div className="card stat"><div className="label">Suchbereich</div><div className="value">{result.min_mtu}–{result.max_mtu}</div></div>
            <div className="card stat"><div className="label">Proben</div><div className="value">{result.probe_count}</div></div>
          </div>

          {!result.ok && result.error && <div className="error-box">{result.error}</div>}

          <div className="card">
            <h2>Proben</h2>
            <div className="table-scroll">
              <table>
                <thead><tr><th className="num">MTU (Byte)</th><th className="num">ICMP-Payload</th><th>Ergebnis</th></tr></thead>
                <tbody>
                  {result.probes.map((p, i) => (
                    <tr key={i}>
                      <td className="num">{p.mtu}</td>
                      <td className="num">{p.mtu - 28}</td>
                      <td><span className={`badge ${p.ok ? "ok" : "err"}`}>{p.ok ? "passt" : "zu groß / keine Antwort"}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
