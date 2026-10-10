import { useEffect, useMemo, useState } from "react";
import { get, type Row } from "../api";
import EChart, { baseOption, cssVar } from "../components/EChart";
import { Card, Loading, Section, Tile } from "../components/ui";
import { day, isoTime, mln, n0, pct } from "../format";

interface OverviewData {
  coverage: Row[];
  days: { day: string; tokens_created: number; mayhem_created: number; graduations: number; grad_in_create_slot: number }[];
  totals: { tokens: number; graduated: number; graduated_in_create_slot: number; curve_trades: number; pool_trades: number; median_curve_trades: number };
}

export default function Overview() {
  const [d, setD] = useState<OverviewData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get<OverviewData>("overview").then(setD).catch((e) => setErr(e.message)); }, []);

  const charts = useMemo(() => {
    if (!d) return null;
    const days = d.days.filter((x) => x.day);
    const first = days[0]?.day, last = days[days.length - 1]?.day;
    const labels = days.map((x) => day(x.day) + (x.day === first || x.day === last ? "*" : ""));
    const b = baseOption() as Record<string, object>;
    const s1 = cssVar("--s1"), s2 = cssVar("--s2");
    const bar = { type: "bar", barMaxWidth: 48, itemStyle: { borderRadius: [4, 4, 0, 0] } };
    const tokens = {
      ...b, legend: { show: false },
      xAxis: { ...b.xAxis, type: "category", data: labels },
      yAxis: { ...b.yAxis, type: "value" },
      series: [{ ...bar, name: "nieuwe tokens", color: s1, data: days.map((x) => x.tokens_created) }],
    };
    const grads = {
      ...b, grid: { ...(b.grid as object), top: 56 },
      xAxis: { ...b.xAxis, type: "category", data: labels },
      yAxis: { ...b.yAxis, type: "value" },
      series: [
        { ...bar, stack: "g", name: "na de aanmaak-slot (handelbaar)", color: s1, itemStyle: { borderRadius: 0, borderColor: cssVar("--surface"), borderWidth: 1 },
          data: days.map((x) => (x.graduations ?? 0) - (x.grad_in_create_slot ?? 0)) },
        { ...bar, stack: "g", name: "in de aanmaak-slot (niet handelbaar)", color: s2, data: days.map((x) => x.grad_in_create_slot ?? 0) },
      ],
    };
    return { tokens, grads };
  }, [d]);

  if (err) return <p className="text-warn">Overzicht niet beschikbaar: {err}</p>;
  if (!d || !charts) return <Loading what="Overzicht" />;
  const t = d.totals;
  const cov = d.coverage;

  return (
    <div className="grid grid-cols-1 gap-7">
      <div className="grid grid-cols-1 gap-1">
        <h1 className="text-2xl font-semibold m-0 tracking-tight">Overzicht</h1>
        <p className="text-muted m-0 text-sm">
          pump.fun en PumpSwap, ontwikkeldata epochs {String(cov[0]?.epoch)}–{String(cov[cov.length - 1]?.epoch)} ·{" "}
          {isoTime(cov[0]?.first_time as string)} tot {isoTime(cov[cov.length - 1]?.last_time as string)} UTC. De hold-out blijft dicht.
        </p>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-2.5">
        <Tile value={n0(t.tokens)} label="tokens aangemaakt" />
        <Tile value={n0(t.graduated)} label={`graduaties (${pct(t.graduated, t.tokens)})`} />
        <Tile value={n0(t.graduated_in_create_slot)} label={`in de aanmaak-slot (${pct(t.graduated_in_create_slot, t.graduated)}), niet handelbaar`} />
        <Tile value={mln(t.curve_trades)} label="trades op de bonding curve" />
        <Tile value={mln(t.pool_trades)} label="trades op PumpSwap" />
        <Tile value={n0(t.median_curve_trades)} label="curvetrades per token (mediaan)" />
      </div>
      <Section title="Nieuwe tokens per dag" note="UTC-dagen; * = dag maar deels in de data.">
        <Card className="p-3"><EChart option={charts.tokens} label="Nieuwe tokens per dag" /></Card>
      </Section>
      <Section title="Graduaties per dag" note="Graduaties in de aanmaak-slot zelf zijn voor ons nooit handelbaar.">
        <Card className="p-3"><EChart option={charts.grads} height={260} label="Graduaties per dag, gestapeld" /></Card>
      </Section>
      <Section title="Dekking per epoch" note="Eén epoch is 432.000 slots, ongeveer 32 uur.">
        <Card className="overflow-x-auto">
          <table className="w-full text-sm tabular">
            <thead>
              <tr className="text-muted text-xs uppercase tracking-wide">
                {["epoch", "van (UTC)", "tot (UTC)", "blokken", "lege slots", "nieuwe tokens", "graduaties", "curve-trades", "PumpSwap-trades"].map((h, i) => (
                  <th key={h} className={`px-3 py-2 font-medium whitespace-nowrap ${i < 3 ? "text-left" : "text-right"}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cov.map((r) => (
                <tr key={String(r.epoch)} className="border-t border-line">
                  <td className="px-3 py-2 font-mono">{String(r.epoch)}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{isoTime(r.first_time as string)}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{isoTime(r.last_time as string)}</td>
                  {["blocks", "skipped_slots", "tokens_created", "graduations", "curve_trades", "pool_trades"].map((k) => (
                    <td key={k} className="px-3 py-2 text-right">{n0(r[k])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </Section>
    </div>
  );
}
