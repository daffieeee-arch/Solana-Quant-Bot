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

function ActionButton({ s, action, onLog }: { s: Session; action: string; onLog: () => void }) {
  const [confirm, setConfirm] = useState<{ nonce: string; text: string } | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ask = () => {
    setErr(null);
    setStatus(null);
    postAction<{ nonce: string; text: string }>("prepare", { csrf: s.csrf, action, params: {} }).then(setConfirm).catch((e) => setErr(e.message));
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
    <div className="flex flex-wrap items-center gap-3 py-2 border-b border-line-soft last:border-0">
      <div className="grow min-w-0">
        <div className="text-sm font-bold">{ACTION_LABEL[action] ?? action}</div>
        <div className="text-[11px] text-muted">{s.actions[action]}</div>
      </div>
      <Button type="button" onClick={ask} disabled={!!s.actions_off}>Uitvoeren…</Button>
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
            {Object.keys(state.s.actions).map((a) => <ActionButton key={a} s={state.s} action={a} onLog={() => setRefresh((n) => n + 1)} />)}
            <p className="m-0 mt-2 text-[11px] text-muted">Pauze en start/stop van de bot komen pas als de webapp als eigen systeemgebruiker draait (variant A).</p>
          </Panel>
          <Log refresh={refresh} />
        </>
      )}
    </div>
  );
}
