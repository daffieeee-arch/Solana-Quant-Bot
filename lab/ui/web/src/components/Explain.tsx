import { useEffect, useState, type ReactNode } from "react";
import { get } from "../api";
import { cn } from "../lib/utils";
import { compactPrice, shortAddr, unixClock } from "../format";
import NoteBox from "./NoteBox";
import type { PaperPosition } from "../paperTypes";

// "Verklaar deze trade" (backlog 2): why the bot entered (the F7 trigger, the farm label and farmer history), what
// the entry cost against the market (the pool's price at the signal and just before the entry, from the recorder,
// against our fill with its fees and impact), and how it ended (reason, slots held, the peak). Read-only
// (lab/ui/paper.py explain).
interface X {
  signal: { status: string; reason: string | null; venue: string | null; orientation: string | null; depth_sol: number; trigger_trader: string | null;
    farm: string | null; farm_complete: number | null; farm_window_complete: number | null; latency_ms: number | null; d_ms: number | null } | null;
  entry: { status: string; reason: string | null; slot: number; time_unix: number | null; delay_slots: number | null; size_sol: number;
    price_at_signal: number | null; price_before_entry: number | null; fill_price: number | null; drift_pct: number | null; fill_vs_market_pct: number | null } | null;
  exit: { reason: string; slot: number; time_unix: number | null; held_slots: number | null; fill_price: number | null; ret: number | null;
    pnl_sol: number; attempts: number | null; peak_ret: number | null; peak_slots: number | null } | null;
  open: { mark_pnl_sol: number | null } | null;
}
const EXIT: Record<string, string> = {
  take_profit: "doel geraakt", stop_loss: "stop-loss", time: "tijdslimiet", outflow: "uitstroom", graduation: "migratie",
  window_end: "einde data", restart_lost: "verloren bij herstart", after_window_end: "einde data", end_of_data: "einde data",
};
const px = (v: number | null | undefined) => (v == null ? "–" : compactPrice(v));
const pct = (v: number | null | undefined, d = 2) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("nl-NL", { maximumFractionDigits: d })}%`);

function Row({ k, children }: { k: string; children: ReactNode }) {
  return <div className="flex justify-between gap-3 py-1 border-b border-line-soft last:border-0 text-xs min-w-0"><span className="text-muted shrink-0">{k}</span><span className="num text-right truncate">{children}</span></div>;
}

export default function Explain({ p }: { p: PaperPosition | null | undefined }) {
  const [x, setX] = useState<X | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const t = p?.signal_slot;
  useEffect(() => {
    setX(null);
    setErr(null);
    if (!p || t == null) return;
    const ac = new AbortController();
    get<X>("paper_explain", { key: p.key, variant: p.variant, t }, ac.signal).then(setX).catch((e) => e.name !== "AbortError" && setErr(e.message));
    return () => ac.abort();
  }, [p?.key, p?.variant, t]);
  if (!p || t == null) return null;
  if (err) return <p className="m-0 text-xs text-muted">Geen verklaring: {err}</p>;
  if (!x) return <p className="m-0 text-xs text-muted">Verklaring laden…</p>;
  const s = x.signal, e = x.entry, o = x.exit;
  return (
    <div className="grid gap-3">
    <div className="grid gap-3 sm:grid-cols-3 text-xs">
      <div>
        <h3 className="m-0 mb-1 text-xs font-extrabold text-fg-2">Waarom instap</h3>
        {s ? (
          <>
            <Row k="trigger">F7 · {s.venue === "pool" ? (s.orientation === "reversed" ? "pool (reversed)" : "pool") : "curve"}</Row>
            <Row k="diepte">{s.depth_sol.toLocaleString("nl-NL", { maximumFractionDigits: 1 })} SOL</Row>
            <Row k="trader">{s.trigger_trader ? shortAddr(s.trigger_trader) : "–"}</Row>
            <Row k="farm-label">{s.farm === "organic" ? "organisch" : s.farm === "flagged" ? "farming" : s.farm ?? "–"}</Row>
            <Row k="farmerhistorie">{s.farm_complete == null ? "–" : s.farm_complete ? "compleet" : "onvolledig"}{s.farm_window_complete === 0 ? " · venster onvolledig" : ""}</Row>
            {s.latency_ms != null && <Row k="aankomst">{s.latency_ms} ms{s.d_ms != null ? ` (d ${s.d_ms} ms)` : ""}</Row>}
          </>
        ) : <p className="m-0 text-muted">geen signaalregel</p>}
      </div>
      <div>
        <h3 className="m-0 mb-1 text-xs font-extrabold text-fg-2">Instap</h3>
        {e ? (
          <>
            <Row k="vertraging">{e.delay_slots != null ? `${e.delay_slots} slots` : "–"} · {e.time_unix ? unixClock(e.time_unix).slice(0, 8) : "–"}</Row>
            <Row k="prijs bij signaal">{px(e.price_at_signal)}</Row>
            <Row k="prijs vóór instap">{px(e.price_before_entry)} <span className="text-muted">({pct(e.drift_pct)})</span></Row>
            <Row k="onze fill">{e.status === "filled" ? <>{px(e.fill_price)} <span className={cn(e.fill_vs_market_pct != null && e.fill_vs_market_pct > 1 ? "text-warn" : "text-muted")}>({pct(e.fill_vs_market_pct)} t.o.v. markt)</span></> : `mislukt${e.reason ? `: ${e.reason}` : ""}`}</Row>
            <p className="m-0 mt-1 text-[10px] text-muted">Prijs in SOL per token na de laatste trade (recorder); de fill bevat de fees en onze eigen prijsimpact.</p>
          </>
        ) : <p className="m-0 text-muted">geen order</p>}
      </div>
      <div>
        <h3 className="m-0 mb-1 text-xs font-extrabold text-fg-2">{o ? "Uitstap" : "Open"}</h3>
        {o ? (
          <>
            <Row k="reden">{EXIT[o.reason] ?? o.reason}</Row>
            <Row k="duur">{o.held_slots != null ? `${o.held_slots.toLocaleString("nl-NL")} slots` : "–"}{o.attempts && o.attempts > 1 ? ` · ${o.attempts} pogingen` : ""}</Row>
            <Row k="piek">{o.peak_ret != null ? `${pct(o.peak_ret * 100, 1)}${o.peak_slots != null ? ` na ${o.peak_slots} slots` : ""}` : "–"}</Row>
            <Row k="uitkomst"><span className={o.pnl_sol >= 0 ? "text-buy" : "text-sell"}>{pct(o.ret != null ? o.ret * 100 : null, 1)} · {o.pnl_sol.toLocaleString("nl-NL", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} SOL</span></Row>
          </>
        ) : x.open ? <Row k="nu (mark)">{x.open.mark_pnl_sol == null ? "–" : `${x.open.mark_pnl_sol.toLocaleString("nl-NL", { maximumFractionDigits: 3 })} SOL`}</Row> : <p className="m-0 text-muted">–</p>}
      </div>
    </div>
    {/* The owner's note on this trade (backlog 5): only on a paired device, as the day card's. */}
    <NoteBox target={{ key: p.key, variant: p.variant, t }} />
    </div>
  );
}
