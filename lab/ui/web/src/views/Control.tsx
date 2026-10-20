import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { get, postAction } from "../api";
import { Badge, Loading } from "../components/ui";
import { Button } from "../components/Button";
import { cn } from "../lib/utils";
import { unixTime } from "../format";

// Bediening (B0b, interactive-UI spec v0.3 §2): pairing a device, actions that need a confirmation, and the audit
// log. The server checks everything (lab/ui/actions.py): who is calling (Tailscale), the session, CSRF, the
// confirmation, the rate and the Tailscale exposure; the executor runs only its own allowlist (lab/ops/control).
// Paper only: no key, no signing, nothing is sent. Bot actions (pause, start/stop) come only after variant A.
interface Session { paired: true; name: string; csrf: string; expires_unix: number; actions_off: string | null; actions: Record<string, string> }
interface Ev { ts: number; source: string; event: string; action?: string | null; outcome?: string; reason?: string; error?: string | null; name?: string; request_id?: string | null; status?: number }
type State = { kind: "loading" } | { kind: "session"; s: Session } | { kind: "unpaired"; why: string } | { kind: "blocked"; why: string };

const ACTION_LABEL: Record<string, string> = { ping: "Test: is de executor bereikbaar?" };
const EVENT_LABEL: Record<string, string> = {
  paired: "gekoppeld", prepare: "bevestiging gevraagd", commit: "bevestigd", executed: "uitgevoerd", refused: "geweigerd",
};

function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={cn("rounded-2xl p-3.5 bg-surface border border-line min-w-0", className)}>{children}</section>;
}

function Pairing({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = () => {
    setBusy(true);
    setErr(null);
    postAction("pair", { code }).then(onDone).catch((e) => setErr(e.message)).finally(() => setBusy(false));
  };
  return (
    <Panel className="grid gap-3">
      <h2 className="m-0 text-base font-extrabold">Dit apparaat koppelen</h2>
      <p className="m-0 text-sm text-fg-2">
        Draai op de VPS <code className="num text-xs px-1 py-0.5 rounded bg-surface-2">python3 -I lab/ui/pair.py</code> en typ de code hier
        (20 tekens, 5 minuten geldig, één keer bruikbaar). Alleen je eigen telefoon en laptop staan op de lijst.
      </p>
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <input value={code} onChange={(e) => setCode(e.target.value)} autoCapitalize="characters" autoComplete="one-time-code" spellCheck={false}
          aria-label="Koppelcode" placeholder="XXXX-XXXX-XXXX-XXXX-XXXX" maxLength={40}
          className="num grow min-w-[220px] min-h-11 px-3 rounded-xl bg-surface-2 border border-line text-sm tracking-wider" />
        <Button type="submit" disabled={busy || code.replace(/[^0-9a-z]/gi, "").length < 20}>Koppelen</Button>
      </form>
      {err && <p className="m-0 text-sm text-sell">{err}</p>}
    </Panel>
  );
}

function ActionButton({ s, action, onLog, params = {}, label, disabled = false, bare = false }: {
  s: Session; action: string; onLog: () => void; params?: Record<string, unknown>; label?: string; disabled?: boolean; bare?: boolean;
}) {
  const [confirm, setConfirm] = useState<{ nonce: string; text: string } | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ask = () => {
    setErr(null);
    setStatus(null);
    postAction<{ nonce: string; text: string }>("prepare", { csrf: s.csrf, action, params }).then(setConfirm).catch((e) => setErr(e.message));
  };
  const go = async () => {
    const c = confirm;
    setConfirm(null);
    if (!c) return;
    try {
      const { id } = await postAction<{ id: string }>("commit", { csrf: s.csrf, nonce: c.nonce });
      setStatus("wacht op de executor …");
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const r = await get<{ state: string; result?: { ok?: boolean; error?: string } }>("action/result", { id });
        if (r.state === "done" || r.state === "refused") {
          setStatus(r.state === "done" ? "uitgevoerd" : `geweigerd: ${r.result?.error ?? "?"}`);
          onLog();
          return;
        }
      }
      setStatus("niet uitgevoerd: de executor antwoordde niet binnen 30 s");
    } catch (e) {
      setErr((e as Error).message);
    }
    onLog();
  };
  return (
    <div className={cn("flex flex-wrap items-center gap-3", !bare && "py-2 border-b border-line-soft last:border-0")}>
      {!bare && (
        <div className="grow min-w-0">
          <div className="text-sm font-bold">{ACTION_LABEL[action] ?? action}</div>
          <div className="text-[11px] text-muted">{s.actions[action]}</div>
        </div>
      )}
      <Button type="button" onClick={ask} disabled={!!s.actions_off || disabled}>{label ?? "Uitvoeren…"}</Button>
      {(status || err) && <span className={cn("w-full text-xs", err ? "text-sell" : "text-fg-2")}>{err ?? status}</span>}
      <Dialog.Root open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60 z-40" />
          <Dialog.Content className="fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[min(92vw,420px)] rounded-2xl bg-surface border border-line p-4 grid gap-3">
            <Dialog.Title className="m-0 text-base font-extrabold">Bevestigen</Dialog.Title>
            <Dialog.Description className="m-0 text-sm text-fg-2">{confirm?.text}</Dialog.Description>
            <p className="m-0 text-[11px] text-muted">Geldig 60 seconden. Wordt vastgelegd in het logboek.</p>
            <div className="flex gap-2 justify-end">
              <Dialog.Close asChild><Button type="button" variant="ghost">Annuleren</Button></Dialog.Close>
              <Button type="button" onClick={go}>Bevestig</Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

// The strategy chooser (B3b): a backtest on the NAS from a fixed template, its queue and its result beside K3.
interface JobRow { variant: string; d: number; regime: string; n: number; filled: number; days: number; mean_pnl_sol: number | null;
  pnl_ci_lo: number | null; pnl_ci_hi: number | null; sum_sol_per_day: number | null; failed_entries: number }
interface Job { id: string; state: string; params?: Record<string, string | number>; reason?: string | null; ts?: number;
  result?: { rows?: JobRow[]; k3?: string; variant?: string; note?: string } }
interface Jobs { jobs: Job[]; in_flight: boolean; dispatcher: { state: "off" | "stale" | "error" | "ok"; last_error?: string | null };
  options: { grids: Record<string, string[]>; k3: string; segments: string[]; farms: string[]; sizes: number[]; delays_ms: number[];
    max_epochs: number; stores: Record<string, { from: number; to: number }> } }
const JOB_STATE: Record<string, string> = {
  queued_here: "in de wachtrij (VPS)", sent: "naar de NAS gestuurd", queued: "in de wachtrij op de NAS", running: "draait op de NAS",
  done: "klaar", failed: "mislukt", refused: "geweigerd",
};
const DISPATCHER: Record<string, string> = {
  off: "De NAS-runner is nog niet geïnstalleerd: jobs wachten in de wachtrij tot de coördinator hem na de paperstart installeert.",
  stale: "De dispatcher heeft al 10 minuten niet gedraaid: jobs wachten.", error: "De laatste verbinding met de NAS mislukte; de dispatcher probeert het opnieuw.",
};
const sol4 = (v: number | null | undefined) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("nl-NL", { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`);

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="grid gap-1 text-[11px] text-muted min-w-0">{label}{children}</label>;
}
const selCls = "min-h-10 px-2 rounded-lg bg-surface-2 border border-line text-sm text-fg";

function Jobs({ s, refresh, onLog }: { s: Session; refresh: number; onLog: () => void }) {
  const [d, setD] = useState<Jobs | null>(null);
  const [f, setF] = useState({ variant: "flow300_tp100_H6", segment: "pool_reversed", farm: "organic", size_sol: 0.5, d_ms: 600, store: "dev2", epoch_from: 1040, epoch_to: 1046 });
  useEffect(() => { get<Jobs>("ui_jobs").then(setD).catch(() => setD(null)); }, [refresh]);
  if (!d) return null;
  const st = d.options.stores[f.store];
  const valid = st && f.epoch_from >= st.from && f.epoch_to <= st.to && f.epoch_from <= f.epoch_to && f.epoch_to - f.epoch_from + 1 <= d.options.max_epochs;
  const set = (k: string, v: string | number) => setF((o) => ({ ...o, [k]: v }));
  const epochs = st ? Array.from({ length: st.to - st.from + 1 }, (_, i) => st.from + i) : [];
  return (
    <Panel className="grid gap-3">
      <div>
        <h2 className="m-0 text-base font-extrabold">Backtest op de NAS</h2>
        <p className="m-0 text-[11px] text-muted">Een vast sjabloon: één F7-variant naast K3 ({d.options.k3}) op ontwikkeldata. Hold-out en de clean-periode zijn niet te kiezen. Verandert niets aan de paper-bot. Elke job telt als een trial: verkennend, geen bewijs.</p>
      </div>
      {d.dispatcher.state !== "ok" && <p className="m-0 px-3 py-2 rounded-xl bg-surface-2 border border-line text-xs text-fg-2">{DISPATCHER[d.dispatcher.state]}</p>}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Field label="Variant"><select className={selCls} value={f.variant} onChange={(e) => set("variant", e.target.value)}>
          {Object.entries(d.options.grids).map(([g, vs]) => <optgroup key={g} label={g}>{vs.map((v) => <option key={v} value={v}>{v}</option>)}</optgroup>)}
        </select></Field>
        <Field label="Segment"><select className={selCls} value={f.segment} onChange={(e) => set("segment", e.target.value)}>{d.options.segments.map((v) => <option key={v}>{v}</option>)}</select></Field>
        <Field label="Farm"><select className={selCls} value={f.farm} onChange={(e) => set("farm", e.target.value)}>{d.options.farms.map((v) => <option key={v}>{v}</option>)}</select></Field>
        <Field label="Grootte (SOL)"><select className={selCls} value={f.size_sol} onChange={(e) => set("size_sol", Number(e.target.value))}>{d.options.sizes.map((v) => <option key={v} value={v}>{v}</option>)}</select></Field>
        <Field label="Vertraging (ms)"><select className={selCls} value={f.d_ms} onChange={(e) => set("d_ms", Number(e.target.value))}>{d.options.delays_ms.map((v) => <option key={v} value={v}>{v}</option>)}</select></Field>
        <Field label="Store"><select className={selCls} value={f.store} onChange={(e) => { const o = d.options.stores[e.target.value]; setF((x) => ({ ...x, store: e.target.value, epoch_from: Math.max(o.from, o.to - d.options.max_epochs + 1), epoch_to: o.to })); }}>
          {Object.entries(d.options.stores).map(([k, o]) => <option key={k} value={k}>{k} ({o.from}–{o.to})</option>)}</select></Field>
        <Field label="Van epoch"><select className={selCls} value={f.epoch_from} onChange={(e) => set("epoch_from", Number(e.target.value))}>{epochs.map((v) => <option key={v}>{v}</option>)}</select></Field>
        <Field label="Tot en met epoch"><select className={selCls} value={f.epoch_to} onChange={(e) => set("epoch_to", Number(e.target.value))}>{epochs.map((v) => <option key={v}>{v}</option>)}</select></Field>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <ActionButton bare s={s} action="nas_job" params={f} label="Backtest aanvragen…" disabled={!valid || d.in_flight} onLog={onLog} />
        {!valid && <span className="text-[11px] text-warn">Kies hoogstens {d.options.max_epochs} epochs binnen de store.</span>}
        {d.in_flight && <span className="text-[11px] text-muted">Er loopt al een job (één tegelijk).</span>}
      </div>
      {!!d.jobs.length && (
        <div className="grid gap-2">
          <h3 className="m-0 text-xs font-extrabold text-fg-2">Jobs</h3>
          {d.jobs.map((j) => (
            <div key={j.id} className="rounded-xl border border-line p-2.5 grid gap-1.5 min-w-0">
              <div className="flex flex-wrap justify-between gap-2 text-xs">
                <span className="font-bold">{String(j.params?.variant ?? "?")} <span className="font-normal text-muted">{String(j.params?.segment ?? "")} · {String(j.params?.farm ?? "")} · {String(j.params?.size_sol ?? "")} SOL · {String(j.params?.d_ms ?? "")} ms · {String(j.params?.store ?? "")} {String(j.params?.epoch_from ?? "")}–{String(j.params?.epoch_to ?? "")}</span></span>
                <span className={cn(j.state === "done" ? "text-buy" : j.state === "failed" || j.state === "refused" ? "text-sell" : "text-fg-2")}>{JOB_STATE[j.state] ?? j.state}{j.reason ? `: ${j.reason}` : ""}</span>
              </div>
              {j.result?.rows && (
                <div className="overflow-x-auto"><table className="w-full text-[11px] num border-collapse">
                  <thead><tr className="text-muted text-left"><th className="py-1 pr-2 font-normal">variant</th><th className="font-normal">d · regime</th><th className="font-normal text-right">trades</th><th className="font-normal text-right">SOL/trade</th><th className="font-normal text-right">95%-interval</th><th className="font-normal text-right">Σ/dag</th></tr></thead>
                  <tbody>{j.result.rows.map((r, i) => (
                    <tr key={i} className={cn("border-t border-line-soft", r.variant === j.result?.k3 && "text-fg-2")}>
                      <td className="py-1 pr-2">{r.variant}{r.variant === j.result?.k3 ? " (K3)" : ""}</td><td>{r.d} · {r.regime}</td><td className="text-right">{r.filled.toLocaleString("nl-NL")}</td>
                      <td className={cn("text-right", (r.mean_pnl_sol ?? 0) > 0 ? "text-buy" : (r.mean_pnl_sol ?? 0) < 0 ? "text-sell" : "")}>{sol4(r.mean_pnl_sol)}</td>
                      <td className="text-right">{sol4(r.pnl_ci_lo)} … {sol4(r.pnl_ci_hi)}</td><td className="text-right">{r.sum_sol_per_day == null ? "–" : sol4(r.sum_sol_per_day)}</td>
                    </tr>))}</tbody>
                </table><p className="m-0 mt-1 text-[10px] text-muted">De rijen van het toernooirapport zelf (base-kosten, dag-bootstrap). {j.result.note}</p></div>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function Log({ refresh }: { refresh: number }) {
  const [events, setEvents] = useState<Ev[] | null>(null);
  useEffect(() => { get<{ events: Ev[] }>("action/log").then((d) => setEvents(d.events)).catch(() => setEvents([])); }, [refresh]);
  return (
    <Panel>
      <h2 className="m-0 mb-1 text-base font-extrabold">Logboek</h2>
      <p className="m-0 mb-2 text-[11px] text-muted">Wie, wat, wanneer, uitkomst. Alleen lezen; regels worden nooit aangepast.</p>
      {!events ? <Loading what="Logboek" /> : !events.length ? <p className="m-0 text-sm text-muted">Nog niets.</p> : (
        <div className="grid gap-0 max-h-[420px] overflow-y-auto">
          {events.map((e, i) => (
            <div key={i} className="flex gap-3 py-1.5 border-b border-line-soft last:border-0 text-xs min-w-0">
              <span className="num text-muted shrink-0">{unixTime(e.ts)}</span>
              <span className={cn("shrink-0 font-bold", e.event === "refused" ? "text-sell" : e.event === "executed" ? "text-buy" : "text-fg-2")}>{EVENT_LABEL[e.event] ?? e.event}</span>
              <span className="text-fg-2 truncate">{[e.action, e.name, e.reason ?? e.error].filter(Boolean).join(" · ")}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

export default function Control() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [refresh, setRefresh] = useState(0);
  const load = useCallback(() => {
    fetch("/api/action/session", { credentials: "same-origin" }).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (res.ok) setState({ kind: "session", s: body as Session });
      else if (res.status === 401) setState({ kind: "unpaired", why: body.error ?? "" });
      else setState({ kind: "blocked", why: body.error ?? `Fout ${res.status}` });
    }).catch((e) => setState({ kind: "blocked", why: String(e.message ?? e) }));
  }, []);
  useEffect(load, [load]);

  return (
    <div className="grid gap-3.5 max-w-[860px]">
      <div className="flex flex-col gap-0.5">
        <h1 className="m-0 text-[22px] font-extrabold tracking-[-0.4px]">Bediening</h1>
        <span className="text-xs text-muted">Paper trading: er wordt niets verstuurd, geen sleutels, geen echt geld. Elke actie vraagt een bevestiging en komt in het logboek.</span>
      </div>
      {state.kind === "loading" && <Loading what="Bediening" />}
      {state.kind === "blocked" && (
        <Panel><p className="m-0 text-sm text-warn">Acties zijn niet beschikbaar vanaf dit apparaat: {state.why}.</p>
          <p className="m-0 mt-1 text-[11px] text-muted">Alleen de telefoon en laptop van de eigenaar (op de lijst ui-actors.json), via Tailscale.</p></Panel>
      )}
      {state.kind === "unpaired" && <Pairing onDone={load} />}
      {state.kind === "session" && (
        <>
          <Panel className="flex flex-wrap items-center gap-2">
            <Badge tone="buy">gekoppeld</Badge>
            <span className="text-sm text-fg-2">{state.s.name}</span>
            <span className="ml-auto text-[11px] text-muted">sessie tot {unixTime(state.s.expires_unix)}</span>
          </Panel>
          {state.s.actions_off && <p className="m-0 px-3 py-2.5 rounded-xl bg-warn-soft border border-warn-line text-warn text-xs">Acties staan uit: {state.s.actions_off}.</p>}
          <Panel>
            <h2 className="m-0 mb-1 text-base font-extrabold">Acties</h2>
            {Object.keys(state.s.actions).filter((a) => a !== "nas_job").map((a) => <ActionButton key={a} s={state.s} action={a} onLog={() => setRefresh((n) => n + 1)} />)}
            <p className="m-0 mt-2 text-[11px] text-muted">Pauze en start/stop van de bot komen pas als de webapp als eigen systeemgebruiker draait (variant A).</p>
          </Panel>
          {"nas_job" in state.s.actions && <Jobs s={state.s} refresh={refresh} onLog={() => setRefresh((n) => n + 1)} />}
          <Log refresh={refresh} />
        </>
      )}
    </div>
  );
}
