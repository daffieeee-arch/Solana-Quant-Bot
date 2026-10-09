import type { ReactNode } from "react";

export function Section({ title, note, children, actions }: { title: string; note?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="grid grid-cols-1 gap-2 min-w-0">
      <div className="flex items-end gap-3 flex-wrap">
        <h2 className="text-base font-semibold m-0">{title}</h2>
        {actions && <div className="ml-auto flex gap-2">{actions}</div>}
      </div>
      {note && <p className="text-sm text-muted m-0 max-w-prose">{note}</p>}
      {children}
    </section>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`bg-surface border border-line rounded-lg min-w-0 ${className}`}>{children}</div>;
}

export function Tile({ value, label }: { value: ReactNode; label: ReactNode }) {
  return (
    <Card className="px-3.5 py-3 grid gap-0.5">
      <div className="text-2xl font-semibold tabular tracking-tight">{value}</div>
      <div className="text-sm text-muted leading-snug">{label}</div>
    </Card>
  );
}

export function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className={`text-sm px-3 py-1 rounded-full border ${active ? "bg-fg text-bg border-fg" : "bg-surface border-line text-fg"}`}>
      {children}
    </button>
  );
}

export function Badge({ tone, children }: { tone: "accent" | "warn" | "muted" | "buy" | "sell"; children: ReactNode }) {
  const cls = {
    accent: "bg-accent-soft text-accent", warn: "bg-warn-soft text-warn", muted: "bg-surface-2 text-muted",
    buy: "bg-surface-2 text-buy", sell: "bg-surface-2 text-sell",
  }[tone];
  return <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${cls}`}>{children}</span>;
}

export function Loading({ what }: { what: string }) {
  return <p className="text-muted text-sm">{what} laden…</p>;
}
