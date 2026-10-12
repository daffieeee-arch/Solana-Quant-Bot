import { useEffect, useState, type ReactNode } from "react";
import { get } from "../api";
import { Badge, Card, DexLink, Loading, Section, Tile } from "../components/ui";
import { n0, n1, shortAddr, sol, unixTime } from "../format";

// Paper tab: the paper engine's journal and the live recorder's meter (lab/ui/paper.py, read-only).
// Numbers mirror the engine's daily report (lab/paper/report.py); nothing here sends or signs anything.
interface Status {
  running: boolean; last_heartbeat_unix: number | null; last_slot: number | null; open_positions: number | null;
  gaps_today: number; largest_gap_slots: number | null; last_gap: string | null;
}
interface Group { trades: number; entry_failed: number; net_sol: number; net_sol_per_trade: number | null; win_rate: number | null; open: number }
interface Position {
  family: string; variant: string; key: string; venue: string | null; orientation: string | null; entry_slot: number;
  cost_sol: number | null; status: string; pnl_sol: number | null; ret?: number | null; mark_sol?: number | null; failed?: boolean;
}
interface Journal {
  journal: string; test_run: boolean; day: string; is_today: boolean; status: Status; totals: Group;
  variants: (Group & { family: string; variant: string })[]; positions: Position[];
  stop_rule: { trades: number; days: number; min_trades: number; min_days_ci: number; mean_sol_per_trade: number | null; ci_lo: number | null; ci_hi: number | null; verdict: string };
  capital: { start_sol: number; max_positions: number; final_sol: number; max_drawdown: number; taken: number; skipped: number; curve: number[] };
  n2: { family: string; variant: string; has_shadow: boolean; own_sol_per_trade: number | null; n2_sol_per_trade: number | null; diff_all: number | null; diff_today: number | null; n2_trades: number }[];
  holdout_rows_dropped: number;
}
interface Recorder {
  sample: boolean; day: string; is_today: boolean; streams: { stream: string; gb: number; messages: number }[];
  spent_gb: number | null; cap_gb: number; usd_per_gb: number; budget_usd: number; stopped: boolean; budget_day: string | null;
  level: number | null; dropped: string[]; last_segment_unix: number | null; segments_today: number; reconnects: number;
  notes: { unix: number; note: string }[];
}
interface PaperData { journal: Journal | null; recorder: Recorder | null; now_unix: number }

const REFRESH_MS = 30_000;
const EXIT: Record<string, string> = {
  take_profit: "winst genomen", stop_loss: "stop-loss", time: "tijdslimiet", window_end: "einde data", open: "open",
};
const VERDICT: Record<string, { text: string; tone: "buy" | "warn" | "sell" }> = {
  GO: { text: "GO", tone: "buy" }, ADJUST: { text: "BIJSTELLEN", tone: "warn" }, STOP: { text: "STOP", tone: "sell" },
};
const signed = (v: number | null | undefined, digits = 4) =>
  v == null ? "–" : (v > 0 ? "+" : "") + v.toLocaleString("nl-NL", { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " SOL";
const pctOf = (v: number | null | undefined) => (v == null ? "–" : n0(v * 100) + "%");
const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "text-buy" : v < 0 ? "text-sell" : "");
const gb = (v: number) => (v >= 0.1 ? n1(v) : v.toLocaleString("nl-NL", { maximumFractionDigits: 3 })) + " GB";
const ago = (secs: number) => (secs < 90 ? `${Math.round(secs)} s` : secs < 5400 ? `${Math.round(secs / 60)} min` : secs < 172800 ? `${n1(secs / 3600)} uur` : `${n0(secs / 86400)} dagen`);

function Spark({ ys }: { ys: number[] }) {
  if (ys.length < 2) return null;
  const lo = Math.min(...ys), hi = Math.max(...ys), w = 300, h = 60;
  const pts = ys.map((y, i) => `${(i / (ys.length - 1)) * w},${h - ((y - lo) / (hi - lo || 1)) * h}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16" preserveAspectRatio="none" role="img" aria-label="Verloop van het kapitaal">
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Row({ k, children }: { k: ReactNode; children: ReactNode }) {
  return <div className="flex justify-between gap-3 py-1 border-b border-line last:border-0"><span className="text-muted">{k}</span><span className="tabular text-right">{children}</span></div>;
}

export default function Paper() {
  const [data, setData] = useState<PaperData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [shown, setShown] = useState(20);

  useEffect(() => {
    let alive = true;
    const load = () => { if (document.visibilityState === "visible") get<PaperData>("paper").then((d) => { if (alive) { setData(d); setErr(null); } }).catch((e) => alive && setErr(e.message)); };
    load();
    const id = setInterval(load, REFRESH_MS);
    document.addEventListener("visibilitychange", load);
    return () => { alive = false; clearInterval(id); document.removeEventListener("visibilitychange", load); };
  }, []);

  if (err && !data) return <p className="text-warn">{err}</p>;
  if (!data) return <Loading what="Paper" />;
  const j = data.journal, r = data.recorder;
  const meter = r && (
        <Section title="Recorder-meter" note={`Data van Triton per stream, ${r.is_today ? "vandaag" : r.day} (UTC). Budget ${r.budget_usd.toLocaleString("nl-NL", { style: "currency", currency: "USD" })} per dag ≈ ${n0(r.cap_gb)} GB.`}
          actions={r.sample ? <Badge tone="warn">voorbeelddata</Badge> : undefined}>
          <Card className="px-3.5 py-3 grid gap-2 text-sm">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="tabular font-semibold text-lg">{n1(r.spent_gb)} / {n0(r.cap_gb)} GB</span>
              <span className="text-muted">≈ ${((r.spent_gb ?? 0) * r.usd_per_gb).toFixed(2)}</span>
              <span className="ml-auto">{r.stopped ? <Badge tone="sell">gestopt: budget op</Badge> : r.level ? <Badge tone="warn">afgeschaald, niveau {r.level}</Badge> : <Badge tone="buy">alle streams aan</Badge>}</span>
            </div>
            <div className="h-2 rounded-full bg-surface-2 overflow-hidden">
              <div className={`h-full ${r.stopped ? "bg-sell" : r.level ? "bg-warn" : "bg-accent"}`} style={{ width: `${Math.min(100, (100 * (r.spent_gb ?? 0)) / (r.cap_gb || 1))}%` }} />
            </div>
            {r.streams.map((s) => (
              <div key={s.stream} className="grid grid-cols-[8.5rem_1fr_4rem] items-center gap-2">
                <span className="font-mono text-xs truncate">{s.stream}</span>
                <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden"><div className="h-full bg-accent" style={{ width: `${Math.min(100, (100 * s.gb) / Math.max(...r.streams.map((x) => x.gb), 1e-9))}%` }} /></div>
                <span className="tabular text-right">{gb(s.gb)}</span>
              </div>
            ))}
            {!!r.dropped.length && <Row k="Uitgezet">{r.dropped.join(", ")}</Row>}
            <Row k="Laatste segment">{unixTime(r.last_segment_unix)}</Row>
            <Row k="Herverbindingen">{r.reconnects}</Row>
            {r.notes.map((n) => <p key={n.unix} className="m-0 text-xs text-muted"><span className="tabular">{unixTime(n.unix)}</span> · {n.note}</p>)}
          </Card>
        </Section>
  );


  return (
    <div className="grid grid-cols-1 gap-6">
      <div className="rounded-lg border-2 border-warn bg-warn-soft px-3.5 py-3 text-sm grid gap-1">
        <p className="m-0 font-semibold">Paper trading: er wordt niets verstuurd, geen echt geld.</p>
        {j?.test_run && (
          <p className="m-0">Testdata: dit is de pariteitsrun op ontwikkeldata ({j.journal}), geen live run. De echte paper-run verschijnt hier vanzelf.</p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-1">
        <h1 className="text-2xl font-semibold m-0 tracking-tight">Paper</h1>
        <p className="text-muted text-sm m-0">Ververst elke 30 seconden. Tijden in UTC.</p>
      </div>

      {!j ? <><p className="text-muted">Nog geen paper-journaal.</p>{meter}</> : (
        <>
          <Card className="px-3.5 py-3 grid gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone={j.status.running ? "buy" : "sell"}>{j.status.running ? "draait" : "gestopt"}</Badge>
              <span className="text-sm text-muted">journaal <span className="font-mono">{j.journal}</span></span>
            </div>
            <div className="text-sm">
              <Row k="Laatste heartbeat">{j.status.last_heartbeat_unix == null ? "–" : `${unixTime(j.status.last_heartbeat_unix)} (${ago(data.now_unix - j.status.last_heartbeat_unix)} geleden)`}</Row>
              <Row k="Laatste slot">{n0(j.status.last_slot)}</Row>
              <Row k={`Feed-gaten ${j.is_today ? "vandaag" : j.day}`}>
                <span className={j.status.gaps_today ? "text-warn" : ""}>{j.status.gaps_today}{j.status.largest_gap_slots != null ? ` (grootste ${n0(j.status.largest_gap_slots)} slots)` : ""}</span>
              </Row>
            </div>
          </Card>

          <Section title={j.is_today ? "Vandaag" : `Laatste dag met data: ${j.day}`}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <Tile value={n0(j.totals.trades)} label={`trades${j.totals.entry_failed ? ` (+${j.totals.entry_failed} mislukte instap)` : ""}`} />
              <Tile value={<span className={tone(j.totals.net_sol)}>{signed(j.totals.net_sol, 2)}</span>} label="netto na kosten" />
              <Tile value={pctOf(j.totals.win_rate)} label="winrate" />
              <Tile value={n0(j.totals.open)} label="open posities" />
            </div>
            {j.variants.length > 1 && (
              <Card className="px-3.5 py-2 text-sm">
                {j.variants.map((v) => <Row key={v.family + v.variant} k={`${v.family} ${v.variant}`}>{v.trades} trades · <span className={tone(v.net_sol)}>{signed(v.net_sol, 2)}</span></Row>)}
              </Card>
            )}
          </Section>

          <Section title="Stopregel-teller" note={`Alle paper-dagen samen, zonder N2. Een oordeel pas vanaf ${j.stop_rule.min_trades} trades; het 95%-interval (bootstrap per dag) vanaf ${j.stop_rule.min_days_ci} dagen.`}>
            <Card className="px-3.5 py-3 grid gap-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="tabular font-semibold text-lg">{n0(j.stop_rule.trades)} / {j.stop_rule.min_trades}</span>
                <span className="text-muted">trades · {j.stop_rule.days} {j.stop_rule.days === 1 ? "dag" : "dagen"}</span>
                <span className="ml-auto">{j.stop_rule.trades < j.stop_rule.min_trades ? <Badge tone="muted">te vroeg</Badge>
                  : <Badge tone={VERDICT[j.stop_rule.verdict]?.tone ?? "muted"}>{VERDICT[j.stop_rule.verdict]?.text ?? j.stop_rule.verdict}</Badge>}</span>
              </div>
              <div className="h-2 rounded-full bg-surface-2 overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={j.stop_rule.min_trades} aria-valuenow={Math.min(j.stop_rule.trades, j.stop_rule.min_trades)}>
                <div className="h-full bg-accent" style={{ width: `${Math.min(100, (100 * j.stop_rule.trades) / j.stop_rule.min_trades)}%` }} />
              </div>
              <Row k="Netto per trade"><span className={tone(j.stop_rule.mean_sol_per_trade)}>{signed(j.stop_rule.mean_sol_per_trade)}</span></Row>
              <Row k="95%-interval">{j.stop_rule.ci_lo == null ? `vanaf ${j.stop_rule.min_days_ci} dagen` : `${signed(j.stop_rule.ci_lo)} … ${signed(j.stop_rule.ci_hi)}`}</Row>
            </Card>
          </Section>

          <Section title="Kapitaalsimulatie" note={`${n0(j.capital.start_sol)} SOL, vaste grootte per trade, hoogstens ${j.capital.max_positions} posities tegelijk; wat niet past wordt overgeslagen.`}>
            <Card className="px-3.5 py-3 grid gap-2 text-sm">
              <div className={tone(j.capital.final_sol - j.capital.start_sol)}><Spark ys={j.capital.curve} /></div>
              <Row k="Eindstand"><span className={tone(j.capital.final_sol - j.capital.start_sol)}>{sol(j.capital.final_sol)}</span></Row>
              <Row k="Grootste daling">{n1(j.capital.max_drawdown * 100)}%</Row>
              <Row k="Trades genomen / overgeslagen">{n0(j.capital.taken)} / {n0(j.capital.skipped)}</Row>
            </Card>
          </Section>

          <Section title="Verschil met N2" note="N2 is de schaduw-controle: dezelfde strategie met een willekeurige instap op een vergelijkbare token. Positief = beter dan toeval.">
            <Card className="px-3.5 py-2 text-sm">
              {j.n2.map((x) => !x.has_shadow
                ? <Row key={x.family + x.variant} k={`${x.family} ${x.variant}`}><span className="text-muted">geen N2-schaduw in dit journaal</span></Row>
                : (
                  <div key={x.family + x.variant} className="py-1">
                    <div className="font-medium">{x.family} {x.variant}</div>
                    <Row k="Per trade: strategie / N2">{signed(x.own_sol_per_trade)} / {signed(x.n2_sol_per_trade)}</Row>
                    <Row k="Verschil, alle dagen"><span className={tone(x.diff_all)}>{signed(x.diff_all)}</span></Row>
                    <Row k={j.is_today ? "Verschil vandaag" : `Verschil ${j.day}`}><span className={tone(x.diff_today)}>{signed(x.diff_today)}</span></Row>
                  </div>
                ))}
            </Card>
          </Section>
          {meter}

          <Section title="Posities" note="Open posities eerst, dan de afgesloten trades van die dag (nieuwste eerst). Netto na alle kosten.">
            {!j.positions.length ? <p className="text-muted text-sm m-0">Geen posities.</p> : (
              <Card className="divide-y divide-line">
                {j.positions.slice(0, shown).map((p) => (
                  <div key={`${p.family}|${p.key}|${p.entry_slot}`} className="px-3.5 py-2.5 grid gap-0.5 text-sm">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge tone={p.status === "open" ? "accent" : "muted"}>{p.failed ? "instap mislukt" : EXIT[p.status] ?? p.status}</Badge>
                      <span className="font-mono">{shortAddr(p.key)}</span>
                      <DexLink mint={p.key}>DexScreener ↗</DexLink>
                      <span className={`ml-auto tabular font-medium ${tone(p.pnl_sol)}`}>
                        {p.status === "open" ? (p.mark_sol != null ? signed(p.pnl_sol) : "nog geen koersstand") : signed(p.pnl_sol)}
                        {p.ret != null ? ` (${(p.ret > 0 ? "+" : "") + n1(p.ret * 100)}%)` : ""}
                      </span>
                    </div>
                    <div className="text-muted text-xs">
                      {p.family} {p.variant} · {p.venue === "pool" ? `pool${p.orientation === "reversed" ? " (omgekeerd)" : ""}` : p.venue ?? "–"} · instap {sol(p.cost_sol)} op slot {n0(p.entry_slot)}
                    </div>
                  </div>
                ))}
              </Card>
            )}
            {j.positions.length > shown && (
              <button type="button" onClick={() => setShown(shown + 50)} className="justify-self-start text-sm px-3 py-1.5 rounded-md border border-line bg-surface">
                Toon meer ({j.positions.length - shown} over)
              </button>
            )}
          </Section>

        </>
      )}

    </div>
  );
}
