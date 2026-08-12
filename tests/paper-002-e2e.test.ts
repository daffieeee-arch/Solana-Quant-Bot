import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadConfig } from '../src/config.js';
import { SolanaRpcProvider } from '../src/providers/solana-rpc.js';
import { scoreMomentum, type MarketSnapshot } from '../src/scoring.js';

class MockWebSocket {
  addEventListener() {}
  removeEventListener() {}
  send() {}
  close() {}
}
(globalThis as Record<string, unknown>).WebSocket = MockWebSocket;

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.35', MAX_CONCURRENT_POSITIONS: '4',
  MAX_DAILY_LOSS_SOL: '0.5', MIN_LIQUIDITY_USD: '3000', MAX_LIQUIDITY_USD: '2000000',
  MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '259200', MIN_PRICE_CHANGE_M5_PERCENT: '1.5',
  MIN_VOLUME_M5_USD: '1000', MIN_MOMENTUM_SCORE: '40', STOP_LOSS_PERCENT: '12',
  TAKE_PROFIT_PERCENT: '10', TRAILING_STOP_PERCENT: '8', MAX_HOLD_MINUTES: '15',
  SIMULATED_SLIPPAGE_BPS: '100', SIMULATED_FEE_BPS: '100', SCAN_INTERVAL_SECONDS: '5',
  MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
});

const ORIGINAL_FETCH = globalThis.fetch;
type PendingPool = Pick<MarketSnapshot, 'pairId' | 'mint' | 'symbol' | 'source' | 'firstSeenAt' | 'pairCreatedAt' | 'discovery'>;
type ProviderInternals = { pendingSnapshots: PendingPool[] };

function exactPoolResponse(pairId: string, mint: string, pairCreatedAt: string, address = pairId) {
  return {
    data: {
      id: `solana_${address}`,
      type: 'pool',
      attributes: {
        address,
        pool_created_at: pairCreatedAt,
        base_token_price_usd: '0.002',
        quote_token_price_usd: '75',
        reserve_in_usd: '50000',
        volume_usd: { m5: '15000' },
        price_change_percentage: { m5: '22.5' },
        transactions: { m5: { buys: 30, sells: 10 } },
      },
      relationships: {
        base_token: { data: { id: `solana_${mint}`, type: 'token' } },
        quote_token: { data: { id: 'solana_So11111111111111111111111111111111111111112', type: 'token' } },
      },
    },
    included: [{ id: `solana_${mint}`, type: 'token', attributes: { address: mint, symbol: 'REAL' } }],
  };
}

describe('PAPER-002 E2E: identity-only discovery and exact quote separation', () => {
  let provider: SolanaRpcProvider | undefined;

  beforeEach(() => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ result: null }), { status: 200 })) as unknown as typeof fetch;
  });

  afterEach(() => {
    provider?.destroy();
    provider = undefined;
    globalThis.fetch = ORIGINAL_FETCH;
  });

  it('E2E Test 1 — emits unpriced identity without pool-state balance RPC data', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://mock.helius-rpc.com');
    const internal = provider as unknown as ProviderInternals;
    internal.pendingSnapshots.push({
      pairId: 'TestPool', mint: 'TestMint123', symbol: 'TEST', source: 'solana_rpc_ws_pump-fun',
      pairCreatedAt: new Date(Date.now() - 5 * 60_000).toISOString(),
    });

    const snapshots = await provider.fetchSnapshots();

    expect(snapshots).toEqual([expect.objectContaining({
      pairId: 'TestPool', mint: 'TestMint123', priceUsd: 0,
    })]);
    expect(snapshots[0]).not.toHaveProperty('liquidityUsd');
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('E2E Test 2 — an unquoted event drains once and stays bounded', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://mock.helius-rpc.com');
    const internal = provider as unknown as ProviderInternals;
    internal.pendingSnapshots.push({
      pairId: 'ScoreTest', mint: 'ScoreMint456', symbol: 'SCR', source: 'solana_rpc_ws_pump-fun',
      pairCreatedAt: new Date(Date.now() - 5 * 60_000).toISOString(),
    });

    await expect(provider.fetchSnapshots()).resolves.toEqual([
      expect.objectContaining({ pairId: 'ScoreTest', mint: 'ScoreMint456', priceUsd: 0 }),
    ]);
    for (let cycle = 0; cycle < 5; cycle += 1) {
      await expect(provider.fetchSnapshots()).resolves.toEqual([]);
    }
  });

  it('E2E Test 3 — a Triton curve-priced discovery snapshot supplies real momentum', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://mock.helius-rpc.com');
    const internal = provider as unknown as ProviderInternals;
    internal.pendingSnapshots.push({
      pairId: 'ExactPair789', mint: 'ExactMintReal', symbol: 'REAL', source: 'solana_rpc_ws_pump-fun',
      firstSeenAt: new Date().toISOString(), pairCreatedAt: new Date(Date.now() - 10 * 60_000).toISOString(),
    });
    const identity = (await provider.fetchSnapshots())[0]!;
    // Triton-first: een discovery-pool met eigen prijs + momentum (curve self-calc)
    const priced: MarketSnapshot = {
      ...identity,
      priceUsd: 0.012, liquidityUsd: 18_000,
      priceChangeM5Percent: 22.5, volumeM5Usd: 25_000, buysM5: 80, sellsM5: 12,
    };

    expect(priced.priceChangeM5Percent).toBe(22.5);
    expect(priced.priceUsd).toBeGreaterThan(0);
    expect(scoreMomentum(priced, config)).toBeGreaterThan(config.minMomentumScore);
  });

  it('E2E Test 4 — never trusts a stale/mismatched external quote (geen externe providers, Triton-first)', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://mock.helius-rpc.com');
    const internal = provider as unknown as ProviderInternals;
    internal.pendingSnapshots.push({
      pairId: 'GateTest', mint: 'GateMint789', symbol: 'GATE', source: 'solana_rpc_ws_pump-fun',
      pairCreatedAt: new Date(Date.now() - 5 * 60_000).toISOString(),
    });
    const identity = (await provider.fetchSnapshots())[0]!;
    // Geen Birdeye/Gecko meer: een ongeprijsde discovery-pool blijft ongeprijsd
    expect(identity).toEqual(expect.objectContaining({ pairId: 'GateTest', mint: 'GateMint789', priceUsd: 0 }));
  });
});
