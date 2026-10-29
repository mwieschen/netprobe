import { Dispatch, SetStateAction, useState } from "react";

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
  "15m": { value: "15m", label: "15 Minuten", ms: 15 * MIN },
  "1h": { value: "1h", label: "1 Stunde", ms: HOUR },
  "3h": { value: "3h", label: "3 Stunden", ms: 3 * HOUR },
  "6h": { value: "6h", label: "6 Stunden", ms: 6 * HOUR },
  "12h": { value: "12h", label: "12 Stunden", ms: 12 * HOUR },
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

/** Ladebereich (epoch ms): sichtbares Fenster plus je eine Fensterbreite Puffer links und
 *  rechts (nie über "jetzt" hinaus). So lässt sich im Diagramm verschieben und herauszoomen,
 *  ohne sofort ins Leere zu laufen; am Rand des Puffers lädt die Seite automatisch nach. */
export function loadBounds(w: TimeWindow, now = Date.now()): { min?: number; max?: number } {
  const b = windowBounds(w, now);
  if (b.min === undefined || b.max === undefined) return b;
  const span = b.max - b.min;
  return { min: b.min - span, max: Math.min(b.max + span, now) };
}

/** Query-String für den Ladebereich (Diagrammdaten). Kennzahlen/Tabellen nutzen `windowQuery`. */
export function loadQuery(w: TimeWindow, now = Date.now()): string {
  const b = loadBounds(w, now);
  if (b.min === undefined) return windowQuery(w);
  const start = `start=${encodeURIComponent(new Date(b.min).toISOString())}`;
  // Live-Fenster ohne Ende laden, damit auch Werte bis "jetzt" dabei sind.
  if (isLiveWindow(w) || b.max === undefined) return start;
  return `${start}&end=${encodeURIComponent(new Date(b.max).toISOString())}`;
}

/** Liegt der Zeitpunkt im sichtbaren Fenster? (Tabellen/Kennzahlen ohne den Ladepuffer.) */
export function inWindow(w: TimeWindow, ts: string | number, now = Date.now()): boolean {
  const t = typeof ts === "number" ? ts : new Date(ts).getTime();
  const b = windowBounds(w, now);
  return (b.min === undefined || t >= b.min) && (b.max === undefined || t <= b.max);
}

/** Endet das Fenster bei "jetzt"? (Dann ergeben Live-Daten Sinn.) */
export function isLiveWindow(w: TimeWindow): boolean {
  return w.range !== "custom";
}

// Kleinster Ausschnitt beim Hineinzoomen.
const MIN_SPAN = MIN;

/** Gezoomten Diagramm-Ausschnitt als absoluten Zeitraum übernehmen. */
export function zoomWindow(start: number, end: number): TimeWindow {
  if (end - start < MIN_SPAN) {
    const c = (start + end) / 2;
    return { range: "custom", start: c - MIN_SPAN / 2, end: c + MIN_SPAN / 2 };
  }
  return { range: "custom", start: Math.round(start), end: Math.round(end) };
}

/** Diagramm-Props für ein Zeitfenster: Achse = Ladebereich, sichtbarer Ausschnitt = Fenster.
 *  Verschieben/Zoomen im Diagramm übernimmt den neuen Ausschnitt als absoluten Zeitraum. */
export function chartWindowProps(
  w: TimeWindow,
  setWin: Dispatch<SetStateAction<TimeWindow>>,
  key?: string | number | null,
) {
  const view = windowBounds(w);
  const load = loadBounds(w);
  return {
    xMin: load.min,
    xMax: load.max,
    viewStart: view.min,
    viewEnd: view.max,
    zoomKey: `${key ?? ""}|${windowQuery(w)}`,
    onZoomRange: (s: number, e: number) =>
      setWin((prev) => {
        const next = zoomWindow(s, e);
        // Gekoppelte Diagramme melden denselben Ausschnitt mehrfach – nur einmal übernehmen.
        const same =
          prev.range === "custom" &&
          Math.abs((prev.start ?? 0) - (next.start ?? 0)) < 1000 &&
          Math.abs((prev.end ?? 0) - (next.end ?? 0)) < 1000;
        return same ? prev : next;
      }),
  };
}

export type ChartWindowProps = ReturnType<typeof chartWindowProps>;

function toLocalInput(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
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

  // Hinein-/Herauszoomen: Live-Presets springen zum nächstkleineren/-größeren Preset,
  // ein absoluter Zeitraum wird um seine Mitte halbiert bzw. verdoppelt.
  function zoom(dir: -1 | 1) {
    const timed = presets.filter((p) => RANGE_OPTIONS[p]?.ms);
    if (value.range !== "custom") {
      if (value.range === "all") {
        if (dir === -1 && timed.length) select(timed[timed.length - 1]);
        return;
      }
      const i = timed.indexOf(value.range);
      const next = timed[i + (dir === 1 ? 1 : -1)];
      if (next) select(next);
      else if (dir === 1 && presets.includes("all")) select("all");
      else if (dir === -1) {
        const { start, end } = currentSpan();
        onChange(zoomWindow(end - (end - start) / 2, end));
      }
      return;
    }
    const { start, end } = currentSpan();
    const span = (end - start) * (dir === 1 ? 2 : 0.5);
    const now = Date.now();
    const c = (start + end) / 2;
    let ns = c - span / 2;
    let ne = c + span / 2;
    if (ne > now) {
      ne = now;
      ns = now - span;
    }
    onChange(zoomWindow(ns, ne));
  }

  function select(v: string) {
    setLastPreset(v);
    onChange(presetWindow(v));
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
            step={1}
            value={value.start !== undefined ? toLocalInput(value.start) : ""}
            onChange={(e) => {
              const t = fromLocalInput(e.target.value);
              if (t !== null && value.end !== undefined && t < value.end) onChange({ ...value, start: t });
            }}
          />
          <span className="muted">bis</span>
          <input
            type="datetime-local"
            step={1}
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
      <button onClick={() => zoom(-1)} title="Hineinzoomen (kürzerer Zeitraum)" style={{ minWidth: 32 }}>+</button>
      <button onClick={() => zoom(1)} disabled={value.range === "all"} title="Herauszoomen (längerer Zeitraum)" style={{ minWidth: 32 }}>−</button>
    </div>
  );
}
