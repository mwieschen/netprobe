import { useEffect, useRef } from "react";
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
  // Sichtbarer Zeitbereich (epoch ms). Ohne Angabe skaliert die Achse auf die Daten.
  xMin?: number;
  xMax?: number;
  // Zoom bleibt erhalten, solange sich zoomKey nicht ändert (z. B. Zeitraum + Auswahl).
  zoomKey?: string;
  // Wird (verzögert) mit dem gezoomten Bereich aufgerufen – die Seite lädt dann
  // die Daten für genau diesen Ausschnitt in voller Auflösung nach.
  onZoomRange?: (start: number, end: number) => void;
  markers?: Marker[];
}

interface ZoomState {
  start: number; // Prozent
  end: number;
}

// Wiederverwendbares Zeitreihendiagramm auf Basis von echarts (ohne Wrapper).
// Zeitachse in lokaler Zeit; die Datenpunkte kommen als [epoch_ms, wert].
// Zoomen: Mausrad / Schieberegler / "Bereich aufziehen" (Toolbox), Zurücksetzen per Toolbox.
export default function TimeSeriesChart({
  series, height = 320, yName, y2Name, xMin, xMax, zoomKey, onZoomRange, markers,
}: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const zoomRef = useRef<ZoomState>({ start: 0, end: 100 });
  const zoomKeyRef = useRef<string | undefined>(zoomKey);
  const onZoomRef = useRef(onZoomRange);
  onZoomRef.current = onZoomRange;

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: "canvas" });
    chartRef.current = chart;
    let timer: number | undefined;

    chart.on("datazoom", () => {
      const dz = (chart.getOption() as { dataZoom?: { start?: number; end?: number; startValue?: number; endValue?: number }[] })
        .dataZoom?.[0];
      if (!dz) return;
      zoomRef.current = { start: dz.start ?? 0, end: dz.end ?? 100 };
      // Box-Zoom der Toolbox in Daten-Zoom umsetzen, kein Dauer-Auswahlmodus.
      chart.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: false });
      window.clearTimeout(timer);
      const zoomed = (dz.start ?? 0) > 0.01 || (dz.end ?? 100) < 99.99;
      if (zoomed && onZoomRef.current && dz.startValue !== undefined && dz.endValue !== undefined) {
        const [s, e] = [Number(dz.startValue), Number(dz.endValue)];
        timer = window.setTimeout(() => onZoomRef.current?.(s, e), 700);
      }
    });
    chart.on("restore", () => {
      zoomRef.current = { start: 0, end: 100 };
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
    if (zoomKeyRef.current !== zoomKey) {
      zoomKeyRef.current = zoomKey;
      zoomRef.current = { start: 0, end: 100 };
    }
    const { start, end } = zoomRef.current;
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const textColor = dark ? "#e2e8f0" : "#0f172a";
    const lineColor = dark ? "#334155" : "#cbd5e1";
    const hasY2 = series.some((s) => s.yAxisIndex === 1);

    chart.setOption(
      {
        animation: false,
        grid: { left: 55, right: hasY2 ? 55 : 20, top: 55, bottom: 70 },
        tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
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
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none", start, end },
          {
            type: "slider",
            xAxisIndex: 0,
            filterMode: "none",
            start,
            end,
            height: 18,
            bottom: 8,
            textStyle: { color: textColor },
            borderColor: lineColor,
          },
        ],
        xAxis: {
          type: "time",
          min: xMin,
          max: xMax,
          axisLine: { lineStyle: { color: lineColor } },
          axisLabel: { color: textColor },
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
  }, [series, yName, y2Name, xMin, xMax, zoomKey, markers]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}
