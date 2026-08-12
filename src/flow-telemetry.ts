/**
 * Real-time per-mint flow telemetry (Laag B).
 *
 * Counts buy/sell events per mint as they stream in from Triton Vixen, kept in a
 * rolling window. This lets the scanner attach fresh buy/sell flow to a *young*
 * pool that has no external 5-minute panel data yet (the root cause of 0 trades).
 *
 * Bounded, pruned, allocation-light. Window is configurable (default 3 minutes).
 */

export class FlowTelemetry {
  private readonly buys = new Map<string, number[]>();
  private readonly sells = new Map<string, number[]>();
  private readonly windowMs: number;
  private readonly clock: () => number;
  private readonly MAX_MINTS = 2_000;

  constructor(windowMs = 3 * 60_000, clock: () => number = () => Date.now()) {
    this.windowMs = windowMs;
    this.clock = clock;
  }

  recordBuy(mint: string, atMs = this.clock()): void {
    this.record(this.buys, mint, atMs);
  }
  recordSell(mint: string, atMs = this.clock()): void {
    this.record(this.sells, mint, atMs);
  }

  /** (buys, sells) counts within the last windowMs for a mint (0,0 if unseen). */
  snapshot(mint: string, atMs = this.clock()): { buys: number; sells: number } {
    return {
      buys: this.count(this.buys, mint, atMs),
      sells: this.count(this.sells, mint, atMs),
    };
  }

  private record(map: Map<string, number[]>, mint: string, atMs: number): void {
    let arr = map.get(mint);
    if (!arr) {
      if (map.size >= this.MAX_MINTS) this.pruneMap(map, atMs);
      arr = [];
      map.set(mint, arr);
    }
    arr.push(atMs);
  }

  private count(map: Map<string, number[]>, mint: string, atMs: number): number {
    const arr = map.get(mint);
    if (!arr) return 0;
    const cutoff = atMs - this.windowMs;
    // remove stale entries in place
    while (arr.length && arr[0]! < cutoff) arr.shift();
    return arr.length;
  }

  private pruneMap(map: Map<string, number[]>, atMs: number): void {
    const cutoff = atMs - this.windowMs;
    for (const [mint, arr] of map) {
      while (arr.length && arr[0]! < cutoff) arr.shift();
      if (arr.length === 0) map.delete(mint);
    }
  }
}
