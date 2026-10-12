import type { ReactNode } from "react";

export function Section({ title, note, children, actions }: { title: string; note?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="grid grid-cols-1 gap-2 min-w-0">
      <div className="flex items-end gap-3 flex-wrap">
        <h2 className="text-base font-extrabold m-0 tracking-tight">{title}</h2>
        {actions && <div className="ml-auto flex gap-2">{actions}</div>}
      </div>
      {note && <p className="text-sm text-muted m-0 max-w-prose">{note}</p>}
      {children}
    </section>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`bg-surface border border-line rounded-2xl min-w-0 ${className}`}>{children}</div>;
}

export function Tile({ value, label }: { value: ReactNode; label: ReactNode }) {
  return (
    <Card className="px-3.5 py-3 grid gap-0.5">
      <div className="text-2xl font-semibold num tracking-tight">{value}</div>
      <div className="text-sm text-muted leading-snug">{label}</div>
    </Card>
  );
}

export function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className={`text-sm min-h-9 px-3 py-1 rounded-lg border font-semibold ${active ? "bg-accent-soft text-accent-fg border-accent-line" : "bg-transparent border-track text-fg-2"}`}>
      {children}
    </button>
  );
}

export function Badge({ tone, children }: { tone: "accent" | "warn" | "muted" | "buy" | "sell"; children: ReactNode }) {
  const cls = {
    accent: "bg-accent-soft text-accent-fg", warn: "bg-warn-soft text-warn", muted: "bg-surface-2 text-fg-2",
    buy: "bg-buy-soft text-buy", sell: "bg-sell-soft text-sell",
  }[tone];
  return <span className={`inline-flex items-center gap-1.5 text-xs font-bold px-2 py-0.5 rounded-md ${cls}`}>{children}</span>;
}

export function Loading({ what }: { what: string }) {
  return <p className="text-muted text-sm">{what} laden…</p>;
}

const MINT_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

/** Outbound link to DexScreener for a Solana mint. Only a plain link the owner clicks: no API calls,
 *  embeds, prefetch or favicons; noreferrer keeps the local UI address private. */
export function dexUrl(mint: string | null | undefined): string | null {
  return mint && MINT_RE.test(mint) ? `https://dexscreener.com/solana/${encodeURIComponent(mint)}` : null;
}

export function DexLink({ mint, children, className = "" }: { mint: string | null | undefined; children?: ReactNode; className?: string }) {
  const href = dexUrl(mint);
  if (!href) return <>{children ?? null}</>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" title={`${mint} op DexScreener`}
      className={`text-accent-fg font-bold underline-offset-2 hover:underline ${className}`} onClick={(e) => e.stopPropagation()}>
      {children ?? <>{`${mint!.slice(0, 4)}…${mint!.slice(-4)}`} ↗</>}
    </a>
  );
}
