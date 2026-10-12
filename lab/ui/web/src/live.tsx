import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { get } from "./api";
import type { PaperData } from "./paperTypes";

// The paper journal is shared by the shell (status pill, ticker bar) and the Paper page: one fetch for both.
// Server-Sent Events (/api/stream) say when the journal or the recorder wrote; every page that shows live data
// refetches on that tick. Without a stream (blocked, too many connections) the 30 s poll keeps things current.
const POLL_MS = 30_000, POLL_WITH_STREAM_MS = 120_000;
const Ctx = createContext<{ data: PaperData | null; error: string | null; tick: number; live: boolean }>({ data: null, error: null, tick: 0, live: false });

export function PaperProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const [data, setData] = useState<PaperData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let alive = true, streaming = false, last = 0;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      last = Date.now();
      get<PaperData>("paper").then((d) => { if (alive) { setData(d); setError(null); } }).catch((e) => alive && setError(e.message));
    };
    let es: EventSource | null = null;
    if ("EventSource" in window) {
      es = new EventSource("/api/stream");
      es.addEventListener("open", () => { streaming = true; setLive(true); });
      es.addEventListener("error", () => { streaming = false; setLive(false); });
      es.addEventListener("change", () => { setTick((t) => t + 1); load(); });
    }
    load();
    const id = setInterval(() => { if (Date.now() - last >= (streaming ? POLL_WITH_STREAM_MS : POLL_MS) - 500) { setTick((t) => t + 1); load(); } }, 5_000);
    const onVis = () => { if (document.visibilityState === "visible") { setTick((t) => t + 1); load(); } };
    document.addEventListener("visibilitychange", onVis);
    return () => { alive = false; es?.close(); clearInterval(id); document.removeEventListener("visibilitychange", onVis); };
  }, [enabled]);
  return <Ctx.Provider value={{ data, error, tick, live }}>{children}</Ctx.Provider>;
}

export const usePaper = () => useContext(Ctx);

/** Fetch an endpoint again on every live tick (SSE change or poll). */
export function useLive<T>(path: string) {
  const { tick } = usePaper();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    get<T>(path, {}, ac.signal).then((d) => { setData(d); setError(null); }).catch((e) => e.name !== "AbortError" && setError(e.message));
    return () => ac.abort();
  }, [path, tick]);
  return { data, error };
}

const ACCENTS = ["#8B6CFF", "#5B8CFF", "#22C3A6", "#F5A524"] as const;
export { ACCENTS };

/** The owner's accent colour (Meer), kept per device. */
export function applyAccent(color?: string) {
  let c = color;
  if (!c) { try { c = localStorage.getItem("lab-accent") ?? undefined; } catch { /* private mode */ } }
  if (!c || !ACCENTS.includes(c as (typeof ACCENTS)[number])) return;
  document.documentElement.style.setProperty("--accent", c);
  if (color) { try { localStorage.setItem("lab-accent", c); } catch { /* private mode */ } }
}
