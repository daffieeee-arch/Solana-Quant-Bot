import { useEffect, useState } from "react";

// Hash routes: #/overzicht, #/tokens, #/tokens/<mint>, #/ticker, #/strategieen
export interface Route { tab: string; arg?: string }

function parse(): Route {
  const [tab, arg] = location.hash.replace(/^#\/?/, "").split("/");
  return { tab: tab || "", arg: arg ? decodeURIComponent(arg) : undefined };
}

export function useRoute(): Route {
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}

export const go = (tab: string, arg?: string) => {
  location.hash = "/" + tab + (arg ? "/" + encodeURIComponent(arg) : "");
};
