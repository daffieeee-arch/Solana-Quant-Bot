import { useEffect, useMemo, useState, type ReactNode } from "react";
import ReactGridLayout, { useContainerWidth, verticalCompactor, type Layout } from "react-grid-layout";
import { get, type TokenDetail as Detail } from "../api";
import PriceChart, { eventMarkers } from "../components/PriceChart";
import { Badge, Card, DexLink, Loading } from "../components/ui";
import { go } from "../route";
import { isoTime, label, n0, shortAddr, sol, unixClock } from "../format";

const ROW_H = 30;
const DEFAULT_LAYOUT: Layout = [
  { i: "chart", x: 0, y: 0, w: 8, h: 13, minW: 4, minH: 8 },
  { i: "info", x: 8, y: 0, w: 4, h: 13, minW: 3, minH: 6 },
  { i: "tape", x: 0, y: 13, w: 12, h: 11, minW: 4, minH: 5 },
];
const LAYOUT_KEY = "token-layout-v1";

function loadLayout(): Layout {
  try {
    const s = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "null");
    if (Array.isArray(s) && s.length === DEFAULT_LAYOUT.length) return s;
  } catch { /* ignore */ }
  return DEFAULT_LAYOUT;
}

function Panel({ title, children, drag }: { title: string; children: ReactNode; drag?: boolean }) {
  return (
    <Card className="h-full flex flex-col min-w-0 overflow-hidden">
      <div className={`px-3 py-2 border-b border-line text-xs uppercase tracking-wide text-muted flex items-center ${drag ? "panel-handle" : ""}`}>
        {title}{drag && <span className="ml-auto" aria-hidden="true">⠿</span>}
      </div>
      <div className="flex-1 min-h-0 overflow-auto">{children}</div>
    </Card>
  );
}

function Info({ d }: { d: Detail }) {
  const t = d.token;
  const rows: [string, ReactNode][] = [
    ["Mint", <DexLink mint={t.mint} className="font-mono text-xs break-all">{t.mint} ↗</DexLink>],
    ["Maker", <span className="font-mono text-xs">{shortAddr(t.creator)}</span>],
    ["Aangemaakt", isoTime(t.create_time) + " UTC"],
    ["Graduatie", t.graduated ? (t.grad_in_create_slot ? "in de aanmaak-slot (niet handelbaar)" : `slot ${n0(t.complete_slot)}`) : "nee"],
    ["Curve-trades", n0(t.curve_trades)],
    ["Kopers / verkopers SOL", `${sol(t.curve_buy_quote)} / ${sol(t.curve_sell_quote)}`],
    ["Max. mcap curve", sol(t.curve_max_mcap)],
    ["Pool-trades", n0(t.pool_trades)],
    ["Max. mcap pool", sol(t.pool_max_mcap)],
    ["Totaal volume", sol(t.total_volume_quote)],
  ];
  return (
    <dl className="m-0 px-3 py-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="m-0 tabular min-w-0">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Tape({ d }: { d: Detail }) {
  const supply = d.token.supply_tokens;
  return (
    <table className="w-full text-sm tabular">
      <thead className="sticky top-0 bg-surface">
        <tr className="text-muted text-xs">
          {["tijd (UTC)", "plek", "kant", "SOL", "mcap", "handelaar"].map((h, i) => (
            <th key={h} className={`px-3 py-1.5 font-medium ${i >= 3 && i <= 4 ? "text-right" : "text-left"}`}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {d.tape.map((r) => (
          <tr key={`${r.venue}-${r.slot}-${r.tx_index}-${r.quote}`} className="border-t border-line">
            <td className="px-3 py-1 whitespace-nowrap">{unixClock(r.time)}</td>
            <td className="px-3 py-1">{r.venue === "curve" ? "curve" : "pool"}</td>
            <td className={`px-3 py-1 ${r.side === "buy" ? "text-buy" : "text-sell"}`}>{r.side === "buy" ? "koop" : "verkoop"}</td>
            <td className="px-3 py-1 text-right">{sol(r.quote).replace(" SOL", "")}</td>
            <td className="px-3 py-1 text-right">{n0(r.price * supply)}</td>
            <td className="px-3 py-1 font-mono text-xs">{shortAddr(r.trader)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function TokenDetail({ mint }: { mint: string }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [layout, setLayout] = useState<Layout>(loadLayout);
  const { width, containerRef, mounted } = useContainerWidth();
  const wide = width >= 900;

  useEffect(() => {
    const ac = new AbortController();
    setD(null); setErr(null);
    get<Detail>("token", { mint }, ac.signal).then(setD).catch((e) => { if (!ac.signal.aborted) setErr(e.message); });
    return () => ac.abort();
  }, [mint]);

  const markers = useMemo(() => (d ? eventMarkers(d.events, Math.floor(Date.parse(d.token.create_time + "Z") / 1000)) : []), [d]);

  const header = (
    <div className="grid grid-cols-1 gap-1.5">
      <button type="button" className="text-sm text-accent justify-self-start" onClick={() => go("tokens")}>← Tokens</button>
      {d && (
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-2xl font-semibold m-0 tracking-tight break-all">{label(d.token.symbol, 24) || "?"}</h1>
          <span className="text-muted break-all">{label(d.token.name, 60)}</span>
          <span className="flex gap-1.5 flex-wrap">
            {d.token.graduated && <Badge tone={d.token.grad_in_create_slot ? "warn" : "accent"}>{d.token.grad_in_create_slot ? "graduatie in aanmaak-slot" : "gegradueerd"}</Badge>}
            {d.token.mayhem && <Badge tone="warn">mayhem</Badge>}
            {!d.token.sol_quote && <Badge tone="muted">geen SOL-quote</Badge>}
            {d.token.holder_reward && <Badge tone="muted">holder rewards</Badge>}
          </span>
          <DexLink mint={d.token.mint} className="text-sm">Bekijk op DexScreener ↗</DexLink>
        </div>
      )}
    </div>
  );

  if (err) return <div className="grid grid-cols-1 gap-3">{header}<p className="text-warn">{err}</p></div>;
  if (!d) return <div className="grid grid-cols-1 gap-3">{header}<Loading what="Token" /></div>;

  const chartTitle = "Marktwaarde per minuut (SOL, UTC) · ▲▼ trades ≥ 25 SOL";
  const chartEmpty = d.candles.length === 0;
  const panels = {
    chart: (h: number) => chartEmpty ? <p className="p-3 text-muted text-sm">Geen trades in de ontwikkeldata.</p>
      : <PriceChart candles={d.candles} supply={d.token.supply_tokens} markers={markers} height={h} />,
    info: <Info d={d} />,
    tape: <Tape d={d} />,
  };

  return (
    <div className="grid grid-cols-1 gap-3" ref={containerRef}>
      {header}
      {mounted && wide ? (
        <ReactGridLayout
          width={width}
          layout={layout}
          gridConfig={{ cols: 12, rowHeight: ROW_H, margin: [12, 12], containerPadding: [0, 0] }}
          dragConfig={{ enabled: true, handle: ".panel-handle" }}
          compactor={verticalCompactor}
          onLayoutChange={(l: Layout) => { setLayout(l); try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(l)); } catch { /* ignore */ } }}
        >
          <div key="chart"><Panel title={chartTitle} drag>{panels.chart(layoutHeight(layout, "chart") - 40)}</Panel></div>
          <div key="info"><Panel title="Gegevens" drag>{panels.info}</Panel></div>
          <div key="tape"><Panel title={`Laatste ${d.tape.length} trades`} drag>{panels.tape}</Panel></div>
        </ReactGridLayout>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          <Panel title={chartTitle}>{panels.chart(320)}</Panel>
          <Panel title="Gegevens">{panels.info}</Panel>
          <div style={{ maxHeight: 480 }} className="grid"><Panel title={`Laatste ${d.tape.length} trades`}>{panels.tape}</Panel></div>
        </div>
      )}
      <p className="text-xs text-muted m-0">Prijzen zijn spotprijzen uit de reserves (curve na de trade, pool vóór de trade). Alleen ontwikkeldata.</p>
    </div>
  );
}

function layoutHeight(layout: Layout, id: string) {
  const it = layout.find((x) => x.i === id);
  return it ? it.h * ROW_H + (it.h - 1) * 12 : 360;
}
