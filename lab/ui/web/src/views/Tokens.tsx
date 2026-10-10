import { useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { themeQuartz, type ColDef, type IDatasource, type GridApi } from "ag-grid-community";
import { get, type TokenRow } from "../api";
import { go } from "../route";
import { Chip, DexLink } from "../components/ui";
import { isoTime, label, n0, solShort } from "../format";

export const gridTheme = themeQuartz.withParams({
  backgroundColor: "var(--surface)", foregroundColor: "var(--fg)", headerBackgroundColor: "var(--surface)",
  headerTextColor: "var(--muted)", borderColor: "var(--line)", rowHoverColor: "var(--surface-2)",
  accentColor: "var(--accent)", fontFamily: "inherit", fontSize: 13, headerFontSize: 12, wrapperBorderRadius: 8,
  rowHeight: 40, headerHeight: 36,
});

const flags = (r: TokenRow) =>
  [r.graduated ? (r.grad_in_create_slot ? "gegradueerd (aanmaak-slot)" : "gegradueerd") : "", r.mayhem ? "mayhem" : "",
   r.sol_quote ? "" : "geen SOL-quote", r.holder_reward ? "holder rewards" : "", r.cashback ? "cashback" : ""]
    .filter(Boolean).join(" · ");

export default function Tokens() {
  const [term, setTerm] = useState(() => sessionStorage.getItem("tokens-q") ?? "");
  const [onlyGrad, setOnlyGrad] = useState(false);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 700px)").matches);
  const api = useRef<GridApi<TokenRow> | null>(null);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 700px)");
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const cols = useMemo<ColDef<TokenRow>[]>(() => [
    { field: "symbol", headerName: "Symbool", width: narrow ? 110 : 120, pinned: narrow ? undefined : "left", valueFormatter: (p) => label(p.value, 16) },
    { field: "name", headerName: "Naam", flex: 1, minWidth: 150, hide: narrow, valueFormatter: (p) => label(p.value, 48) },
    { field: "create_time", headerName: "Aangemaakt (UTC)", width: 150, hide: narrow, valueFormatter: (p) => isoTime(p.value) },
    { colId: "flags", headerName: "Kenmerken", width: 190, sortable: false, hide: narrow, valueGetter: (p) => (p.data ? flags(p.data) : "") },
    { field: "curve_trades", headerName: "Curve-trades", width: 120, type: "rightAligned", hide: narrow, valueFormatter: (p) => n0(p.value) },
    { field: "pool_trades", headerName: "Pool-trades", width: 115, type: "rightAligned", hide: narrow, valueFormatter: (p) => n0(p.value) },
    { field: "mint", headerName: "DexScreener", width: 130, sortable: false,
      cellRenderer: (p: { value: string }) => <DexLink mint={p.value} /> },
    { field: "total_volume_quote", headerName: "Volume SOL", width: 120, type: "rightAligned", sort: "desc", valueFormatter: (p) => solShort(p.value) },
    { colId: "max_mcap", headerName: "Max. mcap SOL", width: 125, type: "rightAligned", hide: narrow, valueGetter: (p) => (p.data as unknown as { max_mcap?: number })?.max_mcap, valueFormatter: (p) => solShort(p.value) },
  ], [narrow]);

  const datasource = useMemo<IDatasource>(() => ({
    getRows: (p) => {
      const s = p.sortModel[0];
      const limit = p.endRow - p.startRow;
      get<{ tokens: TokenRow[] }>("tokens", {
        q: term.trim() || undefined, offset: p.startRow, limit, graduated: onlyGrad ? 1 : undefined,
        sort: s?.colId ?? "volume", dir: s?.sort ?? "desc",
      }).then((r) => p.successCallback(r.tokens, r.tokens.length < limit ? p.startRow + r.tokens.length : undefined))
        .catch(() => p.failCallback());
    },
  }), [term, onlyGrad]);

  useEffect(() => {
    const t = setTimeout(() => { try { sessionStorage.setItem("tokens-q", term); } catch { /* ignore */ } }, 300);
    return () => clearTimeout(t);
  }, [term]);

  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="grid grid-cols-1 gap-1">
        <h1 className="text-2xl font-semibold m-0 tracking-tight">Tokens</h1>
        <p className="text-muted text-sm m-0">Zoek op naam, symbool of mint. Tik op een token voor de grafiek en de trades.</p>
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <input id="token-search" type="search" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="bijv. e/acc of een mint-adres"
          className="flex-1 min-w-0 sm:max-w-sm bg-surface border border-line rounded-md px-3 py-2 text-base" aria-label="Zoek token" autoComplete="off" />
        <Chip active={onlyGrad} onClick={() => setOnlyGrad((v) => !v)}>alleen gegradueerd</Chip>
      </div>
      <div className="lab-grid" style={{ height: "calc(100dvh - 15rem)", minHeight: 360 }}>
        <AgGridReact<TokenRow>
          theme={gridTheme}
          rowModelType="infinite"
          datasource={datasource}
          cacheBlockSize={100}
          maxBlocksInCache={20}
          columnDefs={cols}
          defaultColDef={{ sortable: true, resizable: true, suppressMovable: true }}
          getRowId={(p) => p.data.mint}
          onGridReady={(e) => { api.current = e.api; }}
          onRowClicked={(e) => e.data && go("tokens", e.data.mint)}
          overlayNoRowsTemplate="Geen tokens gevonden."
          rowStyle={{ cursor: "pointer" }}
        />
      </div>
      <p className="text-xs text-muted m-0">Volume en marktwaarde in de quote-munt van de token; voor bijna alle tokens is dat SOL. Namen en symbolen zijn door de makers gekozen.</p>
    </div>
  );
}
