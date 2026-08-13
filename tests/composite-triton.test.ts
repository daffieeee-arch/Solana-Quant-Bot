import { describe, it, expect, vi } from 'vitest';
import { CompositeProvider } from '../src/providers/composite.js';
import { TritonProvider, type TritonClientLike, type TritonStreamLike, type VixenUpdate } from '../src/providers/triton.js';
import type { MarketSnapshot } from '../src/scoring.js';

const PUMPFUN = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const PAIR = '4yUUpM9h9pXZLn54X4jLP4FoV8JsBK4nsSMLGaeyVuCP';
const MINT = 'C8VZE8cy71FKrVKMdi8Ne9q9JNyFD7vJEbGMuaS3pump';
const TOKEN_FAKE = '00000000-0000-4test-0000-000000000000';

// ─── fake Triton client seam ────────────────────────────────────────────────
function makeFakeClient() {
  const subscribers: Array<{ program: string; push: (u: VixenUpdate) => void }> = [];
  const client: TritonClientLike = {
    Subscribe(request: { program: string }) {
      const stream: TritonStreamLike = {
        on(event: string, listener: (v: unknown) => void) {
          if (event === 'data') {
            subscribers.push({ program: request.program, push: (u) => listener(u) });
          }
          return stream;
        },
        cancel() { /* noop */ },
      };
      return stream;
    },
  };
  return { client, pushTo: (program: string, update: VixenUpdate) => {
    subscribers.filter((s) => s.program === program).forEach((s) => s.push(update));
  } };
}

function priced(pairId: string, mint: string): MarketSnapshot {
  return {
    pairId, mint, symbol: 'X', source: 'birdeye_ws',
    observedAt: '2026-08-06T00:00:00.000Z', pairCreatedAt: '2026-08-06T00:00:00.000Z',
    priceUsd: 1.5, liquidityUsd: 100_000, volumeM5Usd: 20_000, priceChangeM5Percent: 8,
    buysM5: 10, sellsM5: 2,
  };
}

// Stub pattern matching composite.test.ts, extended with Triton.
function compositeWith(opts: {
  solanaFetch?: () => Promise<MarketSnapshot[]>;
  triton?: TritonProvider;
  minAgeMinutes?: number;
  maxAgeMinutes?: number;
}) {
  const provider = Object.create(CompositeProvider.prototype) as {
    triton: TritonProvider | undefined;
    solana: { fetchSnapshots(): Promise<MarketSnapshot[]>; whaleActivity: ReadonlyMap<string, number>; drainDiagnostics(): string[] };
    pushDiagnostic(message: string): void;
    diagnostics: string[];
    discoveryInFlight: Promise<MarketSnapshot[]> | undefined;
    agePreFilterMinMs: number;
    agePreFilterMaxMs: number;
    disabledProviders: Set<string>;
    lastProviderLatency: Record<string, number>;
    fetchSnapshotsOnce(): Promise<MarketSnapshot[]>;
    fetchSnapshots(): Promise<MarketSnapshot[]>;
  };
  provider.solana = {
    fetchSnapshots: opts.solanaFetch ?? (async () => []),
    whaleActivity: new Map(),
    drainDiagnostics: () => [],
  };
  provider.triton = opts.triton;
  provider.diagnostics = [];
  provider.discoveryInFlight = undefined;
  provider.disabledProviders = new Set();
  provider.lastProviderLatency = {};
  provider.pushDiagnostic = (message: string) => { provider.diagnostics.push(message); };
  provider.agePreFilterMinMs = (opts.minAgeMinutes ?? 0) * 60_000;
  provider.agePreFilterMaxMs = (opts.maxAgeMinutes ?? Number.MAX_SAFE_INTEGER) * 60_000;
  return provider as unknown as CompositeProvider;
}

describe('CompositeProvider + Triton discovery', () => {
  it('keeps a Triton Vixen discovery identity that is already priced (Triton-first, curve self-calc)', async () => {
    const { client, pushTo } = makeFakeClient();
    const triton = new TritonProvider('endpoint', 'token', () => client);
    const composite = compositeWith({
      triton,
      solanaFetch: async () => [{
        pairId: PAIR, mint: MINT, symbol: 'PUMP', source: 'triton_vixen_pumpfun',
        observedAt: '2026-08-06T00:00:00.000Z', pairCreatedAt: '2026-08-06T00:00:00.000Z',
        priceUsd: 1.5, liquidityUsd: 100_000,
        discovery: {
          signature: 'sig-triton', slot: 42, programId: PUMPFUN,
          instructionLocation: 'inner' as const, instructionIndex: 1, receiptAt: '2026-08-06T00:00:00.000Z',
        },
      }],
    });

    const snapshots = await composite.fetchSnapshots();
    const fromTriton = snapshots.find((s) => s.pairId === PAIR && s.mint === MINT);
    expect(fromTriton).toBeDefined();
    expect(fromTriton?.priceUsd).toBe(1.5);
    // provenance retained from Triton discovery
    expect(fromTriton?.source).toBe('triton_vixen_pumpfun');
    expect(fromTriton?.discovery?.programId).toBe(PUMPFUN);
  });

  it('keeps the discovery mint-derived ticker when no external enrichment exists (Triton-first)', async () => {
    const { client, pushTo } = makeFakeClient();
    const triton = new TritonProvider('endpoint', 'token', () => client);
    const composite = compositeWith({
      triton,
      solanaFetch: async () => [{
        pairId: PAIR, mint: MINT, symbol: 'C8VZE8', source: 'triton_vixen_pumpfun',
        observedAt: '2026-08-06T00:00:00.000Z', pairCreatedAt: '2026-08-06T00:00:00.000Z',
        priceUsd: 1.5, liquidityUsd: 100_000,
      }],
    });
    pushTo(PUMPFUN, { buy: { accounts: { mint: MINT, bondingCurve: PAIR } } });

    const snapshots = await composite.fetchSnapshots();
    const fromTriton = snapshots.find((s) => s.pairId === PAIR);
    expect(fromTriton).toBeDefined();
    // geen Birdeye/Gecko meer: discovery-ticker blijft
    expect(fromTriton?.symbol).toBe('C8VZE8');
  });

  it('leaves an unpriced, unenriched Triton pool out fail-closed', async () => {
    const { client, pushTo } = makeFakeClient();
    const triton = new TritonProvider('endpoint', 'token', () => client);
    const composite = compositeWith({ triton }); // no enrichment

    pushTo(PUMPFUN, { buy: { accounts: { mint: MINT, bondingCurve: PAIR } } });

    const snapshots = await composite.fetchSnapshots();
    expect(snapshots.find((s) => s.pairId === PAIR)).toBeUndefined();
  });

  it('dedupes the same pairId emitted by both Solana WS and Triton Vixen', async () => {
    const { client, pushTo } = makeFakeClient();
    const triton = new TritonProvider('endpoint', 'token', () => client);
    const composite = compositeWith({
      triton,
      solanaFetch: async () => [{
        pairId: PAIR, mint: MINT, symbol: 'PUMP', source: 'solana_ws',
        observedAt: '2026-08-06T00:00:00.000Z', pairCreatedAt: '2026-08-06T00:00:00.000Z', priceUsd: 1.5,
      }],
    });
    pushTo(PUMPFUN, { buy: { accounts: { mint: MINT, bondingCurve: PAIR } } });

    const result = await composite.fetchSnapshots();
    expect(result.filter((s) => s.pairId === PAIR).length).toBe(1);
  });

  it('keeps working when Triton is disabled (uses Solana WS only)', async () => {
    const composite = compositeWith({
      solanaFetch: async () => [priced('solana-1', 'solana-mint')],
    });
    const result = await composite.fetchSnapshots();
    expect(result.some((s) => s.pairId === 'solana-1')).toBe(true);
  });

  it('drops an old unpriced pool fail-closed without external enrichment (Triton-first)', async () => {
    const composite = compositeWith({
      minAgeMinutes: 3,
      maxAgeMinutes: 180,
      solanaFetch: async () => [{
        pairId: 'old-pool', mint: 'old-mint', symbol: 'OLD', source: 'solana_ws',
        observedAt: new Date().toISOString(),
        pairCreatedAt: new Date(Date.now() - 200 * 60_000).toISOString(), // 200 min oud
        priceUsd: 0,
      }],
    });
    const result = await composite.fetchSnapshots();
    // geen externe quotes (Triton-first): oude onprijsde pool → fail-closed
    expect(result.some((s) => s.pairId === 'old-pool')).toBe(false);
  });

  it('Fase C: position-mark quotes worden binnen 30s gecached (geen dubbele RPC per scan)', async () => {
    // Directe TritonProvider waarin reserveReader gemockt is: 2× fetchPositionPriceUsd
    // binnen de 30s-cache moeten MAAR 1 RPC-diepte-ophaling triggern (de 2e leest cache).
    let depthCalls = 0;
    const fakeDepth = { baseReserve: 1e6, quoteReserve: 3e9, baseDecimals: 6, quoteDecimals: 9 };
    const fakeReserveReader = {
      fetchPumpDepthByMint: async () => { depthCalls += 1; return fakeDepth; },
      fetchPumpDepth: async () => fakeDepth,
      resolveSymbol: async () => undefined,
      assessRugSafety: async () => undefined,
      drainDiagnostics: () => [],
    };
    const provider = new TritonProvider(
      'johnb-mainnet-2781.mainnet.rpcpool.com',
      TOKEN_FAKE,
      // clientFactory: functie die de gRPC-client seam retourneert (geen echte gRPC)
      (() => ({ Subscribe: () => ({ on: () => ({ on: () => undefined, cancel: () => undefined }), cancel: () => undefined }) })) as never,
      // clock (4e) — default
      undefined,
      // reserveReader (5e) — mock
      fakeReserveReader as never,
      // options (6e)
      { solPriceUsd: 74 } as never,
    );
    const p1 = await provider.fetchPositionPriceUsd(MINT);
    const p2 = await provider.fetchPositionPriceUsd(MINT);
    expect(p1).toBeGreaterThan(0);
    expect(p2).toBe(p1);
    // 2 oproepen, 1 echte depth-fetch (2e = cache-hit)
    expect(depthCalls).toBe(1);
  });

  it('Fase-O: position-mark gebruikt verse lokale stream-prijs → 0 extra RPC', async () => {
    // Als emitDiscovery een verse prijs voor de mint heeft geregistreerd (gratis
    // stream balance-pricing), moet fetchPositionPriceUsd die lokale prijs gebruiken
    // ZONDER nieuwe getAccountInfo/haaste calls.
    let depthCalls = 0;
    const fakeDepth = { baseReserve: 1e6, quoteReserve: 3e9, baseDecimals: 6, quoteDecimals: 9 };
    const fakeReserveReader = {
      fetchPumpDepthByMint: async () => { depthCalls += 1; return fakeDepth; },
      fetchPumpDepth: async () => { depthCalls += 1; return fakeDepth; },
      resolveSymbol: async () => undefined,
      assessRugSafety: async () => undefined,
      drainDiagnostics: () => [],
    };
    const provider = new TritonProvider(
      'johnb-mainnet-2781.mainnet.rpcpool.com', TOKEN_FAKE,
      (() => ({ Subscribe: () => ({ on: () => ({ on: () => undefined, cancel: () => undefined }), cancel: () => undefined }) })) as never,
      undefined, fakeReserveReader as never, { solPriceUsd: 74 } as never,
    );
    // registreer een verse lokale prijs voor de mint (alsof emitDiscovery net draaide)
    (provider as unknown as { lastPriceByMint: Map<string, { priceUsd: number; at: number }> }).lastPriceByMint.set(MINT, { priceUsd: 222, at: Date.now() });
    const p = await provider.fetchPositionPriceUsd(MINT);
    expect(p).toBe(222);
    // géén RPC (locale verse prijs), zelfs als de curve onbekend is
    expect(depthCalls).toBe(0);
  });
});
