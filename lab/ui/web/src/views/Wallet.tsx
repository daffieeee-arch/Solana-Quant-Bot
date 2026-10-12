import type { ReactNode } from "react";
import { Badge, DexLink, Loading } from "../components/ui";
import { Num } from "../components/Num";
import { useLive } from "../live";
import { cn } from "../lib/utils";
import { label, shortAddr, unixTime } from "../format";

// Wallet (design v1, "Wallet (live) — iPhone"). Read-only: no keys, no node calls. The paper part is real (journal,
// risk.yaml); the on-chain part is the live engine's wallet.json, shown as "voorbeelddata" until it exists.
interface PaperTx { key: string; kind: string; slot: number; time_unix: number | null; amount_sol: number; status: string;
  delay_slots: number | null; reason: string | null; ret: number | null; symbol?: string | null; mint?: string | null }
interface ChainTx { signature: string; slot: number | null; time_unix: number | null; kind: string; mint: string | null; symbol: string | null;
  amount_sol: number; fee_sol: number; tip_sol: number; status: string; landed_slot_delta: number | null; note: string | null }
interface WalletData {
  address: string | null; now_unix: number;
  paper: { journal: string; test_run: boolean; day: string; is_today: boolean; start_sol: number; balance_sol: number; realised_sol: number;
    sim_taken: number; sim_skipped: number; in_positions_sol: number; open_positions: number; today_net_sol: number; failed_today: number;
    failed_cost_sol: number; daily_loss_limit_sol: number | null; risk_found: boolean; kill_switch: boolean | null;
    max_open_positions: number | null; size_sol: number | null; txs: PaperTx[] } | null;
  onchain: { sample: boolean; mode: "paper" | "onchain"; address: string | null; slot: number | null; time_unix: number | null; balance_sol: number | null;
    note: string | null; txs: ChainTx[] } | null;
}
const EXIT: Record<string, string> = { take_profit: "doel geraakt", stop_loss: "stop-loss", time: "tijdslimiet", window_end: "einde data" };
const f3 = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("nl-NL", { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`;
const solscanAccount = (a: string) => `https://solscan.io/account/${encodeURIComponent(a)}`;

function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={cn("rounded-2xl p-3.5 bg-surface border border-line min-w-0", className)}>{children}</section>;
}

function TxRow({ icon, tone, title, meta, amount, amountTone, status, statusTone }: {
  icon: string; tone: "buy" | "accent" | "sell"; title: ReactNode; meta: ReactNode; amount: string; amountTone: string; status: string; statusTone: string;
}) {
  const ic = { buy: "bg-buy-soft text-buy", accent: "bg-accent-soft text-accent-fg", sell: "bg-sell-soft text-sell" }[tone];
  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-line-soft last:border-0 min-w-0">
      <span className={cn("w-9 h-9 rounded-xl inline-flex items-center justify-center font-extrabold text-sm shrink-0", ic)} aria-hidden="true">{icon}</span>
      <div className="flex flex-col gap-0.5 grow min-w-0">
        <span className="font-bold text-sm truncate">{title}</span>
        <span className="text-[11px] text-muted truncate">{meta}</span>
      </div>
      <div className="flex flex-col items-end gap-0.5 shrink-0">
        <span className={cn("num text-sm font-semibold", amountTone)}>{amount}</span>
        <span className={cn("text-[11px] font-semibold", statusTone)}>{status}</span>
      </div>
    </div>
  );
}

export default function Wallet() {
  const { data, error } = useLive<WalletData>("wallet");
  if (error && !data) return <p className="text-warn">{error}</p>;
  if (!data) return <Loading what="Wallet" />;
  const p = data.paper, oc = data.onchain;
  const enginePaper = oc?.mode === "paper" && !oc.sample; // the engine's paper wallet: simulated, no address
  const address = enginePaper ? null : data.address ?? (oc && !oc.sample ? oc.address : null);
  const limit = p?.daily_loss_limit_sol ?? null;
  const lossToday = p ? Math.min(0, p.today_net_sol) : 0;

  return (
    <div className="grid gap-3.5 lg:grid-cols-2 lg:items-start">
      <div className="grid gap-3.5 min-w-0">
        {/* On-chain wallet: the live engine's file; until then the marked sample. */}
        <section className={cn("hero rounded-[20px] p-[18px] grid gap-3 min-w-0", oc?.sample && "!border-2 !border-warn")}>
          <div className="flex items-center gap-3 min-w-0">
            <span className="w-9 h-9 rounded-xl bg-accent inline-flex items-center justify-center shrink-0" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 18 18"><rect x="2" y="4" width="14" height="10" rx="3" stroke="#0A0D13" strokeWidth="1.8" fill="none" /><circle cx="12.5" cy="9" r="1.3" fill="#0A0D13" /></svg>
            </span>
            <div className="flex flex-col gap-0.5 min-w-0">
              <span className="text-[13px] text-fg-2 font-semibold">{enginePaper ? "Paper-wallet van de bot" : "Bot-wallet (apart van je Phantom)"}</span>
              <span className="text-[11px] text-muted num truncate">
                {enginePaper ? "geen adres (paper)" : address ? <>{shortAddr(address)} · <a href={solscanAccount(address)} target="_blank" rel="noopener noreferrer" className="text-accent-fg font-bold">Solscan ↗</a></> : "nog geen adres ingesteld"}
              </span>
            </div>
            {oc?.sample ? <span className="ml-auto shrink-0 whitespace-nowrap"><Badge tone="warn">voorbeelddata</Badge></span>
              : enginePaper ? <span className="ml-auto shrink-0 whitespace-nowrap"><Badge tone="accent">paper (gesimuleerd)</Badge></span> : null}
          </div>
          <div className="flex items-baseline gap-2.5">
            <Num value={oc?.balance_sol} format={{ minimumFractionDigits: 2, maximumFractionDigits: 2 }} className="text-[40px] leading-none font-semibold tracking-[-1.5px]" />
            <span className="text-base text-fg-2 font-bold">SOL</span>
          </div>
          <p className="m-0 text-[11px] text-muted">
            {oc?.sample ? "Voorbeeld van het formaat. Het echte on-chain saldo verschijnt als de live-engine wallet.json schrijft; deze pagina vraagt zelf niets op bij een node."
              : enginePaper ? `Gesimuleerd door de paper-engine: 10 SOL plus gerealiseerde winst, min open posities tegen inleg. Er bestaat geen wallet en er wordt niets verstuurd (besluit eigenaar). Stand op slot ${oc?.slot?.toLocaleString("nl-NL") ?? "–"}.`
              : `Stand op slot ${oc?.slot?.toLocaleString("nl-NL") ?? "–"} · ${unixTime(oc?.time_unix)} UTC`}
          </p>
        </section>

        {p && (
          <Panel className="grid gap-2">
            <div className="flex justify-between items-baseline gap-2 flex-wrap">
              <span className="text-[13px] font-extrabold">{enginePaper ? "Kapitaalsimulatie (controle)" : "Paper-wallet"}</span>
              {p.test_run ? <Badge tone="warn">testdata · {p.journal}</Badge> : <span className="text-[11px] text-muted">{p.journal}</span>}
            </div>
            <div className="flex items-baseline gap-2">
              <Num value={p.balance_sol} format={{ minimumFractionDigits: 2, maximumFractionDigits: 2 }} className="text-[28px] font-semibold" />
              <span className="text-sm text-fg-2 font-bold">SOL</span>
              <span className={cn("ml-auto num text-sm", p.balance_sol >= p.start_sol ? "text-buy" : "text-sell")}>{p.balance_sol >= p.start_sol ? "▲" : "▼"} {f3(p.balance_sol - p.start_sol)}</span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="flex flex-col gap-0.5"><span className="text-[11px] text-muted">Startkapitaal</span><span className="num font-semibold">{p.start_sol.toLocaleString("nl-NL", { minimumFractionDigits: 2 })}</span></div>
              <div className="flex flex-col gap-0.5"><span className="text-[11px] text-muted">In posities</span><span className="num font-semibold">{p.in_positions_sol.toLocaleString("nl-NL", { minimumFractionDigits: 2 })}</span></div>
              <div className="flex flex-col gap-0.5"><span className="text-[11px] text-muted">Mislukte tx {p.is_today ? "vandaag" : p.day.slice(5)}</span><span className="num font-semibold">{p.failed_today} · {p.failed_cost_sol.toLocaleString("nl-NL", { maximumFractionDigits: 3 })}</span></div>
            </div>
            <p className="m-0 text-[11px] text-muted">Saldo volgens de kapitaalsimulatie: {p.start_sol} SOL, hoogstens {p.max_open_positions ?? 5} posities tegelijk ({p.sim_taken.toLocaleString("nl-NL")} trades genomen, {p.sim_skipped.toLocaleString("nl-NL")} overgeslagen).</p>
          </Panel>
        )}

        {p && (
          <Panel className="grid gap-2">
            <div className="flex justify-between gap-2"><span className="text-[13px] font-extrabold">Daglimiet verlies</span>
              <span className="num text-sm">{f3(lossToday)} / {limit == null ? "–" : `−${limit.toLocaleString("nl-NL", { minimumFractionDigits: 2 })}`} SOL</span></div>
            <div className="h-2 rounded bg-track overflow-hidden">
              <div className={cn("h-2", limit && -lossToday >= limit ? "bg-sell-strong" : "bg-warn")} style={{ width: `${limit ? Math.min(100, (100 * -lossToday) / limit) : 0}%` }} />
            </div>
            <span className="text-[11px] text-muted">
              {limit == null ? "Geen risk.yaml gevonden." : `Bij −${limit.toLocaleString("nl-NL")} SOL gerealiseerd verlies opent de bot die dag niets meer, tot 00:00 UTC.`}
              {p.kill_switch ? " Noodschakelaar staat AAN: de bot opent niets." : ""}
            </span>
          </Panel>
        )}

        <button type="button" disabled aria-disabled="true"
          className="min-h-12 rounded-2xl border border-sell-soft bg-sell-soft/40 text-sell/60 font-extrabold text-sm cursor-not-allowed">
          Noodstop: bot pauzeren · komt in live-fase
        </button>
      </div>

      <div className="grid gap-3.5 min-w-0">
        {p && (
          <Panel>
            <div className="flex justify-between items-baseline mb-1"><h2 className="m-0 text-base font-extrabold">Paper-transacties</h2><span className="text-[11px] text-muted">gesimuleerd · niets verstuurd</span></div>
            {!p.txs.length ? <p className="text-sm text-muted m-0">Nog geen transacties.</p> : p.txs.slice(0, 20).map((t, i) => {
              const sym = label(t.symbol, 16) || shortAddr(t.mint ?? t.key);
              const failed = t.status !== "filled";
              return failed
                ? <TxRow key={i} icon="!" tone="sell" title={<>Koop {sym}</>} meta={<>{t.reason ?? t.status} · <DexLink mint={t.mint ?? t.key}>Dex ↗</DexLink></>} amount={f3(t.amount_sol)} amountTone="text-muted" status="mislukt" statusTone="text-sell" />
                : t.kind === "buy"
                  ? <TxRow key={i} icon="K" tone="buy" title={<>Koop {sym}</>} meta={<>{t.delay_slots != null ? `gevuld in slot +${t.delay_slots}` : "gevuld"} · {unixTime(t.time_unix)}</>} amount={f3(t.amount_sol)} amountTone="text-fg" status="gevuld" statusTone="text-buy" />
                  : <TxRow key={i} icon="V" tone="accent" title={<>Verkoop {sym}</>} meta={<>{EXIT[t.reason ?? ""] ?? t.reason ?? "verkocht"}{t.ret != null ? ` ${t.ret > 0 ? "+" : "−"}${Math.abs(t.ret * 100).toLocaleString("nl-NL", { maximumFractionDigits: 1 })}%` : ""} · {unixTime(t.time_unix)}</>}
                    amount={f3(t.amount_sol)} amountTone={(t.ret ?? 0) >= 0 ? "text-buy" : "text-sell"} status="gevuld" statusTone="text-buy" />;
            })}
          </Panel>
        )}

        {oc && !enginePaper && (
          <Panel>
            <div className="flex justify-between items-baseline mb-1 gap-2"><h2 className="m-0 text-base font-extrabold">On-chain transacties</h2>
              {oc.sample ? <Badge tone="warn">voorbeelddata</Badge> : <span className="text-[11px] text-muted">landing · tip · fee</span>}</div>
            {oc.txs.map((t, i) => {
              const sym = t.symbol ? label(t.symbol, 16) : t.mint ? shortAddr(t.mint) : "";
              const ok = t.status === "landed";
              return <TxRow key={i} icon={!ok ? "!" : t.kind === "buy" ? "K" : "V"} tone={!ok ? "sell" : t.kind === "buy" ? "buy" : "accent"}
                title={`${t.kind === "buy" ? "Koop" : t.kind === "sell" ? "Verkoop" : "Tx"} ${sym}`}
                meta={`${ok && t.landed_slot_delta != null ? `landde in slot +${t.landed_slot_delta}` : t.note ?? "niet geland"} · tip ${t.tip_sol.toLocaleString("nl-NL", { maximumFractionDigits: 4 })} · fee ${t.fee_sol.toLocaleString("nl-NL", { maximumFractionDigits: 6 })}`}
                amount={f3(t.amount_sol)} amountTone={!ok ? "text-muted" : t.amount_sol > 0 ? "text-buy" : "text-fg"} status={ok ? "geland" : "mislukt"} statusTone={ok ? "text-buy" : "text-sell"} />;
            })}
          </Panel>
        )}
      </div>
    </div>
  );
}
