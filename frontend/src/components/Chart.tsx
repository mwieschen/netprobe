import { useEffect, useReducer, useRef } from "react";
import * as echarts from "echarts";

export interface Series {
  name: string;
  data: [number, number | null][];
  color?: string;
  type?: "line" | "scatter" | "bar";
  yAxisIndex?: number;
}

// Senkrechte Markierung zu einem Zeitpunkt (z. B. Störungsmeldung eines Benutzers).
export interface Marker {
  ts: number;
  label: string;
  color?: string;
}

interface Props {
  series: Series[];
  height?: number;
  yName?: string;
  y2Name?: string;
  // Achsenbereich (epoch ms) – i. d. R. der geladene Bereich inkl. Puffer. Ohne Angabe
  // skaliert die Achse auf die Daten.
  xMin?: number;
  xMax?: number;
  // Sichtbarer Ausschnitt (epoch ms) innerhalb von xMin/xMax. Ohne Angabe: alles sichtbar.
  viewStart?: number;
  viewEnd?: number;
  // Der Ausschnitt des Benutzers bleibt erhalten, solange sich zoomKey nicht ändert.
  zoomKey?: string;
  // Wird (verzögert) mit dem neuen Ausschnitt aufgerufen, nachdem verschoben/gezoomt
  // wurde – die Seite übernimmt ihn als Zeitraum und lädt passend nach.
  onZoomRange?: (start: number, end: number) => void;
  markers?: Marker[];
  // Diagramme derselben Gruppe zoomen/verschieben gemeinsam und teilen das Fadenkreuz.
  group?: string;
}

interface ZoomState {
  touched: boolean; // vom Benutzer verändert (sonst gelten die Props)
  start: number; // Prozent (ohne viewStart/viewEnd)
  end: number;
  startValue?: number; // epoch ms (mit viewStart/viewEnd)
  endValue?: number;
}

const FRESH: ZoomState = { touched: false, start: 0, end: 100 };
const DEBOUNCE_MS = 500;

const p2 = (n: number) => String(n).padStart(2, "0");
function fmtTs(ms: number): string {
  const d = new Date(ms);
  return `${p2(d.getDate())}.${p2(d.getMonth() + 1)}.${d.getFullYear()} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
}
function fmtVal(v: unknown): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const n = Number(v);
  return Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(2).replace(/\.?0+$/, "");
}
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

// Wiederverwendbares Zeitreihendiagramm auf Basis von echarts (ohne Wrapper).
// Zeitachse in lokaler Zeit; die Datenpunkte kommen als [epoch_ms, wert].
// Bedienung: Mausrad = Zoom um den Mauszeiger, Ziehen = Verschieben, Shift+Mausrad =
// Verschieben, Schieberegler unten, "Bereich aufziehen" (Toolbox) für einen Box-Zoom.
export default function TimeSeriesChart({
  series, height = 320, yName, y2Name, xMin, xMax, viewStart, viewEnd, zoomKey, onZoomRange, markers, group,
}: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const zoomRef = useRef<ZoomState>(FRESH);
  const zoomKeyRef = useRef<string | undefined>(zoomKey);
  const onZoomRef = useRef(onZoomRange);
  onZoomRef.current = onZoomRange;
  // Neu zeichnen nach "Zurücksetzen" (echarts stellt sonst die allererste Option wieder her).
  const [rev, bump] = useReducer((x: number) => x + 1, 0);
  const viewRef = useRef<{ start?: number; end?: number }>({});
  viewRef.current = { start: viewStart, end: viewEnd };

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: "canvas" });
    chartRef.current = chart;
    let timer: number | undefined;

    chart.on("datazoom", () => {
      const dz = (chart.getOption() as { dataZoom?: { start?: number; end?: number; startValue?: number; endValue?: number }[] })
        .dataZoom?.[0];
      if (!dz) return;
      // Box-Zoom der Toolbox in Daten-Zoom umsetzen, kein Dauer-Auswahlmodus.
      chart.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: false });
      window.clearTimeout(timer);
      const s = Number(dz.startValue);
      const e = Number(dz.endValue);
      const { start: vs, end: ve } = viewRef.current;
      let changed: boolean;
      if (vs !== undefined && ve !== undefined) {
        // Absoluter Ausschnitt: nur echte Änderungen (> 0,5 % der Breite) melden.
        const tol = (ve - vs) * 0.005;
        changed = Math.abs(s - vs) > tol || Math.abs(e - ve) > tol;
      } else {
        changed = (dz.start ?? 0) > 0.01 || (dz.end ?? 100) < 99.99;
      }
      zoomRef.current = { touched: true, start: dz.start ?? 0, end: dz.end ?? 100, startValue: s, endValue: e };
      if (changed && onZoomRef.current && Number.isFinite(s) && Number.isFinite(e)) {
        timer = window.setTimeout(() => onZoomRef.current?.(s, e), DEBOUNCE_MS);
      }
    });
    chart.on("restore", () => {
      window.clearTimeout(timer);
      zoomRef.current = FRESH;
      bump();
    });

    // Größe an den Container koppeln (nicht nur ans Fenster) – volle verfügbare Breite.
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      window.clearTimeout(timer);
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.group = group ?? "";
    if (group) echarts.connect(group);
  }, [group]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (zoomKeyRef.current !== zoomKey) {
      zoomKeyRef.current = zoomKey;
      zoomRef.current = FRESH;
    }
    const z = zoomRef.current;
    const absolute = viewStart !== undefined && viewEnd !== undefined;
    // Solange der Benutzer nichts verändert hat, folgt der Ausschnitt den Props (Live-Fenster).
    const range = absolute
      ? z.touched && z.startValue !== undefined
        ? { startValue: z.startValue, endValue: z.endValue }
        : { startValue: viewStart, endValue: viewEnd }
      : { start: z.start, end: z.end };
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const textColor = dark ? "#e2e8f0" : "#0f172a";
    const lineColor = dark ? "#334155" : "#cbd5e1";
    const hasY2 = series.some((s) => s.yAxisIndex === 1);

    chart.setOption(
      {
        animation: false,
        grid: { left: 55, right: hasY2 ? 55 : 20, top: 55, bottom: 70 },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross", label: { formatter: (p: { axisDimension: string; value: number }) =>
            p.axisDimension === "x" ? fmtTs(p.value) : fmtVal(p.value) } },
          formatter: (params: unknown) => {
            const arr = (Array.isArray(params) ? params : [params]) as {
              axisValue: number; marker: string; seriesName: string; value: [number, number | null];
            }[];
            if (!arr.length) return "";
            const rows = arr.map((p) => `${p.marker}${esc(p.seriesName)}: <b>${fmtVal(p.value?.[1])}</b>`);
            return [`<b>${fmtTs(Number(arr[0].axisValue))}</b>`, ...rows].join("<br/>");
          },
        },
        legend: { textStyle: { color: textColor }, top: 0, left: "center" },
        toolbox: {
          right: 10,
          top: 0,
          iconStyle: { borderColor: textColor },
          feature: {
            dataZoom: { yAxisIndex: "none", title: { zoom: "Bereich aufziehen", back: "Zoom zurück" } },
            restore: { title: "Zurücksetzen" },
          },
        },
        // weakFilter: die y-Achse skaliert auf den sichtbaren Ausschnitt (nicht auf den Ladepuffer),
        // Linien über den Rand hinaus bleiben trotzdem erhalten.
        dataZoom: [
          {
            type: "inside",
            xAxisIndex: 0,
            filterMode: "weakFilter",
            zoomOnMouseWheel: true,
            moveOnMouseMove: true,
            moveOnMouseWheel: "shift",
            minValueSpan: 10_000,
            ...range,
          },
          {
            type: "slider",
            xAxisIndex: 0,
            filterMode: "weakFilter",
            minValueSpan: 10_000,
            ...range,
            height: 18,
            bottom: 8,
            textStyle: { color: textColor },
            borderColor: lineColor,
            labelFormatter: (v: number) => fmtTs(v),
          },
        ],
        xAxis: {
          type: "time",
          min: xMin,
          max: xMax,
          axisLine: { lineStyle: { color: lineColor } },
          axisLabel: { color: textColor, hideOverlap: true },
          splitLine: { show: true, lineStyle: { color: lineColor, opacity: 0.25 } },
        },
        yAxis: [
          {
            type: "value",
            name: yName,
            nameTextStyle: { color: textColor },
            axisLabel: { color: textColor },
            splitLine: { lineStyle: { color: lineColor, opacity: 0.4 } },
          },
          ...(hasY2
            ? [
                {
                  type: "value",
                  name: y2Name,
                  nameTextStyle: { color: textColor },
                  axisLabel: { color: textColor },
                  splitLine: { show: false },
                },
              ]
            : []),
        ],
        series: series.map((s, i) => ({
          name: s.name,
          type: s.type ?? "line",
          showSymbol: false,
          smooth: false,
          connectNulls: false,
          yAxisIndex: s.yAxisIndex ?? 0,
          itemStyle: s.color ? { color: s.color } : undefined,
          lineStyle: s.color ? { color: s.color } : undefined,
          data: s.data,
          ...(i === 0 && markers?.length
            ? {
                markLine: {
                  symbol: "none",
                  silent: false,
                  label: { formatter: "{b}", color: textColor, fontSize: 11 },
                  data: markers.map((m) => ({
                    name: m.label,
                    xAxis: m.ts,
                    lineStyle: { color: m.color ?? "#f59e0b", type: "dashed", width: 1.5 },
                  })),
                },
              }
            : {}),
        })),
      },
      { notMerge: true },
    );
  }, [series, yName, y2Name, xMin, xMax, viewStart, viewEnd, zoomKey, markers, rev]);

  return (
    <div>
      <div ref={ref} style={{ height, width: "100%" }} />
      {onZoomRange && (
        <div className="muted" style={{ fontSize: 12, textAlign: "right", marginTop: 2 }}>
          Mausrad: Zoom · Ziehen / Shift+Mausrad: Verschieben · Toolbox: Bereich aufziehen
        </div>
      )}
    </div>
  );
}
