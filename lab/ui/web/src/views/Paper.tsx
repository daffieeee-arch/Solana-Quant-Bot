import { AnimatePresence, motion } from "motion/react";
import { useState, type ReactNode } from "react";
import { Badge, DexLink, Loading, SolscanLink } from "../components/ui";
import { Button } from "../components/Button";
import PaperChart from "../components/PaperChart";
import Explain from "../components/Explain";
import OnCourse from "../components/OnCourse";
import DayCard from "../components/DayCard";
import { Change, Num } from "../components/Num";
import { Segmented } from "../components/Segmented";
import { usePaper } from "../live";
import { cn } from "../lib/utils";
import { label, n0, n1, shortAddr, unixClock, unixTime } from "../format";
import type { PaperAssessmentItem, PaperJournal, PaperPosition, PaperRecorder } from "../paperTypes";

// Paper tab (design v1, "Paper — iPhone"): the paper engine's journal and the recorder's meter, read-only
// (lab/ui/paper.py). Numbers mirror the engine's daily report; nothing here sends or signs anything.
// Every exit reason the engine journals (lab/paper/engine.py, backtest/strategies.py), in Dutch.
const EXIT: Record<string, string> = {
  take_profit: "doel geraakt", stop_loss: "stop-loss", time: "tijdslimiet", window_end: "einde data", open: "open",
  outflow: "uitstroom", graduation: "migratie", restart_lost: "verloren bij herstart", entry_slippage: "instap: slippage",
  after_window_end: "einde data", end_of_data: "einde data",
};
const VERDICT: Record<string, { text: string; tone: "buy" | "warn" | "sell" }> = {
  GO: { text: "GO", tone: "buy" }, ADJUST: { text: "BIJSTELLEN", tone: "warn" }, STOP: { text: "STOP", tone: "sell" },
};
// Below this many trading days the day bootstrap is coarse (lab/paper/stoprule.py FEW_DAYS).
const FEW_DAYS = 14;
// What a verdict rests on, in Dutch, per key of stoprule.assessment; an unknown key shows the report's English text.
const pct = (x: number) => `${(x * 100).toLocaleString("nl-NL", { maximumFractionDigits: 1 })}%`;
const ASSUMPTION: Record<string, (a: PaperAssessmentItem) => string> = {
  paper: () => "Paper trading: elke fill is gesimuleerd tegen de reserves van de pool of curve, inclusief onze eigen prijsimpact. Er is geen transactie verstuurd.",
  landing: (a) => a.land_prob != null
    ? `Landing is gesimuleerd: elke transactie landt met kans ${pct(a.land_prob)}; een mislukte poging kost de fee van een mislukte transactie.`
    : "Landing is gesimuleerd.",
  delay: (a) => a.d_ms != null
    ? `De instap komt ${n0(a.d_ms)} ms na het signaal${a.q != null ? `, op positie ${a.q.toLocaleString("nl-NL")} in het slot` : ""}.`
    : "De instapvertraging is die uit de configuratie van de engine.",
  regime: (a) => `${n0(a.after_switch ?? 0)} van de ${n0(a.trades ?? 0)} instappen zijn na de Alpenglow-overgang (epoch 1064).`,
};
const UNCERTAINTY: Record<string, (a: PaperAssessmentItem) => string> = {
  not_paper_trading: (a) => `Dit journaal is een ${a.feed === "replay" ? "replay" : a.feed ?? "test"}: een test, geen live paper trading. De stopregel beslist hier niets.`,
  few_days: (a) => `Pas ${n0(a.days ?? 0)} handelsdagen. Onder ${FEW_DAYS} dagen is het interval per dag grof.`,
  regime: (a) => a.after_switch
    ? "Een nieuw regime: de backtests komen allemaal van vóór de Alpenglow-overgang."
    : "Alle trades zijn van vóór de Alpenglow-overgang; het regime daarna is nog niet gemeten.",
  simulated_landing: () => "Landing, priority fees en concurrentie om dezelfde pools zijn gesimuleerd, niet gemeten.",
  many_trials: () => "De kandidaat is gekozen uit veel varianten in het lab. Dit is de eerste toets op data die hij niet kende; bij veel pogingen kan geluk op een voorsprong lijken.",
};
// stoprule.costs, beyond the fill prices (venue fees are in the fill prices).
const COSTS: Record<string, string> = {
  network: "Netwerkfees", tips: "Tips", landing_retries: "Herhaalpogingen tot landing", failed_exit_attempts: "Mislukte uitstappogingen",
  failed_entries: "Mislukte instappen", ata_rent_lost: "Verloren ATA-huur", adverse_execution: "Ongunstige uitvoering",
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
    <section className={cn("hero rounded-[20px] p-[18px] grid gap-2.5 min-w-0", j.test_run && "!border-2 !border-warn")}>
      {j.test_run && <TestLabel j={j} />}
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

// The clock's state in words (lab/paper/stoprule.py clock_state); without a clock: a count, no decision.
function clockText(s: PaperJournal["stop_rule"]): string {
  const c = s.clock;
  if (!c) return "geen klokstart: telling, geen beslissing";
  if (c.phase === "not_started") return `klok start ${c.date}`;
  if (c.limit) return `klokdag ${c.clock_day}: grens van dag ${s.clock_max_days} bereikt`;
  if (c.phase === "due") return `klokdag ${c.clock_day}: oordeel`;
  return `klokdag ${c.clock_day} · gepland ${s.clock_days}, uiterlijk ${s.clock_max_days}`;
}

// Why there is no verdict yet (stoprule.decide); null when there is one.
function waitText(s: PaperJournal["stop_rule"]): string | null {
  if (s.verdict === "too few trades") return `te vroeg: ${n0(s.trades)} van ${s.min_trades} trades`;
  if (s.verdict === "too few days") return `te vroeg: ${s.days} van ${s.min_days_ci} dagen`;
  return null;
}

function TestLabel({ j }: { j: PaperJournal }) {
  const parity = j.source.journal.startsWith("parity");
  return (
    <span className="justify-self-start self-start px-2 py-1 rounded-md bg-warn-soft border border-warn-line text-warn text-[11px] font-extrabold tracking-wide">
      TEST · geen paper trading{parity ? " (pariteitsrun)" : j.source.feed !== "live" ? ` (${j.source.feed})` : ""}
    </span>
  );
}

function StopCard({ j }: { j: PaperJournal }) {
  // A verdict badge only where the stop rule decides (stoprule.assessment `applies`): live paper trading, a clock
  // start and a verdict. A replay journal is a test and gets its label instead.
  const s = j.stop_rule, a = s.assessment, v = VERDICT[s.verdict], wait = waitText(s);
  return (
    <Panel className="flex items-center gap-3">
      <Ring part={s.trades / s.min_trades} />
      <div className="flex flex-col gap-0.5 min-w-0 items-start">
        <span className="text-[11px] text-muted">Stopregel</span>
        <span className="num text-[15px] font-semibold">{n0(s.trades)} / {s.min_trades}</span>
        {j.test_run ? <TestLabel j={j} /> : a.applies && v && <Badge tone={v.tone}>{v.text}</Badge>}
        <span className="text-[11px] text-fg-2">{wait && !j.test_run && s.clock ? `${wait} · ${clockText(s)}` : clockText(s)}</span>
      </div>
    </Panel>
  );
}

// What the verdict rests on, its assumptions and what stays uncertain (stoprule.assessment), with every verdict.
function Assessment({ j }: { j: PaperJournal }) {
  const s = j.stop_rule, a = s.assessment, b = a.basis, v = VERDICT[a.verdict];
  const sol = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toLocaleString("nl-NL", { minimumFractionDigits: 4, maximumFractionDigits: 4 })} SOL`;
  const costs = b.costs_sol ? Object.entries(b.costs_sol) : [];
  const verdictText = j.test_run ? "test, geen paper trading" : !a.applies && v ? `${v.text} (telt nog niet)` : v?.text ?? waitText(s) ?? a.verdict;
  return (
    <Panel className="lg:col-span-3">
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <h2 className="m-0 text-[13px] font-extrabold">Oordeel: waarop het rust</h2>
        {j.test_run ? <TestLabel j={j} /> : a.applies && v ? <Badge tone={v.tone}>{v.text}</Badge> : <Badge tone="muted">{verdictText}</Badge>}
      </div>
      <p className="m-0 my-2 px-3 py-2 rounded-lg bg-warn-soft border border-warn-line text-warn text-[13px] font-bold">{a.not_permission}</p>
      <div className="grid gap-3.5 lg:grid-cols-3">
        <div>
          <h3 className="m-0 mb-1 text-xs font-extrabold text-fg-2">Basis</h3>
          <Line k="Gevulde trades">{n0(b.filled_trades)}</Line>
          <Line k="Mislukte instappen">{n0(b.failed_entries)}</Line>
          <Line k="Handelsdagen">{n0(b.trading_days)}</Line>
          <Line k="Klokdag">{b.clock_day == null ? "–" : n0(b.clock_day)}</Line>
          <Line k="Netto per trade"><span className={tone(b.mean_sol)}>{b.mean_sol == null ? "–" : sol(b.mean_sol)}</span></Line>
          <Line k="95%-interval">{b.ci95_sol ? `${sol(b.ci95_sol[0])} … ${sol(b.ci95_sol[1])}` : `vanaf ${s.min_days_ci} dagen`}</Line>
          <p className="m-0 mt-1 text-[11px] text-muted">Interval: bootstrap over hele handelsdagen, {n0(b.draws)} keer.</p>
        </div>
        <div>
          <h3 className="m-0 mb-1 text-xs font-extrabold text-fg-2">Kosten (naast de fill-prijzen)</h3>
          {costs.length ? costs.map(([k, x]) => <Line key={k} k={COSTS[k] ?? k}>{x.toLocaleString("nl-NL", { maximumFractionDigits: 4 })} SOL</Line>)
            : <p className="m-0 text-xs text-muted">Geen kostenscenario in het journaal.</p>}
          <p className="m-0 mt-1 text-[11px] text-muted">De fees van pool en curve zitten al in de fill-prijzen.</p>
        </div>
        <div>
          <h3 className="m-0 mb-1 text-xs font-extrabold text-fg-2">Aannames</h3>
          <ul className="m-0 mb-2 pl-4 text-xs text-fg-2 grid gap-1">{a.assumptions.map((x) => <li key={x.key}>{ASSUMPTION[x.key]?.(x) ?? x.text}</li>)}</ul>
          <h3 className="m-0 mb-1 text-xs font-extrabold text-fg-2">Onzeker</h3>
          <ul className="m-0 pl-4 text-xs text-fg-2 grid gap-1">{a.uncertainty.map((x) => <li key={x.key}>{UNCERTAINTY[x.key]?.(x) ?? x.text}</li>)}</ul>
        </div>
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
  const slots = p.slots ?? (open && lastSlot && lastSlot >= p.entry_slot ? lastSlot - p.entry_slot : null);
  const sym = label(p.symbol, 16), name = label(p.name, 40);
  const ret = p.ret ?? null; // open: mark_pnl / cost from the engine's mark (after all costs)
  return (
    <div className="flex items-center gap-3 p-3 rounded-[14px] bg-surface border border-line min-w-0">
      <span className="w-9 h-9 rounded-full inline-flex items-center justify-center text-xs font-extrabold text-on-accent shrink-0" style={{ background: avatar(p.key) }} aria-hidden="true">
        {(sym || p.mint || p.key).slice(0, 2).toUpperCase()}
      </span>
      <div className="flex flex-col gap-0.5 grow min-w-0">
        <div className="flex flex-wrap gap-x-1.5 gap-y-0.5 items-center min-w-0">
          <span className="font-bold text-sm truncate max-w-full" title={p.mint ?? p.key}>{sym || shortAddr(p.mint ?? p.key)}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-surface-2 text-fg-2 font-bold">{kind}</span>
          {p.farm && p.farm !== "n/a" && <span className={cn("text-[10px] px-1.5 py-0.5 rounded-md font-bold", p.farm === "organic" ? "bg-buy-soft text-buy" : "bg-warn-soft text-warn")}>{p.farm === "organic" ? "organisch" : "farming"}</span>}
        </div>
        {name && <span className="text-[11px] text-fg-2 truncate -mt-0.5" title={p.mint ?? p.key}>{name}</span>}
        <span className="text-[11px] text-muted truncate">
          <span className="num">{p.cost_sol == null ? "–" : p.cost_sol.toLocaleString("nl-NL", { minimumFractionDigits: 2 })} SOL{slots != null ? ` · ${n0(slots)} slots` : ""} · {"group" in p ? p.variant.replace(/_H\d+$/, "") : p.variant.split("_")[0]}</span>
          {" · "}<DexLink mint={p.mint ?? p.key}>Dex ↗</DexLink>
        </span>
      </div>
      <div className="flex flex-col items-end gap-0.5 shrink-0">
        <span className={cn("num text-sm font-semibold", tone(ret))}>
          {p.failed ? "mislukt" : ret == null ? "–" : `${ret > 0 ? "+" : ret < 0 ? "−" : ""}${n1(Math.abs(ret * 100))}%`}
        </span>
        <span className="text-[11px] text-muted whitespace-nowrap">
          {open ? (p.pnl_sol == null ? "open · geen stand" : "open") : EXIT[p.status] ?? p.status}
        </span>
      </div>
    </div>
  );
}

const dur = (p: PaperPosition) => {
  const secs = p.entry_time != null && p.exit_time != null ? p.exit_time - p.entry_time : p.slots != null ? p.slots * 0.4 : null;
  if (secs == null) return "–";
  return secs < 90 ? `${Math.max(1, Math.round(secs))} s` : secs < 5400 ? `${Math.round(secs / 60)} min` : `${n1(secs / 3600)} u`;
};
const retOf = (p: PaperPosition) => p.ret ?? null;
const pctTxt = (v: number | null) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${n1(Math.abs(v * 100))}%`);
// The table's views: K3 (all, open), each further group's own positions ("arm:H"), and K3's N2 shadow.
type Filter = string;
const filtersFor = (groups: string[]) => [{ value: "all", label: "K3" }, { value: "open", label: "K3 open" },
  ...groups.map((g) => ({ value: `arm:${g}`, label: `Groep ${g}` })), { value: "shadow", label: "N2-schaduw" }];

const rowId = (p: PaperPosition) => `${p.family}|${p.variant}|${p.key}|${p.entry_slot}`;

function PositionsTable({ rows, sel, onSel }: { rows: PaperPosition[]; sel: string | null; onSel: (k: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] border-collapse text-[13px]">
        <thead>
          <tr className="text-muted text-left">
            {["Token", "Strategie", "Instap", "Grootte", "Duur", "P&L", "Netto", "Status"].map((h, i) => (
              <th key={h} className={cn("px-2 py-2.5 font-semibold border-b border-line", i >= 3 && i <= 6 && "text-right")}>{h}</th>
            ))}
            <th className="px-2 py-2.5 border-b border-line"><span className="sr-only">Link</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const ret = retOf(p), sym = label(p.symbol, 16) || shortAddr(p.mint ?? p.key), name = label(p.name, 32), on = sel === rowId(p);
            return (
              <tr key={`${p.family}|${p.variant}|${p.key}|${p.entry_slot}`} onClick={() => onSel(rowId(p))} aria-selected={on}
                className={cn("border-b border-[#161D2A] cursor-pointer hover:bg-surface-2/50", on && "bg-accent-soft")}>
                <td className="px-2 py-2.5">
                  <span className="inline-flex items-center gap-2.5">
                    <span className="w-[26px] h-[26px] rounded-full inline-flex items-center justify-center text-[10px] font-extrabold text-on-accent" style={{ background: avatar(p.key) }} aria-hidden="true">{(sym || p.mint || p.key).slice(0, 2).toUpperCase()}</span>
                    <span className="flex flex-col min-w-0" title={p.mint ?? p.key}><span className="font-bold">{sym}</span>{name && <span className="text-[11px] text-muted truncate max-w-[180px]">{name}</span>}</span>
                  </span>
                </td>
                <td className="px-2 py-2.5 text-fg-2">{p.family} {p.variant}</td>
                <td className="px-2 py-2.5 num text-fg-2">{p.entry_time != null ? unixClock(p.entry_time).slice(0, 5) : "–"}</td>
                <td className="px-2 py-2.5 num text-right">{p.cost_sol == null ? "–" : p.cost_sol.toLocaleString("nl-NL", { minimumFractionDigits: 2 })}</td>
                <td className="px-2 py-2.5 num text-right text-fg-2">{dur(p)}</td>
                <td className={cn("px-2 py-2.5 num text-right", tone(ret))}>{p.failed ? "mislukt" : pctTxt(ret)}</td>
                <td className={cn("px-2 py-2.5 num text-right", tone(p.pnl_sol))}>{p.pnl_sol == null ? "–" : `${p.pnl_sol > 0 ? "+" : ""}${p.pnl_sol.toLocaleString("nl-NL", { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`}</td>
                <td className="px-2 py-2.5">
                  <Badge tone={p.status === "open" ? "accent" : p.status === "take_profit" ? "buy" : p.status === "stop_loss" || p.failed ? "sell" : "muted"}>
                    {p.failed ? "instap mislukt" : EXIT[p.status] ?? p.status}
                  </Badge>
                </td>
                <td className="px-2 py-2.5 whitespace-nowrap"><DexLink mint={p.mint ?? p.key}>Dex ↗</DexLink> <SolscanLink mint={p.mint ?? p.key} className="ml-2">Scan ↗</SolscanLink></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function Paper() {
  const { data, error } = usePaper();
  const [shown, setShown] = useState(20);
  const [open, setOpen] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [list, setList] = useState<string>("main"); // phone: K3's positions or a further group's
  if (error && !data) return <p className="text-warn">{error}</p>;
  if (!data) return <Loading what="Paper" />;
  const j = data.journal, r = data.recorder;
  const nOpen = j?.positions.filter((p) => p.status === "open").length ?? 0;
  const allRows = j ? [...j.positions, ...j.shadow_positions, ...(j.arm_positions ?? [])] : [];
  const selRow = allRows.find((p) => rowId(p) === sel) ?? j?.positions[0] ?? null;
  const chartKey = selRow?.key ?? null;
  const armGroups = j ? [...new Set([...j.arms.map((a) => a.group), ...j.arm_positions.map((p) => p.group)])] : [];
  const armRows = (g: string) => j?.arm_positions.filter((p) => p.group === g) ?? [];
  const tableRows = !j ? [] : filter === "shadow" ? j.shadow_positions : filter === "open" ? j.positions.filter((p) => p.status === "open")
    : filter.startsWith("arm:") ? armRows(filter.slice(4)) : j.positions;
  const phoneRows = !j ? [] : list === "main" || !armGroups.includes(list) ? j.positions : armRows(list);
  const phoneOpen = phoneRows.filter((p) => p.status === "open").length;

  return (
    <div className="grid gap-3.5">
      <div className="flex flex-wrap gap-2">
        <span className="px-2.5 py-1.5 rounded-lg bg-warn-soft border border-warn-line text-warn text-[11px] font-bold">Paper trading: er wordt niets verstuurd, geen echt geld.</span>
        {(data.alerts ?? []).map((al) => (
          <span key={al.key} role="status" className={cn("px-2.5 py-1.5 rounded-lg border text-[11px] font-bold",
            al.level === "alert" ? "bg-sell-soft border-sell-soft text-sell" : "bg-warn-soft border-warn-line text-warn")}>{al.level === "alert" ? "⚠ " : ""}{al.text}</span>
        ))}
        {j?.groups_inferred && <span className="px-2.5 py-1.5 rounded-lg bg-warn-soft border border-warn-line text-warn text-[11px] font-semibold">Groepen staan niet in dit journaal: K3 en H zijn afgeleid uit de variantnamen (…_H6 = groep H).</span>}
        {j?.test_run && <span className="px-2.5 py-1.5 rounded-lg bg-accent-soft text-accent-fg text-[11px] font-semibold">Test, geen paper trading: {j.source.feed === "replay" ? "replay" : j.source.feed} van {j.journal}. Geen oordeel.</span>}
      </div>

      {!j ? <p className="text-muted">Nog geen paper-journaal.</p> : (
        <>
          {/* Desktop dashboard: chart of the selected position beside the hero and cards, the positions table below. */}
          <div className="hidden lg:flex flex-wrap gap-[18px]">
            <section className="flex-[999_1_640px] min-w-0 rounded-[18px] bg-surface-alt border border-line p-[18px]">
              {chartKey ? <PaperChart k={chartKey} height={400} /> : <p className="text-muted m-0">Nog geen posities.</p>}
              {selRow && <div className="mt-4 pt-3 border-t border-line"><Explain p={selRow} /></div>}
            </section>
            <aside className="flex-[1_1_340px] min-w-0 grid gap-3.5 content-start">
              <Hero j={j} />
              <StopCard j={j} />
              {r && <TritonCard r={r} />}
              <DayCard j={j} r={r} />
            </aside>
          </div>
          <section className="hidden lg:grid rounded-[18px] bg-surface-alt border border-line p-[18px] gap-3">
            <div className="flex flex-wrap gap-2.5 items-center">
              <h2 className="m-0 text-base font-extrabold">Posities</h2>
              <span className="text-xs text-muted">{nOpen} open · {j.positions.length - nOpen} gesloten {j.is_today ? "vandaag" : j.day}</span>
              <div className="ml-auto flex gap-1.5">
                {filtersFor(armGroups).map((f) => (
                  <button key={f.value} type="button" onClick={() => setFilter(f.value)} aria-pressed={filter === f.value}
                    className={cn("min-h-9 px-3 rounded-lg border text-xs", filter === f.value ? "border-accent-line bg-accent-soft text-accent-fg font-bold" : "border-track text-[#A3ADBF] font-semibold")}>
                    {f.label}
                  </button>
                ))}
              </div>
            </div>
            {tableRows.length ? <PositionsTable rows={tableRows.slice(0, shown * 2)} sel={selRow ? rowId(selRow) : null} onSel={setSel} />
              : <p className="text-sm text-muted m-0">{filter === "shadow" ? "Geen N2-schaduw in dit journaal." : filter.startsWith("arm:") ? `Geen posities van groep ${filter.slice(4)} ${j.is_today ? "vandaag" : j.day}.` : "Geen posities."}</p>}
            {tableRows.length > shown * 2 && <Button type="button" onClick={() => setShown(shown + 25)} className="justify-self-start">Toon meer ({tableRows.length - shown * 2} over)</Button>}
          </section>

          {/* Phone: hero, two cards, positions as a list; a tap opens the position's chart. */}
          <div className="grid gap-3.5 lg:hidden">
            <Hero j={j} />
            <div className="grid grid-cols-2 gap-2.5">
              <StopCard j={j} />
              {r && <TritonCard r={r} />}
            </div>
            <DayCard j={j} r={r} />
            <section className="grid gap-2 min-w-0">
              <div className="flex justify-between items-center gap-2 flex-wrap">
                <h2 className="m-0 text-base font-extrabold">Posities</h2>
                {!!armGroups.length && <Segmented label="Groep" value={armGroups.includes(list) ? list : "main"} onChange={setList}
                  options={[{ value: "main", label: "K3" }, ...armGroups.map((g) => ({ value: g, label: `Groep ${g}` }))]} />}
                <span className="text-xs text-muted">{phoneOpen} open · {phoneRows.length - phoneOpen} gesloten</span>
              </div>
              {list !== "main" && armGroups.includes(list) && <p className="m-0 text-[11px] text-muted">Groep {list}: meting, telt niet mee voor de stopregel of de kapitaalsimulatie.</p>}
              {!phoneRows.length ? <p className="text-muted text-sm m-0">Geen posities.</p> : (
                <div className="grid gap-2">
                  <AnimatePresence initial={false}>
                    {phoneRows.slice(0, shown).map((p) => {
                      const id = `${p.family}|${p.variant}|${p.key}|${p.entry_slot}`;
                      return (
                        <motion.div key={id} layout initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                          {/* A div, not a button: the row holds the DexScreener link. */}
                          <div role="button" tabIndex={0} className="cursor-pointer" aria-expanded={open === id}
                            onClick={() => setOpen(open === id ? null : id)}
                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(open === id ? null : id); } }}>
                            <PositionRow p={p} lastSlot={j.status.last_slot} />
                          </div>
                          {open === id && (
                            <div className="mt-2 rounded-[14px] bg-surface-alt border border-line p-3 grid gap-3"><PaperChart k={p.key} height={240} /><Explain p={p} /></div>
                          )}
                        </motion.div>
                      );
                    })}
                  </AnimatePresence>
                </div>
              )}
              {phoneRows.length > shown && (
                <Button type="button" onClick={() => setShown(shown + 50)} className="justify-self-start">
                  Toon meer ({phoneRows.length - shown} over)
                </Button>
              )}
            </section>
          </div>

          <div className="grid gap-3.5 lg:grid-cols-3 lg:items-start">
            <OnCourse oc={j.on_course} />
            <Assessment j={j} />
            <Details j={j} r={r} now={data.now_unix} />
          </div>
        </>
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
        {j.status.config && <Line k="Config"><span title={`sha256 ${j.status.config_sha256 ?? "–"}`}>{j.status.config}{j.status.config_sha256 ? ` · ${j.status.config_sha256.slice(0, 8)}` : ""}</span></Line>}
        <Line k={`Feed-gaten ${j.is_today ? "vandaag" : j.day}`}><span className={j.status.gaps_today ? "text-warn" : ""}>{j.status.gaps_today}{j.status.largest_gap_slots != null ? ` (grootste ${n0(j.status.largest_gap_slots)} slots)` : ""}</span></Line>
      </Panel>

      <Panel>
        <h2 className="m-0 mb-1 text-[13px] font-extrabold">Stopregel-teller</h2>
        <p className="m-0 mb-1 text-xs text-muted">
          {s.clock ? `Trades ingestapt vanaf de klokstart ${s.clock.date} (reden: ${s.clock.reason}). ` : "Geen klokstart in het journaal: telling van elke paper-dag, geen beslissing. "}
          {s.primary ? `Alleen de hoofdgroep (${s.primary.join(", ")}), zonder N2. ` : "Zonder N2. "}
          Alleen gevulde trades; de kosten van mislukte instappen tellen wel mee.
          {` Het oordeel komt vanaf ${s.min_trades} gevulde trades en ${s.min_days_ci} handelsdagen (dan is er een 95%-interval per dag). Vier weken (${s.clock_days} dagen) is de geplande lengte, geen minimum; de grens is dag ${s.clock_max_days}.`}
        </p>
        <Line k="Klok">{clockText(s)}</Line>
        <Line k="Trades · mislukt · dagen">{n0(s.trades)} · {n0(s.entry_failed)} · {s.days}</Line>
        <Line k="Netto per trade"><Num value={s.mean_sol_per_trade} signed format={{ minimumFractionDigits: 4, maximumFractionDigits: 4 }} suffix=" SOL" className={tone(s.mean_sol_per_trade)} /></Line>
        <Line k="95%-interval">{s.ci_lo == null ? `vanaf ${s.min_days_ci} dagen` : `${s.ci_lo.toFixed(4)} … ${s.ci_hi?.toFixed(4)} SOL`}</Line>
      </Panel>

      <Panel>
        <h2 className="m-0 mb-1 text-[13px] font-extrabold">Kapitaalsimulatie</h2>
        <p className="m-0 mb-1 text-xs text-muted">{n0(c.start_sol)} SOL, vaste grootte, hoogstens {c.max_positions} posities tegelijk; dezelfde trades als de stopregel-teller.</p>
        <Area ys={c.curve} id="g-cap" />
        <Line k="Eindstand"><span className={tone(c.final_sol - c.start_sol)}>{c.final_sol.toLocaleString("nl-NL", { maximumFractionDigits: 2 })} SOL</span></Line>
        <Line k="Grootste daling">{n1(c.max_drawdown * 100)}%</Line>
        <Line k="Genomen / overgeslagen">{n0(c.taken)} / {n0(c.skipped)}</Line>
      </Panel>

      {j.arms.map((g) => (
        <Panel key={g.group}>
          <h2 className="m-0 mb-1 text-[13px] font-extrabold">Groep {g.group}: meting, geen toets</h2>
          <p className="m-0 mb-1 text-xs text-muted">Telt niet mee voor de stopregel of de kapitaalsimulatie. Alle paper-dagen tot {j.is_today ? "vandaag" : j.day}.</p>
          {g.variants.map((x) => (
            <div key={x.variant}>
              <Line k={`${x.variant} · trades · mislukt · open`}>{n0(x.trades)} · {n0(x.entry_failed)} · {n0(x.open)}</Line>
              <Line k="Netto per trade"><Num value={x.net_sol_per_trade} signed format={{ minimumFractionDigits: 4, maximumFractionDigits: 4 }} suffix=" SOL" className={tone(x.net_sol_per_trade)} /></Line>
              <Line k="Totaal · vandaag"><span className={tone(x.net_sol)}>{x.net_sol.toFixed(3)} SOL</span> · <span className={tone(x.today_sol)}>{x.today_sol.toFixed(3)} SOL</span></Line>
            </div>
          ))}
        </Panel>
      ))}

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
