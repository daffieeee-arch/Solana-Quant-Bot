import { useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import StrategyOverview from "./StrategyOverview";
import { get } from "../api";
import EChart, { baseOption, cssVar } from "../components/EChart";
import { Card, Chip, Loading, Section } from "../components/ui";
import { gridTheme } from "./Tokens";

interface SummaryRow {
  family: string; variant: string; size_sol: number; d: number; tau: string; scenario: string;
  n: number; filled: number; mean_ret: number | null; median_ret: number | null; win_rate: number | null;
  ci_lo: number | null; ci_hi: number | null; total_pnl_sol: number; mean_pnl_sol: number | null;
  n2_mean_ret: number | null; edge_vs_n2: number | null; hist_drift: number | null; fail_rate: number | null;
  verdict?: string | null; pnl_ci_lo?: number | null; pnl_ci_hi?: number | null;
  q?: number | null; segment?: string | null; regime?: string | null; farm?: string | null; overlay?: string | null;
}
type RunKey = "q" | "segment" | "regime" | "farm" | "overlay";
interface Curve { family: string; size_sol: number; points: [number, number | null, number][] }

const SCEN: Record<string, string> = { optimistic: "optimistisch", base: "basis", pessimistic: "pessimistisch" };
const pctf = (v: number | null | undefined, sign = true) =>
  v == null ? "–" : (sign && v > 0 ? "+" : "") + (v * 100).toLocaleString("nl-NL", { maximumFractionDigits: 2 }) + "%";
const N_MIN = 30;
// The run's own verdict (report.md): GO = 95%-CI of net SOL per trade above 0, ADJUST = mean above 0, STOP = mean ≤ 0.
// Only the run's gate sizes count (run.gate_sizes from its report.md); other sizes are informative. Unknown: all count.
const sizesTxt = (xs: number[]) => xs.map((x) => x.toLocaleString("nl-NL")).join(xs.length > 2 ? ", " : " en ") + " SOL";
const VERDICT: Record<string, string> = { GO: "GO", ADJUST: "bijstellen", STOP: "stop" };
// Run keys that split a family x variant into separate groups (lab/analytics/strategies.py GROUP). One value per key
// is shown at a time, so no group is counted twice: farm 'all' is organic + flagged together, overlay O7* are
// paired variants of the same triggers.
const KEY_TEXT: Record<string, Record<string, string>> = {
  segment: { curve: "curve", pool_normal: "normale pool", pool_reversed: "omgekeerde pool" },
  farm: { all: "alle handel", organic: "organisch", flagged: "farming" },
  overlay: { none: "geen overlay" },
  regime: { "320ms": "slots ~320 ms (1033–1036)", "270ms": "slots ~270 ms (vanaf 1037)" },
};
const keyText = (k: string, v: string) => KEY_TEXT[k]?.[v] ?? (k === "q" ? `q ${v}` : k === "regime" ? `regime ${v}` : v);
const PREFER: Record<string, string> = { farm: "all", overlay: "none" };
// 'n/a': the group does not split by this key (farm for M1, or for F7 on the curve); treated like no value.
const keyVal = (r: SummaryRow, k: RunKey) => (r[k] == null || r[k] === "n/a" ? null : String(r[k]));

export default function Strategies() {
  const [scenario, setScenario] = useState("base");
  const [data, setData] = useState<{ run: { run_id: string; config: Record<string, unknown>; gate_sizes?: number[] | null; note?: { holdout: string; points: string[] } | null } | null; summary: SummaryRow[]; keys?: RunKey[] } | null>(null);
  const [keySel, setKeySel] = useState<Partial<Record<RunKey, string>>>({});
  const [d, setD] = useState<number | null>(null);
  const [tau, setTau] = useState<string | null>(null);
  const [pick, setPick] = useState<SummaryRow | null>(null);
  const [curves, setCurves] = useState<Curve[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { setData(null); get<typeof data>("strategies", { scenario }).then(setData).catch((e) => setErr(e.message)); }, [scenario]);

  const ds = useMemo(() => [...new Set((data?.summary ?? []).map((r) => r.d))].sort((a, b) => a - b), [data]);
  const taus = useMemo(() => [...new Set((data?.summary ?? []).map((r) => r.tau))].sort(), [data]);
  const dSel = d ?? ds[0], tauSel = tau ?? taus[0];
  const keyOpts = useMemo(() => (data?.keys ?? []).map((k) => {
    const vals = [...new Set((data?.summary ?? []).map((r) => keyVal(r, k)).filter((v): v is string => v != null))].sort();
    return { k, vals, def: vals.includes(PREFER[k]) ? PREFER[k] : vals[0] };
  }).filter((o) => o.vals.length > 0), [data]);
  const keyNow = useMemo(() => Object.fromEntries(keyOpts.map((o) => [o.k, keySel[o.k] ?? o.def])) as Partial<Record<RunKey, string>>, [keyOpts, keySel]);
  // A row without a value for a key (e.g. farm on a family that does not split by it) always passes.
  const keyOk = (r: SummaryRow) => keyOpts.every((o) => { const v = keyVal(r, o.k); return v == null || v === keyNow[o.k]; });
  const rows = useMemo(() => (data?.summary ?? []).filter((r) => r.d === dSel && r.tau === tauSel && !r.family.startsWith("N2_") && keyOk(r)), [data, dSel, tauSel, keyNow]); // eslint-disable-line react-hooks/exhaustive-deps
  const ranked = useMemo(() => [...rows].filter((r) => r.mean_ret != null).sort((a, b) => (b.mean_ret ?? 0) - (a.mean_ret ?? 0)), [rows]);
  const sel = pick && rows.find((r) => r.family === pick.family && r.variant === pick.variant && r.size_sol === pick.size_sol) || ranked[0] || null;

  const selKeys = sel ? Object.fromEntries(keyOpts.map((o) => [o.k, keyVal(sel, o.k) ?? undefined])) : {};
  const selKeyStr = JSON.stringify(selKeys);
  useEffect(() => {
    if (!sel) return;
    setCurves(null);
    get<{ curves: Curve[] }>("strategy_curve", { family: sel.family, variant: sel.variant, d: sel.d, tau: sel.tau, scenario, ...selKeys })
      .then((r) => setCurves(r.curves)).catch((e) => setErr(e.message));
  }, [sel?.family, sel?.variant, sel?.d, sel?.tau, scenario, selKeyStr]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasVerdict = rows.some((r) => r.verdict != null);
  const gates = data?.run?.gate_sizes ?? null;
  const live = rows.filter((r) => !gates || gates.includes(r.size_sol));
  const goLive = live.filter((r) => r.verdict === "GO").length;

  const cols = useMemo<ColDef<SummaryRow>[]>(() => [
    { field: "family", headerName: "Familie", width: 90, pinned: "left" },
    { field: "variant", headerName: "Variant", width: 130 },
    { field: "size_sol", headerName: "SOL", width: 75, type: "rightAligned" },
    { field: "filled", headerName: "n", width: 80, type: "rightAligned", cellStyle: (p) => ((p.value ?? 0) < N_MIN ? { color: "var(--muted)" } : null) },
    { field: "verdict", headerName: "Oordeel", width: 110, hide: !hasVerdict,
      valueFormatter: (p) => (p.value == null ? "–" : VERDICT[p.value as string] ?? "te weinig trades"),
      cellStyle: (p) => (p.value === "GO" ? { color: "var(--buy)" } : p.value === "STOP" ? { color: "var(--sell)" } : null) },
    { field: "mean_ret", headerName: "Gem. na kosten", width: 130, type: "rightAligned", valueFormatter: (p) => pctf(p.value),
      cellStyle: (p) => ({ color: (p.value ?? 0) > 0 ? "var(--buy)" : "var(--sell)" }) },
    { colId: "ci", headerName: "95%-CI (per dag)", width: 170, type: "rightAligned", sortable: false,
      valueGetter: (p) => (p.data ? `${pctf(p.data.ci_lo)} … ${pctf(p.data.ci_hi)}` : "") },
    { field: "win_rate", headerName: "Win%", width: 85, type: "rightAligned", valueFormatter: (p) => pctf(p.value, false) },
    { field: "edge_vs_n2", headerName: "Edge vs N2", width: 115, type: "rightAligned", valueFormatter: (p) => pctf(p.value) },
    { field: "total_pnl_sol", headerName: "Totaal SOL", width: 115, type: "rightAligned",
      valueFormatter: (p) => (p.value == null ? "–" : (p.value > 0 ? "+" : "") + Number(p.value).toLocaleString("nl-NL", { maximumFractionDigits: 2 })) },
  ], [hasVerdict]);

  const ciChart = useMemo(() => {
    const b = baseOption() as Record<string, object>;
    const items = ranked.slice(0, 40);
    const labels = items.map((r) => `${r.family} ${r.variant} · ${r.size_sol}`);
    const accent = cssVar("--s1"), muted = cssVar("--muted"), n2c = cssVar("--s2");
    return {
      ...b, grid: { left: 150, right: 16, top: 24, bottom: 28 },
      tooltip: { trigger: "item", ...(b.tooltip as object), formatter: (p: { dataIndex: number }) => {
        const r = items[p.dataIndex];
        return `${labels[p.dataIndex]}<br/>gem. ${pctf(r.mean_ret)} · CI ${pctf(r.ci_lo)} … ${pctf(r.ci_hi)}<br/>N2 ${pctf(r.n2_mean_ret)} · n ${r.filled}`;
      } },
      legend: { ...(b.legend as object), data: ["gemiddelde met 95%-CI", "N2-schaduw"] },
      xAxis: { ...b.yAxis, type: "value", axisLabel: { color: muted, formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
      yAxis: { ...b.xAxis, type: "category", data: labels, inverse: true, axisLabel: { color: muted, fontSize: 11 } },
      series: [
        { name: "gemiddelde met 95%-CI", type: "custom", color: accent,
          renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[]; style: () => object }) => {
            const y = api.value(0), lo = api.coord([api.value(1), y]), hi = api.coord([api.value(2), y]), m = api.coord([api.value(3), y]);
            return { type: "group", children: [
              { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: { stroke: accent, lineWidth: 2 } },
              { type: "circle", shape: { cx: m[0], cy: m[1], r: 4 }, style: { fill: accent } },
            ] };
          },
          encode: { x: [1, 2, 3], y: 0 },
          data: items.map((r, i) => [i, r.ci_lo ?? r.mean_ret, r.ci_hi ?? r.mean_ret, r.mean_ret]) },
        { name: "N2-schaduw", type: "scatter", symbol: "diamond", symbolSize: 8, color: n2c,
          data: items.map((r) => [r.n2_mean_ret, labels[items.indexOf(r)]]) },
      ],
    };
  }, [ranked]);

  const curveChart = useMemo(() => {
    if (!curves || !sel) return null;
    const b = baseOption() as Record<string, object>;
    const own = curves.find((c) => c.family === sel.family && c.size_sol === sel.size_sol);
    const n2 = curves.find((c) => c.family === "N2_" + sel.family && c.size_sol === sel.size_sol);
    const series = [];
    if (own) series.push({ name: `${sel.family} ${sel.variant} · ${sel.size_sol} SOL`, type: "line", showSymbol: false, color: cssVar("--s1"), lineStyle: { width: 2 },
      data: own.points.map(([step, , pnl]) => [step, pnl]) });
    if (n2) {
      // Scale N2 to the strategy's trade count so both lines compare per trade.
      const nOwn = own?.points.at(-1)?.[0] ?? 0, nN2 = n2.points.at(-1)?.[0] ?? 0;
      const k = nOwn && nN2 ? nOwn / nN2 : 1;
      series.push({ name: "N2-schaduw (geschaald)", type: "line", showSymbol: false, color: cssVar("--muted"),
        lineStyle: { width: 2, type: "dashed" }, data: n2.points.map(([step, , pnl]) => [step * k, pnl * k]) });
    }
    return {
      ...b, grid: { left: 48, right: 16, top: 56, bottom: 36 },
      tooltip: { ...(b.tooltip as object), axisPointer: { type: "line" }, valueFormatter: (v: number) => `${v > 0 ? "+" : ""}${v.toLocaleString("nl-NL", { maximumFractionDigits: 2 })} SOL` },
      xAxis: { ...b.xAxis, type: "value", name: "trade nr.", nameLocation: "middle", nameGap: 24, min: 1 },
      yAxis: { ...b.yAxis, type: "value", axisLabel: { color: cssVar("--muted") } },
      series: series.map((s, i) => (i === 0 ? { ...s, markLine: { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: cssVar("--line") }, label: { show: false } } } : s)),
    };
  }, [curves, sel]);

  if (err) return <p className="text-warn">{err}</p>;
  if (!data) return <Loading what="Strategieën" />;
  if (!data.run) return <p className="text-muted">Nog geen afgeronde toernooirun.</p>;
  // Always on top: in-sample development data, not proof of profit; the run's note adds the hold-out plan.
  const store = (data.run.config.store ?? {}) as { slot_start?: number; slot_end_exclusive?: number };
  const epoch = (s?: number) => (s == null ? "?" : Math.floor(s / 432000));

  return (
    <div className="grid grid-cols-1 gap-6">
      <StrategyOverview />
      <div className="grid grid-cols-1 gap-1 pt-2 border-t border-line-soft">
        <h2 className="text-lg font-extrabold m-0 tracking-tight">Alle combinaties</h2>
        <p className="text-muted text-sm m-0">
          Toernooirun <span className="font-mono">{data.run.run_id}</span>, epochs {epoch(store.slot_start)}–{epoch(store.slot_end_exclusive == null ? undefined : store.slot_end_exclusive - 1)}, in-sample,
          kosten {SCEN[scenario]}. Rendement per trade na fees, netwerk, huur en mislukte transacties;
          N2 is dezelfde strategie met willekeurige instap. Onder n = {N_MIN} grijs: rapporteren, niet rangschikken.
        </p>
        {hasVerdict && (
          <p className={`text-sm m-0 mt-1 ${goLive ? "" : "text-warn"}`}>
            <b className="tabular">{goLive} van {live.length}</b> combinaties {gates ? `op de beoordeelde groottes (${sizesTxt(gates)}, ` : "("}kosten {SCEN[scenario]}{keyOpts.some((o) => o.vals.length > 1) ? ", deze selectie" : ""}) krijgen GO:
            netto winst per trade met het 95%-interval boven nul. {gates ? "Andere groottes zijn alleen informatief." : "Het rapport noemt geen beoordeelde groottes; alle groottes tellen mee."}
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        {Object.keys(SCEN).map((s) => <Chip key={s} active={scenario === s} onClick={() => setScenario(s)}>{SCEN[s]}</Chip>)}
        {ds.length > 1 && ds.map((x) => <Chip key={`d${x}`} active={dSel === x} onClick={() => setD(x)}>{`vertraging ${x}`}</Chip>)}
        {taus.length > 1 && taus.map((x) => <Chip key={x} active={tauSel === x} onClick={() => setTau(x)}>{`τ ${x}`}</Chip>)}
      </div>
      {keyOpts.filter((o) => o.vals.length > 1).map((o) => (
        <div key={o.k} className="flex flex-wrap gap-2 items-center -mt-4">
          {o.vals.map((v) => (
            <Chip key={v} active={keyNow[o.k] === v} onClick={() => { setKeySel((s) => ({ ...s, [o.k]: v })); setPick(null); }}>{keyText(o.k, v)}</Chip>
          ))}
        </div>
      ))}
      {sel && curveChart && (
        <Section title="P&L-curve" note={`Opgetelde winst/verlies in SOL na kosten voor ${sel.family} ${sel.variant}, ${sel.size_sol} SOL, tegen de N2-schaduw (willekeurige instap, geschaald naar hetzelfde aantal trades). Tik een rij in de tabel om te wisselen.`}>
          <Card className="p-3"><EChart option={curveChart} height={300} label="P&L-curve tegen N2" /></Card>
        </Section>
      )}
      <Section title="Tabel" note="Familie × variant × grootte, gesorteerd op gemiddeld rendement na kosten.">
        <div className="lab-grid" style={{ height: Math.min(560, 44 + rows.length * 40) }}>
          <AgGridReact<SummaryRow> theme={gridTheme} rowData={rows} columnDefs={cols}
            defaultColDef={{ sortable: true, resizable: true, suppressMovable: true }}
            onRowClicked={(e) => e.data && setPick(e.data)} rowStyle={{ cursor: "pointer" }}
            initialState={{ sort: { sortModel: [{ colId: "mean_ret", sort: "desc" }] } }} />
        </div>
      </Section>
      <Section title="Gemiddelde met 95%-betrouwbaarheidsinterval" note="Top 40 op gemiddeld rendement. Ruit = gemiddelde van de N2-schaduw.">
        <Card className="p-3 overflow-x-auto"><EChart option={ciChart} height={Math.max(260, ranked.slice(0, 40).length * 22 + 60)} label="Gemiddeld rendement met foutbalken" /></Card>
      </Section>
    </div>
  );
}
