import { useEffect, useState } from "react";
import { get } from "../api";
import { Loading } from "../components/ui";
import { cn } from "../lib/utils";
import { mln } from "../format";

// Top of the Strategieën tab (design v1, "Strategieën — iPhone"): the in-sample banner, the pre-registered hold-out
// candidates (numbers from the frozen pre-registration, via lab/ui/notes/<run>.json) and the best variant per family.
interface Candidate { id: string; name: string; variant: string; farm: string; role: string; ev: number | null; ci_lo: number | null; ci_hi: number | null; d1_q09: number | null; d2_q05: number | null; d3_q09: number | null }
// The one-time hold-out evaluation (holdout_eval.json): the pre-registered main measure on the decision group.
interface HoldoutResult { source: string; group: string; pick: string | null; highest_mean: string | null;
  candidates: Record<string, { point: string; mean: number | null; lo: number | null; hi: number | null; filled: number | null }> }
interface Overview {
  run: { run_id: string; positions: number | null; store: { slot_start?: number; slot_end_exclusive?: number };
    note: { holdout: string; points: string[]; phase: string; candidates: Candidate[]; candidates_source: string;
      holdout_result: HoldoutResult | null; owner_decision: string; owner_decision_source: string; holdout_report: string } | null } | null;
  regime: string | null; regimes: string[];
  families: { family: string; segment: string | null; variant: string; mean_ret: number; ci_lo: number | null; ci_hi: number | null; n: number }[];
}
const SEG: Record<string, string> = { curve: "curve", pool_reversed: "reversed", pool_normal: "pool" };
const REGIME: Record<string, string> = { "270ms": "~270 ms (vanaf 1037)", "320ms": "~320 ms (1033–1036)" };
const pctS = (v: number | null | undefined, digits = 2) =>
  v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("nl-NL", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;

function CiBar({ ev, ciLo, ciHi, lo, hi, band }: { ev: number | null; ciLo: number | null; ciHi: number | null; lo: number; hi: number; band: string }) {
  const x = (v: number) => `${(((v - lo) / (hi - lo)) * 100).toFixed(1)}%`;
  return (
    <div className="relative h-[18px] grow" aria-hidden="true">
      <div className="absolute inset-x-0 top-2 h-0.5 bg-track" />
      <div className="absolute top-0.5 w-px h-3.5 bg-[#4A5568]" style={{ left: x(0) }} />
      {ciLo != null && ciHi != null && (
        <div className="absolute top-[5px] h-2 rounded" style={{ left: x(ciLo), width: `calc(${x(ciHi)} - ${x(ciLo)})`, background: band }} />
      )}
      {ev != null && <div className="absolute top-[3px] w-3 h-3 -ml-1.5 rounded-full bg-fg border-2 border-bg" style={{ left: x(ev) }} />}
    </div>
  );
}

function Pill({ v, label }: { v: number | null; label: string }) {
  const up = (v ?? 0) > 0;
  return <span className={cn("num text-[11px] px-2 py-1 rounded-lg", up ? "bg-buy-soft text-buy" : "bg-sell-soft text-sell")}>{label} {pctS(v)}</span>;
}

export default function StrategyOverview() {
  const [data, setData] = useState<Overview | null>(null);
  const [regime, setRegime] = useState<string | undefined>(undefined);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get<Overview>("strategy_overview", { regime }).then(setData).catch((e) => setErr(e.message)); }, [regime]);
  if (err) return <p className="text-warn">{err}</p>;
  if (!data) return <Loading what="Overzicht" />;
  if (!data.run) return <p className="text-muted">Nog geen afgeronde toernooirun.</p>;
  const { run } = data, note = run.note;
  const ep = (s?: number) => (s == null ? "?" : Math.floor(s / 432000));
  const cands = note?.candidates ?? [];
  const ho = note?.holdout_result ?? null;
  const hoOf = (id: string) => ho?.candidates[id] ?? null;
  const lo = Math.min(-0.5, ...cands.flatMap((c) => [c.ci_lo ?? 0, hoOf(c.id)?.lo ?? 0]));
  const hi = Math.max(2, ...cands.flatMap((c) => [c.ci_hi ?? 0, hoOf(c.id)?.hi ?? 0]));
  const failed = !!ho && ho.pick == null;
  const maxAbs = Math.max(1e-9, ...data.families.map((f) => Math.abs(f.mean_ret * 100)));

  return (
    <div className="grid gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5 min-w-0">
          <h1 className="m-0 text-[22px] font-extrabold tracking-[-0.4px]">Strategieën</h1>
          <span className="text-xs text-muted">Toernooi {run.run_id.replace(/-\d+-\d+$/, "")}{run.positions ? ` · ${mln(run.positions)} posities` : ""}</span>
        </div>
        {note?.phase && (
          <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-bold whitespace-nowrap",
            failed ? "bg-sell-soft text-sell" : ho ? "bg-buy-soft text-buy" : "bg-accent-soft text-accent-fg")}>
            <span className={cn("w-[7px] h-[7px] rounded-full", failed ? "bg-sell-strong" : ho ? "bg-buy-strong" : "bg-accent")} />{note.phase}
          </span>
        )}
      </div>

      <div className="px-3 py-2.5 rounded-xl bg-warn-soft border border-warn-line text-warn text-xs leading-relaxed grid gap-1">
        <p className="m-0">Ontwikkeldata {ep(run.store.slot_start)}–{ep(run.store.slot_end_exclusive == null ? undefined : run.store.slot_end_exclusive - 1)}, in-sample, basiskosten. <b>Dit is geen winstbewijs.</b>{note?.holdout ? ` ${note.holdout}` : ""}</p>
        {!!note?.points.length && <ul className="m-0 pl-4 list-disc">{note.points.map((p) => <li key={p}>{p}</li>)}</ul>}
      </div>

      {note?.owner_decision && (
        <div className="px-3 py-2.5 rounded-xl bg-accent-soft border border-accent-line text-xs leading-relaxed grid gap-0.5">
          <span className="font-bold text-accent-fg">{note.owner_decision}</span>
          {note.owner_decision_source && <span className="text-muted">Bron: {note.owner_decision_source}{note.holdout_report ? `; uitslag in ${note.holdout_report}` : ""}.</span>}
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2 lg:items-start">
        {!!cands.length && (
          <section className="grid gap-2.5 min-w-0">
            <h2 className="m-0 text-[15px] font-extrabold">Kandidaten · 0,5 SOL</h2>
            {cands.map((c, i) => (
              <div key={c.id} className={cn("rounded-2xl p-3.5 bg-surface border grid gap-2.5", i === 0 ? "border-accent-line" : "border-line")}>
                <div className="flex items-center gap-2 min-w-0">
                  <span className={cn("num text-xs font-semibold px-[7px] py-[3px] rounded-md", i === 0 ? "bg-accent text-on-accent" : "bg-surface-2 text-[#CBD3E1]")}>{c.id}</span>
                  <span className="font-bold text-sm truncate">{c.name}</span>
                  <span className="ml-auto text-[11px] text-muted">dev</span>
                  <span className={cn("num text-lg font-semibold", ho ? "text-fg-2" : (c.ev ?? 0) > 0 ? "text-buy" : "text-sell")}>{pctS(c.ev)}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-[11px] text-muted">dev</span>
                  <CiBar ev={c.ev} ciLo={c.ci_lo} ciHi={c.ci_hi} lo={lo} hi={hi}
                    band={i === 0 ? "color-mix(in srgb, var(--accent) 55%, transparent)" : (c.ci_lo ?? 0) > 0 ? "color-mix(in srgb, var(--accent) 35%, transparent)" : "rgba(142,153,173,0.35)"} />
                </div>
                <div className="flex justify-between text-[11px] text-muted"><span className="num">CI {pctS(c.ci_lo)} … {pctS(c.ci_hi)}</span><span>{c.role}</span></div>
                {hoOf(c.id) && (() => {
                  const h = hoOf(c.id)!;
                  return (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="w-16 shrink-0 text-[11px] text-fg-2 font-semibold">hold-out</span>
                        <CiBar ev={h.mean} ciLo={h.lo} ciHi={h.hi} lo={lo} hi={hi}
                          band={(h.lo ?? 0) > 0 ? "color-mix(in srgb, var(--buy-strong) 55%, transparent)" : "color-mix(in srgb, var(--sell-strong) 45%, transparent)"} />
                      </div>
                      <div className="flex justify-between gap-2 text-[11px]">
                        <span className={cn("num font-semibold", (h.lo ?? 0) > 0 ? "text-buy" : (h.hi ?? 0) < 0 ? "text-sell" : (h.mean ?? 0) < 0 ? "text-sell" : "text-fg-2")}>{pctS(h.mean)} <span className="text-muted font-normal">[{pctS(h.lo)} … {pctS(h.hi)}]</span></span>
                        <span className="text-muted num">{ho!.group} · {h.filled?.toLocaleString("nl-NL")} trades</span>
                      </div>
                    </>
                  );
                })()}
                <div className="flex gap-1.5 flex-wrap">
                  <Pill v={c.d1_q09} label="d1" /><Pill v={c.d2_q05} label="d2" /><Pill v={c.d3_q09} label="d3" />
                </div>
              </div>
            ))}
            <p className="m-0 text-[11px] text-muted">Per trade, na kosten. Hoofdmaat: de slechtste van d2 (q 0,5) en d1 (q 0,9); d3 (q 0,9) is de robuustheidstoets. Bron dev: {note?.candidates_source}.{ho ? ` Bron hold-out: ${ho.source} (eenmalige test, epochs 1047–1051, deel ${ho.group}).` : ""}</p>
          </section>
        )}

        <section className="rounded-2xl p-3.5 bg-surface border border-line grid gap-2 min-w-0">
          <div className="flex justify-between items-baseline gap-2 flex-wrap">
            <span className="text-[13px] font-extrabold">Families · beste variant</span>
            <span className="text-[11px] text-muted">d=1 · 0,5 SOL · %/trade</span>
          </div>
          {data.regimes.length > 1 && (
            <div className="flex gap-1.5 flex-wrap">
              {data.regimes.map((r) => (
                <button key={r} type="button" onClick={() => setRegime(r)} aria-pressed={data.regime === r}
                  className={cn("min-h-8 px-2.5 rounded-lg border text-[11px] font-semibold", data.regime === r ? "bg-accent-soft text-accent-fg border-accent-line" : "border-track text-fg-2")}>
                  {REGIME[r] ?? r}
                </button>
              ))}
            </div>
          )}
          {data.families.map((f) => {
            const v = f.mean_ret * 100, pos = v >= 0;
            const w = Math.min((Math.abs(v) / maxAbs) * 60 + 2, pos ? 58 : 40);
            return (
              <div key={f.family + f.segment} className="grid grid-cols-[96px_minmax(0,1fr)_58px] gap-2 items-center">
                <span className="text-xs text-fg-2 font-semibold truncate" title={f.variant}>{f.family} {SEG[f.segment ?? ""] ?? f.segment ?? ""}</span>
                <div className="relative h-2 rounded bg-[#1A2131] overflow-hidden">
                  <div className={cn("absolute top-0 h-2 rounded", pos ? "bg-buy-strong" : "bg-[#C2475A]")} style={{ left: pos ? "40%" : `${40 - w}%`, width: `${w}%` }} />
                </div>
                <span className={cn("num text-xs text-right", pos ? "text-buy" : "text-sell")}>{pctS(v)}</span>
              </div>
            );
          })}
          <p className="m-0 text-[11px] text-muted">Per familie en segment de variant met het hoogste gemiddelde (alle handel, geen overlay, q 0,5, ≥ 200 trades). Gemiddelde, geen bewijs: zie het interval in de tabel hieronder.</p>
        </section>
      </div>
    </div>
  );
}
