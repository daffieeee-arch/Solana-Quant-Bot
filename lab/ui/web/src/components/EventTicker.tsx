import { useState } from "react";
import { DexLink } from "./ui";
import { Segmented } from "./Segmented";
import { useLive } from "../live";
import { cn } from "../lib/utils";
import { label, shortAddr, unixClock } from "../format";

// Live event ticker (backlog 1): the bot's newest signals, entries and exits as they land in the journal, each with
// why (lab/ui/paper.py events_view). Read-only; refetched on every live tick (SSE change or poll).
interface Ev {
  kind: "signal" | "entry" | "exit"; status: string; reason: string | null; slot: number; time_unix: number | null;
  variant: string; key: string; group: string | null; mint?: string | null; symbol?: string | null; name?: string | null;
  venue?: string | null; orientation?: string | null; depth?: number | null; farm?: string | null; farm_complete?: number | null;
  farm_window_complete?: number | null; latency_ms?: number | null; d_ms?: number | null;
  size_sol?: number; delay_slots?: number | null; ret?: number | null; pnl_sol?: number; held_slots?: number | null;
}
// Why a signal was skipped or an entry failed (lab/paper/engine.py _signal, _risk_block, the fill), in Dutch.
const WHY: Record<string, string> = {
  "farm flagged": "farming-pool (deze groep neemt alleen organisch)", "farm history incomplete": "farmerhistorie nog niet compleet",
  "farm window incomplete": "farm-venster van een uur nog niet gedekt", busy: "al een positie in deze pool",
  "kill switch": "noodschakelaar staat aan", "family not enabled": "familie staat uit", "pool cooldown": "afkoeltijd na een verlies in deze pool",
  "max open positions": "maximum aantal open posities", "max exposure": "maximale inzet bereikt", "daily loss limit": "daglimiet verlies bereikt",
  entry_slippage: "slippage te groot bij de instap",
};
const EXIT: Record<string, string> = {
  take_profit: "doel geraakt", stop_loss: "stop-loss", time: "tijdslimiet", outflow: "uitstroom", graduation: "migratie",
  window_end: "einde data", restart_lost: "verloren bij herstart", after_window_end: "einde data", end_of_data: "einde data",
};
const FILTERS = [{ value: "trades", label: "Trades" }, { value: "skipped", label: "Overgeslagen" }, { value: "all", label: "Alles" }] as const;
type Filter = (typeof FILTERS)[number]["value"];
const sol = (v: number, d = 3) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("nl-NL", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const pct = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toLocaleString("nl-NL", { maximumFractionDigits: 1 })}%`;

function detail(e: Ev): string {
  if (e.kind === "exit") return `${EXIT[e.reason ?? ""] ?? e.reason ?? "verkocht"}${e.held_slots != null ? ` · ${e.held_slots.toLocaleString("nl-NL")} slots` : ""}`;
  if (e.kind === "entry") return e.status === "filled" ? `gevuld${e.delay_slots != null ? ` in slot +${e.delay_slots}` : ""}` : WHY[e.reason ?? ""] ?? e.reason ?? "mislukt";
  const parts = [`F7-trigger${e.depth != null ? `, diepte ${(e.depth / 1e9).toLocaleString("nl-NL", { maximumFractionDigits: 1 })} SOL` : ""}`];
  if (e.venue) parts.push(e.venue === "pool" ? (e.orientation === "reversed" ? "pool (reversed)" : "pool") : "curve");
  if (e.farm && e.farm !== "n/a") parts.push(e.farm === "organic" ? "organisch" : "farming");
  if (e.farm_complete === 0) parts.push("farmerhistorie onvolledig");
  if (e.latency_ms != null) parts.push(`aankomst ${e.latency_ms} ms${e.d_ms != null ? ` (d ${e.d_ms} ms)` : ""}`);
  return parts.join(" · ");
}

function headline(e: Ev): { text: string; tone: string; icon: string } {
  if (e.kind === "exit") return { text: `Verkocht ${e.ret != null ? pct(e.ret) : ""}`, tone: (e.pnl_sol ?? 0) >= 0 ? "text-buy" : "text-sell", icon: "V" };
  if (e.kind === "entry") return e.status === "filled" ? { text: `Gekocht ${(e.size_sol ?? 0).toLocaleString("nl-NL", { minimumFractionDigits: 2 })} SOL`, tone: "text-fg", icon: "K" }
    : { text: "Instap mislukt", tone: "text-sell", icon: "!" };
  return e.status === "entered" ? { text: "Signaal: instap", tone: "text-accent-fg", icon: "S" }
    : { text: `Overgeslagen: ${WHY[e.reason ?? ""] ?? e.reason ?? "?"}`, tone: "text-muted", icon: "–" };
}

export default function EventTicker() {
  const { data } = useLive<{ events: Ev[]; journal: string | null }>("paper_events");
  const [filter, setFilter] = useState<Filter>("trades");
  if (!data?.journal) return null;
  const rows = data.events.filter((e) => filter === "all" || (filter === "skipped" ? e.kind === "signal" && e.status === "skipped" : !(e.kind === "signal" && e.status === "skipped")));
  return (
    <section className="rounded-2xl p-3.5 bg-surface border border-line min-w-0 lg:col-span-3">
      <div className="flex flex-wrap items-center gap-2 mb-1.5">
        <h2 className="m-0 text-[13px] font-extrabold">Live gebeurtenissen</h2>
        <span className="text-[11px] text-muted">signalen, instappen en exits zodra ze in het journaal staan · zonder N2</span>
        <span className="ml-auto"><Segmented label="Gebeurtenissen" value={filter} options={[...FILTERS]} onChange={setFilter} /></span>
      </div>
      {!rows.length ? <p className="m-0 text-sm text-muted">Nog niets.</p> : (
        <div className="max-h-[440px] overflow-y-auto divide-y divide-line-soft">
          {rows.map((e, i) => {
            const h = headline(e), sym = label(e.symbol, 16) || shortAddr(e.mint ?? e.key);
            return (
              <div key={`${e.kind}|${e.variant}|${e.key}|${e.slot}|${i}`} className="flex items-start gap-2.5 py-2 min-w-0">
                <span className="num text-[11px] text-muted w-[42px] shrink-0 pt-0.5">{e.time_unix ? unixClock(e.time_unix).slice(0, 5) : "–"}</span>
                <span className={cn("w-6 h-6 rounded-lg inline-flex items-center justify-center text-[11px] font-extrabold shrink-0",
                  e.kind === "exit" ? "bg-accent-soft text-accent-fg" : e.kind === "entry" ? (e.status === "filled" ? "bg-buy-soft text-buy" : "bg-sell-soft text-sell") : "bg-surface-2 text-fg-2")} aria-hidden="true">{h.icon}</span>
                <div className="flex flex-col gap-0.5 min-w-0 grow">
                  <div className="flex flex-wrap items-baseline gap-x-1.5 min-w-0">
                    <span className="font-bold text-sm" title={e.mint ?? e.key}>{sym}</span>
                    {e.group && <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-surface-2 text-fg-2 font-bold">{e.group === "K3" ? "K3" : `groep ${e.group}`}</span>}
                    <span className={cn("text-sm", h.tone)}>{h.text}</span>
                    {e.kind === "exit" && e.pnl_sol != null && <span className={cn("num text-xs", h.tone)}>{sol(e.pnl_sol)} SOL</span>}
                  </div>
                  <span className="text-[11px] text-muted truncate">{detail(e)} · {e.variant} · <DexLink mint={e.mint ?? e.key}>Dex ↗</DexLink></span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
