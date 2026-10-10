import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, CustomChart, LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, CustomChart, LineChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);

export const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Shared axis/tooltip styling from the page tokens, so charts follow light and dark mode. */
export function baseOption(): echarts.EChartsCoreOption {
  const muted = cssVar("--muted"), line = cssVar("--line"), fg = cssVar("--fg"), surface = cssVar("--surface");
  return {
    textStyle: { fontFamily: "IBM Plex Sans, system-ui, sans-serif", color: muted },
    grid: { left: 48, right: 12, top: 28, bottom: 28 },
    tooltip: { trigger: "axis", backgroundColor: surface, borderColor: line, textStyle: { color: fg }, axisPointer: { type: "shadow" } },
    xAxis: { axisLine: { lineStyle: { color: line } }, axisTick: { show: false }, axisLabel: { color: muted } },
    yAxis: { splitLine: { lineStyle: { color: line } }, axisLabel: { color: muted, formatter: (v: number) => v.toLocaleString("nl-NL") } },
    legend: { top: 0, left: 0, textStyle: { color: muted }, icon: "roundRect", itemWidth: 12, itemHeight: 12 },
  };
}

export default function EChart({ option, height = 240, label, onClick }: {
  option: echarts.EChartsCoreOption; height?: number; label: string; onClick?: (p: { dataIndex: number; seriesIndex: number }) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.setOption(option);
    if (onClick) chart.on("click", (p) => onClick(p as unknown as { dataIndex: number; seriesIndex: number }));
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const recolor = () => chart.setOption({ ...option, ...baseOption() }, { notMerge: false });
    mq.addEventListener("change", recolor);
    return () => { ro.disconnect(); mq.removeEventListener("change", recolor); chart.dispose(); };
  }, [option, onClick]);
  return <div ref={el} role="img" aria-label={label} style={{ height, width: "100%", minWidth: 0, overflow: "hidden" }} />;
}
