import ReactECharts from "echarts-for-react";

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
  markPoints?: { name: string; ts: number; color?: string }[];
}

// Wiederverwendbares Zeitreihendiagramm (ECharts). Zeitachse in lokaler Zeit.
export default function TimeSeriesChart({ series, height = 320, yName, y2Name }: Props) {
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const textColor = dark ? "#e2e8f0" : "#0f172a";
  const lineColor = dark ? "#334155" : "#cbd5e1";

  const hasY2 = series.some((s) => s.yAxisIndex === 1);

  const option = {
    animation: false,
    grid: { left: 55, right: hasY2 ? 55 : 20, top: 30, bottom: 45 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
    },
    legend: { textStyle: { color: textColor }, top: 0 },
    xAxis: {
      type: "time",
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
  };

  return <ReactECharts option={option} style={{ height }} notMerge={true} />;
}
