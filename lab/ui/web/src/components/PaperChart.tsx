import { useEffect, useMemo, useState } from "react";
import { get, type Candle } from "../api";
import { label, shortAddr } from "../format";
import { cn } from "../lib/utils";
import { Change } from "./Num";
import PriceChart, { mcapCandles, type ChartMarker } from "./PriceChart";
import { DexLink, SolscanLink } from "./ui";

// One paper position's chart (design v1, dashboard): candles of its token from the development data, with the
// bot's own entries ("in") and exits ("uit ±x%") as markers. Tokens without a pump.fun history (most reversed
// pools) get price candles straight from the store's pool trades.
interface ChartData {
  key: string; mint?: string | null; symbol?: string | null; name?: string | null; venue: string | null; orientation: string | null;
  trades: { family: string; variant: string; side: "entry" | "exit"; slot: number; time: number | null; ret?: number | null; open?: boolean }[];
  token: { supply_tokens: number } | null; candles: Candle[]; unit: "mcap" | "price";
}
const FRAMES = [{ s: 60, label: "1m" }, { s: 300, label: "5m" }, { s: 900, label: "15m" }, { s: 3600, label: "1u" }];
const fmtVal = (v: number, unit: "mcap" | "price") => unit === "price"
  ? v.toLocaleString("nl-NL", { maximumSignificantDigits: 3 })
  : (v >= 1000 ? (v / 1000).toLocaleString("nl-NL", { maximumFractionDigits: 1 }) + "k" : v.toLocaleString("nl-NL", { maximumFractionDigits: 1 })) + " SOL";

export default function PaperChart({ k, height = 320, compact = false }: { k: string; height?: number; compact?: boolean }) {
  const [d, setD] = useState<ChartData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [bucket, setBucket] = useState(60);
  useEffect(() => {
    setD(null); setErr(null);
    const ac = new AbortController();
    get<ChartData>("paper_chart", { key: k }, ac.signal).then(setD).catch((e) => e.name !== "AbortError" && setErr(e.message));
    return () => ac.abort();
  }, [k]);

  // Arrows for every trade; labels only while they stay readable (a busy token gets bare arrows).
  const own = useMemo(() => (d?.trades ?? []).filter((t) => t.time != null && !t.family.startsWith("N2_")), [d]);
  const markers = useMemo<ChartMarker[]>(() => {
    const words = own.length <= 12;
    return own.map((t) => (t.side === "entry" ? { time: t.time!, kind: "entry", text: words ? "in" : "" }
      : { time: t.time!, kind: "exit", win: (t.ret ?? 0) > 0,
          text: !words || t.ret == null ? "" : `uit ${t.ret > 0 ? "+" : "−"}${Math.abs(t.ret * 100).toLocaleString("nl-NL", { maximumFractionDigits: 1 })}%` }));
  }, [own]);
  const focus = useMemo<[number, number] | undefined>(() => (own.length ? [Math.min(...own.map((t) => t.time!)), Math.max(...own.map((t) => t.time!))] : undefined), [own]);
  const bars = useMemo(() => (d ? mcapCandles(d.candles, d.unit === "price" ? 1 : d.token?.supply_tokens ?? 1, bucket) : []), [d, bucket]);

  if (err) return <p className="text-sm text-muted m-0">Geen grafiek: {err}</p>;
  if (!d) return <div style={{ height }} className="rounded-xl bg-chart animate-pulse" />;
  const sym = label(d.symbol, 16) || shortAddr(d.mint ?? d.key);
  const kind = d.venue === "pool" ? (d.orientation === "reversed" ? "PumpSwap · reversed" : "PumpSwap") : "pump.fun-curve";
  const last = bars.length ? bars[bars.length - 1].c : null, first = bars.length ? bars[0].o : null;
  const change = last != null && first ? ((last - first) / first) * 100 : null;

  return (
    <div className="grid gap-3 min-w-0">
      {!compact && (
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="flex gap-2 items-center flex-wrap">
              <span className="font-extrabold text-lg" title={d.mint ?? d.key}>{sym} / SOL</span>
              {label(d.name, 40) && <span className="text-sm text-fg-2 truncate max-w-[220px]" title={d.mint ?? d.key}>{label(d.name, 40)}</span>}
              <span className="text-[11px] px-[7px] py-[3px] rounded-md bg-surface-2 text-fg-2 font-bold">{kind}</span>
              <DexLink mint={d.mint ?? d.key}>Dex ↗</DexLink>
              <SolscanLink mint={d.mint ?? d.key} />
            </div>
            <span className="text-xs text-muted">{d.unit === "price" ? "Prijs" : "Marktwaarde"} per {FRAMES.find((f) => f.s === bucket)?.label} · <span className="text-accent-fg">▲</span> instap, <span className="text-buy">▼</span>/<span className="text-sell">▼</span> uitstap van de bot ({own.filter((t) => t.side === "exit").length}×)</span>
          </div>
          {last != null && (
            <div className="ml-auto flex items-baseline gap-2.5">
              <span className="num text-[26px] font-semibold">{fmtVal(last, d.unit)}</span>
              <Change value={change} />
            </div>
          )}
        </div>
      )}
      <div className="flex gap-1.5 flex-wrap" role="group" aria-label="Tijdvak">
        {FRAMES.map((f) => (
          <button key={f.s} type="button" onClick={() => setBucket(f.s)} aria-pressed={bucket === f.s}
            className={cn("min-h-9 px-3 rounded-lg border num text-xs font-semibold",
              bucket === f.s ? "bg-accent-soft border-accent-line text-accent-fg" : "border-track text-fg-2")}>
            {f.label}
          </button>
        ))}
      </div>
      {d.candles.length
        ? <div className="rounded-xl overflow-hidden"><PriceChart candles={d.candles} supply={d.token?.supply_tokens ?? 1} unit={d.unit} bucket={bucket} markers={markers} focus={focus} height={height} /></div>
        : <p className="text-sm text-muted m-0">Geen koersdata voor deze token in de ontwikkeldata (live koersen komen met de recorder).</p>}
    </div>
  );
}
