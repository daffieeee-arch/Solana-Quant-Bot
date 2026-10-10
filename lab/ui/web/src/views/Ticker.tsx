import { useCallback, useEffect, useState } from "react";
import { get, type MarketEvent } from "../api";
import { Badge, Card, Chip, DexLink, Loading } from "../components/ui";
import { go } from "../route";
import { label, n0, shortAddr, sol, unixTime } from "../format";

type Kind = "all" | "graduation" | "large";
const KIND_LABEL: Record<Kind, string> = { all: "alles", graduation: "graduaties", large: "grote trades (≥ 25 SOL)" };

function line(e: MarketEvent) {
  if (e.kind === "graduation") return e.note ? `gradueerde ${e.note}` : "gradueerde naar PumpSwap";
  const what = e.kind === "large_buy" ? "kocht" : "verkocht";
  return `${shortAddr(e.wallet)} ${what} voor ${sol(e.sol)} op ${e.venue === "pool" ? "PumpSwap" : "de curve"}`;
}

export default function Ticker() {
  const [kind, setKind] = useState<Kind>("all");
  const [events, setEvents] = useState<MarketEvent[] | null>(null);
  const [markers, setMarkers] = useState<{ slot: number; note: string }[]>([]);
  const [more, setMore] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback((before?: number) => {
    get<{ events: MarketEvent[]; markers: { slot: number; note: string }[] }>("ticker", { kind, limit: 100, before })
      .then((r) => {
        setEvents((prev) => (before ? [...(prev ?? []), ...r.events] : r.events));
        setMarkers(r.markers);
        setMore(r.events.length === 100);
      })
      .catch((e) => setErr(e.message));
  }, [kind]);

  useEffect(() => { setEvents(null); load(); }, [load]);

  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="grid grid-cols-1 gap-1">
        <h1 className="text-2xl font-semibold m-0 tracking-tight">On-chain ticker</h1>
        <p className="text-muted text-sm m-0">Marktnieuws uit de ontwikkeldata, nieuwste eerst: graduaties en grote aan- en verkopen in SOL. Live volgt later via de recorder.</p>
      </div>
      {markers.length > 0 && (
        <Card className="px-3 py-2 text-sm grid gap-1">
          <span className="text-xs uppercase tracking-wide text-muted">Vaste protocol-markers (alleen label, buiten de ontwikkeldata)</span>
          {markers.map((m) => <span key={m.slot}><span className="font-mono text-xs text-muted">slot {n0(m.slot)}</span> · {m.note}</span>)}
        </Card>
      )}
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter">
        {(Object.keys(KIND_LABEL) as Kind[]).map((k) => <Chip key={k} active={kind === k} onClick={() => setKind(k)}>{KIND_LABEL[k]}</Chip>)}
      </div>
      {err && <p className="text-warn">{err}</p>}
      {!events ? <Loading what="Ticker" /> : (
        <Card>
          <ul className="m-0 p-0 list-none">
            {events.map((e) => (
              <li key={`${e.kind}-${e.slot}-${e.tx_index ?? ""}-${e.mint}-${e.sol ?? ""}`} className="border-t border-line first:border-t-0 flex items-center">
                <button type="button" className="flex-1 min-w-0 text-left px-3 py-2.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 hover:bg-surface-2" onClick={() => e.mint && go("tokens", e.mint)}>
                  <span className="row-span-2 pt-0.5">
                    <Badge tone={e.kind === "graduation" ? "accent" : e.kind === "large_buy" ? "buy" : "sell"}>
                      {e.kind === "graduation" ? "graduatie" : e.kind === "large_buy" ? "koop" : "verkoop"}
                    </Badge>
                  </span>
                  <span className="min-w-0 truncate"><b>{label(e.symbol, 20) || shortAddr(e.mint)}</b> <span className="text-muted">{label(e.name, 40)}</span></span>
                  <span className="text-sm text-muted min-w-0 truncate">{unixTime(e.time)} UTC · {line(e)}</span>
                </button>
                <DexLink mint={e.mint} className="px-3 py-2.5 text-sm whitespace-nowrap shrink-0">Dex ↗</DexLink>
              </li>
            ))}
          </ul>
          {more && events.length > 0 && (
            <div className="border-t border-line p-2 text-center">
              <button type="button" className="text-sm text-accent px-3 py-1" onClick={() => load(events[events.length - 1].slot)}>Oudere laden</button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
