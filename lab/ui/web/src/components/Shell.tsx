import * as Dialog from "@radix-ui/react-dialog";
import { useState, type ReactNode } from "react";
import { ACCENTS, applyAccent, usePaper } from "../live";
import { cn } from "../lib/utils";
import { Button } from "./Button";
import { label, shortAddr } from "../format";
import { go } from "../route";

export interface Tab { key: string; slug: string; label: string; short?: string }

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" aria-hidden="true">
      <rect x="1" y="1" width="28" height="28" rx="9" fill="var(--accent)" />
      <path d="M9 19.5l4.2-6 3.6 3.4L21 10.5" stroke="#0A0D13" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Live / Gestopt / Testdata, from the paper journal's heartbeat. */
export function StatusPill() {
  const j = usePaper().data?.journal;
  if (!j) return null;
  const [text, cls, dot] = j.test_run ? ["Testdata", "bg-accent-soft text-accent-fg", "bg-accent"]
    : j.status.running ? ["Live", "bg-buy-soft text-buy", "bg-buy-strong shadow-[0_0_0_3px_rgba(46,189,133,0.25)]"]
      : ["Gestopt", "bg-sell-soft text-sell", "bg-sell-strong"];
  return (
    <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-bold whitespace-nowrap", cls)}>
      <span className={cn("w-[7px] h-[7px] rounded-full", dot)} />{text}
    </span>
  );
}

/** The latest closed paper trades, scrolling slowly (static with reduced motion). */
export function TickerBar() {
  const j = usePaper().data?.journal;
  if (!j?.ticker.length) return null;
  const items = j.ticker.map((t, i) => (
    <span key={i} className="inline-flex gap-1.5">
      <span className="text-muted">{label(t.symbol, 14) || shortAddr(t.key)}</span>
      {t.failed ? <span className="text-fg-2">mislukt</span> : t.ret == null ? <span className="text-fg-2">–</span> : (
        <span className={t.ret > 0 ? "text-buy" : t.ret < 0 ? "text-sell" : "text-fg-2"}>
          {t.ret > 0 ? "▲ +" : t.ret < 0 ? "▼ −" : ""}{Math.abs(t.ret * 100).toLocaleString("nl-NL", { maximumFractionDigits: 1 })}%
        </span>
      )}
    </span>
  ));
  return (
    <div className="border-b border-line-soft bg-bg-ticker overflow-hidden" aria-label="Laatste paper-trades">
      <div className="ticker-track flex w-max gap-[18px] md:gap-7 px-[18px] md:px-7 py-2.5 num text-xs md:text-[13px] whitespace-nowrap">
        {j.test_run && <span className="text-accent-fg">test</span>}
        {items}
        <span aria-hidden="true" className="inline-flex gap-[18px] md:gap-7">{items}</span>
      </div>
    </div>
  );
}

function MoreSheet({ tabs, open, onOpenChange }: { tabs: Tab[]; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [accent, setAccent] = useState(() => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim().toUpperCase());
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed inset-x-0 bottom-0 z-50 rounded-t-[20px] bg-surface border-t border-line px-[18px] pt-4 grid gap-4"
          style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 20px)" }}>
          <div className="mx-auto w-10 h-1 rounded-full bg-track" aria-hidden="true" />
          <Dialog.Title className="m-0 text-base font-extrabold">Meer</Dialog.Title>
          <Dialog.Description className="sr-only">Andere schermen en de accentkleur</Dialog.Description>
          <nav className="grid gap-1" aria-label="Meer schermen">
            {tabs.map((t) => (
              <Button key={t.key} type="button" variant="ghost" size="lg" onClick={() => { onOpenChange(false); go(t.slug); }}
                className="justify-start rounded-xl bg-surface-3 text-fg">{t.label}</Button>
            ))}
          </nav>
          <div className="grid gap-2">
            <span className="text-sm text-muted">Accentkleur</span>
            <div className="flex gap-3">
              {ACCENTS.map((c) => (
                <button key={c} type="button" aria-label={`Accent ${c}`} aria-pressed={accent === c}
                  onClick={() => { applyAccent(c); setAccent(c); }}
                  className={cn("w-10 h-10 rounded-full border-2", accent === c ? "border-fg" : "border-transparent")} style={{ background: c }} />
              ))}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** App frame: header (+ desktop nav), ticker bar, content, and the phone's tab bar with "Meer". */
export function Shell({ tabs, active, children }: { tabs: Tab[]; active: string; children: ReactNode }) {
  const [more, setMore] = useState(false);
  const PHONE = ["paper", "strategies", "tokens", "cases"];
  const phoneTabs = PHONE.map((k) => tabs.find((t) => t.key === k)).filter((t): t is Tab => !!t);
  const rest = tabs.filter((t) => !PHONE.includes(t.key));
  const moreActive = rest.some((t) => t.key === active);
  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-20 bg-bg/95 md:bg-bg-header backdrop-blur border-b border-line-soft" style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}>
        <div className="mx-auto max-w-[1440px] flex items-center gap-x-7 gap-y-3 px-[18px] md:px-7 py-3 md:py-3.5">
          <a href="#/paper" className="flex items-center gap-2.5 no-underline text-fg">
            <Logo />
            <span className="flex flex-col">
              <span className="font-extrabold text-[17px] md:text-lg tracking-tight">Solana Lab</span>
              <span className="text-[11px] text-muted md:hidden">Paper trading · niets wordt verstuurd</span>
            </span>
          </a>
          <nav className="hidden md:flex flex-wrap gap-1" aria-label="Hoofdmenu">
            {tabs.map((t) => (
              <button key={t.key} type="button" onClick={() => go(t.slug)} aria-current={t.key === active ? "page" : undefined}
                className={cn("px-3.5 py-2.5 rounded-[10px] text-sm", t.key === active ? "bg-accent-soft text-accent-fg font-bold" : "text-[#A3ADBF] font-semibold hover:text-fg")}>
                {t.label}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2.5">
            <span className="hidden md:inline text-xs text-muted">Paper trading · niets wordt verstuurd</span>
            <StatusPill />
          </div>
        </div>
      </header>
      <TickerBar />

      <main className="flex-1 mx-auto w-full max-w-[1440px] px-[18px] md:px-7 pt-3.5 md:pt-5 pb-28 md:pb-10 min-w-0">{children}</main>

      <nav className="md:hidden fixed bottom-0 inset-x-0 z-30 border-t border-line-soft bg-bg/90 backdrop-blur" aria-label="Tabbalk"
        style={{ paddingBottom: "max(env(safe-area-inset-bottom, 0px), 8px)" }}>
        <div className="grid px-1.5 pt-2" style={{ gridTemplateColumns: `repeat(${phoneTabs.length + 1}, minmax(0, 1fr))` }}>
          {phoneTabs.map((t) => <BarItem key={t.key} label={t.short ?? t.label} on={t.key === active} onClick={() => go(t.slug)} />)}
          <BarItem label="Meer" on={moreActive || more} onClick={() => setMore(true)} />
        </div>
      </nav>
      <MoreSheet tabs={rest} open={more} onOpenChange={setMore} />
    </div>
  );
}

function BarItem({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-current={on ? "page" : undefined}
      className={cn("flex flex-col items-center justify-center gap-1 min-h-11 text-[11px] font-bold", on ? "text-accent" : "text-muted")}>
      <span className={cn("w-[22px] h-[3px] rounded-sm", on ? "bg-accent" : "bg-transparent")} />
      {label}
    </button>
  );
}
