import { useEffect, useState } from "react";
import { get, type Meta } from "./api";
import { go, useRoute } from "./route";
import Overview from "./views/Overview";
import Tokens from "./views/Tokens";
import TokenDetail from "./views/TokenDetail";
import Ticker from "./views/Ticker";
import Strategies from "./views/Strategies";
import { unixTime } from "./format";

// Tabs appear only when the API reports data for them (no empty tabs).
const TABS: Record<string, { slug: string; label: string; icon: string }> = {
  strategies: { slug: "strategieen", label: "Strategieën", icon: "M4 19V9m6 10V5m6 14v-7m4 7H2" },
  tokens: { slug: "tokens", label: "Tokens", icon: "M11 4a7 7 0 1 0 4.9 12l4.6 4.6M11 4a7 7 0 0 1 4.9 12" },
  overview: { slug: "overzicht", label: "Overzicht", icon: "M3 13h4v7H3zm7-9h4v16h-4zm7 5h4v11h-4z" },
  ticker: { slug: "ticker", label: "Ticker", icon: "M4 6h16M4 12h10M4 18h13" },
};

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export default function App() {
  const route = useRoute();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Meta>("meta").then(setMeta).catch((e) => setError(String(e.message ?? e)));
  }, []);

  const tabs = (meta?.tabs ?? ["overview", "tokens", "ticker"]).map((t) => ({ key: t, ...TABS[t] })).filter((t) => t.slug);
  const active = tabs.find((t) => t.slug === route.tab) ?? tabs.find((t) => t.key === "overview")!;

  let view;
  if (active.key === "tokens" && route.arg) view = <TokenDetail mint={route.arg} />;
  else if (active.key === "tokens") view = <Tokens />;
  else if (active.key === "ticker") view = <Ticker />;
  else if (active.key === "strategies") view = <Strategies />;
  else view = <Overview />;

  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur" style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}>
        <div className="mx-auto max-w-6xl px-4 h-12 flex items-center gap-6">
          <a href="#/overzicht" className="font-semibold tracking-tight text-fg no-underline">Solana-lab</a>
          <nav className="hidden md:flex gap-1" aria-label="Hoofdmenu">
            {tabs.map((t) => (
              <button key={t.key} type="button" onClick={() => go(t.slug)}
                className={`px-3 py-1.5 rounded-md text-sm ${t.key === active.key ? "bg-accent-soft text-accent font-medium" : "text-muted hover:text-fg"}`}
                aria-current={t.key === active.key ? "page" : undefined}>
                {t.label}
              </button>
            ))}
          </nav>
          <span className="ml-auto text-xs text-muted font-mono hidden sm:inline">
            {meta ? `ontwikkeldata · build ${unixTime(meta.meta.built_at_unix)} UTC` : ""}
          </span>
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-6xl px-4 pt-4 pb-24 md:pb-10 min-w-0">
        {error ? <p className="text-warn">De data is nu niet bereikbaar: {error}</p> : view}
      </main>

      <nav className="md:hidden fixed bottom-0 inset-x-0 z-30 border-t border-line bg-surface/95 backdrop-blur"
        style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }} aria-label="Tabbalk">
        <div className="grid" style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}>
          {tabs.map((t) => (
            <button key={t.key} type="button" onClick={() => go(t.slug)}
              className={`flex flex-col items-center gap-0.5 py-2 text-[11px] ${t.key === active.key ? "text-accent" : "text-muted"}`}
              aria-current={t.key === active.key ? "page" : undefined}>
              <Icon d={t.icon} />
              {t.label}
            </button>
          ))}
        </div>
      </nav>
    </div>
  );
}
