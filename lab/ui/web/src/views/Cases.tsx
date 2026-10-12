import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { get, type Candle, type MarketEvent } from "../api";
import PriceChart, { eventMarkers } from "../components/PriceChart";
import { Badge, Card, DexLink, Loading, Section } from "../components/ui";
import { go } from "../route";
import { day, label, n0, n1, shortAddr, solShort, unixTime } from "../format";

// Casussen: lab/wallets/cases-v1 (another session's lists, read by the server on every request).
type CaseRow = Record<string, unknown> & { list: string; rank: number; mint: string; symbol?: string | null; name?: string | null };
interface Timeline {
  symbol?: string; name?: string; grad_in_create?: boolean; max_mult?: number; end_mult?: number;
  t5_curve_minutes?: number; t15_curve_minutes?: number; t5_buyers?: number; t15_buyers?: number;
  t5_insider_share?: number; t15_insider_share?: number; t5_pool_traders?: number; t15_pool_traders?: number;
  t5_organic_sol?: number; t15_organic_sol?: number; t5_px_mult?: number; t15_px_mult?: number;
  big_sellers?: { wallet: string; sol: number; minutes_after_open: number; creator: boolean; insider: boolean }[];
}
interface CasesIndex { days: string[]; windows: string[]; updated_unix: number | null }
interface Cases { key: string; lists: string[]; flags: string[]; rows: CaseRow[]; timelines: Record<string, Timeline>; dropped_holdout: number }

const LISTS: Record<string, [string, string]> = {
  trending_24h: ["Trending 24 uur", "Gerangschikt op koersstijging over de dag × volume. Inclusief zelfgefinancierde tokens."],
  trending_24h_organic: ["Trending 24 uur, zonder dev-bundels", "Dezelfde lijst zonder zelfgefinancierde tokens / dev-bundels."],
  dumpers_24h: ["Dumpers 24 uur", "Eind van de dag op hooguit 10% van de piek; gerangschikt op volume."],
  stayers_7d: ["Blijvers na 7 dagen", "Hoogste marktwaarde 7 dagen na de graduatie."],
  stayers_7d_organic: ["Blijvers na 7 dagen, zonder dev-bundels", "Dezelfde lijst zonder zelfgefinancierde tokens / dev-bundels."],
  collapsed_7d: ["Instorters binnen 7 dagen", "Na 7 dagen op hooguit 5% van de piek; gerangschikt op piek-marktwaarde."],
  collapsed_7d_organic: ["Instorters binnen 7 dagen, zonder dev-bundels", "Dezelfde lijst zonder zelfgefinancierde tokens / dev-bundels."],
};
// Short (phone) and full badge text per flag column; an unknown boolean column shows its own name.
const FLAGS: Record<string, [string, string]> = { self_funded: ["dev-bundel", "zelfgefinancierd / dev-bundel"] };
const ORDER = ["trending_24h", "trending_24h_organic", "dumpers_24h", "stayers_7d", "stayers_7d_organic", "collapsed_7d", "collapsed_7d_organic"];

const num = (r: CaseRow, ...ks: string[]) => { for (const k of ks) if (typeof r[k] === "number") return r[k] as number; return null; };
const pct0 = (v: number | null) => (v == null ? "–" : `${n0(v * 100)}%`);
const signedPct = (v: number | null) => (v == null ? "–" : `${v > 0 ? "+" : ""}${n0(v * 100)}%`);
const mult = (v: number | null) =>
  v == null ? "–" : `${v >= 100 ? n0(v) : v >= 1 ? n1(v) : v.toLocaleString("nl-NL", { maximumSignificantDigits: 2 })}×`;

// Columns shown when at least one row of the period has the field (day and 7-day lists differ).
interface Col { h: string; title: string; get: (r: CaseRow) => number | null; fmt: (v: number | null) => string; wide?: boolean }
const COLS: Col[] = [
  { h: "grootste koper", title: "Aandeel van de grootste koper in de pool-kopen", get: (r) => num(r, "top_buyer_share"), fmt: pct0 },
  { h: "piek ×", title: "Piekprijs ten opzichte van de eerste prijs (dag) of de open-prijs (7 d)",
    get: (r) => num(r, "peak_mult") ?? (num(r, "p_peak") != null && num(r, "p_open") ? num(r, "p_peak")! / num(r, "p_open")! : null), fmt: mult },
  { h: "eind t.o.v. piek", title: "Laatste prijs als deel van de piek", get: (r) => num(r, "end_of_peak"), fmt: pct0 },
  { h: "handelaren", title: "Unieke handelaren", get: (r) => num(r, "traders", "traders_7d"), fmt: n0 },
  { h: "24 u", title: "Koersverandering over de dag", get: (r) => num(r, "ret_24h"), fmt: signedPct, wide: true },
  { h: "volume SOL", title: "Handelsvolume in SOL", get: (r) => num(r, "volume_sol", "volume_7d"), fmt: solShort, wide: true },
  { h: "mcap 7 d", title: "Marktwaarde na 7 dagen (SOL)", get: (r) => num(r, "mcap_7d_sol"), fmt: solShort, wide: true },
  { h: "piek-mcap", title: "Hoogste marktwaarde binnen 7 dagen (SOL)", get: (r) => num(r, "mcap_peak_sol"), fmt: solShort, wide: true },
];

function periodText(key: string, idx: CasesIndex) {
  if (key.startsWith("7d-")) return idx.windows.length > 1 ? `7 dagen (vanaf slot ${n0(Number(key.slice(3)))})` : "7 dagen (hele periode)";
  return day(key);
}

export default function Cases({ period }: { period?: string }) {
  const [idx, setIdx] = useState<CasesIndex | null>(null);
  const [stored] = useState<string | null>(() => { try { return sessionStorage.getItem("cases-key"); } catch { return null; } });
  const key = period ?? stored;
  const [data, setData] = useState<Cases | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { get<CasesIndex>("cases").then(setIdx).catch((e) => setErr(e.message)); }, []);
  const keys = useMemo(() => (idx ? [...idx.windows, ...[...idx.days].reverse()] : []), [idx]);
  const sel = key && keys.includes(key) ? key : keys[0];

  useEffect(() => {
    if (!sel) return;
    const ac = new AbortController();
    setData(null); setOpen(null);
    get<Cases>("cases", sel.startsWith("7d-") ? { window: sel } : { day: sel }, ac.signal).then(setData)
      .catch((e) => { if (!ac.signal.aborted) setErr(e.message); });
    try { sessionStorage.setItem("cases-key", sel); } catch { /* ignore */ }
    return () => ac.abort();
  }, [sel]);

  const lists = useMemo(() => (data ? [...data.lists].sort((a, b) => (ORDER.indexOf(a) + 99 * +(ORDER.indexOf(a) < 0)) - (ORDER.indexOf(b) + 99 * +(ORDER.indexOf(b) < 0))) : []), [data]);
  const cols = useMemo(() => COLS.filter((c) => data?.rows.some((r) => c.get(r) != null)), [data]);

  return (
    <div className="grid grid-cols-1 gap-5">
      <div className="grid grid-cols-1 gap-1">
        <h1 className="text-[22px] font-extrabold m-0 tracking-[-0.4px]">Casussen</h1>
        <p className="text-muted text-sm m-0 max-w-prose">
          Tokens die opvielen in de eigen data (Old Faithful, ontwikkelperiode): per dag de stijgers en dumpers, en over 7 dagen de
          blijvers en instorters. Labels zijn waarschijnlijkheden, geen oordeel. Tik op een token voor de koers en de tijdlijn.
        </p>
      </div>
      {err && <p className="text-warn">{err}</p>}
      {!idx && !err && <Loading what="Casussen" />}
      {idx && (
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="cases-period" className="text-sm text-muted">Periode</label>
          <select id="cases-period" value={sel ?? ""} onChange={(e) => go("casussen", e.target.value)}
            className="bg-surface border border-line rounded-md px-3 py-2 text-base min-w-0">
            {keys.map((k) => <option key={k} value={k}>{periodText(k, idx)}</option>)}
          </select>
          {idx.updated_unix && <span className="text-xs text-muted">bestanden van {unixTime(idx.updated_unix)} UTC</span>}
        </div>
      )}
      {sel && !data && !err && <Loading what="Lijsten" />}
      {data && lists.map((l) => {
        const rs = data.rows.filter((r) => r.list === l).sort((a, b) => a.rank - b.rank);
        const [title, note] = LISTS[l] ?? [l.replace(/_/g, " "), ""];
        return (
          <Section key={l} title={title} note={note}>
            <Card className="overflow-x-auto">
              <table className="w-full text-sm tabular">
                <thead>
                  <tr className="text-muted text-xs">
                    <th className="px-3 py-2 font-medium text-left w-8">#</th>
                    <th className="px-3 py-2 font-medium text-left">token</th>
                    <th className="px-3 py-2 font-medium text-left">vlag</th>
                    {cols.map((c) => <th key={c.h} title={c.title} className={`px-3 py-2 font-medium text-right whitespace-nowrap ${c.wide ? "hidden md:table-cell" : "hidden sm:table-cell"}`}>{c.h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {rs.map((r) => {
                    const id = `${l}|${r.mint}`;
                    const isOpen = open === id;
                    const flagged = data.flags.filter((f) => r[f] === true);
                    return (
                      <Fragment key={id}>
                        <tr className={`border-t border-line cursor-pointer ${isOpen ? "bg-surface-2" : "hover:bg-surface-2"}`}
                          onClick={() => setOpen(isOpen ? null : id)} aria-expanded={isOpen}>
                          <td className="px-3 py-2 text-muted">{r.rank}</td>
                          <td className="px-3 py-2 min-w-0">
                            <div className="flex flex-wrap items-baseline gap-x-2">
                              <span className="font-medium [overflow-wrap:anywhere]">{label(r.symbol, 18) || shortAddr(r.mint)}</span>
                              <DexLink mint={r.mint} className="text-xs">Dex ↗</DexLink>
                            </div>
                            <div className="text-xs text-muted [overflow-wrap:anywhere]">{label(r.name, 36)}</div>
                          </td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              {flagged.length ? flagged.map((f) => (
                                <Badge key={f} tone="warn">
                                  {FLAGS[f] ? <><span className="sm:hidden">{FLAGS[f][0]}</span><span className="hidden sm:inline">{FLAGS[f][1]}</span></> : f.replace(/_/g, " ")}
                                </Badge>)) : <span className="text-muted text-xs">–</span>}
                            </div>
                            <div className="sm:hidden text-xs text-muted mt-1">
                              {cols.slice(0, 3).map((c) => `${c.h} ${c.fmt(c.get(r))}`).join(" · ")}
                            </div>
                          </td>
                          {cols.map((c) => <td key={c.h} className={`px-3 py-2 text-right whitespace-nowrap ${c.wide ? "hidden md:table-cell" : "hidden sm:table-cell"}`}>{c.fmt(c.get(r))}</td>)}
                        </tr>
                        {isOpen && (
                          <tr className="border-t border-line">
                            <td colSpan={3 + cols.length} className="p-0"><CaseDetail row={r} tl={data.timelines[r.mint]} /></td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </Card>
          </Section>
        );
      })}
      {data && data.dropped_holdout > 0 && <p className="text-xs text-muted m-0">{data.dropped_holdout} rijen uit de afgeschermde eindtestperiode zijn weggelaten.</p>}
      <p className="text-xs text-muted m-0">
        Zelfgefinancierd / dev-bundel: de token gradueerde in de aanmaak-slot of hooguit een handvol wallets kocht de hele curve.
        Namen en symbolen zijn door de makers gekozen. DexScreener-links openen alleen een pagina; DexScreener is geen databron.
      </p>
    </div>
  );
}

interface Chart { token: { supply_tokens: number; create_time: string }; candles: Candle[]; events: MarketEvent[] }

function CaseDetail({ row, tl }: { row: CaseRow; tl?: Timeline }) {
  const [c, setC] = useState<Chart | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    get<Chart>("candles", { mint: row.mint }, ac.signal).then(setC).catch((e) => { if (!ac.signal.aborted) setErr(e.message); });
    return () => ac.abort();
  }, [row.mint]);
  const markers = useMemo(() => (c ? eventMarkers(c.events, Math.floor(Date.parse(c.token.create_time + "Z") / 1000)) : []), [c]);

  const facts: [string, ReactNode][] = tl ? [
    ["Graduatie", tl.grad_in_create ? "in de aanmaak-slot" : `${n1(tl.t5_curve_minutes)} / ${n1(tl.t15_curve_minutes)} min (op +5 / +15 min)`],
    ["Piek / eind 24 u", `${mult(tl.max_mult ?? null)} / ${mult(tl.end_mult ?? null)} van de open-prijs`],
    ["Curve-kopers (+5 / +15 min)", `${n0(tl.t5_buyers)} / ${n0(tl.t15_buyers)}`],
    ["Insider-aandeel", `${pct0(tl.t5_insider_share ?? null)} / ${pct0(tl.t15_insider_share ?? null)}`],
    ["Pool-handelaren", `${n0(tl.t5_pool_traders)} / ${n0(tl.t15_pool_traders)}`],
    ["Organisch pool-volume", `${solShort(tl.t5_organic_sol)} / ${solShort(tl.t15_organic_sol)} SOL`],
    ["Prijs t.o.v. open", `${mult(tl.t5_px_mult ?? null)} / ${mult(tl.t15_px_mult ?? null)}`],
  ] : [];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-3 p-3 bg-surface-2">
      <div className="min-w-0 grid gap-1">
        <div className="text-xs text-muted">Marktwaarde per minuut (SOL, UTC) · ▲▼ trades ≥ 25 SOL</div>
        {err ? <p className="text-warn text-sm">{err}</p> : !c ? <Loading what="Koers" />
          : c.candles.length ? <PriceChart candles={c.candles} supply={c.token.supply_tokens} markers={markers} height={260} />
          : <p className="text-muted text-sm">Geen trades in de ontwikkeldata.</p>}
      </div>
      <div className="min-w-0 grid gap-2 content-start text-sm">
        {facts.length > 0 ? (
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            {facts.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="m-0 tabular">{v}</dd></div>)}
          </dl>
        ) : <p className="text-muted m-0">Geen tijdlijn voor deze token in deze periode.</p>}
        {tl?.big_sellers?.length ? (
          <div className="grid gap-0.5">
            <div className="text-muted">Grootste verkopers (eerste 24 u)</div>
            {tl.big_sellers.map((s) => (
              <div key={s.wallet + s.minutes_after_open} className="tabular">
                {solShort(s.sol)} SOL na {n0(s.minutes_after_open)} min <span className="font-mono text-xs text-muted">{shortAddr(s.wallet)}</span>
                {s.creator && <> <Badge tone="warn">maker</Badge></>}{s.insider && <> <Badge tone="warn">insider</Badge></>}
              </div>
            ))}
          </div>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <button type="button" className="text-accent text-sm" onClick={() => go("tokens", row.mint)}>Volledige tokenpagina →</button>
          <DexLink mint={row.mint} className="text-sm">DexScreener ↗</DexLink>
        </div>
      </div>
    </div>
  );
}
