import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { get } from "./api";
import type { PaperData } from "./paperTypes";

// The paper journal is shared by the shell (status pill, ticker bar) and the Paper page: one poll for both.
// Phase 3 replaces the poll with Server-Sent Events (polling stays as the fallback).
const REFRESH_MS = 30_000;
const Ctx = createContext<{ data: PaperData | null; error: string | null }>({ data: null, error: null });

export function PaperProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const [data, setData] = useState<PaperData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      get<PaperData>("paper").then((d) => { if (alive) { setData(d); setError(null); } }).catch((e) => alive && setError(e.message));
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    document.addEventListener("visibilitychange", load);
    return () => { alive = false; clearInterval(id); document.removeEventListener("visibilitychange", load); };
  }, [enabled]);
  return <Ctx.Provider value={{ data, error }}>{children}</Ctx.Provider>;
}

export const usePaper = () => useContext(Ctx);

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
