import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, HistogramSeries, createChart, createSeriesMarkers,
  type CandlestickData, type HistogramData, type SeriesMarker, type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, MarketEvent } from "../api";
import { cssVar } from "./EChart";

// buy/sell: large market trades; entry/exit: the paper bot's own trades (exit: win = profit).
export interface ChartMarker { time: number; kind: "create" | "graduation" | "buy" | "sell" | "entry" | "exit"; text: string; win?: boolean }

/** Market cap (quote) per bucket (60 s = the source minutes); curve and pool candles in one bucket are merged. */
export function mcapCandles(candles: Candle[], supply: number, bucket = 60) {
  const byTime = new Map<number, { o: number; h: number; l: number; c: number; v: number; b: number }>();
  for (const c0 of [...candles].sort((a, b) => a.time - b.time)) {
    const c = { ...c0, time: Math.floor(c0.time / bucket) * bucket };
    const k = byTime.get(c.time);
    const o = c.open * supply, h = c.high * supply, l = c.low * supply, cl = c.close * supply;
    if (!k) byTime.set(c.time, { o, h, l, c: cl, v: c.volume_quote, b: c.buy_quote });
    else Object.assign(k, { h: Math.max(k.h, h), l: Math.min(k.l, l), c: cl, v: k.v + c.volume_quote, b: k.b + c.buy_quote });
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

const fmtPrice = (v: number) => (v === 0 ? "0" : Math.abs(v) >= 1 ? v.toLocaleString("nl-NL", { maximumFractionDigits: 3 })
  : v.toLocaleString("nl-NL", { maximumSignificantDigits: 3 }));
const fmtMcap = (v: number) => (v >= 1000 ? (v / 1000).toLocaleString("nl-NL", { maximumFractionDigits: 1 }) + "k" : v.toLocaleString("nl-NL", { maximumFractionDigits: 1 }));

export default function PriceChart({ candles, supply, markers, height = 360, unit = "mcap", bucket = 60, focus }: {
  candles: Candle[]; supply: number; markers: ChartMarker[]; height?: number; unit?: "mcap" | "price"; bucket?: number;
  focus?: [number, number]; // unix seconds: open the chart on this window (the bot's trades), the rest is a scroll away
}) {
  const el = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!el.current) return;
    const bars = mcapCandles(candles, unit === "price" ? 1 : supply, bucket);
    const fmt = unit === "price" ? fmtPrice : fmtMcap;
    const up = cssVar("--buy-strong"), down = cssVar("--sell-strong"), muted = cssVar("--muted"), line = cssVar("--line-soft");
    const chart = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: cssVar("--chart") }, textColor: muted, attributionLogo: true, fontFamily: "JetBrains Mono Variable, ui-monospace, monospace" },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line, timeVisible: true, secondsVisible: false },
      localization: { locale: "nl-NL", priceFormatter: fmt },
    });
    const price = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false,
      priceFormat: { type: "custom", formatter: fmt, minMove: unit === "price" ? 1e-12 : 0.01 },
    });
    price.setData(bars.map((b): CandlestickData => ({ time: b.time as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c })));
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    vol.setData(bars.map((b): HistogramData => ({ time: b.time as UTCTimestamp, value: b.v, color: b.b >= b.v / 2 ? up + "66" : down + "66" })));

    // Snap markers onto the bars they fall in.
    const times = bars.map((b) => b.time);
    const snap = (t: number) => {
      const m = Math.floor(t / bucket) * bucket;
      let lo = 0, hi = times.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] < m) lo = mid + 1; else hi = mid; }
      return times[lo];
    };
    const accent = cssVar("--accent");
    const ms: SeriesMarker<UTCTimestamp>[] = times.length ? markers.map((m) => ({
      time: snap(m.time) as UTCTimestamp,
      position: m.kind === "sell" || m.kind === "exit" ? ("aboveBar" as const) : ("belowBar" as const),
      color: m.kind === "buy" ? up : m.kind === "sell" ? down : m.kind === "exit" ? (m.win ? up : down) : accent,
      shape: m.kind === "buy" || m.kind === "entry" ? ("arrowUp" as const) : m.kind === "sell" || m.kind === "exit" ? ("arrowDown" as const) : ("circle" as const),
      text: m.text,
    })).sort((a, b) => (a.time as number) - (b.time as number)) : [];
    createSeriesMarkers(price, ms);
    chart.timeScale().fitContent();
    if (focus && times.length) {
      const pad = Math.max(bucket * 8, (focus[1] - focus[0]) * 0.25);
      const from = Math.max(times[0], focus[0] - pad), to = Math.min(times[times.length - 1], focus[1] + pad);
      if (to > from) chart.timeScale().setVisibleRange({ from: from as UTCTimestamp, to: to as UTCTimestamp });
    }
    return () => chart.remove();
  }, [candles, supply, markers, unit, bucket, focus?.[0], focus?.[1]]); // eslint-disable-line react-hooks/exhaustive-deps

  return <div ref={el} style={{ height, width: "100%" }} role="img" aria-label={unit === "price" ? "Koersgrafiek: prijs met volume" : "Koersgrafiek: marktwaarde met volume"} />;
}
