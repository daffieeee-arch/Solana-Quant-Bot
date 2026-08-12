import type { MarketSnapshot, PoolDepth } from '../scoring.js';
import { defaultHttpFetcher, fetchWithTimeout, type HttpFetcher } from './http.js';
import { decodePumpCurve, pumpCurveToDepth } from '../pump-curve.js';

const MAX_CACHE_ENTRIES = 5_000;
const MAX_DIAGNOSTICS = 500;

type TokenAccountBalance = {
  context?: { slot?: number };
  value?: { amount?: string; decimals?: number; uiAmount?: number };
};

/**
 * Reads live on-chain pool reserves via Triton JSON-RPC `getTokenAccountBalance`
 * for the vault accounts that Vixen stream updates reference. Used to build a
 * PoolDepth for realistic price-impact fills. Bounded, cached, fail-closed.
 */
export class TritonReserveReader {
  private readonly fetcher: HttpFetcher;
  private readonly rpcUrl: string;
  private readonly diagnostics: string[] = [];
  private readonly cache = new Map<string, { expiresAt: number; poolDepth: PoolDepth }>();
  private readonly symbolCache = new Map<string, { symbol: string; expiresAt: number }>();
  private readonly rugCache = new Map<string, { risk: import('../rug-risk.js').RugEvidence; expiresAt: number }>();
  private readonly inFlight = new Map<string, Promise<PoolDepth | undefined>>();

  /** Fetch holder concentration of top N token accounts (0..1 of raw supply). */
  private async holderConcentration(mint: string): Promise<number | undefined> {
    try {
      const result = await this.rpc('getTokenLargestAccounts', [mint]) as { value?: Array<{ amount?: string }> };
      const amounts = (result?.value ?? []).map((v) => BigInt(v.amount ?? '0'));
      const totalSupply = amounts.reduce((a, b) => a + b, 0n);
      if (totalSupply === 0n) return undefined;
      // concentratie van top-3 (de 3 grootste) — eerste top-holder-dump-gevoeligheid
      const top3 = amounts.slice(0, 3).reduce((a, b) => a + b, 0n);
      return Number(Number(top3) / Number(totalSupply));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.pushDiagnostic(`rug holderConcentration: mint=${mint.slice(0, 8)}; ${message.slice(0, 100)}`);
      return undefined;
    }
  }

  constructor(
    endpoint: string,
    token: string,
    fetcher: HttpFetcher = defaultHttpFetcher,
    private readonly timeoutMs = 8_000,
    private readonly cacheMs = 3 * 60_000,
    private readonly clock: () => number = () => Date.now(),
  ) {
    if (!endpoint || !token) throw new Error('TritonReserveReader requires endpoint and token');
    this.rpcUrl = `https://${endpoint.replace(/^https?:\/\//, '')}/${token}`;
    const base = fetcher;
    this.fetcher = (url, init) => fetchWithTimeout(base, url, init, timeoutMs);
  }

  private pushDiagnostic(message: string): void {
    if (this.diagnostics.length >= MAX_DIAGNOSTICS) this.diagnostics.shift();
    this.diagnostics.push(message);
  }

  drainDiagnostics(): string[] {
    return this.diagnostics.splice(0);
  }

  private async rpc(method: string, params: unknown[] | Record<string, unknown>): Promise<unknown> {
    this.rpcCallCounts.set(method, (this.rpcCallCounts.get(method) ?? 0) + 1);
    const response = await this.fetcher(this.rpcUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: Date.now(), method, params }),
    });
    if (!response.ok) {
      throw new Error(`Triton RPC HTTP ${response.status}`);
    }
    const data = await response.json() as { result?: unknown; error?: { message?: string; code?: number } };
    if (data.error) throw new Error(`Triton RPC error ${data.error.code ?? ''}: ${data.error.message ?? ''}`);
    return data.result;
  }

  /** Cumulative Triton JSON-RPC calls per method (verbruiksregistratie). */
  private readonly rpcCallCounts = new Map<string, number>();

  /** Cumulatieve Triton RPC-call-count per methode (voor /api/debug verbruiksmonitoring). */
  rpcCallCountsSnapshot(): Record<string, number> {
    return Object.fromEntries(this.rpcCallCounts);
  }

  /**
   * Resolve the real coin ticker (e.g. 'USDC', 'POPCAT', 'SOL') for a mint via
   * Triton's Digital Assets API `getAsset` (Metaplex DAS) — fully within the paid
   * Triton subscription. Cached per mint for 24h. Returns undefined on failure so
   * callers can fall back to GeckoTerminal, then to a mint-derived label.
   */
  async resolveSymbol(mint: string): Promise<string | undefined> {
    const now = this.clock();
    const cached = this.symbolCache.get(mint);
    if (cached && cached.expiresAt > now) return cached.symbol;
    if (cached) this.symbolCache.delete(mint);
    try {
      const result = await this.rpc('getAsset', { id: mint }) as {
        content?: { metadata?: { symbol?: string } };
        token_info?: { symbol?: string; decimals?: number };
      };
      const symbol = result?.content?.metadata?.symbol ?? result?.token_info?.symbol;
      if (symbol && symbol.length > 0 && symbol.length <= 12) {
        this.symbolCache.set(mint, { symbol, expiresAt: this.clock() + 24 * 60 * 60_000 });
        while (this.symbolCache.size > MAX_CACHE_ENTRIES) {
          const oldest = this.symbolCache.keys().next().value as string | undefined;
          if (oldest === undefined) break;
          this.symbolCache.delete(oldest);
        }
        return symbol;
      }
      return undefined;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.pushDiagnostic(`getAsset symbol: mint=${mint.slice(0, 8)}; ${message.slice(0, 100)}`);
      return undefined;
    }
  }

  /**
   * Assess rug/honeypot evidence for a mint using Triton on-chain data (DAS
   * getAsset + getTokenLargestAccounts). Cached per mint (TTL). Returns evidence
   * consumed by assessRugRisk(); undefined means data unavailable (→ high risk).
   */
  async assessRugSafety(mint: string): Promise<import('../rug-risk.js').RugEvidence | undefined> {
    const now = this.clock();
    const cached = this.rugCache.get(mint);
    if (cached && cached.expiresAt > now) return cached.risk;
    if (cached) this.rugCache.delete(mint);
    try {
      const asset = await this.rpc('getAsset', { id: mint }) as {
        burnt?: boolean; mutable?: boolean; authorities?: Array<{ scopes?: string[] }>;
      };
      const fullAuthorityCount = (asset?.authorities ?? []).filter((a) => a?.scopes?.includes('full')).length;
      const evidence = {
        burnt: asset?.burnt ?? false,
        mutable: asset?.mutable ?? true,
        fullAuthorityCount,
        holderConcentration: await this.holderConcentration(mint),
      };
      this.rugCache.set(mint, { risk: evidence, expiresAt: this.clock() + 7 * 24 * 60 * 60_000 });
      return evidence;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.pushDiagnostic(`rug assessRugSafety: mint=${mint.slice(0, 8)}; ${message.slice(0, 100)}`);
      return undefined;
    }
  }

  /** Resolve the pool's base (traded token) and quote (SOL/stable) reserve balances
   * from explicit vault token-account addresses. `quoteVault` + `baseVault` must be
   * provided by the provider (Vixen swap/state accounts). Returns a PoolDepth, or
   * undefined if either balance can't be read (fail-closed → flat-slippage fallback).
   */
  async fetchDepth(
    key: string,
    quoteVault: string,
    baseVault: string,
    quoteDecimals?: number,
    baseDecimals?: number,
    opts: { feeNumerator?: number; feeDenominator?: number; bondingCurve?: boolean } = {},
  ): Promise<PoolDepth | undefined> {
    const now = this.clock();
    const cached = this.cache.get(key);
    if (cached && cached.expiresAt > now) return cached.poolDepth;

    const inFlight = this.inFlight.get(key);
    if (inFlight) return inFlight;

    const p = this.loadDepth(key, quoteVault, baseVault, quoteDecimals, baseDecimals, opts);
    this.inFlight.set(key, p);
    try {
      const depth = await p;
      return depth;
    } finally {
      this.inFlight.delete(key);
    }
  }

  private async loadDepth(
    key: string,
    quoteVault: string,
    baseVault: string,
    quoteDecimals?: number,
    baseDecimals?: number,
    opts: { feeNumerator?: number; feeDenominator?: number; bondingCurve?: boolean } = {},
  ): Promise<PoolDepth | undefined> {
    try {
      const [quoteRes, baseRes] = await Promise.all([
        this.rpc('getTokenAccountBalance', [quoteVault]),
        this.rpc('getTokenAccountBalance', [baseVault]),
      ]) as [TokenAccountBalance, TokenAccountBalance];

      const quoteRaw = Number(quoteRes.value?.amount);
      const baseRaw = Number(baseRes.value?.amount);
      if (!Number.isFinite(quoteRaw) || quoteRaw <= 0 || !Number.isFinite(baseRaw) || baseRaw <= 0) {
        this.pushDiagnostic(`pool_depth: non-positive balance key=${key}`);
        return undefined;
      }
      // Decimals: autoritatief uit de RPC-response (token-account heeft altijd
      // `decimals`) — de caller-params waren hardcoded 6/9 en voor SOL-quoted
      // pools OMGEKEERD (tot 10^6× prijsfout). Caller-waarden zijn slechts
      // fallback als de response ze niet meelevert.
      const baseDec = Number(baseRes.value?.decimals);
      const quoteDec = Number(quoteRes.value?.decimals);
      const depth: PoolDepth = {
        baseReserve: baseRaw,
        quoteReserve: quoteRaw,
        baseDecimals: Number.isFinite(baseDec) && Number.isInteger(baseDec) ? baseDec : (Number.isInteger(baseDecimals) ? baseDecimals as number : 0),
        quoteDecimals: Number.isFinite(quoteDec) && Number.isInteger(quoteDec) ? quoteDec : (Number.isInteger(quoteDecimals) ? quoteDecimals as number : 0),
        ...(opts.feeNumerator !== undefined ? { feeNumerator: opts.feeNumerator } : {}),
        ...(opts.feeDenominator !== undefined ? { feeDenominator: opts.feeDenominator } : {}),
        ...(opts.bondingCurve ? { bondingCurve: true } : {}),
      };
      this.cache.set(key, { expiresAt: this.clock() + this.cacheMs, poolDepth: depth });
      this.pruneCache();
      return depth;
    } catch (error) {
      this.pushDiagnostic(`pool_depth: ${error instanceof Error ? error.message : String(error)} key=${key}`);
      return undefined;
    }
  }

  /**
   * Read a Pump.fun bonding-curve account via Triton RPC `getAccountInfo` (base64)
   * and decode its virtual reserves into a PoolDepth for in-stream self-calc price.
   * Fail-closed (undefined) when the account can't be read or decoded.
   */
  async fetchPumpDepth(bondingCurve: string, solPriceUsd: number): Promise<PoolDepth | undefined> {
    const now = this.clock();
    const cached = this.cache.get(`pump:${bondingCurve}`);
    if (cached && cached.expiresAt > now) return cached.poolDepth;
    try {
      const acct = await this.rpc('getAccountInfo', [
        bondingCurve,
        { encoding: 'base64' },
      ]) as { value?: { data?: [string, string] } };
      const data = acct?.value?.data?.[0];
      if (!data) {
        this.pushDiagnostic(`pump_depth: no account data curve=${bondingCurve.slice(0, 8)}`);
        return undefined;
      }
      const curve = decodePumpCurve(data);
      const depth = curve ? pumpCurveToDepth(curve) : undefined;
      // Sanity-check (zelfde als fetchPumpDepthByMint): een ECHTE verse pump-curve
      // heeft virtuele SOL-reserves van ~10-85 SOL. Een gedeelde/liquidated curve-
      // account decodeert met absurde reserves (miljarden SOL) → liquiditeit >1M
      // USD is onmogelijk voor een verse curve → fail-closed i.p.v. absurde
      // liquiditeit door te geven (die alle gates op liquidity_above_maximum zet).
      if (depth && Number.isFinite(depth.quoteReserve) && depth.quoteReserve > 0 && Number.isFinite(solPriceUsd) && solPriceUsd > 0) {
        const solReserves = depth.quoteReserve / 10 ** (depth.quoteDecimals ?? 9);
        const liqUsd = 2 * solReserves * solPriceUsd;
        if (liqUsd > 1_000_000) {
          this.pushDiagnostic(`pump_depth: implausible curve liq=${Math.round(liqUsd)} curve=${bondingCurve.slice(0, 8)}`);
          return undefined;
        }
      }
      if (depth) {
        this.cache.set(`pump:${bondingCurve}`, { expiresAt: this.clock() + this.cacheMs, poolDepth: depth });
        this.pruneCache();
      }
      return depth;
    } catch (error) {
      this.pushDiagnostic(`pump_depth: ${error instanceof Error ? error.message : String(error)} curve=${bondingCurve.slice(0, 8)}`);
      return undefined;
    }
  }

  /**
   * Pump.fun curve-identiteit afleiden uit alleen de MINT (voor de generic
   * multi-DEX lane, waar de txn geen pump-CPI-curve meelevert):
   *   getTokenLargestAccounts(mint) → grootste token-account → owner = de
   *   bonding curve (de curve houdt de reserve-tokens) → fetchPumpDepth(owner).
   * Fail-closed: undefined bij enige fout. Gecached per mint (cacheMs).
   */
  async fetchPumpDepthByMint(mint: string, solPriceUsd: number): Promise<PoolDepth | undefined> {
    const now = this.clock();
    const cached = this.cache.get(`pumpmint:${mint}`);
    if (cached && cached.expiresAt > now) return cached.poolDepth;
    try {
      const largest = await this.rpc('getTokenLargestAccounts', [mint]) as {
        value?: Array<{ address?: string }>;
      };
      const top = largest?.value?.[0]?.address;
      if (!top) {
        this.pushDiagnostic(`pumpmint: no largest account mint=${mint.slice(0, 8)}`);
        return undefined;
      }
      const acct = await this.rpc('getAccountInfo', [
        top,
        { encoding: 'jsonParsed' },
      ]) as { value?: { data?: { parsed?: { info?: { owner?: string } } } } };
      const curve = acct?.value?.data?.parsed?.info?.owner;
      if (!curve) {
        this.pushDiagnostic(`pumpmint: no owner mint=${mint.slice(0, 8)}`);
        return undefined;
      }
      const depth = await this.fetchPumpDepth(curve, solPriceUsd);
      // Sanity-check: een ECHTE verse pump-curve heeft virtuele SOL-reserves van
      // ~10-85 SOL (~1-6k USD liquide per zijde; curve-liquiditeit ≤ ~1M USD).
      // Een grootste-holder-vault ná AMM-migratie decodeert als onzinfactoren
      // (miljarden SOL-reserves) → fail-closed i.p.v. absurde liquiditeit door te geven.
      if (depth && Number.isFinite(depth.quoteReserve) && depth.quoteReserve > 0) {
        const solReserves = depth.quoteReserve / 10 ** (depth.quoteDecimals ?? 9);
        const liqUsd = 2 * solReserves * solPriceUsd;
        if (liqUsd > 1_000_000) {
          this.pushDiagnostic(`pumpmint: implausible curve liq=${Math.round(liqUsd)} mint=${mint.slice(0, 8)}`);
          return undefined;
        }
      }
      if (depth) {
        this.cache.set(`pumpmint:${mint}`, { expiresAt: this.clock() + this.cacheMs, poolDepth: depth });
        this.pruneCache();
      }
      return depth;
    } catch (error) {
      this.pushDiagnostic(`pumpmint: ${error instanceof Error ? error.message : String(error)} mint=${mint.slice(0, 8)}`);
      return undefined;
    }
  }

  private pruneCache(): void {
    if (this.cache.size <= MAX_CACHE_ENTRIES) return;
    const now = this.clock();
    const expired: string[] = [];
    this.cache.forEach((value, key) => {
      if (value.expiresAt <= now) expired.push(key);
    });
    for (const k of expired) this.cache.delete(k);
    // If still over (all fresh), drop entries until under cap (rare).
    for (const key of Array.from(this.cache.keys())) {
      if (this.cache.size <= MAX_CACHE_ENTRIES) break;
      this.cache.delete(key);
    }
  }
}
