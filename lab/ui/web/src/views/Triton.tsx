import type { ReactNode } from "react";
import { Badge, Loading } from "../components/ui";
import { Num } from "../components/Num";
import { useLive } from "../live";
import { cn } from "../lib/utils";
import { n0, n1, unixClock, unixTime } from "../format";
import type { PaperRecorder } from "../paperTypes";

// Triton One (design v1, "Triton One — iPhone"): what the live recorder (#182) receives and costs, from its own
// files (budget.json, segments/*/_segment.json, arrivals/, reconcile.json). The prepaid balance is entered by
// hand in ui-cache/triton.json until Triton offers an API. Sample data is marked as such.
interface TritonData {
  triton: {
    sample: boolean; recorder: PaperRecorder | null; month_usd: number; month_gb: number;
    // The month: spent so far plus the rate per day (from the month's first recorded minute, at most the cap).
    forecast_usd: number; rate_usd_per_day: number; since_unix: number | null;
    balance: { usd: number; as_of: string } | null;
    streams: { stream: string; gb: number; messages: number; per_min: number; dropped: boolean }[];
    latency: { p50: number; p90: number; n: number; edges_ms: number[]; hist: number[] } | null;
    reconcile: { ok: boolean; missing: number; extra: number; mismatched: number; segments_checked: number; segments_waiting: number; last_slot: number | null } | null;
    events: { unix: number; kind: string; text: string }[];
  } | null;
}
const usd = (v: number) => "$" + v.toLocaleString("nl-NL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={cn("rounded-2xl p-3.5 bg-surface border border-line min-w-0", className)}>{children}</section>;
}

export default function Triton() {
  const { data, error } = useLive<TritonData>("triton");
  if (error && !data) return <p className="text-warn">{error}</p>;
  if (!data) return <Loading what="Triton" />;
  const t = data.triton;
  if (!t || !t.recorder) return <p className="text-muted">Nog geen recorder-gegevens.</p>;
  const r = t.recorder, spentUsd = (r.spent_gb ?? 0) * r.usd_per_gb;
  const peak = Math.max(1, ...(t.latency?.hist ?? [1]));

  return (
    <div className="grid gap-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 text-[22px] font-extrabold tracking-[-0.4px]">Triton One</h1>
          <span className="text-xs text-muted">Live data van de recorder · {r.is_today ? "vandaag" : r.day} (UTC)</span>
        </div>
        {t.sample ? <Badge tone="warn">voorbeelddata</Badge>
          : r.stopped ? <Badge tone="sell">{r.unknown ? "gestopt: verbruik onbekend" : "gestopt: budget op"}</Badge> : r.level ? <Badge tone="warn">afgeschaald {r.level}</Badge> : <Badge tone="buy">alle streams</Badge>}
      </div>
      {r.unknown && (
        <p className="m-0 px-3 py-2.5 rounded-xl bg-sell-soft border border-sell-soft text-sell text-xs">
          De recorder is gestopt omdat het verbruik van vandaag niet vast te stellen was ({r.unknown}). Alleen de eigenaar start hem opnieuw, met het bedrag uit het Triton-dashboard (runbook §5).
        </p>
      )}
      {t.sample && <p className="m-0 px-3 py-2.5 rounded-xl bg-warn-soft border border-warn-line text-warn text-xs">De recorder draait nog niet: alles op deze pagina is een voorbeeld in het echte bestandsformaat. Zodra de recorder schrijft, staan hier de echte cijfers.</p>}

      <div className="grid gap-3.5 lg:grid-cols-2 lg:items-start">
        <div className="grid gap-3.5 min-w-0">
          <section className={cn("hero rounded-[20px] p-[18px] grid gap-2.5", t.sample && "!border-2 !border-warn")}>
            <div className="flex justify-between gap-2"><span className="text-[13px] text-fg-2 font-semibold">Kosten {r.is_today ? "vandaag" : r.day}</span>
              <span className="text-xs text-muted">cap {usd(r.budget_usd)} · auto-stop</span></div>
            <div className="flex items-baseline gap-3">
              <Num value={spentUsd} prefix="$" format={{ minimumFractionDigits: 2, maximumFractionDigits: 2 }} className="text-[40px] leading-none font-semibold tracking-[-1.5px]" />
              <span className="num text-base text-fg-2">{n1(r.spent_gb)} GB</span>
            </div>
            <div className="h-2 rounded bg-track overflow-hidden">
              <div className={cn("h-2", r.stopped ? "bg-sell-strong" : r.level ? "bg-warn" : "bg-accent")} style={{ width: `${Math.min(100, (100 * (r.spent_gb ?? 0)) / (r.cap_gb || 1))}%` }} />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="flex flex-col gap-0.5"><span className="text-[11px] text-muted">Deze maand</span><span className="num font-semibold">{usd(t.month_usd)}</span></div>
              <div className="flex flex-col gap-0.5" title={t.since_unix ? `Gerekend vanaf de eerste opname deze maand (${unixTime(t.since_unix)} UTC), hoogstens de daglimiet per dag.` : undefined}>
                <span className="text-[11px] text-muted">Prognose maand</span><span className="num font-semibold">{usd(t.forecast_usd)}</span>
                <span className="text-[10px] text-muted num">≈ {usd(t.rate_usd_per_day)} per dag</span></div>
              <div className="flex flex-col gap-0.5"><span className="text-[11px] text-muted">Tegoed</span>
                <span className="num font-semibold">{t.balance ? usd(t.balance.usd) : "–"}</span>
                <span className="text-[10px] text-muted">{t.balance ? `handmatig · ${t.balance.as_of}` : "nog niet ingevuld"}</span></div>
            </div>
          </section>

          <Panel>
            <div className="grid grid-cols-[minmax(0,1fr)_56px_64px] gap-2 text-[11px] text-muted pb-1.5 border-b border-line-soft">
              <span>Stream</span><span className="text-right">GB</span><span className="text-right">/min</span>
            </div>
            {t.streams.map((s) => (
              <div key={s.stream} className="grid grid-cols-[minmax(0,1fr)_56px_64px] gap-2 items-center py-2 border-b border-line-soft last:border-0 text-sm">
                <span className="flex items-center gap-2 min-w-0">
                  <span className={cn("w-2 h-2 rounded-full shrink-0", s.dropped ? "bg-[#4A5568]" : "bg-buy-strong")} aria-label={s.dropped ? "uitgezet" : "actief"} />
                  <span className="num text-xs truncate">{s.stream}</span>
                </span>
                <span className="num text-right">{s.gb >= 0.1 ? n1(s.gb) : s.gb.toLocaleString("nl-NL", { maximumFractionDigits: 3 })}</span>
                <span className="num text-right text-fg-2">{s.per_min >= 1000 ? `${n1(s.per_min / 1000)}k` : n0(s.per_min)}</span>
              </div>
            ))}
            {!!r.dropped.length && <p className="m-0 mt-1 text-[11px] text-warn">Uitgezet door het budget: {r.dropped.join(", ")}</p>}
          </Panel>
        </div>

        <div className="grid gap-3.5 min-w-0">
          <div className="grid grid-cols-2 gap-2.5">
            <Panel className="grid gap-2 content-start">
              <span className="text-[11px] text-muted">Vertraging na aanmaak</span>
              {t.latency ? (
                <>
                  <div className="flex items-end gap-[3px] h-14" role="img" aria-label={`Vertraging: mediaan ${Math.round(t.latency.p50)} ms`}>
                    {t.latency.hist.map((h, i) => <div key={i} className={cn("flex-1 rounded-sm", t.latency!.edges_ms[i] < t.latency!.p50 ? "bg-accent" : "bg-[#3A4256]")} style={{ height: `${Math.max(4, (100 * h) / peak)}%` }} />)}
                  </div>
                  <span className="num text-xs text-fg-2">p50 {Math.round(t.latency.p50)} · p90 {Math.round(t.latency.p90)} ms</span>
                </>
              ) : <span className="text-xs text-muted">nog geen aankomsttijden</span>}
            </Panel>
            <Panel className="grid gap-1 content-start">
              <span className="text-[11px] text-muted">Controle tegen OF1</span>
              {t.reconcile ? (
                <>
                  <span className={cn("num text-[15px] font-semibold", t.reconcile.ok ? "text-buy" : "text-sell")}>{t.reconcile.missing + t.reconcile.extra + t.reconcile.mismatched} afwijkingen</span>
                  <span className="text-[11px] text-fg-2">{n0(t.reconcile.segments_checked)} segmenten gecontroleerd · {n0(t.reconcile.segments_waiting)} wachten op OF1</span>
                </>
              ) : <span className="text-xs text-muted">nog niet gedraaid (reconcile.json)</span>}
            </Panel>
          </div>

          <Panel>
            <h2 className="m-0 mb-1 text-base font-extrabold">Gebeurtenissen</h2>
            {!t.events.length ? <p className="m-0 text-sm text-muted">Geen.</p> : t.events.map((e, i) => (
              <div key={i} className="flex gap-3 py-2 border-b border-line-soft last:border-0 text-sm">
                <span className="num text-xs text-muted shrink-0 pt-0.5" title={unixTime(e.unix)}>{unixClock(e.unix).slice(0, 5)}</span>
                <span className={e.kind === "reconnect" ? "text-fg-2" : "text-warn"}>{e.text}</span>
              </div>
            ))}
          </Panel>
        </div>
      </div>
    </div>
  );
}
