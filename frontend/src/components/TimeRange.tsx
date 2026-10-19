import { useState } from "react";

// Zeitraum-Auswahl für Verlaufsdiagramme: Presets relativ zu "jetzt" oder ein
// absoluter Zeitraum (Start/Ende), durch den man mit ◀ / ▶ zurück- und vorblättern
// kann – so lassen sich auch ältere Aufzeichnungen ansehen.

export interface TimeWindow {
  range: string; // Preset-Wert oder "custom"
  start?: number; // epoch ms (nur bei "custom")
  end?: number;
}

export interface RangeOption {
  value: string;
  label: string;
  ms?: number; // Länge des Presets ("all" hat keine)
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export const RANGE_OPTIONS: Record<string, RangeOption> = {
  "5m": { value: "5m", label: "5 Minuten", ms: 5 * MIN },
  "1h": { value: "1h", label: "1 Stunde", ms: HOUR },
  "6h": { value: "6h", label: "6 Stunden", ms: 6 * HOUR },
  "24h": { value: "24h", label: "24 Stunden", ms: DAY },
  "7d": { value: "7d", label: "7 Tage", ms: 7 * DAY },
  "30d": { value: "30d", label: "30 Tage", ms: 30 * DAY },
  "90d": { value: "90d", label: "90 Tage", ms: 90 * DAY },
  "365d": { value: "365d", label: "1 Jahr", ms: 365 * DAY },
  all: { value: "all", label: "Gesamt" },
};

export function presetWindow(range: string): TimeWindow {
  return { range };
}

/** Query-String-Teil für die Verlaufs-Endpunkte (`range=…` bzw. `start=…&end=…`). */
export function windowQuery(w: TimeWindow): string {
  if (w.range === "custom" && w.start !== undefined && w.end !== undefined) {
    return `start=${encodeURIComponent(new Date(w.start).toISOString())}&end=${encodeURIComponent(
      new Date(w.end).toISOString(),
    )}`;
  }
  return `range=${encodeURIComponent(w.range)}`;
}

/** Sichtbarer Bereich der x-Achse (epoch ms) – `undefined` = automatisch. */
export function windowBounds(w: TimeWindow, now = Date.now()): { min?: number; max?: number } {
  if (w.range === "custom") return { min: w.start, max: w.end };
  const ms = RANGE_OPTIONS[w.range]?.ms;
  return ms ? { min: now - ms, max: now } : {};
}

/** Endet das Fenster bei "jetzt"? (Dann ergeben Live-Daten Sinn.) */
export function isLiveWindow(w: TimeWindow): boolean {
  return w.range !== "custom";
}

function toLocalInput(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fromLocalInput(v: string): number | null {
  if (!v) return null;
  const t = new Date(v).getTime();
  return Number.isNaN(t) ? null : t;
}

export default function TimeRangePicker({
  value,
  onChange,
  presets,
}: {
  value: TimeWindow;
  onChange: (w: TimeWindow) => void;
  presets: string[];
}) {
  // Zuletzt gewählte Fensterlänge – bestimmt die Schrittweite beim Blättern.
  const [lastPreset, setLastPreset] = useState(value.range === "custom" ? presets[0] : value.range);

  function currentSpan(): { start: number; end: number } {
    if (value.range === "custom" && value.start !== undefined && value.end !== undefined) {
      return { start: value.start, end: value.end };
    }
    const end = Date.now();
    const ms = RANGE_OPTIONS[value.range]?.ms ?? RANGE_OPTIONS[lastPreset]?.ms ?? DAY;
    return { start: end - ms, end };
  }

  function shift(dir: -1 | 1) {
    const { start, end } = currentSpan();
    const span = end - start;
    const now = Date.now();
    let ns = start + dir * span;
    let ne = end + dir * span;
    if (ne >= now) {
      // Wieder in der Gegenwart angekommen → zurück zum Live-Preset.
      if (RANGE_OPTIONS[lastPreset]?.ms) {
        onChange(presetWindow(lastPreset));
        return;
      }
      ne = now;
      ns = now - span;
    }
    onChange({ range: "custom", start: ns, end: ne });
  }

  function onSelect(v: string) {
    if (v === "custom") {
      const { start, end } = currentSpan();
      onChange({ range: "custom", start, end });
    } else {
      setLastPreset(v);
      onChange(presetWindow(v));
    }
  }

  const isCustom = value.range === "custom";
  const canShift = value.range !== "all";
  const atNow = !isCustom;

  return (
    <div className="row" style={{ flexWrap: "wrap", gap: 6, alignItems: "center", justifyContent: "flex-end" }}>
      {/* Datumsfelder links, damit ◀ / ▶ beim Blättern nicht verrutschen. */}
      {isCustom && (
        <>
          <input
            type="datetime-local"
            value={value.start !== undefined ? toLocalInput(value.start) : ""}
            onChange={(e) => {
              const t = fromLocalInput(e.target.value);
              if (t !== null && value.end !== undefined && t < value.end) onChange({ ...value, start: t });
            }}
          />
          <span className="muted">bis</span>
          <input
            type="datetime-local"
            value={value.end !== undefined ? toLocalInput(value.end) : ""}
            onChange={(e) => {
              const t = fromLocalInput(e.target.value);
              if (t !== null && value.start !== undefined && t > value.start) onChange({ ...value, end: t });
            }}
          />
          <button onClick={() => onChange(presetWindow(lastPreset))} title="Zurück zur Live-Ansicht">Jetzt</button>
        </>
      )}
      <button onClick={() => shift(-1)} disabled={!canShift} title="Zeitraum zurück">◀</button>
      <select value={value.range} onChange={(e) => onSelect(e.target.value)} style={{ width: 170 }}>
        {presets.map((p) => (
          <option key={p} value={p}>{RANGE_OPTIONS[p]?.label ?? p}</option>
        ))}
        <option value="custom">Benutzerdefiniert…</option>
      </select>
      <button onClick={() => shift(1)} disabled={!canShift || atNow} title="Zeitraum vor">▶</button>
    </div>
  );
}
