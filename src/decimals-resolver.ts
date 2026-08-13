/** Fase-QH: decimals-resolutie met expliciete prioriteit.
 *  1) reeds-gedecodeerde event/state; 2) lokale mint-metadata-cache;
 *  3) bestaande stream/account-state; 4) alleen indien nodig een gebatchte
 *  Triton RPC fallback (getMultipleAccounts). Voert géén decimals-call per scan;
 *  cachet langdurig per mint + in-flight-dedup. */

export type Decimals = { base: number; quote: number };

export type DecimalsRpcFn = (mints: string[]) => Promise<Map<string, number>>;

const DEFAULT_TTL_MS = 24 * 60 * 60_000; // long-lived per mint

export class DecimalsResolver {
  /** known base-decimals per base-mint (uit event/state, geen RPC) */
  private knownBase = new Map<string, number>();
  private knownQuote = new Map<string, number>();
  private cache = new Map<string, { decimals: number; expiresAt: number }>();
  private inFlight = new Map<string, Promise<number | undefined>>();

  constructor(private readonly rpc: DecimalsRpcFn) {}

  /** Persist lokaal bekende decimals (uit event/state), géén RPC. */
  setKnown(baseMint: string, base: number, quoteMint: string, quote: number): void {
    this.knownBase.set(baseMint, base);
    this.knownQuote.set(quoteMint, quote);
  }

  /** Verdeelde decimals uit reeds-bekende event/state (géén RPC). */
  resolveKnown(baseMint: string, base: number, quoteMint: string, quote: number): Decimals {
    this.setKnown(baseMint, base, quoteMint, quote);
    return { base, quote };
  }

  private cacheGet(mint: string): number | undefined {
    const c = this.cache.get(mint);
    if (c && c.expiresAt > Date.now()) return c.decimals;
    if (c) this.cache.delete(mint);
    return undefined;
  }

  /** Resolve decimals; gebatchte fallback (1 RPC voor base+quote) + cache + dedup. */
  async resolveWithRpc(baseMint: string, quoteMint: string): Promise<Decimals | undefined> {
    const kb = this.knownBase.get(baseMint);
    const kq = this.knownQuote.get(quoteMint);
    const cb = kb ?? this.cacheGet(baseMint);
    const cq = kq ?? this.cacheGet(quoteMint);
    if (cb !== undefined && cq !== undefined) return { base: cb, quote: cq };
    const missing = [...(cb === undefined ? [baseMint] : []), ...(cq === undefined ? [quoteMint] : [])];
    const map = await this.batch(missing);
    const base = cb ?? map.get(baseMint);
    const quote = cq ?? map.get(quoteMint);
    if (base === undefined || quote === undefined) return undefined;
    return { base, quote };
  }

  private async batch(mints: string[]): Promise<Map<string, number>> {
    const out = new Map<string, number>();
    const toFetch: string[] = [];
    const inflightRes = new Map<string, Promise<number | undefined>>();
    for (const mint of mints) {
      const cached = this.cacheGet(mint);
      if (cached !== undefined) { out.set(mint, cached); continue; }
      const infl = this.inFlight.get(mint);
      if (infl) { inflightRes.set(mint, infl); continue; }
      toFetch.push(mint);
    }
    if (toFetch.length) {
      const p = this.rpc(toFetch).then((m) => m);
      for (const mint of toFetch) this.inFlight.set(mint, p.then((m) => m.get(mint)));
      const m = await p;
      for (const [mint, dec] of m) {
        if (dec !== undefined) { this.cache.set(mint, { decimals: dec, expiresAt: Date.now() + DEFAULT_TTL_MS }); out.set(mint, dec); }
      }
      for (const mint of toFetch) this.inFlight.delete(mint);
    }
    for (const [mint, pr] of inflightRes) {
      const d = await pr;
      if (d !== undefined) out.set(mint, d);
    }
    return out;
  }
}
