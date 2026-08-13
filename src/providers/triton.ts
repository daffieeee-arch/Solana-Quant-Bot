import type { MarketSnapshot, PoolDepth } from '../scoring.js';
import { defaultHttpFetcher, fetchWithTimeout, type HttpFetcher } from './http.js';
import { TritonReserveReader } from './triton-reserves.js';
import { spotPriceUsd, usdPerQuoteUnit } from '../stream-price.js';
import { FlowTelemetry } from '../flow-telemetry.js';

/** Derive a stable pseudo-ticker from the mint address (first 6 chars, uppercase). */
function mintTicker(mint: string): string {
  return mint.slice(0, 6).toUpperCase();
}

/**
 * Triton One Vixen Program Data Streams provider.
 *
 * Subscribes to pre-parsed, program-specific updates (Pump.fun bonding curve
 * and buy/sell, Raydium AMMv4 pool state, Raydium CPMM pool state) and drains
 * newly discovered pools as bounded identity records with `priceUsd: 0`.
 * Exact-pair pricing is owned by CompositeProvider (Birdeye primary,
 * GeckoTerminal secondary) — never quote within this provider.
 *
 * Design mirrors SolanaRpcProvider discovery: identity + provenance only, no
 * market-data aggregation, bounded FIFO pending queue.
 */

const MAX_DIAGNOSTICS = 500;
// Bounded pending-discovery queue. Mirrors other providers' caps.
const MAX_PENDING_IDENTITIES = 2_000;

// Re-emit cooldown for an already-seen pool so the scanner can re-evaluate it as
// it ripens. Suppresses duplicate within-window floods but allows later re-offers.
const REEMIT_COOLDOWN_MS = 30_000;
/** Zuinigheid: bonding-curve depth fetch throttlen per mint (niet op elk event). */
const PUMP_DEPTH_THROTTLE_MS = 10 * 60_000;
/** Fase C: RPC-resultaat position-mark cache — gebruikt als FALLBACK wanneer
 *  geen verse lokale stream-prijs beschikbaar is. > scan-interval (30s) zodat
 *  binnen één venster niet elke scan opnieuw RPC doet. Keuze 90s = 3 scans.
 *  Primaire bron is POSITION_MARK_FRESH_MS (lokale stream-prijs, 0 RPC). */
const POSITION_MARK_CACHE_MS = 90_000;
/** Fase-O: freshnes-venster voor de LOKALE stream-prijs (gratis balance-pricing
 *  uit emitDiscovery). Zolang er recente stream-prijs is (dit venster), géén RPC
 *  voor position-marks. Bewaart actuele prijzen + risk-reactie zonder RPC-kost. */
const POSITION_MARK_FRESH_MS = 60_000;

// Program IDs verified from Triton Vixen docs + live sample (2026-08-06).
const PROGRAMS = {
  pumpfun: '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',
  raydiumAmmv4: '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8',
  raydiumCpmm: 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C',
  raydiumClmm: 'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK',
  meteoraAmm: 'cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG',
  orcaWhirlpool: 'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc',
  pumpSwaps: 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
  moonshot: 'MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG',
  jupiterSwap: 'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4',
} as const;

const WSOL_MINT = 'So11111111111111111111111111111111111111112';

// Minimal structural types for Vixen ProgramUpdateType payloads.
type PumpAccounts = { mint?: string; bondingCurve?: string; user?: string };
type PumpBuySell = { accounts?: PumpAccounts; data?: Record<string, string | number | undefined> };
type PumpBondingCurveState = { creator?: string; virtualTokenReserves?: number; virtualSolReserves?: number };
type PumpUpdate = { bondingCurve?: PumpBondingCurveState; buy?: PumpBuySell; sell?: PumpBuySell; migrate?: unknown };

type AmmInfo = {
  coinMint?: string;
  pcMint?: string;
  lpMint?: string;
  tokenCoin?: string;
  tokenPc?: string;
  openOrders?: string;
  market?: string;
  lpAmount?: string;
  status?: string;
};
type RaydiumUpdate = { ammInfo?: AmmInfo; swapBaseIn?: { accounts?: Record<string, string> } };

type CpmmPoolState = {
  mintA?: string;
  mintB?: string;
  lpMint?: string;
  vaultA?: string;
  vaultB?: string;
  status?: string;
};
type CpmmUpdate = { poolState?: CpmmPoolState; swapBaseInput?: { accounts?: Record<string, string> } };

/** A stream client exposes a subscribe(program) returning { on, cancel }. */
export type VixenUpdate =
  | { [program: string]: unknown };

export type TritonStreamLike = {
  // Permissive event registration: 'data' delivers a VixenUpdate, 'error' an
  // Error. The union keeps the seam thin; TritonProvider validates every field.
  on(event: string, listener: (value: unknown) => void): unknown;
  cancel?: () => void;
};

export type TritonClientLike = {
  Subscribe(request: { program: string }): TritonStreamLike;
};

/** Injected seam so the real gRPC client can be used in prod and a fake in tests. */
export type TritonClientFactory = (endpoint: string, token: string) => TritonClientLike;

function isSolanaMint(value: unknown): value is string {
  return typeof value === 'string' && /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(value);
}

function baseMintFromWsolPair(a: string | undefined, b: string | undefined): string | undefined {
  if (!a || !b) return undefined;
  if (a === WSOL_MINT) return b;
  if (b === WSOL_MINT) return a;
  return undefined; // not a SOL pair — skip (scanner targets SOL pairs)
}

/** Pick the non-WSOL, non-USDC-USDT mint as the traded base mint for wide pairs. */
function nonStableMint(a: string | undefined, b: string | undefined): string | undefined {
  if (!a || !b) return undefined;
  const STABLE = new Set([
    WSOL_MINT, // WSOL
    'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', // USDC
    'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', // USDT
  ]);
  if (STABLE.has(a) && !STABLE.has(b)) return b;
  if (STABLE.has(b) && !STABLE.has(a)) return a;
  return undefined;
}

export class TritonProvider {
  private readonly diagnostics: string[] = [];
  private readonly pending: MarketSnapshot[] = [];
  private readonly seenNewPools = new Map<string, number>();
  private readonly clock: () => number;
  private readonly client: TritonClientLike;
  private readonly reserveReader: TritonReserveReader | undefined;
  private readonly solPriceUsd: number;
  private readonly flow = new FlowTelemetry();
  /** Zuinigheid: last-fetch timestamp per mint voor bonding-curve depth (throttle). */
  private readonly pumpDepthThrottle = new Map<string, number>();
  /** Fase C: position-mark cache — beperkt herhaalde getAccountInfo/getTokenLargestAccounts
   * voor open-positie-marks (deze worden per scan-cycle gecalld; 30s-cache halveert de
   * RPC-last zonder de risk-exit-evaluatie te vertragen ten opzichte van scan-interval). */
  private readonly positionMarkCache = new Map<string, { loadedAt: number; priceUsd: number }>();
  /** Fase-O: laatst-geprijsde lokale stream-prijs per mint (gratis balance-pricing
   *  uit emitDiscovery). Primaire verse bron voor position-marks — zolang deze
   *  recent is (POSITION_MARK_FRESH_MS) wordt géén RPC gedaan. Bounded. */
  private readonly lastPriceByMint = new Map<string, { priceUsd: number; at: number }>();
  /** Fase-W: actieve position-watch-mints (open posities). Deze mints krijgen
   *  ALTIJD een verse mark-update uit de binnenkomende stream (vóór de discovery-
   *  cooldown), onafhankelijk van discovery/candidate-status. Bounded. */
  private readonly positionWatchMints = new Set<string>();
  /** Fase-W: observability — mark-source per mint (laatste bron) + tellers. */
  private readonly positionMarkSource = new Map<string, 'STREAM' | 'TITAN' | 'LOCAL_STATE' | 'REGISTRY' | 'RPC' | 'STALE'>();
  private positionMarkSourceCounts: Record<string, number> = { STREAM: 0, TITAN: 0, LOCAL_STATE: 0, REGISTRY: 0, RPC: 0, STALE: 0 };
  private positionMarkStaleTotal = 0;
  private positionMarkRpcFallbackTotal = 0;

  /** Fase-W: registreer een open positie-mint voor watch (idempotent, dedup). */
  addPositionWatch(mint: string): void {
    if (!mint || this.positionWatchMints.has(mint)) return;
    this.positionWatchMints.add(mint);
    if (this.positionWatchMints.size > 1_000) {
      const first = this.positionWatchMints.values().next().value as string | undefined;
      if (first !== undefined) this.positionWatchMints.delete(first);
    }
  }
  /** Fase-W: verwijder de watch bij position-close (idempotent). */
  removePositionWatch(mint: string): void {
    this.positionWatchMints.delete(mint);
    // reviewer: prune de source-map-entry zodat deze niet onbounded groeit.
    this.positionMarkSource.delete(mint);
  }
  /** Fase-W: reconstructie na restart — herstel watches uit de WAL/open-posities. */
  setPositionWatches(mints: readonly string[]): void {
    this.positionWatchMints.clear();
    for (const m of mints) this.addPositionWatch(m);
  }
  /** Fase-W: actieve watch-count (observability). */
  activePositionWatches(): number {
    return this.positionWatchMints.size;
  }
  /** Fase-W: observability snapshot (source-distributie + tellers). */
  positionWatchMetrics(): Record<string, unknown> {
    return {
      active: this.positionWatchMints.size,
      sourceCounts: { ...this.positionMarkSourceCounts },
      staleTotal: this.positionMarkStaleTotal,
      rpcFallbackTotal: this.positionMarkRpcFallbackTotal,
      sourceByMint: Object.fromEntries(this.positionMarkSource),
    };
  }
  /** Fase-W: routeer een binnenkomende stream-prijs naar een ge-watchte positie
   *  (vóór discovery-cooldown; 0 RPC). Return true als de mint een watch had. */
  private _routeStreamPriceToWatch(mint: string, priceUsd: number, source: 'STREAM' | 'LOCAL_STATE' | 'REGISTRY'): boolean {
    if (!this.positionWatchMints.has(mint)) return false;
    if (!Number.isFinite(priceUsd) || priceUsd <= 0) return false;
    const now = this.clock();
    this.lastPriceByMint.set(mint, { priceUsd, at: now });
    this.positionMarkSource.set(mint, source);
    this.positionMarkSourceCounts[source] = (this.positionMarkSourceCounts[source] ?? 0) + 1;
    return true;
  }

  private cleanupPumpDepthThrottle(): void {
    if (this.pumpDepthThrottle.size < 20_000) return;
    const now = this.clock();
    for (const [mint, ts] of this.pumpDepthThrottle) {
      if (now - ts >= PUMP_DEPTH_THROTTLE_MS) this.pumpDepthThrottle.delete(mint);
    }
    if (this.pumpDepthThrottle.size > 20_000) {
      const oldest = this.pumpDepthThrottle.keys().next().value as string | undefined;
      if (oldest !== undefined) this.pumpDepthThrottle.delete(oldest);
    }
  }
  private destroyed = false;
  private discoveryInFlight?: Promise<MarketSnapshot[]>;
  /** Verbose discovery telemetry: events + subscribed programs per scan-window. */
  private readonly eventCounter = new Map<string, number>();
  private readonly subscriptionErrors: string[] = [];
  private streamOpenStatus = new Map<string, 'open' | 'error' | 'pending'>();
  /** mint → bonding curve correlatie (voor de zwaar gethrottled getAccountInfo-fallback). */
  private readonly curveRegistry = new Map<string, string>();
  /** Laatste reconnect-poging per programma (anti hot-retry bij bulk-drops). */
  private readonly lastReconnectAt = new Map<string, number>();
  /** Actieve reconnect-timers per programma (opgeruimd bij destroy). */
  private readonly reconnectTimers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(
    endpoint: string,
    token: string,
    clientFactory: TritonClientFactory = () => {
      throw new Error('Triton client factory not provided (Vixen SDK integration required)');
    },
    clock: () => number = () => Date.now(),
    reserveReader?: TritonReserveReader,
    options: { solPriceUsd?: number } = {},
  ) {
    if (!endpoint || endpoint.includes('\n') || endpoint.includes('\r')) {
      throw new Error('Triton endpoint must be a non-empty single line');
    }
    if (!token || token.includes('\n') || token.includes('\r')) {
      throw new Error('Triton token must be a non-empty single line');
    }
    this.clock = clock;
    this.solPriceUsd = Number.isFinite(options.solPriceUsd) ? options.solPriceUsd! : 0;
    // Real SDK integration: construct the gRPC client via the provided factory.
    this.client = clientFactory(endpoint, token);
    this.reserveReader = reserveReader;
    this.subscribeAll();
  }

  private pushDiagnostic(message: string): void {
    if (this.diagnostics.length >= MAX_DIAGNOSTICS) this.diagnostics.shift();
    this.diagnostics.push(message);
  }

  private subscribeAll(): void {
    const subscriptions: Array<[string, (u: VixenUpdate) => void]> = [
      [PROGRAMS.pumpfun, (u) => { void this.onPumpUpdate(u); }],
      [PROGRAMS.raydiumAmmv4, (u) => { void this.onRaydiumUpdate(u); }],
      [PROGRAMS.raydiumCpmm, (u) => { void this.onCpmmUpdate(u); }],
      // Multi-DEX (geyser generic parse, mint-based): de geyser-factory stuurt
      // voor deze programma's een genericLaunch-event door.
      [PROGRAMS.raydiumClmm, (u) => { void this.onGenericLaunchUpdate(u); }],
      [PROGRAMS.meteoraAmm, (u) => { void this.onGenericLaunchUpdate(u); }],
      [PROGRAMS.orcaWhirlpool, (u) => { void this.onGenericLaunchUpdate(u); }],
      [PROGRAMS.pumpSwaps, (u) => { void this.onGenericLaunchUpdate(u); }],
      [PROGRAMS.moonshot, (u) => { void this.onGenericLaunchUpdate(u); }],
      [PROGRAMS.jupiterSwap, (u) => { void this.onGenericLaunchUpdate(u); }],
    ];
    for (const [program, handler] of subscriptions) {
      this.subscribeOnce(program, handler, undefined);
    }
  }

  /**
   * Subscribe met AUTOMATISCHE reconnect (exponentiële backoff + jitter). Voorheen
   * zette een 'error'/'end'-event de stream op 'error' én stopte — de hele
   * Vixen-discovery stierf stilletjes tot een proces-herstart (audit T2-B4).
   * Elk error/end-reset hersubscribed na ~1s..30s (jitter), status blijft zichtbaar.
   */
  private subscribeOnce(program: string, handler: (u: VixenUpdate) => void, backoffMs: number | undefined): void {
    const attempt = (): void => {
      try {
        this.streamOpenStatus.set(program, backoffMs === undefined ? 'pending' : 'error');
        const stream = this.client.Subscribe({ program });
        stream.on('data', (value: unknown) => {
          this.streamOpenStatus.set(program, 'open');
          this.eventCounter.set(program, (this.eventCounter.get(program) ?? 0) + 1);
          this.eventTotalCounter.set(program, (this.eventTotalCounter.get(program) ?? 0) + 1);
          try {
            handler(value as VixenUpdate);
          } catch (error) {
            this.pushDiagnostic(`triton: handler error program=${program}: ${error instanceof Error ? error.message : String(error)}`);
          }
        });
        stream.on('error', (error: unknown) => {
          this.streamOpenStatus.set(program, 'error');
          this.subscriptionErrors.push(`program=${program}: ${error instanceof Error ? error.message : String(error)}`);
          this.pushDiagnostic(`triton: stream error program=${program}: ${error instanceof Error ? error.message : String(error)}`);
          this.scheduleReconnect(program, handler, backoffMs);
        });
        stream.on('end', () => {
          this.streamOpenStatus.set(program, 'error');
          this.pushDiagnostic(`triton: stream closed program=${program}; reconnecting`);
          this.scheduleReconnect(program, handler, backoffMs);
        });
        // Best-effort 'open'/'ready' signal — a data event also implies open.
        const streamAny = stream as unknown as { once?: (e: string, cb: () => void) => unknown };
        if (typeof streamAny.once === 'function') {
          streamAny.once('open', () => this.streamOpenStatus.set(program, 'open'));
          streamAny.once('ready', () => this.streamOpenStatus.set(program, 'open'));
        }
      } catch (error) {
        this.streamOpenStatus.set(program, 'error');
        this.subscriptionErrors.push(`program=${program}: ${error instanceof Error ? error.message : String(error)}`);
        this.pushDiagnostic(`triton: subscribe failed program=${program}: ${error instanceof Error ? error.message : String(error)}`);
        this.scheduleReconnect(program, handler, backoffMs);
      }
    };
    attempt();
  }

  /** Reconnect na exponentiële backoff (1s→2s→4s…→30s max) + jitter, gecapped per uur. */
  private scheduleReconnect(program: string, handler: (u: VixenUpdate) => void, backoffMs: number | undefined): void {
    if (this.destroyed) return;
    const current = backoffMs ?? 1000;
    const next = Math.min(current * 2, 30_000);
    const jitter = Math.round(Math.random() * 500);
    // Zuinigheid: max 1 reconnect-poging per ~10s per programma (voorkomt 9×
    // hot-retry bij een tijdelijke gRPC-bulk-drop die alle streams samen treft).
    const now = Date.now();
    const last = this.lastReconnectAt.get(program) ?? 0;
    if (now - last < 8_000 && backoffMs !== undefined) {
      this.lastReconnectAt.set(program, now);
      return; // al in backoff; laat de volgende geplande poging de stream herstellen
    }
    this.lastReconnectAt.set(program, now);
    this.reconnectTimers.set(program, setTimeout(() => {
      if (!this.destroyed) this.reconnectTimers.delete(program);
      this.subscribeOnce(program, handler, next);
    }, next + jitter));
  }

  /** Cumulatieve Vixen stream-events per program (nooit gewist; verbruiksregistratie). */
  private readonly eventTotalCounter = new Map<string, number>();

  /** Cumulatief verbruik: Vixen events per program + Triton JSON-RPC calls per methode. */
  usageSnapshot(): Record<string, number | Record<string, number>> {
    const events: Record<string, number> = {};
    for (const [p, n] of this.eventTotalCounter) events[p.slice(0, 6)] = n;
    return {
      vixenEvents: events,
      jsonRpcCalls: this.reserveReader?.rpcCallCountsSnapshot() ?? {},
    };
  }

  /**
   * Verbose discovery telemetry for dashboard/live-debug: per-program event counts
   * since last call, stream open/error status, and cumulative subscribe errors.
   */
  tritonDiscoveryDebug(): string {
    const events = Array.from(this.eventCounter.entries()).map(([p, n]) => `${p.slice(0, 4)}:${n}`).join(' ');
    const streams = Array.from(this.streamOpenStatus.entries()).map(([p, s]) => `${p.slice(0, 4)}:${s}`).join(' ');
    this.eventCounter.clear();
    return `triton_debug ${new Date().toISOString()} events(${events || 'none'}) streams(${streams || 'none'}) subErrors(${this.subscriptionErrors.length})`;
  }

  /**
   * Resolve the real coin ticker for a mint via Triton DAS `getAsset`
   * (paid subscription; GeckoTerminal is the caller's fallback).
   */
  async resolveSymbol(mint: string): Promise<string | undefined> {
    if (!this.reserveReader) return undefined;
    return this.reserveReader.resolveSymbol(mint);
  }

  /**
   * Verse USDT-prijs voor een open positie (mark-ticker). Triton-first zuinig:
   *  - Als de curve al bekend is (curveRegistry, van de discovery-flow), pak dan
   *    DIRECT fetchPumpDepth(curve) = 1 RPC-call. Alleen als de curve ontbreekt
   *    valt hij terug op fetchPumpDepthByMint (2 calls: largest+accountInfo).
   *  - Eigen position-mark cache (POSITION_MARK_CACHE_MS = 30s): de mark hoeft
   *    niet méér dan één keer per scan-window ververst te worden — daalt de
   *    per-scan getAccountInfo/getTokenLargestAccounts drastisch zonder dat de
   *    risk-exit-evaluatie (max-hold/time-stop) aan latency verliest.
   *  - fallback: undefined (fail-closed; scanner houdt de positie open).
   */
  async fetchPositionPriceUsd(mint: string): Promise<number | undefined> {
    if (!this.reserveReader || !this.solPriceUsd) return undefined;
    const nowMark = this.clock();
    // Fase-O freshness-regel (primaire bron): verse LOKALE stream-prijs
    // (gratis balance-pricing uit emitDiscovery) → 0 RPC zolang recent.
    const local = this.lastPriceByMint.get(mint);
    if (local && nowMark - local.at < POSITION_MARK_FRESH_MS) return local.priceUsd;
    // Fallback: eerder RPC-resultaat (mark-cache, nu 90s > scan-interval)
    const markCached = this.positionMarkCache.get(mint);
    if (markCached && nowMark - markCached.loadedAt < POSITION_MARK_CACHE_MS) return markCached.priceUsd;
    try {
      const curve = this.curveRegistry.get(mint);
      const depth = curve
        ? await this.reserveReader.fetchPumpDepth(curve, this.solPriceUsd)
        : await this.reserveReader.fetchPumpDepthByMint(mint, this.solPriceUsd);
      if (!depth || !Number.isFinite(depth.baseReserve) || depth.baseReserve <= 0) return undefined;
      // spotPrijs = quoteReserve/baseReserve (SOL per token) × SOL-prijs → USD.
      const quoteUnits = depth.quoteReserve / 10 ** (depth.quoteDecimals ?? 9);
      const baseUnits = depth.baseReserve / 10 ** (depth.baseDecimals ?? 6);
      if (baseUnits <= 0) return undefined;
      const solPerToken = quoteUnits / baseUnits;
      if (!Number.isFinite(solPerToken) || solPerToken <= 0) return undefined;
      const priceUsd = solPerToken * this.solPriceUsd;
      // position-mark RPC-cache opslaan; bounded (max 500 posities-marks)
      if (this.positionMarkCache.size > 500) this.positionMarkCache.clear();
      this.positionMarkCache.set(mint, { loadedAt: nowMark, priceUsd });
      // ook de lokale stream-prijs bijwerken (zodat volgende frames het gebruiken)
      this.lastPriceByMint.set(mint, { priceUsd, at: nowMark });
      return priceUsd;
    } catch {
      return undefined;
    }
  }

  /** Verse positie-snapshot: echte coin-naam (DAS) + verse mark-prijs (curve). */
  async fetchPositionSnapshot(mint: string): Promise<{ symbol: string; priceUsd: number } | undefined> {
    const [priceUsd, symbol] = await Promise.all([
      this.fetchPositionPriceUsd(mint),
      this.resolveSymbol(mint),
    ]);
    if (priceUsd === undefined || !Number.isFinite(priceUsd) || priceUsd <= 0) return undefined;
    return { symbol: symbol ?? mintTicker(mint), priceUsd };
  }

  /**
   * Assess rug/honeypot evidence for a mint via Triton on-chain data
   * (DAS getAsset + holder concentration). Definitions in src/rug-risk.ts.
   */
  async assessRugSafety(mint: string): Promise<import('../rug-risk.js').RugEvidence | undefined> {
    if (!this.reserveReader) return undefined;
    return this.reserveReader.assessRugSafety(mint);
  }

  /** Live buy/sell flow counts for a mint (rolling window, from Vixen events). */
  getFlow(mint: string): { buys: number; sells: number } {
    return this.flow.snapshot(mint);
  }

  /* ─── Pump.fun ─────────────────────────────────────────── */
  private async onPumpUpdate(update: VixenUpdate): Promise<void> {
    // De geyser-factory stuurt voor élke pump-txn TWEE formats naar deze
    // subscriber: (a) het klassieke pump-format {buy|sell:{accounts:{mint,
    // bondingCurve}}}, en (b) het genericLaunch-format {genericLaunch:{mint,
    // kind, priceLamportsPerToken, baseRawDelta, wsolRawDelta, …}} dat UIT DE
    // TXN-BALANCES een execution-prijs + swap-diepte meebrengt (0 RPC-calls).
    // Voorheen werd (b) genegeerd → de balance-prijs ging verloren én de bot
    // viel terug op de dure getAccountInfo-curve-route (13k calls!).
    // ZUINIGE prioriteit: balance-pricing (gratis) eerst; getAccountInfo-curve
    // alleen als fallback en zwaar gethrottled.
    const pump = update as PumpUpdate;
    const trade = pump.buy ?? pump.sell;
    const gen = (update as { genericLaunch?: { mint?: string; curve?: string; kind?: 'buy' | 'sell'; priceLamportsPerToken?: number; baseRawDelta?: number; wsolRawDelta?: number; baseDecimalsForPrice?: number } }).genericLaunch;
    // Balance-priced genericLaunch pad (prioriteit, 0 RPC-calls)
    if (gen?.mint && gen.priceLamportsPerToken && gen.priceLamportsPerToken > 0 && (gen.baseRawDelta ?? 0) > 0 && (gen.wsolRawDelta ?? 0) > 0) {
      if (gen.kind === 'buy') this.flow.recordBuy(gen.mint);
      else if (gen.kind === 'sell') this.flow.recordSell(gen.mint);
      const dec = gen.baseDecimalsForPrice ?? 6;
      // Synthetische depth: prijs = execution-prijs, liquiditeit = virtuele
      // curve-reserve (~30 SOL) i.p.v. de swap-omvang (die absurd laag is).
      const poolDepth: PoolDepth = this.syntheticDepth(gen.priceLamportsPerToken, this.solPriceUsd ?? 74, dec, gen.mint);
      this.emitDiscovery({
        pairId: gen.curve ? `gx:${gen.curve}` : `gx:${gen.mint}`,
        mint: gen.mint,
        symbol: mintTicker(gen.mint),
        source: 'triton_vixen_pumpfun',
        programId: PROGRAMS.pumpfun,
        poolDepth,
        quoteMint: WSOL_MINT,
        synthetic: true,
      });
      // Registeer de curve-relatie als die er is (voor de getAccountInfo-fallback)
      if (gen.curve) this.curveRegistry.set(gen.mint, gen.curve);
      return;
    }
    // Klassiek pump-format (buy/sell met curve-identiteit)
    if (trade?.accounts?.mint && trade.accounts.bondingCurve) {
      // Laag B: live per-mint flow telemetry.
      if (pump.buy) this.flow.recordBuy(trade.accounts.mint);
      if (pump.sell) this.flow.recordSell(trade.accounts.mint);
      this.curveRegistry.set(trade.accounts.mint, trade.accounts.bondingCurve);
      // ZUINIGHEID (kritiek): getAccountInfo is de duurste call (13k/8u). De
      // carve-met-reserves is grotendeels vervangen door balance-pricing hier-
      // boven. getAccountInfo draait nu ALLEEN als fallback, zwaar gethrottled
      // (LANG throttling-venster), via de curveRegistry.
      let poolDepth: PoolDepth | undefined;
      const nowDepth = this.clock();
      const lastDepth = this.pumpDepthThrottle.get(trade.accounts.mint);
      if (this.reserveReader && (lastDepth === undefined || nowDepth - lastDepth >= PUMP_DEPTH_THROTTLE_MS)) {
        this.pumpDepthThrottle.set(trade.accounts.mint, nowDepth);
        poolDepth = await this.reserveReader.fetchPumpDepth(trade.accounts.bondingCurve, this.solPriceUsd).catch(() => undefined);
        if (lastDepth === undefined) this.cleanupPumpDepthThrottle();
      }
      this.emitDiscovery({
        pairId: trade.accounts.bondingCurve,
        mint: trade.accounts.mint,
        symbol: mintTicker(trade.accounts.mint),
        source: 'triton_vixen_pumpfun',
        programId: PROGRAMS.pumpfun,
        poolDepth,
        quoteMint: WSOL_MINT,
      });
      return;
    }
    // Bonding-curve STATE carries virtual reserves but no mint identity and no curve
    // address in the Vixen payload — so it cannot be safely priced/attached to a mint
    // here. Discovery (from buy/sell) enqueues an unpriced record that composite prices
    // via the exact-pair/REST fallback. Raydium pools ARE stream-priced per-pool (they
    // carry vault accounts) — see emitDiscovery. `migrate` = graduation; no mint; skip.
  }

  /* ─── Raydium AMMv4 ────────────────────────────────────── */
  private async onRaydiumUpdate(update: VixenUpdate): Promise<void> {
    const raydium = update as RaydiumUpdate;
    const info = raydium.ammInfo;
    if (!info) return;
    const mint = baseMintFromWsolPair(info.coinMint, info.pcMint) ?? nonStableMint(info.coinMint, info.pcMint);
    if (!mint || !info.lpMint) return;
    // Pool identity: for AMMv4 the ammInfo block carries lpMint; use lpMint as pairId
    // anchor but note composite must verify via Birdeye exact pair. Use the
    // coin/pc token accounts when available else lpMint.
    const pairId = info.tokenCoin || info.lpMint;
    if (!pairId) return;
    // Live depth: AMMv4 coin vault (tokenCoin) = base vault, pc vault (tokenPc) = quote vault.
    let poolDepth: PoolDepth | undefined;
    const isWsolCoin = info.coinMint === WSOL_MINT;
    const quoteMint = isWsolCoin ? info.coinMint : info.pcMint;
    if (this.reserveReader && info.tokenCoin && info.tokenPc) {
      const quoteVault = isWsolCoin ? info.tokenCoin : info.tokenPc;
      const baseVault = isWsolCoin ? info.tokenPc : info.tokenCoin;
      poolDepth = await this.reserveReader.fetchDepth(
        `amm4:${pairId}`,
        quoteVault,
        baseVault,
        undefined, // decimals uit RPC-response (autoritatief) — zie fetchDepth/loadDepth
        undefined,
        { feeNumerator: 25, feeDenominator: 10_000 },
      );
    }
    this.emitDiscovery({
      pairId,
      mint,
      symbol: mintTicker(mint),
      source: 'triton_vixen_raydium_ammv4',
      programId: PROGRAMS.raydiumAmmv4,
      poolDepth,
      quoteMint,
    });
  }

  /* ─── Raydium CPMM ─────────────────────────────────────── */
  private async onCpmmUpdate(update: VixenUpdate): Promise<void> {
    const cpmm = update as CpmmUpdate;
    const pool = cpmm.poolState;
    if (!pool) return;
    const mint = baseMintFromWsolPair(pool.mintA, pool.mintB) ?? nonStableMint(pool.mintA, pool.mintB);
    if (!mint) return;
    const pairId = pool.lpMint || pool.mintA || pool.mintB;
    if (!pairId) return;
    let poolDepth: PoolDepth | undefined;
    const isWsolA = pool.mintA === WSOL_MINT;
    const quoteMint = isWsolA ? pool.mintA : pool.mintB;
    if (this.reserveReader && pool.vaultA && pool.vaultB) {
      const quoteVault = isWsolA ? pool.vaultA : pool.vaultB;
      const baseVault = isWsolA ? pool.vaultB : pool.vaultA;
      poolDepth = await this.reserveReader.fetchDepth(
        `cpmm:${pairId}`,
        quoteVault,
        baseVault,
        undefined, // decimals uit RPC-response (autoritatief)
        undefined,
        { feeNumerator: 25, feeDenominator: 10_000 },
      );
    }
    this.emitDiscovery({
      pairId,
      mint,
      symbol: mintTicker(mint),
      source: 'triton_vixen_raydium_cpmm',
      programId: PROGRAMS.raydiumCpmm,
      poolDepth,
      quoteMint,
    });
  }

  private async onGenericLaunchUpdate(update: VixenUpdate): Promise<void> {
    // Generieke multi-DEX discovery via mint (geen ammInfo/pcMint-afhankelijkheid).
    // Geproduceerd door de geyser-factory (parseGenericSwap): verse mint uit een
    // willekeurige swap-txn (Raydium CLMM, Meteora, Orca, PumpSwaps, Moonshot).
    const g = (update as { genericLaunch?: { mint?: string; curve?: string; kind?: 'buy' | 'sell'; priceLamportsPerToken?: number; baseRawDelta?: number; wsolRawDelta?: number; baseDecimalsForPrice?: number } }).genericLaunch;
    const mint = g?.mint;
    if (!mint) return;
    // Laag B flow: tel generic buy/sell (mint-keyed, zelfde telemetrie als pump).
    if (g?.kind === 'buy') this.flow.recordBuy(mint);
    else if (g?.kind === 'sell') this.flow.recordSell(mint);
    // Prijs-afleiding: de betrouwbaarste is de execution-prijs uit de txn-balances
    // (priceLamportsPerToken — robuust voor v1 én v2). De bonding-curve-decode is
    // onbetrouwbaar geworden (wisselende v2-account-volgordes/1045-byte accounts).
    // Pump-mints: lees de bonding-curve reserves via de reserveReader (zelfde pad
    // als de Vixen-pump-tak) zodat de generic discovery een echte prijs + liquiditeit
    // krijgt i.p.v. een prijsloze identiteit die op liquidity_below_minimum crasht.
    // ZUINIGHEID (credits!): de mint-derivatielaag (getTokenLargestAccounts +
    // getAccountInfo, 3 RPC-calls per mint) draait ALLEEN voor mints die op 'pump'
    // eindigen — alleen die hebben een bonding curve. Niet-pump mints (Raydium
    // CLMM/CPMM etc.) hebben geen curve-reserves via deze route → 0 RPC-calls.
    // (Voorheen kostte élke generic mint 3 calls — bij honderden mints per scan
    // was dat honderden onnodige Triton-calls/min.)
    let poolDepth: PoolDepth | undefined;
    if (this.reserveReader && this.solPriceUsd && g?.curve) {
      // Alleen als de txn een EXPLICIET curve-adres meeleverde (getAccountInfo,
      // 1 call). De dure fetchPumpDepthByMint-route (getTokenLargestAccounts +
      // getAccountInfo, 3 calls per mint) is UIT in de generic-lane: balance-
      // pricing levert de prijs al gratis, en die route was een credits-killer
      // (getTokenLargestAccounts 1226×, getAccountInfo 13k×/8u).
      poolDepth = await this.reserveReader.fetchPumpDepth(g.curve, this.solPriceUsd).catch(() => undefined);
    }
    // Fallback: synthetische PoolDepth uit de txn-balances (robust voor v1 én v2).
    // De curve-account-decode is onbetrouwbaar (v2-account-volgordes/1045-byte
    // accounts) → als de curve geen depth oplevert, gebruiken we de execution-prijs
    // en de swap-omvang als minimale liquiditeit. ZUINIG: 0 extra RPC-calls.
    if (!poolDepth && g?.priceLamportsPerToken && g.priceLamportsPerToken > 0 && (g.baseRawDelta ?? 0) > 0 && (g.wsolRawDelta ?? 0) > 0) {
      const baseDec = g.baseDecimalsForPrice ?? 6;
      // Synthetische depth: prijs = execution-prijs (correct), liquiditeit =
      // virtuele curve-reserve (~30 SOL) i.p.v. de swap-omvang.
      poolDepth = this.syntheticDepth(g.priceLamportsPerToken, this.solPriceUsd ?? 74, baseDec, mint);
    }
    this.emitDiscovery({
      pairId: g.curve ? `gx:${g.curve}` : `gx:${mint}`,
      mint,
      symbol: mintTicker(mint),
      source: 'triton_geyser_generic_multidex',
      // programId unknown per-txn here; composite prices via mint + reserves.
      programId: '',
      poolDepth: poolDepth && (poolDepth.quoteReserve ?? 0) > 0 && (poolDepth.baseReserve ?? 0) > 0 ? poolDepth : undefined,
      quoteMint: poolDepth && (poolDepth.quoteReserve ?? 0) > 0 ? WSOL_MINT : undefined,
      synthetic: !!(g?.priceLamportsPerToken && poolDepth && !g?.curve),
    });
  }

  /* ─── shared ───────────────────────────────────────────── */
  /** Synthetische pool-diepte uit de balance-pricing (0 RPC). De prijs klopt
   *  (execution-prijs), maar de liquiditeit kan niet uit 1 txn komen. Gebruik de
   *  virtuele curve-reserve van een verse pump (~VIRTUAL_CURVE_SOL) als minimale
   *  realistische liquiditeit i.p.v. de swap-omvang (die absurd laag is). */
  private static readonly VIRTUAL_CURVE_SOL = 30; // SOL — standaard verse pump.fun
  private syntheticDepth(priceLamportsPerToken: number, solPriceUsd: number, baseDecimals: number, mint: string): PoolDepth {
    // baseReserve zodanig dat spotPriceUsd de execution-prijs reproduceert.
    // prijs (SOL/token) = quoteReserve/1e9 ÷ baseReserve/10^baseDecimals
    const P_sol = priceLamportsPerToken / 1e9;
    const quoteReserve = (TritonProvider.VIRTUAL_CURVE_SOL) * 1e9; // 30 SOL lamports
    const baseReserve = P_sol > 0 ? (30 / P_sol) * (10 ** baseDecimals) : 0;
    return {
      quoteReserve,
      baseReserve: Math.round(baseReserve),
      quoteDecimals: 9,
      baseDecimals,
      syntheticPriceLamportsPerToken: priceLamportsPerToken,
    };
  }
  private emitDiscovery(input: {
    pairId: string;
    mint: string;
    symbol: string;
    source: string;
    programId: string;
    poolDepth?: PoolDepth;
    quoteMint?: string;
    synthetic?: boolean;
  }): void {
    if (this.destroyed) return;
    const now = this.clock();
    // Fase-W: bereken de stream-prijs EERST (zodat ge-watchte positie-mints altijd
    // een verse update krijgen, onafhankelijk van de discovery-cooldown hieronder).
    // Dit is de kern van de fix: na entry verdwijnt een mint uit discovery-consumptie,
    // maar de al-binnenkomende stream-prijs moet de position-mark blijven voeden.
    let priceUsd = 0;
    if (input.poolDepth && input.quoteMint) {
      const usdPerQuote = usdPerQuoteUnit(input.quoteMint, this.solPriceUsd);
      if (usdPerQuote !== null) {
        const usd = spotPriceUsd(input.poolDepth, usdPerQuote);
        if (usd !== null && usd > 0) priceUsd = usd;
      }
    }
    // Fase-W: routeer de STREAM-prijs altijd naar ge-watchte positie-mints (0 RPC).
    if (priceUsd > 0) this._routeStreamPriceToWatch(input.mint, priceUsd, 'STREAM');
    // Registreer de gratis stream-prijs als lokale verse-prijs-bron (bounded).
    if (priceUsd > 0) {
      if (this.lastPriceByMint.size > 5_000) this.lastPriceByMint.clear();
      this.lastPriceByMint.set(input.mint, { priceUsd, at: now });
    }
    // Cooldown-windowdedededeupe: re-emit an already-seen pool so the scanner can
    // re-evaluate it as it ripens (Laag A), instead of deduping it forever. Only
    // suppress duplicates that arrive within the REEMIT_COOLDOWN_MS window.
    const lastSeen = this.seenNewPools.get(input.pairId);
    if (lastSeen !== undefined && now - lastSeen < REEMIT_COOLDOWN_MS) return;
    this.seenNewPools.set(input.pairId, now);
    this.cleanupSeenPools();
    const observedAt = new Date(this.clock()).toISOString();
    this.enqueuePending({
      pairId: input.pairId,
      mint: input.mint,
      symbol: input.symbol,
      source: input.source,
      observedAt,
      // Echte leeftijd: behoud het EERSTE discovery-tijdstip voor deze pool bij
      // re-emits (cooldown), zodat `pairCreatedAt` niet elke ronde opnieuw ≈0 is
      // en de rippings-queue kan rijpen tot >= minAge. Zonder dit blijft elke
      // pool eeuwig "te jong" → nooit evalueerbaar → 0 trades.
      pairCreatedAt: this.firstSeenPairCreatedAt(input.pairId, observedAt),
      priceUsd,
      poolDepth: input.poolDepth,
      // Laag B flow + liquiditeit: hang de live buy/sell-telling en de uit de
      // bonding-curve afgeleide liquiditeits-schatting direct aan de snapshot,
      // zodat de surge/liquiditeits-gates echte data hebben i.p.v. undefined.
      buysM5: this.flow.snapshot(input.mint).buys,
      sellsM5: this.flow.snapshot(input.mint).sells,
      ...(input.poolDepth && this.solPriceUsd ? {
        liquidityUsd: this.curveLiquidityUsd(input.poolDepth, input.quoteMint),
      } : {}),
      discovery: {
        signature: `vixen:${input.programId}:${input.pairId}`,
        programId: input.programId,
        instructionLocation: 'inner',
        instructionIndex: 0,
        receiptAt: observedAt,
      },
    });
  }

  /** Eerste-seen tijdstip per pool, onthouden zodat re-emits de echte leeftijd behouden. */
  private readonly pairCreationByPair = new Map<string, number>();

  private firstSeenPairCreatedAt(pairId: string, observedAtIso: string): string {
    const now = this.clock();
    const existing = this.pairCreationByPair.get(pairId);
    if (existing !== undefined) {
      this.pairCreationByPair.set(pairId, now); // touch voor cleanup-ordening
      return new Date(existing).toISOString();
    }
    this.pairCreationByPair.set(pairId, now);
    if (this.pairCreationByPair.size > 20_000) {
      const oldest = this.pairCreationByPair.keys().next().value as string | undefined;
      if (oldest !== undefined) this.pairCreationByPair.delete(oldest);
    }
    return observedAtIso;
  }

  /** Liquiditeit ≈ 2× quote-reserves in USD via de quote-mint (T2-B3-fix). */
  private curveLiquidityUsd(depth: PoolDepth, quoteMint: string | undefined): number | undefined {
    if (!this.solPriceUsd || !Number.isFinite(this.solPriceUsd) || this.solPriceUsd <= 0) return undefined;
    if (!Number.isFinite(depth.quoteReserve) || depth.quoteReserve <= 0) return undefined;
    const quoteUnitUsd = quoteMint ? usdPerQuoteUnit(quoteMint, this.solPriceUsd) : null;
    // Onbekende quote (niet WSOL/USDC/USDT) → geen liquiditeits-schatting (fail-closed).
    if (quoteUnitUsd === null) return undefined;
    const quoteUnits = depth.quoteReserve / 10 ** (depth.quoteDecimals ?? 9);
    // 2× (beide zijden van de curve/pool) × USD-waarde per quote-unit.
    return Number((2 * quoteUnits * quoteUnitUsd).toFixed(2));
  }

  private enqueuePending(snapshot: MarketSnapshot): void {
    if (this.pending.length >= MAX_PENDING_IDENTITIES) this.pending.shift();
    this.pending.push(snapshot);
  }

  private cleanupSeenPools(): void {
    // Bounded dedupe: clear at 200k to bound memory (mirrors knownPools pattern).
    if (this.seenNewPools.size >= 200_000) this.seenNewPools.clear();
  }

  drainDiagnostics(): string[] {
    return this.diagnostics.splice(0);
  }

  async fetchSnapshots(): Promise<MarketSnapshot[]> {
    if (this.discoveryInFlight) return this.discoveryInFlight;
    const drain = this.fetchSnapshotsOnce();
    const tracked = drain.finally(() => {
      if (this.discoveryInFlight === tracked) this.discoveryInFlight = undefined;
    });
    this.discoveryInFlight = tracked;
    return tracked;
  }

  private async fetchSnapshotsOnce(): Promise<MarketSnapshot[]> {
    const identities = this.pending.splice(0, MAX_PENDING_IDENTITIES);
    return identities;
  }

  destroy(): void {
    this.destroyed = true;
    for (const timer of this.reconnectTimers.values()) clearTimeout(timer);
    this.reconnectTimers.clear();
    this.pending.length = 0;
    this.discoveryInFlight = undefined;
  }
}
