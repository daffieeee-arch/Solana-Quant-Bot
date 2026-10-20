import { cn } from "../lib/utils";
import { label, shortAddr } from "../format";
import type { PaperJournal, PaperRecorder } from "../paperTypes";

// Day report card (backlog 7): per group the day's trades, Σ, best and worst trade, and Triton's use today.
// Read-only; from the journal (lab/ui/paper.py journal_view day_card) and the recorder's budget.json.
const sol = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("nl-NL", { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`;
const pct = (v: number | null) => (v == null ? "" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toLocaleString("nl-NL", { maximumFractionDigits: 1 })}%`);
type Trade = PaperJournal["day_card"][number]["best"];

function TradeLine({ k, t }: { k: string; t: Trade }) {
  if (!t) return null;
  const sym = label(t.symbol, 12) || shortAddr(t.mint ?? t.key);
  return (
    <div className="flex justify-between gap-2 text-xs min-w-0">
      <span className="text-muted shrink-0">{k}</span>
      <span className="truncate" title={t.mint ?? t.key}><span className="font-bold">{sym}</span> <span className={cn("num", t.pnl_sol >= 0 ? "text-buy" : "text-sell")}>{pct(t.ret)} · {sol(t.pnl_sol)}</span></span>
    </div>
  );
}

export default function DayCard({ j, r }: { j: PaperJournal; r: PaperRecorder | null }) {
  if (!j.day_card?.length) return null;
  const usd = r && r.spent_gb != null ? r.spent_gb * r.usd_per_gb : null;
  return (
    <section className="rounded-2xl p-3.5 bg-surface border border-line min-w-0 grid gap-2.5">
      <div className="flex justify-between items-baseline gap-2">
        <h2 className="m-0 text-[13px] font-extrabold">Dagrapport {j.is_today ? "vandaag" : j.day}</h2>
        {usd != null && !r?.sample && <span className="num text-[11px] text-fg-2" title="Triton-verbruik vandaag (budget.json)">Triton ${usd.toFixed(2).replace(".", ",")} · {r!.spent_gb!.toLocaleString("nl-NL", { maximumFractionDigits: 1 })} GB</span>}
      </div>
      {j.day_card.map((c) => (
        <div key={c.group} className="grid gap-1 pt-2 border-t border-line-soft first:border-0 first:pt-0">
          <div className="flex justify-between items-baseline gap-2">
            <span className="text-xs font-bold">{c.group === "K3" ? "K3" : `Groep ${c.group}`}{c.group !== "K3" && <span className="text-muted font-normal"> · meting</span>}</span>
            <span className={cn("num text-sm font-semibold", c.net_sol > 0 ? "text-buy" : c.net_sol < 0 ? "text-sell" : "")}>{sol(c.net_sol)} SOL</span>
          </div>
          <span className="text-[11px] text-muted">{c.trades.toLocaleString("nl-NL")} trades · {c.wins.toLocaleString("nl-NL")} winst{c.entry_failed ? ` · ${c.entry_failed} mislukt` : ""}</span>
          <TradeLine k="Beste" t={c.best} />
          {c.trades > 1 && <TradeLine k="Slechtste" t={c.worst} />}
        </div>
      ))}
    </section>
  );
}
