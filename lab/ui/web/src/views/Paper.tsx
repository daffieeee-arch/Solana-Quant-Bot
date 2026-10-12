import { AnimatePresence, motion } from "motion/react";
import { useState, type ReactNode } from "react";
import { Badge, DexLink, Loading } from "../components/ui";
import { Button } from "../components/Button";
import { Change, Num } from "../components/Num";
import { Segmented } from "../components/Segmented";
import { usePaper } from "../live";
import { cn } from "../lib/utils";
import { label, n0, n1, shortAddr, unixTime } from "../format";
import type { PaperJournal, PaperPosition, PaperRecorder } from "../paperTypes";

// Paper tab (design v1, "Paper — iPhone"): the paper engine's journal and the recorder's meter, read-only
// (lab/ui/paper.py). Numbers mirror the engine's daily report; nothing here sends or signs anything.
const EXIT: Record<string, string> = {
  take_profit: "doel geraakt", stop_loss: "stop-loss", time: "tijdslimiet", window_end: "einde data", open: "open",
};
const VERDICT: Record<string, { text: string; tone: "buy" | "warn" | "sell" }> = {
  GO: { text: "GO", tone: "buy" }, ADJUST: { text: "BIJSTELLEN", tone: "warn" }, STOP: { text: "STOP", tone: "sell" },
};
const AVATARS = ["#F5A524", "#FF7AB6", "#5B8CFF", "#22C3A6", "#8B6CFF", "#FF8A4C", "#4CC9F0", "#C084FC"];
const avatar = (key: string) => AVATARS[[...key].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % AVATARS.length];
const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "text-buy" : v < 0 ? "text-sell" : "");
const ago = (secs: number) => (secs < 90 ? `${Math.round(secs)} s` : secs < 5400 ? `${Math.round(secs / 60)} min` : secs < 172800 ? `${n1(secs / 3600)} uur` : `${n0(secs / 86400)} dagen`);
const RANGES = [{ value: "1d", label: "1D" }, { value: "7d", label: "7D" }, { value: "all", label: "Alles" }] as const;
type RangeKey = (typeof RANGES)[number]["value"];

function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={cn("rounded-2xl p-3.5 bg-surface border border-line min-w-0", className)}>{children}</section>;
}

function Line({ k, children }: { k: ReactNode; children: ReactNode }) {
  return <div className="flex justify-between gap-3 py-1.5 border-b border-line-soft last:border-0 text-sm"><span className="text-muted">{k}</span><span className="num text-right">{children}</span></div>;
}

function Area({ ys, id }: { ys: number[]; id: string }) {
  if (ys.length < 2) return <div className="h-16" />;
  const lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), w = 320, h = 64;
  const y = (v: number) => h - 4 - ((v - lo) / (hi - lo || 1)) * (h - 8);
  const pts = ys.map((v, i) => `${((i / (ys.length - 1)) * w).toFixed(1)} ${y(v).toFixed(1)}`);
  return (
    <svg width="100%" height="64" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label="Verloop van de netto winst">
      <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--accent)" stopOpacity="0.45" /><stop offset="1" stopColor="var(--accent)" stopOpacity="0" /></linearGradient></defs>
      <line x1="0" x2={w} y1={y(0)} y2={y(0)} stroke="var(--track)" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
      <path d={`M${pts.join(" L")} L${w} ${h} L0 ${h} Z`} fill={`url(#${id})`} />
      <path d={`M${pts.join(" L")}`} fill="none" stroke="var(--accent)" strokeWidth="2.2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Stat({ k, children }: { k: string; children: ReactNode }) {
  return <div className="flex flex-col gap-0.5"><span className="text-[11px] text-muted">{k}</span><span className="text-[15px] font-semibold">{children}</span></div>;
}

function Hero({ j }: { j: PaperJournal }) {
  const [range, setRange] = useState<RangeKey>("1d");
  const r = j.ranges[range];
  const title = range === "1d" ? (j.is_today ? "Netto vandaag" : `Netto ${j.day}`) : range === "7d" ? "Netto 7 dagen" : "Netto alles";
  return (
    <section className="hero rounded-[20px] p-[18px] grid gap-2.5 min-w-0">
      <div className="flex justify-between items-center gap-2">
        <span className="text-[13px] text-fg-2 font-semibold">{title}</span>
        <Segmented label="Periode" value={range} options={[...RANGES]} onChange={setRange} />
      </div>
      <div className="flex items-baseline gap-2.5 flex-wrap">
        <Num value={r.net_sol} signed format={{ minimumFractionDigits: 2, maximumFractionDigits: 2 }} className={cn("text-[40px] leading-none font-semibold tracking-[-1.5px]", tone(r.net_sol))} />
        <span className="text-base text-fg-2 font-bold">SOL</span>
        <span className="ml-auto" title="Netto rendement per trade (winst gedeeld door inleg)"><Change value={r.ret_per_trade == null ? null : r.ret_per_trade * 100} unit="% /trade" /></span>
      </div>
      <Area ys={r.curve} id={`g-${range}`} />
      <div className="grid grid-cols-3 gap-2">
        <Stat k="Trades"><Num value={r.trades} /></Stat>
        <Stat k="Winrate"><Num value={r.win_rate == null ? null : r.win_rate * 100} format={{ maximumFractionDigits: 0 }} suffix="%" /></Stat>
        <Stat k="vs N2"><Num value={r.vs_n2_pt} signed format={{ maximumFractionDigits: 1 }} suffix=" pt" className={tone(r.vs_n2_pt)} /></Stat>
      </div>
    </section>
  );
}

function Ring({ part }: { part: number }) {
  const c = 2 * Math.PI * 19;
  return (
    <svg width="46" height="46" viewBox="0 0 46 46" aria-hidden="true" className="shrink-0">
      <circle cx="23" cy="23" r="19" stroke="var(--track)" strokeWidth="6" fill="none" />
      <circle cx="23" cy="23" r="19" stroke="var(--accent)" strokeWidth="6" fill="none" strokeLinecap="round"
        strokeDasharray={`${(Math.min(1, part) * c).toFixed(1)} ${c.toFixed(1)}`} transform="rotate(-90 23 23)" />
    </svg>
  );
}

function StopCard({ j }: { j: PaperJournal }) {
  const s = j.stop_rule, early = s.trades < s.min_trades, v = VERDICT[s.verdict];
  return (
    <Panel className="flex items-center gap-3">
      <Ring part={s.trades / s.min_trades} />
      <div className="flex flex-col gap-0.5 min-w-0 items-start">
        <span className="text-[11px] text-muted">Stopregel</span>
        <span className="num text-[15px] font-semibold">{n0(s.trades)} / {s.min_trades}</span>
        {early ? <span className="text-[11px] text-fg-2">te vroeg</span> : <Badge tone={v?.tone ?? "muted"}>{v?.text ?? s.verdict}</Badge>}
      </div>
    </Panel>
  );
}

function TritonCard({ r }: { r: PaperRecorder }) {
  const usd = (r.spent_gb ?? 0) * r.usd_per_gb;
  return (
    <Panel className="flex flex-col gap-2">
      <div className="flex justify-between gap-1"><span className="text-[11px] text-muted">Triton-data</span>
        <span className="text-[11px] text-fg-2 num">${usd.toFixed(2).replace(".", ",")} / ${r.budget_usd.toFixed(0)}</span></div>
      <Num value={r.spent_gb} format={{ minimumFractionDigits: 1, maximumFractionDigits: 1 }} suffix=" GB" className="text-[15px] font-semibold" />
      <div className="h-1.5 rounded-sm bg-track overflow-hidden">
        <div className={cn("h-1.5", r.stopped ? "bg-sell-strong" : "bg-accent")} style={{ width: `${Math.min(100, (100 * (r.spent_gb ?? 0)) / (r.cap_gb || 1))}%` }} />
      </div>
      {r.sample && <span className="text-[10px] font-bold text-warn">voorbeelddata</span>}
    </Panel>
  );
}

function PositionRow({ p, lastSlot }: { p: PaperPosition; lastSlot: number | null }) {
  const open = p.status === "open";
  const kind = p.venue === "pool" ? (p.orientation === "reversed" ? "REVERSED" : "POOL") : (p.venue ?? "–").toUpperCase();
  const slots = p.slots ?? (open && lastSlot ? lastSlot - p.entry_slot : null);
  const sym = label(p.symbol, 16), name = label(p.name, 40);
  const ret = open ? (p.mark_sol != null && p.cost_sol ? (p.mark_sol - p.cost_sol) / p.cost_sol : null) : p.ret ?? null;
  return (
    <div className="flex items-center gap-3 p-3 rounded-[14px] bg-surface border border-line min-w-0">
      <span className="w-9 h-9 rounded-full inline-flex items-center justify-center text-xs font-extrabold text-on-accent shrink-0" style={{ background: avatar(p.key) }} aria-hidden="true">
        {(sym || p.key).slice(0, 2).toUpperCase()}
      </span>
      <div className="flex flex-col gap-0.5 grow min-w-0">
        <div className="flex gap-1.5 items-center min-w-0">
          <span className="font-bold text-sm truncate" title={name || p.key}>{sym || shortAddr(p.key)}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-surface-2 text-fg-2 font-bold">{kind}</span>
        </div>
        <span className="text-[11px] text-muted truncate">
          <span className="num">{p.cost_sol == null ? "–" : p.cost_sol.toLocaleString("nl-NL", { minimumFractionDigits: 2 })} SOL{slots != null ? ` · ${n0(slots)} slots` : ""} · {p.variant.split("_")[0]}</span>
          {" · "}<DexLink mint={p.mint ?? p.key}>Dex ↗</DexLink>
        </span>
      </div>
      <div className="flex flex-col items-end gap-0.5 shrink-0">
        <span className={cn("num text-sm font-semibold", tone(ret))}>
          {p.failed ? "mislukt" : ret == null ? "–" : `${ret > 0 ? "+" : ret < 0 ? "−" : ""}${n1(Math.abs(ret * 100))}%`}
        </span>
        <span className="text-[11px] text-muted whitespace-nowrap">
          {open ? (p.mark_sol == null ? "open · geen stand" : "open") : EXIT[p.status] ?? p.status}
        </span>
      </div>
    </div>
  );
}

export default function Paper() {
  const { data, error } = usePaper();
  const [shown, setShown] = useState(20);
  if (error && !data) return <p className="text-warn">{error}</p>;
  if (!data) return <Loading what="Paper" />;
  const j = data.journal, r = data.recorder;
  const nOpen = j?.positions.filter((p) => p.status === "open").length ?? 0;

  return (
    <div className="grid gap-3.5">
      <div className="flex flex-wrap gap-2">
        <span className="px-2.5 py-1.5 rounded-lg bg-warn-soft border border-warn-line text-warn text-[11px] font-bold">Paper trading: er wordt niets verstuurd, geen echt geld.</span>
        {j?.test_run && <span className="px-2.5 py-1.5 rounded-lg bg-accent-soft text-accent-fg text-[11px] font-semibold">Testdata · pariteitsrun op ontwikkeldata ({j.journal})</span>}
      </div>

      {!j ? <p className="text-muted">Nog geen paper-journaal.</p> : (
        <div className="grid gap-3.5 lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">
          <div className="grid gap-3.5 min-w-0">
            <Hero j={j} />
            <div className="grid grid-cols-2 gap-2.5 lg:hidden">
              <StopCard j={j} />
              {r && <TritonCard r={r} />}
            </div>

            <section className="grid gap-2 min-w-0">
              <div className="flex justify-between items-baseline">
                <h2 className="m-0 text-base font-extrabold">Posities</h2>
                <span className="text-xs text-muted">{nOpen} open · {j.positions.length - nOpen} gesloten</span>
              </div>
              {!j.positions.length ? <p className="text-muted text-sm m-0">Geen posities.</p> : (
                <div className="grid gap-2">
                  <AnimatePresence initial={false}>
                    {j.positions.slice(0, shown).map((p) => (
                      <motion.div key={`${p.family}|${p.key}|${p.entry_slot}`} layout initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                        <PositionRow p={p} lastSlot={j.status.last_slot} />
                      </motion.div>
                    ))}
                  </AnimatePresence>
                </div>
              )}
              {j.positions.length > shown && (
                <Button type="button" onClick={() => setShown(shown + 50)} className="justify-self-start">
                  Toon meer ({j.positions.length - shown} over)
                </Button>
              )}
            </section>
          </div>

          <aside className="grid gap-3.5 min-w-0">
            <div className="hidden lg:grid gap-3.5">
              <StopCard j={j} />
              {r && <TritonCard r={r} />}
            </div>
            <Details j={j} r={r} now={data.now_unix} />
          </aside>
        </div>
      )}
    </div>
  );
}

function Details({ j, r, now }: { j: PaperJournal; r: PaperRecorder | null; now: number }) {
  const s = j.stop_rule, c = j.capital;
  return (
    <>
      <Panel>
        <h2 className="m-0 mb-1 text-[13px] font-extrabold">Status</h2>
        <Line k="Laatste heartbeat">{j.status.last_heartbeat_unix == null ? "–" : `${unixTime(j.status.last_heartbeat_unix)} (${ago(now - j.status.last_heartbeat_unix)} geleden)`}</Line>
        <Line k="Laatste slot">{n0(j.status.last_slot)}</Line>
        <Line k={`Feed-gaten ${j.is_today ? "vandaag" : j.day}`}><span className={j.status.gaps_today ? "text-warn" : ""}>{j.status.gaps_today}{j.status.largest_gap_slots != null ? ` (grootste ${n0(j.status.largest_gap_slots)} slots)` : ""}</span></Line>
      </Panel>

      <Panel>
        <h2 className="m-0 mb-1 text-[13px] font-extrabold">Stopregel-teller</h2>
        <p className="m-0 mb-1 text-xs text-muted">Alle paper-dagen, zonder N2. Oordeel vanaf {s.min_trades} trades; 95%-interval (per dag) vanaf {s.min_days_ci} dagen.</p>
        <Line k="Trades · dagen">{n0(s.trades)} · {s.days}</Line>
        <Line k="Netto per trade"><Num value={s.mean_sol_per_trade} signed format={{ minimumFractionDigits: 4, maximumFractionDigits: 4 }} suffix=" SOL" className={tone(s.mean_sol_per_trade)} /></Line>
        <Line k="95%-interval">{s.ci_lo == null ? `vanaf ${s.min_days_ci} dagen` : `${s.ci_lo.toFixed(4)} … ${s.ci_hi?.toFixed(4)} SOL`}</Line>
      </Panel>

      <Panel>
        <h2 className="m-0 mb-1 text-[13px] font-extrabold">Kapitaalsimulatie</h2>
        <p className="m-0 mb-1 text-xs text-muted">{n0(c.start_sol)} SOL, vaste grootte, hoogstens {c.max_positions} posities tegelijk.</p>
        <Area ys={c.curve} id="g-cap" />
        <Line k="Eindstand"><span className={tone(c.final_sol - c.start_sol)}>{c.final_sol.toLocaleString("nl-NL", { maximumFractionDigits: 2 })} SOL</span></Line>
        <Line k="Grootste daling">{n1(c.max_drawdown * 100)}%</Line>
        <Line k="Genomen / overgeslagen">{n0(c.taken)} / {n0(c.skipped)}</Line>
      </Panel>

      <Panel>
        <h2 className="m-0 mb-1 text-[13px] font-extrabold">Verschil met N2</h2>
        <p className="m-0 mb-1 text-xs text-muted">N2: dezelfde strategie met een willekeurige instap. Positief = beter dan toeval.</p>
        {j.n2.map((x) => !x.has_shadow
          ? <Line key={x.family + x.variant} k={`${x.family} ${x.variant}`}><span className="text-muted font-sans">geen N2-schaduw in dit journaal</span></Line>
          : (
            <div key={x.family + x.variant}>
              <Line k={`${x.family} ${x.variant} · alle dagen`}><span className={tone(x.diff_all)}>{x.diff_all == null ? "–" : `${x.diff_all > 0 ? "+" : ""}${x.diff_all.toFixed(4)} SOL/trade`}</span></Line>
              <Line k={j.is_today ? "vandaag" : j.day}><span className={tone(x.diff_today)}>{x.diff_today == null ? "–" : `${x.diff_today > 0 ? "+" : ""}${x.diff_today.toFixed(4)} SOL/trade`}</span></Line>
            </div>
          ))}
      </Panel>

      {r && (
        <Panel>
          <div className="flex items-center justify-between mb-1">
            <h2 className="m-0 text-[13px] font-extrabold">Recorder · {r.is_today ? "vandaag" : r.day}</h2>
            {r.sample ? <Badge tone="warn">voorbeelddata</Badge> : r.stopped ? <Badge tone="sell">gestopt</Badge> : r.level ? <Badge tone="warn">afgeschaald {r.level}</Badge> : <Badge tone="buy">alle streams</Badge>}
          </div>
          <div className="grid gap-1.5 py-1">
            {r.streams.map((st) => (
              <div key={st.stream} className="grid grid-cols-[110px_minmax(0,1fr)_64px] items-center gap-2.5">
                <span className="num text-xs text-fg-2 truncate">{st.stream}</span>
                <div className="h-1.5 rounded-sm bg-surface-3 overflow-hidden"><div className="h-1.5 bg-accent" style={{ width: `${Math.min(100, (100 * st.gb) / Math.max(...r.streams.map((x) => x.gb), 1e-9))}%` }} /></div>
                <span className="num text-xs text-right">{st.gb >= 0.1 ? n1(st.gb) : st.gb.toLocaleString("nl-NL", { maximumFractionDigits: 3 })} GB</span>
              </div>
            ))}
          </div>
          {!!r.dropped.length && <Line k="Uitgezet">{r.dropped.join(", ")}</Line>}
          <Line k="Herverbindingen">{r.reconnects}</Line>
          {r.notes.slice(0, 3).map((n) => <p key={n.unix} className="m-0 mt-1 text-[11px] text-muted"><span className="num">{unixTime(n.unix)}</span> · {n.note}</p>)}
        </Panel>
      )}
    </>
  );
}
