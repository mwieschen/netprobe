import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export interface Series {
  name: string;
  data: [number, number | null][];
  color?: string;
  type?: "line" | "scatter" | "bar";
  yAxisIndex?: number;
}

interface Props {
  series: Series[];
  height?: number;
  yName?: string;
  y2Name?: string;
  // Sichtbarer Zeitbereich (epoch ms). Ohne Angabe skaliert die Achse auf die Daten.
  xMin?: number;
  xMax?: number;
}

// Wiederverwendbares Zeitreihendiagramm auf Basis von echarts (ohne Wrapper).
// Zeitachse in lokaler Zeit; die Datenpunkte kommen als [epoch_ms, wert].
// Mausrad/Schieberegler zoomen innerhalb des geladenen Zeitraums.
export default function TimeSeriesChart({ series, height = 320, yName, y2Name, xMin, xMax }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: "canvas" });
    chartRef.current = chart;

    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const textColor = dark ? "#e2e8f0" : "#0f172a";
    const lineColor = dark ? "#334155" : "#cbd5e1";
    const hasY2 = series.some((s) => s.yAxisIndex === 1);

    chart.setOption(
      {
        animation: false,
        grid: { left: 55, right: hasY2 ? 55 : 20, top: 30, bottom: 70 },
        tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
        legend: { textStyle: { color: textColor }, top: 0 },
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none" },
          {
            type: "slider",
            xAxisIndex: 0,
            filterMode: "none",
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
        series: series.map((s) => ({
          name: s.name,
          type: s.type ?? "line",
          showSymbol: false,
          smooth: false,
          connectNulls: false,
          yAxisIndex: s.yAxisIndex ?? 0,
          itemStyle: s.color ? { color: s.color } : undefined,
          lineStyle: s.color ? { color: s.color } : undefined,
          data: s.data,
        })),
      },
      { notMerge: true },
    );
  }, [series, yName, y2Name, xMin, xMax]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}
