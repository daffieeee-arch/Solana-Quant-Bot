import { describe, it, expect } from 'vitest';
import { TritonProvider, type TritonClientLike, type TritonStreamLike, type VixenUpdate } from '../src/providers/triton.js';

// ─── Fake stream + client seam ────────────────────────────────────────────
function makeFakeClient(handlersByProgram: Record<string, (u: VixenUpdate) => void>) {
  const streams: Array<{ program: string; stream: FakeStream }> = [];
  class FakeStream implements TritonStreamLike {
    private dataListener?: (u: VixenUpdate) => void;
    private errorListener?: (e: Error) => void;
    cancelled = false;
    on(event: 'data' | 'error', listener: (value: unknown) => void): unknown {
      if (event === 'data') this.dataListener = listener as (u: VixenUpdate) => void;
      if (event === 'error') this.errorListener = listener as (e: Error) => void;
      return this;
    }
    cancel(): void { this.cancelled = true; }
    emit(update: VixenUpdate) { this.dataListener?.(update); }
    // expose emit for tests
    _push(u: VixenUpdate) { this.dataListener?.(u); }
  }
  const client: TritonClientLike = {
    Subscribe(request: { program: string }) {
      const stream = new FakeStream();
      streams.push({ program: request.program, stream });
      return stream;
    },
  };
  const pushTo = (program: string, update: VixenUpdate) => {
    const found = streams.find((s) => s.program === program);
    found?.stream._push(update);
  };
  return { client, pushTo, streams };
}

const ENDPOINT = 'johnb-mainnet-2781.mainnet.rpcpool.com';
const TOKEN = '#### REDACTED ####';

const PUMP_BUY: VixenUpdate = {
  buy: {
    accounts: {
      mint: 'C8VZE8cy71FKrVKMdi8Ne9q9JNyFD7vJEbGMuaS3pump',
      bondingCurve: '4yUUpM9h9pXZLn54X4jLP4FoV8JsBK4nsSMLGaeyVuCP',
    },
    data: { amount: '1000' },
  },
};

const PUMP_SELL: VixenUpdate = {
  sell: {
    accounts: {
      mint: 'Di858PEoxeGg3Xh95YgyT1hoDdEYRdJdmDeQobwCpump',
      bondingCurve: 'Zu7SGuEWWtFCHze9SnhGmbWWoR3BxTnFwd9CLrZXTq1',
    },
    data: { amount: '500' },
  },
};

const BONDING_CURVE_ONLY: VixenUpdate = {
  bondingCurve: { creator: 'Y3yq1u9s9ciiyjjctWexAtcioDDfnwe5g1yWYMPzPQR' },
};

const RAYDIUM_AMM_INFO: VixenUpdate = {
  ammInfo: {
    coinMint: 'So11111111111111111111111111111111111111112',
    pcMint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
    lpMint: '8HoQnePLqPj4M7PUDzfw8e3Ymdwgc7NLGnaTUapubyvu',
    tokenCoin: 'DQyrAcCrDXQ7NeoqGgDCZwBvWDcYmFCjSb9JtteuvPpz',
    status: '1',
  },
};

const RAYDIUM_SWAP: VixenUpdate = {
  swapBaseIn: {
    accounts: { amm: 'DSUvc5qf5LJHHV5e2tD184ixotSnCnwj7i4jJa4Xsrmt' },
  },
};

const CPMM_POOL_STATE: VixenUpdate = {
  poolState: {
    mintA: 'So11111111111111111111111111111111111111112',
    mintB: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
    lpMint: 'CPMMPoolLpMintPlaceholderAAAAAAAAAAAAAAAAAAAAAAAAA',
    status: '1',
  },
};

describe('TritonProvider', () => {
  it('subscribes to pumpfun, raydium AMMv4 and CPMM programs', () => {
    const { client, streams } = makeFakeClient({});
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    const programs = streams.map((s) => s.program);
    expect(programs).toContain('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P');
    expect(programs).toContain('675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8');
    expect(programs).toContain('CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C');
  });

  it('emits exact-pair identity for pump.fun buy with bondingCurve + mint', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_BUY);
    const snapshots = await provider.fetchSnapshots();
    expect(snapshots).toHaveLength(1);
    expect(snapshots[0].pairId).toBe('4yUUpM9h9pXZLn54X4jLP4FoV8JsBK4nsSMLGaeyVuCP');
    expect(snapshots[0].mint).toBe('C8VZE8cy71FKrVKMdi8Ne9q9JNyFD7vJEbGMuaS3pump');
    expect(snapshots[0].priceUsd).toBe(0);
    expect(snapshots[0].source).toBe('triton_vixen_pumpfun');
    expect(snapshots[0].discovery?.programId).toBe('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P');
  });

  it('emits for pump.fun sell as well', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_SELL);
    const snapshots = await provider.fetchSnapshots();
    expect(snapshots).toHaveLength(1);
    expect(snapshots[0].mint).toBe('Di858PEoxeGg3Xh95YgyT1hoDdEYRdJdmDeQobwCpump');
  });

  it('ignores bonding-curve-only state (no mint identity)', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', BONDING_CURVE_ONLY);
    const snapshots = await provider.fetchSnapshots();
    expect(snapshots).toHaveLength(0);
  });

  it('dedupes the same pairId within the process lifetime', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_BUY);
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_BUY);
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_SELL);
    const snapshots = await provider.fetchSnapshots();
    expect(snapshots).toHaveLength(2); // distinct pairIds (buy curve + sell curve)
  });

  it('emits Raydium AMMv4 pool identity with base mint + lpMint', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    pushTo('675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8', RAYDIUM_AMM_INFO);
    const snapshots = await provider.fetchSnapshots();
    expect(snapshots).toHaveLength(1);
    expect(snapshots[0].source).toBe('triton_vixen_raydium_ammv4');
    // pairId = tokenCoin (coin vault token account) as anchor
    expect(snapshots[0].pairId).toBe('DQyrAcCrDXQ7NeoqGgDCZwBvWDcYmFCjSb9JtteuvPpz');
  });

  it('emits Raydium CPMM pool identity', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    pushTo('CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C', CPMM_POOL_STATE);
    const snapshots = await provider.fetchSnapshots();
    expect(snapshots).toHaveLength(1);
    expect(snapshots[0].source).toBe('triton_vixen_raydium_cpmm');
  });

  it('bounds the pending queue and drain diagnostics', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    for (let i = 0; i < 100; i++) {
      pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', {
        buy: { accounts: { mint: `Mint${i}Tpump`, bondingCurve: `Curve${i}` } },
      });
    }
    const snapshots = await provider.fetchSnapshots();
    expect(snapshots.length).toBeGreaterThan(0);
    const second = await provider.fetchSnapshots();
    expect(second).toHaveLength(0); // drained
  });

  it('rejects invalid endpoint and token', () => {
    expect(() => new TritonProvider('', TOKEN, () => makeFakeClient({}).client)).toThrow();
    expect(() => new TritonProvider(ENDPOINT, '', () => makeFakeClient({}).client)).toThrow();
    expect(() => new TritonProvider(`bad\nendpoint`, TOKEN, () => makeFakeClient({}).client)).toThrow();
  });

  it('destroy clears pending and further updates are dropped', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client);
    provider.destroy();
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_BUY);
    const snapshots = await provider.fetchSnapshots();
    expect(snapshots).toHaveLength(0);
  });

  it('re-emits an already-seen pool after the cooldown (Laag A rijping her-aanbieding)', async () => {
    let t = 1_000_000;
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client, () => t);

    // eerste emit: t=1000000
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_BUY);
    const first = await provider.fetchSnapshots();
    expect(first).toHaveLength(1); // unieke pool

    // binnen cooldown (t+5s): zelfde buy → onderdrukt
    t += 5_000;
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_BUY);
    const suppressed = await provider.fetchSnapshots();
    expect(suppressed).toHaveLength(0);

    // na cooldown (>30s): zelfde pool opnieuw aangeboden → re-emit voor rijping
    t += 30_000;
    pushTo('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', PUMP_BUY);
    const reemitted = await provider.fetchSnapshots();
    expect(reemitted).toHaveLength(1);
    expect(reemitted[0].pairId).toBe('4yUUpM9h9pXZLn54X4jLP4FoV8JsBK4nsSMLGaeyVuCP');
    // Echte leeftijd: de re-emitted pool behoudt de ORIGINELE discovery-tijd als
    // pairCreatedAt (i.p.v. elke ronde een verse ≈0), zodat de rippings-queue kan
    // rijpen tot >= minAge en de pool evalueerbaar wordt.
    expect(reemitted[0].pairCreatedAt).toBe(first[0].pairCreatedAt);
    expect(Date.parse(reemitted[0].pairCreatedAt)).toBe(1_000_000);
  });

  it('prijst een generic pump-launch met curve via de reserveReader (liquidity+price)', async () => {
    const { client, pushTo } = makeFakeClient({});
    // fake reserveReader: geeft een vast poolDepth terug voor de curve
    const fakeDepth = {
      quoteReserve: 60_000_000_000, // 60 SOL (wei)
      baseReserve: 800_000_000_000, // 800 token (6 decimals)
      quoteDecimals: 9,
      baseDecimals: 6,
    };
    const reserveReader = {
      fetchPumpDepth: async () => fakeDepth,
      rpcCallCountsSnapshot: () => ({}),
    } as never;
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client, () => 1_000_000, reserveReader, { solPriceUsd: 74 });
    // generic-launch update met curve — via de pumpSwaps-program-stream (moonshot →
    // onGenericLaunchUpdate). De subscribeAll maakt streams voor deze programma's aan.
    pushTo('MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG', { genericLaunch: { mint: 'C8VZE8cy71FKrVKMdi8Ne9q9JNyFD7vJEbGMuaS3pump', curve: '4yUUpM9h9pXZLn54X4jLP4FoV8JsBK4nsSMLGaeyVuCP' } } as never);
    // async fetchPumpDepth: laat de microtask/await afhandelen vóór fetchSnapshots
    await new Promise((resolve) => setTimeout(resolve, 20));

    const snapshots = await provider.fetchSnapshots();
    const found = snapshots.find((s) => s.mint === 'C8VZE8cy71FKrVKMdi8Ne9q9JNyFD7vJEbGMuaS3pump');
    expect(found).toBeDefined();
    expect(found?.source).toBe('triton_geyser_generic_multidex');
    // eigen curve-calc: liqUsd ≈ 2× quoteReserve in USD
    expect(found?.liquidityUsd).toBeGreaterThan(0);
    expect(found?.priceUsd).toBeGreaterThan(0);
  });

  it('laat een generic-launch zonder curve prijsloos (fail-closed, geen crash)', async () => {
    const { client, pushTo } = makeFakeClient({});
    const provider = new TritonProvider(ENDPOINT, TOKEN, () => client, () => 1_000_000, undefined, { solPriceUsd: 74 });
    pushTo('MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG', { genericLaunch: { mint: 'C8VZE8cy71FKrVKMdi8Ne9q9JNyFD7vJEbGMuaS3pump' } } as never);

    const snapshots = await provider.fetchSnapshots();
    const found = snapshots.find((s) => s.mint === 'C8VZE8cy71FKrVKMdi8Ne9q9JNyFD7vJEbGMuaS3pump');
    expect(found).toBeDefined();
    expect(found?.priceUsd).toBe(0); // geen curve → ongeprijsd, maar wél gedetecteerd
  });
});
