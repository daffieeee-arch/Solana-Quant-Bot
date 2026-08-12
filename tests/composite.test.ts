import { describe, expect, it, vi } from 'vitest';
import { CompositeProvider } from '../src/providers/composite.js';
import type { MarketSnapshot } from '../src/scoring.js';

const snapshot: MarketSnapshot = {
  pairId: 'pair-1',
  mint: 'mint-1',
  symbol: 'PUMP',
  source: 'test',
  observedAt: '2026-07-29T10:00:00.000Z',
  pairCreatedAt: '2026-07-29T09:50:00.000Z',
  priceUsd: 1,
  liquidityUsd: 50_000,
  volumeM5Usd: 10_000,
  priceChangeM5Percent: 10,
  buysM5: 20,
  sellsM5: 10,
};

type MutableComposite = {
  solana: {
    fetchSnapshots(): Promise<MarketSnapshot[]>;
    whaleActivity: ReadonlyMap<string, number>;
    drainDiagnostics(): string[];
  };
};

function providerWith(
  solanaFetch: () => Promise<MarketSnapshot[]>,
  agePreFilterMinMs = 180_000,
  titanFor?: { fetchQuote(): Promise<{ price: number } | undefined>; quoteToUsd(): number },
  solPriceUsd?: number,
): CompositeProvider {
  const provider = Object.create(CompositeProvider.prototype) as MutableComposite;
  provider.solana = { fetchSnapshots: solanaFetch, whaleActivity: new Map(), drainDiagnostics: () => [] };
  (provider as unknown as { disabledProviders: Set<string> }).disabledProviders = new Set();
  (provider as unknown as { lastProviderLatency: Record<string, number> }).lastProviderLatency = {};
  (provider as unknown as { agePreFilterMinMs: number }).agePreFilterMinMs = agePreFilterMinMs;
  (provider as unknown as { agePreFilterMaxMs: number }).agePreFilterMaxMs = Number.MAX_SAFE_INTEGER;
  (provider as unknown as { triton: undefined }).triton = undefined;
  (provider as unknown as { titan: unknown }).titan = titanFor;
  (provider as unknown as { solPriceUsd: number | undefined }).solPriceUsd = solPriceUsd;
  (provider as unknown as { titanQuoteCache: Map<string, { usd: number | undefined; expiresAt: number }> }).titanQuoteCache = new Map();
  (provider as unknown as { lastMergedSnapshots: MarketSnapshot[] | undefined }).lastMergedSnapshots = undefined;
  return provider as unknown as CompositeProvider;
}

describe('CompositeProvider', () => {
  it('returns the same outer promise while one composite discovery epoch is in flight', async () => {
    let resolveSolana!: (snapshots: MarketSnapshot[]) => void;
    const pending = new Promise<MarketSnapshot[]>((resolve) => { resolveSolana = resolve; });
    const solanaFetch = vi.fn(() => pending);
    const provider = providerWith(solanaFetch);

    const first = provider.fetchSnapshots();
    const second = provider.fetchSnapshots();

    expect(second).toBe(first);
    expect(solanaFetch).toHaveBeenCalledTimes(1);

    resolveSolana([snapshot]);
    await expect(first).resolves.toEqual([snapshot]);
    const third = provider.fetchSnapshots();
    expect(third).not.toBe(first);
    expect(solanaFetch).toHaveBeenCalledTimes(2);
    await expect(third).resolves.toEqual([snapshot]);
  });

  it('does not call the Solana WS lane when discovery is already priced (Triton-first)', async () => {
    const provider = providerWith(async () => [snapshot]);
    await expect(provider.fetchSnapshots()).resolves.toEqual([snapshot]);
    expect(provider.getProviderEnabled()).toMatchObject({ TRITON: true });
  });

  it('provider toggles via dashboard-controls affect only active providers (TRITON/SOLANA_WS als solana-lane aan)', async () => {
    const provider = providerWith(async () => [{ ...snapshot, priceUsd: 0 }]);
    provider.setProviderEnabled('SOLANA_WS', false);
    await provider.fetchSnapshots();
    expect(provider.getProviderEnabled()).toMatchObject({ TRITON: true });
  });

  it('lets a too-young discovery-only pool pass through (rijping) instead of dropping it', async () => {
    const jong = {
      pairId: 'pair-jong', mint: 'mint-jong', symbol: 'JONGXY', source: 'triton_vixen_pumpfun',
      observedAt: new Date().toISOString(),
      pairCreatedAt: new Date(Date.now() - 5_000).toISOString(), // 5s oud, ongeprijsd
      priceUsd: 0,
    };
    const provider = providerWith(async () => [jong], 180_000);
    const snapshots = await provider.fetchSnapshots();
    const found = snapshots.find((s) => s.pairId === 'pair-jong');
    expect(found).toBeDefined();
    expect(found?.priceUsd).toBe(0); // discovery-only, rijpings-doorgifte
  });

  it('prices a too-young unpriced pool via Titan route-quote (Triton-first, Pump.fun via Metis)', async () => {
    const jong = {
      pairId: 'pair-titan', mint: 'mint-titan', symbol: 'TITANXY', source: 'triton_vixen_pumpfun',
      observedAt: new Date().toISOString(),
      pairCreatedAt: new Date(Date.now() - 5_000).toISOString(), // 5s oud, ongeprijsd
      priceUsd: 0,
    };
    const titan = {
      fetchQuote: async () => ({ price: 0.014 }),
      quoteToUsd: () => 1.03, // 0.014 SOL/token * ~74 USD/SOL ≈ 1.03 USD
    };
    const provider = providerWith(async () => [jong], 180_000, titan as never, 74);
    const snapshots = await provider.fetchSnapshots();
    const found = snapshots.find((s) => s.pairId === 'pair-titan');
    expect(found).toBeDefined();
    expect(found?.priceUsd).toBeGreaterThan(0); // geprijsd via Titan i.p.v. ongprijsd
  });

  it('fails closed to discovery-only ripening when Titan has no quote', async () => {
    const jong = {
      pairId: 'pair-notitan', mint: 'mint-notitan', symbol: 'NOQXYZ', source: 'triton_vixen_pumpfun',
      observedAt: new Date().toISOString(),
      pairCreatedAt: new Date(Date.now() - 5_000).toISOString(),
      priceUsd: 0,
    };
    const titan = { fetchQuote: async () => undefined, quoteToUsd: () => 0 };
    const provider = providerWith(async () => [jong], 180_000, titan as never, 74);
    const snapshots = await provider.fetchSnapshots();
    const found = snapshots.find((s) => s.pairId === 'pair-notitan');
    expect(found).toBeDefined();
    expect(found?.priceUsd).toBe(0); // discovery-only ripening
  });

  it('fails closed to discovery-only ripening when Solana WebSocket lane rejects', async () => {
    const provider = providerWith(async () => { throw new Error('RPC unavailable'); });

    await expect(provider.fetchSnapshots()).resolves.toEqual([]);
    expect(provider.drainDiagnostics()).toEqual(['discovery: solana: RPC unavailable']);
  });

  it('keeps a healthy priced pool and drops an unpriced stale pool (Triton-first, no external enrichment)', async () => {
    const unpriced = { ...snapshot, pairId: 'pair-unpriced', mint: 'mint-unpriced', priceUsd: 0 };
    const healthy = { ...snapshot, pairId: 'pair-healthy', mint: 'mint-healthy' };
    const provider = providerWith(async () => [unpriced, healthy]);

    await expect(provider.fetchSnapshots()).resolves.toEqual([healthy]);
  });

  it('keeps our own curve-computed liquidity for a priced discovery pool (Triton-first)', async () => {
    // discovery leverde een geprijsde pool met eigen curve-liquiditeit (~12k);
    // zonder Birdeye/Gecko-enrichment blijft de eigen liquiditeit behouden.
    const priced = {
      ...snapshot,
      pairId: 'pair-curveliq', mint: 'mint-curveliq', source: 'triton_vixen_pumpfun',
      priceUsd: 0.014, liquidityUsd: 12_580,
    };
    const provider = providerWith(async () => [priced]);
    const snapshots = await provider.fetchSnapshots();
    const found = snapshots.find((s) => s.pairId === 'pair-curveliq');
    expect(found?.liquidityUsd).toBe(12_580); // onze curve-liquiditeit blijft
  });

  it('passes a priced discovery pool through without enrichment (Triton-first, Birdeye/Gecko off)', async () => {
    // Een geprijsde discovery-pool (zelf-calc uit de bonding curve) moet NIET
    // worden gedropt maar doorgestuurd, zodat de scanner hem kan evalueren.
    const priced = {
      ...snapshot,
      pairId: 'pair-priced-only', mint: 'mint-priced-only', source: 'triton_vixen_pumpfun',
      priceUsd: 0.021, liquidityUsd: 15_000, buysM5: 60, sellsM5: 10,
    };
    const provider = providerWith(async () => [priced]);
    const snapshots = await provider.fetchSnapshots();
    const found = snapshots.find((s) => s.pairId === 'pair-priced-only');
    expect(found).toBeDefined();
    expect(found?.priceUsd).toBe(0.021);
    expect(found?.liquidityUsd).toBe(15_000);
  });

  it('drops an unpriced stale pool fail-closed without external enrichment (Triton-first)', async () => {
    const discovered = { ...snapshot, pairId: 'exact-new-pair', mint: 'shared-mint', priceUsd: 0 };
    const provider = providerWith(async () => [discovered]);

    await expect(provider.fetchSnapshots()).resolves.toEqual([]);
  });

  it('keeps on-chain provenance for a priced discovery pool (Triton-first)', async () => {
    const discovery = {
      signature: 'exact-signature', slot: 123, programId: 'exact-program',
      instructionLocation: 'top_level' as const, instructionIndex: 0, receiptAt: '2026-07-29T09:59:59.000Z',
    };
    const discovered = { ...snapshot, pairId: 'exact-new-pair', mint: 'shared-mint', priceUsd: 1.5, discovery };
    const provider = providerWith(async () => [discovered]);

    await expect(provider.fetchSnapshots()).resolves.toEqual([
      expect.objectContaining({ pairId: 'exact-new-pair', mint: 'shared-mint', priceUsd: 1.5, discovery }),
    ]);
  });

  it('drops a stale unpriced pool fail-closed without external quote providers (Triton-first)', async () => {
    const discovered = { ...snapshot, pairId: 'exact-new-pair', mint: 'shared-mint', priceUsd: 0 };
    const provider = providerWith(async () => [discovered]);

    await expect(provider.fetchSnapshots()).resolves.toEqual([]);
  });

  it('returns no external position quotes (Triton-first; Birdeye/Gecko verwijderd)', async () => {
    const first = { pairId: 'pair-1', mint: 'mint-1' };
    const second = { pairId: 'pair-2', mint: 'mint-2' };
    const provider = providerWith(async () => []) as unknown as CompositeProvider;

    await expect(provider.fetchSnapshotsForPositions([first, second])).resolves.toEqual([]);
  });

  it('hard-caps locally retained composite diagnostics at 500 entries without a consumer drain', async () => {
    let call = 0;
    const provider = providerWith(async () => { throw new Error(`failure-${call++}`); });

    for (let index = 0; index < 600; index += 1) await provider.fetchSnapshots();

    const diagnostics = provider.drainDiagnostics();
    expect(diagnostics).toHaveLength(500);
    expect(diagnostics[0]).toBe('discovery: solana: failure-100');
    expect(diagnostics[499]).toBe('discovery: solana: failure-599');
  });
});