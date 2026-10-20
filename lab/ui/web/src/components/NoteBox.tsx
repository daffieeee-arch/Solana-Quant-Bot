import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import { postAction } from "../api";
import { Button } from "./Button";
import { unixTime } from "../format";

// The owner's notes on a trade or a day (backlog 5). Only a paired device sees and writes them (Bediening); a note
// is an action like any other: the server's text to confirm, the executor writes it to its own file (never the
// journal, the bot never reads it), the audit log records it. Notes are the owner's own words, shown as text.
type Target = { day: string } | { key: string; variant: string; t: number };
interface Note { id: string; ts: number; text: string }

export default function NoteBox({ target }: { target: Target }) {
  const [csrf, setCsrf] = useState<string | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [confirm, setConfirm] = useState<{ nonce: string; text: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const qs = new URLSearchParams(Object.entries(target).map(([k, v]) => [k, String(v)])).toString();
  useEffect(() => {
    let alive = true;
    fetch("/api/action/session", { credentials: "same-origin" }).then(async (r) => {
      if (!r.ok) return; // not paired here: no notes, no button
      const s = await r.json();
      if (!alive) return;
      setCsrf(s.csrf);
      const n = await fetch(`/api/action/notes?${qs}`, { credentials: "same-origin" }).then((x) => (x.ok ? x.json() : { notes: [] }));
      if (alive) setNotes(n.notes ?? []);
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [qs, tick]);
  if (!csrf) return null;
  const ask = () => {
    setErr(null);
    postAction<{ nonce: string; text: string }>("prepare", { csrf, action: "note", params: { ...target, text } }).then(setConfirm).catch((e) => setErr(e.message));
  };
  const save = async () => {
    if (!confirm) return;
    try {
      await postAction("commit", { csrf, nonce: confirm.nonce });
      setConfirm(null);
      setOpen(false);
      setText("");
      setTimeout(() => setTick((n) => n + 1), 2500); // the executor writes it within a few seconds
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="grid gap-1 text-xs">
      {notes.map((n) => <p key={n.id} className="m-0 px-2 py-1.5 rounded-lg bg-surface-2 text-fg-2 whitespace-pre-wrap break-words"><span className="num text-[10px] text-muted">{unixTime(n.ts)} · </span>{n.text}</p>)}
      <Dialog.Root open={open} onOpenChange={(o) => { setOpen(o); if (!o) { setConfirm(null); setErr(null); } }}>
        <Dialog.Trigger asChild><button type="button" className="justify-self-start text-[11px] text-accent-fg font-bold">+ notitie</button></Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60 z-40" />
          <Dialog.Content className="fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[min(92vw,440px)] rounded-2xl bg-surface border border-line p-4 grid gap-3">
            <Dialog.Title className="m-0 text-base font-extrabold">Notitie</Dialog.Title>
            {!confirm ? (
              <>
                <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={500} rows={4} aria-label="Notitie"
                  className="w-full rounded-xl bg-surface-2 border border-line p-2 text-sm" placeholder="Wat viel je op?" />
                <div className="flex justify-between items-center gap-2">
                  <span className="text-[10px] text-muted">{text.length}/500 · een eigen bestand, niet het journaal</span>
                  <Button type="button" onClick={ask} disabled={!text.trim()}>Verder…</Button>
                </div>
              </>
            ) : (
              <>
                <Dialog.Description className="m-0 text-sm text-fg-2">{confirm.text}</Dialog.Description>
                <div className="flex gap-2 justify-end">
                  <Button type="button" variant="ghost" onClick={() => setConfirm(null)}>Terug</Button>
                  <Button type="button" onClick={save}>Bevestig</Button>
                </div>
              </>
            )}
            {err && <p className="m-0 text-xs text-sell">{err}</p>}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
