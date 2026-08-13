import type { MarketSnapshot } from '../scoring.js';
import type { MarketProvider, PositionQuoteIdentity } from '../scanner.js';
import { SolanaRpcProvider } from './solana-rpc.js';
import { TritonProvider } from './triton.js';
import { TitanQuoteProvider } from './titan.js';
import { assessRugRisk as classifyRugRisk } from '../rug-risk.js';

export type CompositeOptions = {
  maxTokens?: number;
  whaleWallets?: string[];
  minWhaleTxSol?: number;
  rpcHttpEndpoint?: string;
  rpcWsEndpoint?: string;
  /** TRITON-ONLY: zet de Solana-WS-lane expliciet aan (standaard UIT). Alleen met
   * useSolanaWs=true én beide endpoints wordt de SolanaRpcProvider geïnstantieerd;
   * zonder deze flag draait discovery puur via Triton (Vixen/geyser + Titan). */
  /** Age window pre-filter (minutes). Pools younger than min / older than max are
   * skipped before any exact-pair enrichment — they'd be rejected anyway, so we
   * don't spend rate-limited budget on them. Only affects discovery.
   */
  minAgeMinutes?: number;
  maxAgeMinutes?: number;
  /**
   * TRITON-ONLY (hard): zet de Solana-WS-lane expliciet aan (standaard UIT).
   * Alleen met useSolanaWs=true én beide RPC-endpoints wordt SolanaRpcProvider
   * geïnstantieerd. Zonder deze flag draait discovery uitsluitend via Triton
   * (Vixen/geyser + Titan + curve self-calc).
   */
  useSolanaWs?: boolean;
  /** Optional Triton Vixen discovery provider. When omitted, Triton is not used. */
  triton?: TritonProvider;
  /**
   * Optional Titan live route-quote provider. When present, entries/exits get a
   * real aggregated multi-venue fill price instead of pair-derived only.
   */
  titan?: TitanQuoteProvider;
  /** SOL/USD for Titan quote→USD conversion + Pump discovery pricing. */
  solPriceUsd?: number;
};

/**
 * Combines real-time discovery sources with exact-pair market data (Triton-first):
 * - Discovery: Solana RPC WebSocket pool detection + Triton Vixen/geyser Program
 *   Data Streams (Pump.fun, Raydium AMMv4/CPMM) — both emit exact identity records.
 * - Pricing: Triton RPC (curve self-calc) + Titan live route-quotes. Birdeye en
 *   GeckoTerminal zijn volledig verwijderd (Triton-first).
 */
export class CompositeProvider implements MarketProvider {
  private static readonly MAX_DIAGNOSTICS = 500;
  private readonly solana: SolanaRpcProvider | undefined;
  private readonly triton: TritonProvider | undefined;
  private readonly titan: TitanQuoteProvider | undefined;
  private readonly agePreFilterMinMs: number;
  private readonly agePreFilterMaxMs: number;
  private diagnostics: string[] = [];
  private discoveryDebugLast = '';
  private titanPricedPools = 0;
  private pricedMergedCount = 0;
  private unpricedMergedCount = 0;
  private discoveryInFlight?: Promise<MarketSnapshot[]>;
  /** Laatst gemeten round-trip (ms) per provider voor de discovery/enrichment-calls. */
  private lastProviderLatency: Record<string, number> = {};
  /** Providers die de gebruiker via de dashboard-controls tijdelijk uitschakelde. */
  private disabledProviders = new Set<string>();
  private readonly solPriceUsd: number | undefined;
  /** Laatste geprijsde discovery-merge; bron voor positie-quotes (Triton-first). */
  private lastMergedSnapshots?: MarketSnapshot[];
  /** Titan route-quote cache per mint (60s) — voorkomt per-scan herhalingscalls. */
  private titanQuoteCache = new Map<string, { usd: number | undefined; expiresAt: number }>();

  constructor(options: CompositeOptions = {}) {
    const {
      maxTokens = 10,
      whaleWallets = [],
      minWhaleTxSol = 5,
      rpcHttpEndpoint,
      rpcWsEndpoint,
      useSolanaWs = false,
      triton, // Triton is optional: enabled only when a provider instance is supplied
      titan, // Titan live quote is optional
      solPriceUsd,
      minAgeMinutes,
      maxAgeMinutes,
    } = options;
    this.solPriceUsd = Number.isFinite(solPriceUsd) ? solPriceUsd : undefined;
    this.agePreFilterMinMs = (minAgeMinutes ?? 0) * 60_000;
    this.agePreFilterMaxMs = (maxAgeMinutes ?? Number.MAX_SAFE_INTEGER) * 60_000;
    // TRITON-ONLY (hard): de Solana-WS-lane is standaard UIT. Alleen expliciete
    // inschakeling (useSolanaWs=true) instantieert de SolanaRpcProvider; zonder
    // endpoints wordt niets aangemaakt (geen mainnet-beta fallback meer — dat was
    // een latente Triton-only-schending).
    if (options.useSolanaWs && rpcWsEndpoint && rpcHttpEndpoint) {
      this.solana = new SolanaRpcProvider(
        whaleWallets,
        minWhaleTxSol,
        rpcWsEndpoint,
        rpcHttpEndpoint,
      );
    }
    this.triton = triton;
    this.titan = titan;
  }

  get whaleActivity(): ReadonlyMap<string, number> {
    return this.solana?.whaleActivity ?? new Map();
  }

  destroy(): void {
    this.solana?.destroy();
    this.triton?.destroy();
  }

  drainDiagnostics(): string[] {
    return [
      ...(this.diagnostics ?? []).splice(0),
      ...(this.solana?.drainDiagnostics() ?? []),
      ...(this.triton?.drainDiagnostics() ?? []),
      ...(this.titan?.drainDiagnostics() ?? []),
    ];
  }

  /** Last scan's discovery lane counts + Triton stream status, for live debugging. */
  discoveryDebug(): string {
    return this.discoveryDebugLast;
  }

  /** Full live-debug payload for the dashboard /api/debug endpoint. */
  debugInfo(): Record<string, unknown> {
    return {
      discovery: this.discoveryDebugLast,
      titanPricedPools: this.titanPricedPools,
      pricedMerged: this.pricedMergedCount,
      unpricedMerged: this.unpricedMergedCount,
      providerLatency: { ...this.lastProviderLatency },
      disabledProviders: Array.from(this.disabledProviders),
      solPriceUsd: this.solPriceUsd,
      titanEnabled: Boolean(this.titan),
      // Triton-first verbruiksregistratie: Vixen events + JSON-RPC calls per methode.
      usage: (this.triton as unknown as { usageSnapshot?: () => Record<string, unknown> })?.usageSnapshot?.(),
      // Scan-tellers over levensduur (voor zuinigheids-volume-analyse per periode).
      scanCount: this.scanCount,
      birthUptimeMs: this.uptimeMs(),
    };
  }

  private scanCount = 0;
  private readonly startTime = Date.now();
  private uptimeMs(): number {
    return Date.now() - this.startTime;
  }

  /** Laatst gemeten round-trip latency (ms) per provider over de laatste scan. */
  getProviderLatency(): Readonly<Record<string, number>> {
    return this.lastProviderLatency;
  }

  /** Schakel een provider aan/uit via de dashboard-controls ('TRITON','SOLANA_WS'). */
  setProviderEnabled(name: string, enabled: boolean): void {
    if (enabled) this.disabledProviders.delete(name.toUpperCase());
    else this.disabledProviders.add(name.toUpperCase());
  }
  /** Huidige enable/disable-status per provider. */
  getProviderEnabled(): Readonly<Record<string, boolean>> {
    const all = this.solana ? ['TRITON', 'SOLANA_WS'] : ['TRITON'];
    return Object.fromEntries(all.map((name) => [name, !this.disabledProviders.has(name)]));
  }
  private providerEnabled(name: string): boolean {
    return !this.disabledProviders.has(name.toUpperCase());
  }

  /**
   * Fetch a live Titan route-quoted fill price (SOL → mint) as USD, using the
   * exact planned position size. Returns undefined if Titan is unavailable or
   * cannot route (caller keeps the pair-derived price — fail-closed).
   * `WSOL` input amounts (lamports) map directly to the quote size.
   */
  async fetchTitanFillUsd(mint: string, amountLamports: number, solPriceUsd: number, baseDecimals?: number): Promise<number | undefined> {
    if (!this.titan) return undefined;
    // input = SOL (v1 API prijst SOL→mint); slippage/numQuotes zitten in de
    // SDK-verbinding zelf (geen per-call params voor getSwapPrice).
    // baseDecimals: vereist voor de correcte SOL-per-token-decimaal-correctie (T1).
    const quote = await this.titan.fetchQuote({
      outputMint: mint,
      amountLamports,
      baseDecimals,
    });
    if (!quote) return undefined;
    const usd = this.titan.quoteToUsd(quote, solPriceUsd);
    return Number.isFinite(usd) && usd !== undefined && usd > 0 ? usd : undefined;
  }

  private pushDiagnostic(message: string): void {
    this.diagnostics ??= [];
    if (this.diagnostics.length >= CompositeProvider.MAX_DIAGNOSTICS) this.diagnostics.shift();
    this.diagnostics.push(message);
  }

  fetchSnapshots(): Promise<MarketSnapshot[]> {
    if (this.discoveryInFlight) return this.discoveryInFlight;
    const epoch = this.fetchSnapshotsOnce();
    const tracked = epoch.finally(() => {
      if (this.discoveryInFlight === tracked) this.discoveryInFlight = undefined;
    });
    this.discoveryInFlight = tracked;
    return tracked;
  }

  private async fetchSnapshotsOnce(): Promise<MarketSnapshot[]> {
    this.scanCount += 1;
    let wsPools: MarketSnapshot[] = [];
    let tritonPools: MarketSnapshot[] = [];
    if (this.solana && this.providerEnabled('SOLANA_WS')) {
      try {
        const t0 = Date.now();
        wsPools = await this.solana.fetchSnapshots();
        this.lastProviderLatency['SOLANA_WS'] = Date.now() - t0;
      } catch (error) {
        this.pushDiagnostic(`discovery: solana: ${errorMessage(error)}`);
      }
    }
    if (this.triton && this.providerEnabled('TRITON')) {
      try {
        const t0 = Date.now();
        tritonPools = await this.triton.fetchSnapshots();
        this.lastProviderLatency['TRITON'] = Date.now() - t0;
      } catch (error) {
        this.pushDiagnostic(`discovery: triton: ${errorMessage(error)}`);
      }
    }

    // Verbose discovery telemetry (kept OUT of the diagnostic queue so it does not
    // churn the cap-500-retained tests): stored on the provider, exposed via
    // drainDiagnostics-free accessor; `merged=` is filled once the merge completes.
    this.discoveryDebugLast = `discovery_debug ws=${wsPools.length} triton=${tritonPools.length} total=${wsPools.length + tritonPools.length} merged=PENDING titanPriced=${this.titanPricedPools} ${this.triton?.tritonDiscoveryDebug?.() ?? ''}`;

    const seen = new Set<string>();
    const merged: MarketSnapshot[] = [];
    const now = Date.now();
    const whaleActivityWindow = 30 * 60 * 1000;

    const annotate = (pool: MarketSnapshot): MarketSnapshot => {
      const ts = this.solana?.whaleActivity.get(pool.mint);
      if (ts && now - ts < whaleActivityWindow) {
        return { ...pool, whaleInterestAt: new Date(ts).toISOString() };
      }
      return pool;
    };

    // Merge discovery from both sources (Solana WS + Triton Vixen), dedupe by pairId.
    const discoveryPools = [...wsPools, ...tritonPools];
    for (const pool of discoveryPools) {
      if (seen.has(pool.pairId)) continue;
      seen.add(pool.pairId);
      if (Number.isFinite(pool.priceUsd) && pool.priceUsd > 0) {
        merged.push(annotate(pool));
        continue;
      }

      // Triton-first: geen Birdeye/Gecko exact-pair enrichment. Een ongeprijsde
      // discovery-pool wordt geprijsd via (1) Titan route-quote of (2) doorgestuurd
      // voor discovery-only-rijping (te-jong) — de scanner evalueert hem pas zodra
      // leeftijd + flow dat toelaten. Geen externe quote-providers.
      let titanUsd: number | undefined;
      if (this.titan && this.solPriceUsd) {
        // Zuinigheid: cache de Titan route-quote per mint (60s) — een ongeprijsde
        // pool wordt niet bij élke scan opnieuw geprobeerd; alleen verse mints
        // kosten één quote-call per minuut.
        const cached = this.titanQuoteCache.get(pool.mint);
        if (cached && cached.expiresAt > now) {
          titanUsd = cached.usd;
        } else {
          try {
            const probe = 200_000_000; // 0.2 SOL probe size voor route-quote
            titanUsd = await this.fetchTitanFillUsd(pool.mint, probe, this.solPriceUsd, pool.poolDepth?.baseDecimals);
          } catch (error) {
            this.pushDiagnostic(`discovery_titan_price: mint=${pool.mint.slice(0, 8)}: ${errorMessage(error)}`);
          }
          this.titanQuoteCache.set(pool.mint, { usd: titanUsd, expiresAt: now + 60_000 });
        }
      }
      if (titanUsd !== undefined && Number.isFinite(titanUsd) && titanUsd > 0) {
        this.titanPricedPools += 1;
        merged.push(annotate({ ...pool, priceUsd: titanUsd }));
        continue;
      }
      // Discovery-only rijping: te-jonge pools (jonger dan de pre-filter-minimum)
      // worden doorgestuurd zonder prijs zodat ze in een latere scan rijp genoeg
      // zijn voor evaluatie. Oudere ongeprijsde pools: fail-closed (niet droppen
      // van geprijsde discovery; de scanner bepaalt de rest).
      if (pool.pairCreatedAt && Number.isFinite(Date.parse(pool.pairCreatedAt))) {
        const ageMs = now - Date.parse(pool.pairCreatedAt);
        if (ageMs < this.agePreFilterMinMs) {
          merged.push(annotate({ ...pool })); // discovery-only, ongeprijsd → scan-rijping
          continue;
        }
      }
      continue;
    }

    // Enrich any discovery pools that still carry a mint-derived placeholder ticker
    // (no exact-pair quote available) with the real coin ticker. Resolution order:
    // Triton DAS `getAsset` (paid subscription, preferred) → GeckoTerminal metadata
    // (free fallback) → keep mint-derived label. Cached 24h in both resolvers, so
    // per-scan cost stays near zero after first resolution.
    const isPriced = (pool: MarketSnapshot): boolean => Number.isFinite(pool.priceUsd) && pool.priceUsd > 0;
    if (merged.some((pool) => isPriced(pool) && isMintDerivedTicker(pool.symbol))) {
      const realSymbols = new Map<string, string | undefined>();
      for (const pool of merged) {
        if (!isPriced(pool)) continue;
        if (realSymbols.has(pool.mint)) continue;
        if (!isMintDerivedTicker(pool.symbol)) { realSymbols.set(pool.mint, pool.symbol); continue; }
        let resolved: string | undefined;
        if (this.triton) resolved = await this.triton.resolveSymbol(pool.mint);
        // Triton-first: Gecko symbol-resolutie uit (DAS getAsset via triton is primair).
        realSymbols.set(pool.mint, resolved);
      }
      for (let i = 0; i < merged.length; i += 1) {
        const real = realSymbols.get(merged[i].mint);
        if (real) merged[i] = { ...merged[i], symbol: real };
      }
    }
    // ZUINIGHEID (credits): géén rug-assessment hier meer — de scanner roept
    // `assessRugRisk` lazy aan (ná de market-gate, vlak vóór een entry). Pools
    // die op age/liquidity/flow afvallen kosten zo nooit een betaalde getAsset-
    // call. De output blijft gelijk: rugRisk wordt pas gezet als er een entry
    // dreigt (de market-gate mist rugRisk niet — het degradeert naar 'unknown',
    // en alleen in STRICT_RISK_MODE wordt dat geblokkeerd).

    // Laag B: koppel live per-mint flow (Vixen buys/sells) aan jonge pools die
    // geen externe 5-min-data hebben, zodat zij kunnen rijpen en scoren.
    if (this.triton) {
      for (let i = 0; i < merged.length; i += 1) {
        const pool = merged[i];
        if (pool.buysM5 !== undefined && pool.sellsM5 !== undefined) continue;
        const flow = this.triton.getFlow(pool.mint);
        if (flow.buys > 0 || flow.sells > 0) {
          merged[i] = { ...pool, buysM5: flow.buys, sellsM5: flow.sells };
        }
      }
    }

    this.pricedMergedCount = merged.filter((pool) => Number.isFinite(pool.priceUsd) && pool.priceUsd > 0).length;
    this.unpricedMergedCount = merged.length - this.pricedMergedCount;
    this.lastMergedSnapshots = merged;
    this.discoveryDebugLast = this.discoveryDebugLast.replace('merged=PENDING', `merged=${merged.length}`);

    return merged;
  }

  /** Triton on-chain rug-assessment (7d-cache). Lazy: alleen bij entry-dreiging. */
  async assessRugRisk(mint: string): Promise<MarketSnapshot['rugRisk'] | undefined> {
    if (!this.triton) return undefined;
    const evidence = await this.triton.assessRugSafety(mint);
    return evidence ? classifyRugRisk(evidence) : classifyRugRisk(undefined);
  }

  async fetchSnapshotsForPositions(positions: readonly PositionQuoteIdentity[]): Promise<MarketSnapshot[]> {
    const unique = Array.from(new Map(positions.map((position) => [positionKey(position), position])).values());
    // Triton-first: positie-quotes komen eerst uit de laatst-geprijsde discovery-merge
    // (curve self-calc). Een positie die NIET (meer) in de actieve discovery-flow zit
    // (na entry stopt de stream voor die mint) krijgt hier een VERSE on-demand quote:
    //   - verse mark-prijs via fetchPositionSnapshot (curve-reserves, 1 gecachte RPC)
    //   - echte coin-naam via DAS getAsset (24h-cache)
    // Zo blijven mark-prijzen / PnL live i.p.v. bevroren op entry-price.
    const priced = this.lastMergedSnapshots ?? [];
    const byKey = new Map(priced.map((p) => [positionKey(p), p]));
    const out: MarketSnapshot[] = [];
    for (const position of unique) {
      const snapshot = byKey.get(positionKey(position));
      if (snapshot && Number.isFinite(snapshot.priceUsd) && snapshot.priceUsd > 0) {
        // discovery-merge hit: gebruik hem (verse flow-prijs)
        out.push(snapshot);
        continue;
      }
      // Fallback: verse on-demand quote voor deze bekende mint (geen herontdekking nodig)
      if (this.triton?.fetchPositionSnapshot) {
        try {
          const fresh = await this.triton.fetchPositionSnapshot(position.mint);
          if (fresh && Number.isFinite(fresh.priceUsd) && fresh.priceUsd > 0) {
            out.push({
              pairId: position.pairId,
              mint: position.mint,
              symbol: fresh.symbol,
              source: 'triton_position_mark',
              observedAt: new Date().toISOString(),
              pairCreatedAt: new Date().toISOString(),
              priceUsd: fresh.priceUsd,
              buysM5: 0,
              sellsM5: 0,
            });
          }
        } catch {
          // fail-closed: positie blijft open, mark onveranderd
        }
      }
    }
    return out;
  }
}

function isExactPositiveQuote(quote: MarketSnapshot | undefined, identity: PositionQuoteIdentity): quote is MarketSnapshot {
  return quote !== undefined
    && quote.pairId === identity.pairId
    && quote.mint === identity.mint
    && Number.isFinite(quote.priceUsd)
    && quote.priceUsd > 0;
}

/**
 * Detect a mint-address-prefix placeholder ticker (exactly 6 uppercase base58
 * chars from the mint address, e.g. 'KINXDE'). These are NOT real coin tickers
 * and should be resolved to the true symbol (BTC, SOL…, POPCAT).
 */
function isMintDerivedTicker(symbol: string | undefined): boolean {
  if (!symbol || symbol.length !== 6) return false;
  return /^[1-9A-HJ-NP-Z]{6}$/.test(symbol) && !['MARKET', 'SCANNER'].includes(symbol);
}

function exactQuoteMap(quotes: readonly MarketSnapshot[], identities: readonly PositionQuoteIdentity[]): Map<string, MarketSnapshot> {
  const expected = new Map(identities.map((identity) => [positionKey(identity), identity]));
  const result = new Map<string, MarketSnapshot>();
  for (const quote of quotes) {
    const key = positionKey(quote);
    const identity = expected.get(key);
    if (identity && isExactPositiveQuote(quote, identity)) result.set(key, quote);
  }
  return result;
}

function positionKey(position: PositionQuoteIdentity): string {
  return `${position.pairId}\0${position.mint}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}