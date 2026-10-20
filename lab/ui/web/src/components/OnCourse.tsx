import { cn } from "../lib/utils";
import type { PaperJournal } from "../paperTypes";

// "Op koers?" (backlog 3): the paper's net P&L per filled trade beside the backtest's expectation (LAB research's
// versioned file, pinned by sha256 in lab/ui/paper.py). Per trade, not per day: the paper's capacity limits make
// days incomparable. The primary band is out-of-sample; secondary bands are dev in-sample ("optimistisch").
// A comparison, never a decision: the stop rule and the forward test decide.
type OC = PaperJournal["on_course"];
type Cand = OC["candidates"][number];
const sol = (v: number | null | undefined) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("nl-NL", { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`);
const POSITION: Record<string, { text: string; tone: string }> = {
  inside: { text: "binnen de verwachting", tone: "text-buy" }, below: { text: "onder de verwachting", tone: "text-sell" },
  above: { text: "boven de verwachting", tone: "text-accent-fg" },
};

function Bands({ c }: { c: Cand }) {
  const vals = [c.paper.mean_sol, ...(c.paper.ci95_sol ?? []), c.primary.mean_sol, ...(c.primary.ci95_sol ?? []),
    ...c.secondary.flatMap((s) => [s.mean_sol, ...(s.ci95_sol ?? [])]), 0].filter((v): v is number => v != null);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo || 0.01) * 0.12, a = lo - pad, b = hi + pad;
  const x = (v: number) => `${(((v - a) / (b - a)) * 100).toFixed(2)}%`;
  const row = (label: string, band: number[] | null | undefined, mean: number | null | undefined, cls: string, dashed = false) => (
    <div className="grid grid-cols-[minmax(0,118px)_minmax(0,1fr)] items-center gap-2" key={label}>
      <span className="text-[10px] text-muted truncate" title={label}>{label}</span>
      <div className="relative h-4">
        <div className="absolute top-1/2 h-px w-full bg-line" />
        {band && <div className={cn("absolute top-1/2 -translate-y-1/2 h-2 rounded-sm", cls, dashed && "opacity-50")} style={{ left: x(band[0]), width: `calc(${x(band[1])} - ${x(band[0])})` }} />}
        {mean != null && <div className="absolute top-0 h-4 w-0.5 bg-fg" style={{ left: x(mean) }} />}
      </div>
    </div>
  );
  return (
    <div className="grid gap-1">
      {row("paper", c.paper.ci95_sol, c.paper.mean_sol, "bg-accent")}
      {c.primary.ci95_sol ? row("verwachting (out-of-sample)", c.primary.ci95_sol, c.primary.mean_sol, "bg-buy-strong")
        : <div className="text-[10px] text-muted">verwachting out-of-sample: {c.primary.status === "pending" ? "nog niet (komt uit de clean-runs)" : "–"}</div>}
      {c.secondary.map((s) => row(`optimistisch ${s.regime?.split(" ")[0] ?? ""}`, s.ci95_sol, s.mean_sol, "bg-warn", true))}
      <div className="grid grid-cols-[minmax(0,118px)_minmax(0,1fr)] gap-2"><span />
        <div className="relative h-3 text-[9px] text-muted num">
          <span className="absolute -translate-x-1/2" style={{ left: x(0) }}>0</span>
        </div>
      </div>
    </div>
  );
}

export default function OnCourse({ oc }: { oc: OC | undefined }) {
  if (!oc) return null;
  return (
    <section className="rounded-2xl p-3.5 bg-surface border border-line min-w-0 lg:col-span-3 grid gap-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="m-0 text-[13px] font-extrabold">Op koers?</h2>
        <span className="text-[11px] text-muted">netto SOL per gevulde trade na alle kosten, paper naast de verwachting uit de backtest · geen belofte, beslist niets</span>
      </div>
      {oc.error ? <p className="m-0 text-sm text-warn">{oc.error}</p> : !oc.candidates.length ? <p className="m-0 text-sm text-muted">Geen kandidaat uit het verwachtingsbestand draait in dit journaal.</p> : (
        <div className="grid gap-4 lg:grid-cols-3">
          {oc.candidates.map((c) => {
            const pos = c.position ? POSITION[c.position] : null;
            return (
              <div key={c.id} className="grid gap-2 min-w-0">
                <div className="flex justify-between items-baseline gap-2">
                  <span className="text-xs font-bold" title={c.name ?? ""}>{c.id} <span className="font-normal text-muted">{c.variant}{c.group && c.group !== "K3" ? ` · groep ${c.group}` : ""}</span></span>
                  <span className={cn("text-xs font-semibold", pos?.tone ?? "text-muted")}>
                    {pos ? pos.text : c.paper.trades < oc.min_trades ? `nog te weinig trades (${c.paper.trades} van ${oc.min_trades})` : c.primary.ci95_sol ? "" : "nog geen out-of-sample-verwachting"}
                  </span>
                </div>
                <Bands c={c} />
                <div className="text-[11px] text-fg-2 num">
                  paper {sol(c.paper.mean_sol)} SOL/trade · {c.paper.trades.toLocaleString("nl-NL")} trades, {c.paper.days} dagen{c.paper.ci95_sol ? ` · 95% ${sol(c.paper.ci95_sol[0])} … ${sol(c.paper.ci95_sol[1])}` : " · interval vanaf 5 dagen"}
                </div>
                {c.primary.ci95_sol && <div className="text-[11px] text-muted">verwachting {sol(c.primary.mean_sol)} [{sol(c.primary.ci95_sol[0])} … {sol(c.primary.ci95_sol[1])}] · {c.primary.trades?.toLocaleString("nl-NL")} trades · {c.primary.verdict}</div>}
                {!!c.caveats.length && (
                  <details className="text-[11px] text-muted"><summary className="cursor-pointer">kanttekeningen</summary>
                    <ul className="m-0 mt-1 pl-4 grid gap-0.5">{c.caveats.map((t, i) => <li key={i}>{t}</li>)}</ul>
                  </details>
                )}
              </div>
            );
          })}
        </div>
      )}
      {!oc.error && (
        <div className="grid gap-1 text-[10px] text-muted">
          {oc.regime_note && <p className="m-0">{oc.regime_note}</p>}
          {oc.rules.map((r, i) => <p key={i} className="m-0">{r}</p>)}
          <p className="m-0 num">verwachting {oc.version} · sha256 {oc.sha256?.slice(0, 12)}…</p>
        </div>
      )}
    </section>
  );
}
