import { AnimatePresence, motion } from "motion/react";
import { Suspense, lazy, useEffect, useState } from "react";
import { get, type Meta } from "./api";
import { useRoute } from "./route";
import Ticker from "./views/Ticker";
import { Loading } from "./components/ui";
import { Shell, type Tab } from "./components/Shell";
import { PaperProvider, applyAccent } from "./live";

// Heavy screens load on demand (ECharts, AG Grid, Lightweight Charts) so the first paint on a phone stays small.
const Overview = lazy(() => import("./views/Overview"));
const Tokens = lazy(() => import("./views/Tokens"));
const TokenDetail = lazy(() => import("./views/TokenDetail"));
const Strategies = lazy(() => import("./views/Strategies"));
const Cases = lazy(() => import("./views/Cases"));
const Paper = lazy(() => import("./views/Paper"));

// Tabs appear only when the API reports data for them (no empty tabs). Order = desktop nav order.
const TABS: Record<string, { slug: string; label: string; short?: string }> = {
  paper: { slug: "paper", label: "Paper" },
  strategies: { slug: "strategieen", label: "Strategieën", short: "Strategie" },
  tokens: { slug: "tokens", label: "Tokens" },
  cases: { slug: "casussen", label: "Casussen" },
  overview: { slug: "overzicht", label: "Overzicht" },
  ticker: { slug: "ticker", label: "Ticker" },
};

export default function App() {
  const route = useRoute();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    applyAccent();
    get<Meta>("meta").then(setMeta).catch((e) => setError(String(e.message ?? e)));
  }, []);

  const have = meta?.tabs ?? ["overview", "tokens", "ticker"];
  const tabs: Tab[] = Object.keys(TABS).filter((k) => have.includes(k)).map((k) => ({ key: k, ...TABS[k] }));
  const fallback = tabs.find((t) => t.key === "paper") ?? tabs.find((t) => t.key === "overview")!;
  const active = tabs.find((t) => t.slug === route.tab) ?? fallback;

  let view;
  if (active.key === "tokens" && route.arg) view = <TokenDetail mint={route.arg} />;
  else if (active.key === "tokens") view = <Tokens />;
  else if (active.key === "ticker") view = <Ticker />;
  else if (active.key === "strategies") view = <Strategies />;
  else if (active.key === "cases") view = <Cases period={route.arg} />;
  else if (active.key === "paper") view = <Paper />;
  else view = <Overview />;

  return (
    <PaperProvider enabled={have.includes("paper")}>
      <Shell tabs={tabs} active={active.key}>
        {error ? <p className="text-warn">De data is nu niet bereikbaar: {error}</p> : (
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={active.key + (route.arg ?? "")} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
              <Suspense fallback={<Loading what="Scherm" />}>{view}</Suspense>
            </motion.div>
          </AnimatePresence>
        )}
      </Shell>
    </PaperProvider>
  );
}
