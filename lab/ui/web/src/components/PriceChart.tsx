import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, HistogramSeries, createChart, createSeriesMarkers,
  type CandlestickData, type HistogramData, type SeriesMarker, type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, MarketEvent } from "../api";
import { cssVar } from "./EChart";

export interface ChartMarker { time: number; kind: "create" | "graduation" | "buy" | "sell"; text: string }

/** Market cap (quote) per minute; curve and pool candles in the same minute are merged. */
export function mcapCandles(candles: Candle[], supply: number) {
  const byTime = new Map<number, { o: number; h: number; l: number; c: number; v: number; b: number }>();
  for (const c of candles) {
    const k = byTime.get(c.time);
    const o = c.open * supply, h = c.high * supply, l = c.low * supply, cl = c.close * supply;
    if (!k) byTime.set(c.time, { o, h, l, c: cl, v: c.volume_quote, b: c.buy_quote });
    else Object.assign(k, { h: Math.max(k.h, h), l: Math.min(k.l, l), c: c.venue === "pool" ? cl : k.c, o: c.venue === "curve" ? o : k.o, v: k.v + c.volume_quote, b: k.b + c.buy_quote });
  }
  return [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([t, k]) => ({ time: t, ...k }));
}

export function eventMarkers(events: MarketEvent[], createTime: number | null): ChartMarker[] {
  const out: ChartMarker[] = [];
  if (createTime != null) out.push({ time: createTime, kind: "create", text: "aanmaak" });
  for (const e of events) {
    if (e.time == null) continue;
    if (e.kind === "graduation") out.push({ time: e.time, kind: "graduation", text: "graduatie" });
    else if (e.kind === "large_buy") out.push({ time: e.time, kind: "buy", text: `${Math.round(e.sol ?? 0)}` });
    else if (e.kind === "large_sell") out.push({ time: e.time, kind: "sell", text: `${Math.round(e.sol ?? 0)}` });
  }
  return out;
}

const fmtMcap = (v: number) => (v >= 1000 ? (v / 1000).toLocaleString("nl-NL", { maximumFractionDigits: 1 }) + "k" : v.toLocaleString("nl-NL", { maximumFractionDigits: 1 }));

export default function PriceChart({ candles, supply, markers, height = 360 }: { candles: Candle[]; supply: number; markers: ChartMarker[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!el.current) return;
    const bars = mcapCandles(candles, supply);
    const up = cssVar("--buy"), down = cssVar("--sell"), muted = cssVar("--muted"), line = cssVar("--line");
    const chart = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: cssVar("--surface") }, textColor: muted, attributionLogo: true, fontFamily: "IBM Plex Sans, system-ui, sans-serif" },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line, timeVisible: true, secondsVisible: false },
      localization: { locale: "nl-NL", priceFormatter: fmtMcap },
    });
    const price = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false,
      priceFormat: { type: "custom", formatter: fmtMcap, minMove: 0.01 },
    });
    price.setData(bars.map((b): CandlestickData => ({ time: b.time as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c })));
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    vol.setData(bars.map((b): HistogramData => ({ time: b.time as UTCTimestamp, value: b.v, color: b.b >= b.v / 2 ? up + "66" : down + "66" })));

    // Snap markers onto the minute bars they fall in.
    const times = bars.map((b) => b.time);
    const snap = (t: number) => {
      const m = Math.floor(t / 60) * 60;
      let lo = 0, hi = times.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] < m) lo = mid + 1; else hi = mid; }
      return times[lo];
    };
    const ms: SeriesMarker<UTCTimestamp>[] = times.length ? markers.map((m) => ({
      time: snap(m.time) as UTCTimestamp,
      position: m.kind === "sell" ? ("aboveBar" as const) : ("belowBar" as const),
      color: m.kind === "buy" ? up : m.kind === "sell" ? down : cssVar("--accent"),
      shape: m.kind === "buy" ? ("arrowUp" as const) : m.kind === "sell" ? ("arrowDown" as const) : ("circle" as const),
      text: m.text,
    })).sort((a, b) => (a.time as number) - (b.time as number)) : [];
    createSeriesMarkers(price, ms);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, supply, markers]);

  return <div ref={el} style={{ height, width: "100%" }} role="img" aria-label="Koersgrafiek: marktwaarde per minuut met volume" />;
}
