import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { createPortfolio } from '../src/portfolio.js';
import { Scanner } from '../src/scanner.js';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
  SOL_PRICE_USD: '100',
});
const now = new Date('2026-07-24T23:00:00.000Z');
const candidate = {
  pairId: 'pair-1', mint: 'mint-1', symbol: 'PUMP', source: 'fake', observedAt: now.toISOString(),
  pairCreatedAt: new Date(now.getTime() - 10 * 60_000).toISOString(), priceUsd: 1,
  liquidityUsd: 50_000, volumeM5Usd: 15_000, priceChangeM5Percent: 20, buysM5: 30, sellsM5: 10,
};

describe('Scanner', () => {
  it('paper-enters one eligible candidate and suppresses a duplicate pair', async () => {
    const scanner = new Scanner({ fetchSnapshots: async () => [candidate] }, config, createPortfolio(config, now.toISOString()), () => now);
    const first = await scanner.runOnce();
    expect(first.decisions).toEqual([expect.objectContaining({
      type: 'paper_entry', mint: 'mint-1', openedAt: now.toISOString(), entryPriceUsd: 1.015,
    })]);
    expect(first.snapshots).toEqual([expect.objectContaining({ mint: 'mint-1', symbol: 'PUMP', liquidityUsd: 50_000 })]);
    expect(first.portfolio.positions).toHaveLength(1);

    const second = await scanner.runOnce();
    expect(second.decisions).toEqual([expect.objectContaining({ type: 'duplicate_suppressed', pairId: 'pair-1' })]);
    expect(second.portfolio.positions).toHaveLength(1);
  });

  it('restores the last committed portfolio after an uncommitted cycle result', async () => {
    const committed = createPortfolio(config, now.toISOString());
    const scanner = new Scanner({ fetchSnapshots: async () => [candidate] }, config, committed, () => now);
    const uncommitted = await scanner.runOnce();
    expect(uncommitted.portfolio.positions).toHaveLength(1);

    scanner.restorePortfolio(committed);
    const replay = await scanner.runOnce();

    expect(replay.portfolio).toEqual(committed);
  });

  it('records a rejected decision instead of opening a position', async () => {
    const scanner = new Scanner({ fetchSnapshots: async () => [{ ...candidate, liquidityUsd: 1 }] }, config, createPortfolio(config, now.toISOString()), () => now);
    const result = await scanner.runOnce();
    expect(result.decisions).toEqual([expect.objectContaining({ type: 'rejected', symbol: 'PUMP', reason: 'liquidity_below_minimum' })]);
    expect(result.portfolio.positions).toHaveLength(0);
  });

  it('ripens a too-young unpriced pool instead of rejecting it', async () => {
    // pool van 0,5 min oud, nog ongeprijsd (priceUsd 0) → rijpt, geen reject
    const youngUnpriced = {
      ...candidate, pairCreatedAt: new Date(now.getTime() - 30_000).toISOString(), priceUsd: 0,
      liquidityUsd: undefined, volumeM5Usd: 0, priceChangeM5Percent: 0, buysM5: 0, sellsM5: 0,
    };
    const scanner = new Scanner({ fetchSnapshots: async () => [youngUnpriced] }, config, createPortfolio(config, now.toISOString()), () => now);
    const result = await scanner.runOnce();
    expect(result.decisions.filter((d) => d.type === 'rejected')).toHaveLength(0);
    expect(result.portfolio.positions).toHaveLength(0);
  });

  it('re-evaluates a fresh quote version for the same pair without waiting five minutes', async () => {
    let currentTime = now;
    let nextSnapshot = { ...candidate, liquidityUsd: 1 };
    const scanner = new Scanner(
      { fetchSnapshots: async () => [nextSnapshot] },
      config,
      createPortfolio(config, now.toISOString()),
      () => currentTime,
    );

    const first = await scanner.runOnce();
    expect(first.decisions).toEqual([expect.objectContaining({ type: 'rejected', reason: 'liquidity_below_minimum' })]);

    currentTime = new Date(now.getTime() + 30_000);
    nextSnapshot = { ...candidate, observedAt: currentTime.toISOString() };
    const second = await scanner.runOnce();

    expect(second.decisions).toEqual([expect.objectContaining({ type: 'paper_entry', pairId: 'pair-1' })]);
    expect(second.decisions).not.toEqual(expect.arrayContaining([expect.objectContaining({ type: 'duplicate_suppressed' })]));
  });

  it('suppresses an older out-of-order quote even when its market fields changed', async () => {
    let currentTime = now;
    let nextSnapshot = { ...candidate, liquidityUsd: 1 };
    const scanner = new Scanner(
      { fetchSnapshots: async () => [nextSnapshot] }, config, createPortfolio(config, now.toISOString()), () => currentTime,
    );
    await scanner.runOnce();
    currentTime = new Date(now.getTime() + 30_000);
    nextSnapshot = { ...candidate, observedAt: new Date(now.getTime() - 1).toISOString() };

    const replay = await scanner.runOnce();

    expect(replay.decisions).toEqual([expect.objectContaining({ type: 'duplicate_suppressed', pairId: 'pair-1' })]);
    expect(replay.portfolio.positions).toEqual([]);
  });

  it('does not let a future quote poison the observation watermark', async () => {
    let currentTime = now;
    let nextSnapshot = { ...candidate, observedAt: new Date(now.getTime() + 60_000).toISOString() };
    const scanner = new Scanner(
      { fetchSnapshots: async () => [nextSnapshot] }, config, createPortfolio(config, now.toISOString()), () => currentTime,
    );
    const future = await scanner.runOnce();
    expect(future.decisions).toEqual([expect.objectContaining({
      type: 'rejected', reason: 'future_market_data', rejectionClass: 'market',
    })]);

    currentTime = new Date(now.getTime() + 30_000);
    nextSnapshot = { ...candidate, observedAt: currentTime.toISOString() };
    const valid = await scanner.runOnce();
    expect(valid.decisions).toEqual([expect.objectContaining({
      type: 'paper_entry', pairId: 'pair-1', firstSeenAt: now.toISOString(), detectionDelayMs: 600_000,
    })]);
  });

  it('hydrates first-seen provenance across a scanner restart', async () => {
    const evaluatedAt = '2026-07-29T14:01:50.842Z';
    const originalFirstSeenAt = '2026-07-29T13:01:50.842Z';
    const snapshot = {
      ...candidate, pairId: 'restart-pair', mint: 'restart-mint', observedAt: evaluatedAt,
      pairCreatedAt: '2026-07-29T12:44:33.000Z',
    };
    const scanner = new Scanner(
      { fetchSnapshots: async () => [snapshot] }, config, createPortfolio(config, evaluatedAt),
      () => new Date(evaluatedAt), new Map([['restart-pair', originalFirstSeenAt]]),
    );
    const result = await scanner.runOnce();
    expect(result.decisions[0]).toEqual(expect.objectContaining({
      type: 'paper_entry', firstSeenAt: originalFirstSeenAt, detectionDelayMs: 1_037_842,
    }));
  });

  it('uses provider discovery time as first-seen before the first evaluation', async () => {
    const evaluatedAt = '2026-07-29T13:02:20.000Z';
    const providerFirstSeenAt = '2026-07-29T13:01:50.000Z';
    const snapshot = {
      ...candidate, observedAt: evaluatedAt, firstSeenAt: providerFirstSeenAt,
      pairCreatedAt: '2026-07-29T12:44:33.000Z',
    };
    const scanner = new Scanner(
      { fetchSnapshots: async () => [snapshot] }, config, createPortfolio(config, evaluatedAt), () => new Date(evaluatedAt),
    );
    const result = await scanner.runOnce();
    expect(result.decisions[0]).toEqual(expect.objectContaining({
      firstSeenAt: providerFirstSeenAt, detectionDelayMs: 1_037_000,
    }));
  });

  it('records market timing context and classifies portfolio risk blocks separately', async () => {
    const marketScanner = new Scanner(
      { fetchSnapshots: async () => [{ ...candidate, liquidityUsd: 1 }] },
      config,
      createPortfolio(config, now.toISOString()),
      () => now,
    );
    const market = await marketScanner.runOnce();
    expect(market.decisions).toEqual([expect.objectContaining({
      type: 'rejected',
      reason: 'liquidity_below_minimum',
      rejectionClass: 'market',
      pairCreatedAt: candidate.pairCreatedAt,
      firstSeenAt: now.toISOString(),
      observedAt: now.toISOString(),
      evaluatedAt: now.toISOString(),
      detectionDelayMs: 600_000,
    })]);

    const riskLocked = {
      ...createPortfolio(config, now.toISOString()),
      dailyRealizedLossLamports: 500_000_000,
    };
    const risk = await new Scanner(
      { fetchSnapshots: async () => [candidate] },
      config,
      riskLocked,
      () => now,
    ).runOnce();
    expect(risk.decisions).toEqual([expect.objectContaining({
      type: 'rejected',
      reason: 'daily_loss_limit',
      rejectionClass: 'risk',
    })]);
  });

  it('hard-caps quote-version state for continuously arriving pairs', async () => {
    const snapshots = Array.from({ length: 10_001 }, (_, index) => ({
      ...candidate,
      pairId: `bounded-pair-${index}`,
      mint: `bounded-mint-${index}`,
      liquidityUsd: 1,
    }));
    const scanner = new Scanner(
      { fetchSnapshots: async () => snapshots },
      config,
      createPortfolio(config, now.toISOString()),
      () => now,
    );

    await scanner.runOnce();

    expect((scanner as unknown as { pairStates: Map<string, unknown> }).pairStates.size).toBe(10_000);
  });

  it('rejects an otherwise eligible candidate below the configured momentum score', async () => {
    const lowScoreCandidate = { ...candidate, priceChangeM5Percent: 8.1, volumeM5Usd: 5_100, buysM5: 11, sellsM5: 10 };
    const scanner = new Scanner({ fetchSnapshots: async () => [lowScoreCandidate] }, config, createPortfolio(config, now.toISOString()), () => now);
    const result = await scanner.runOnce();
    expect(result.decisions).toEqual([expect.objectContaining({ type: 'rejected', reason: 'score_below_minimum' })]);
  });

  it('does not close an open paper position using a different pair for the same mint', async () => {
    const later = new Date(now.getTime() + 65_000);
    const scanner1 = new Scanner({ fetchSnapshots: async () => [candidate] }, config, createPortfolio(config, now.toISOString()), () => now);
    const entered = await scanner1.runOnce();
    expect(entered.decisions).toEqual(expect.arrayContaining([expect.objectContaining({ type: 'paper_entry' })]));

    const scanner2 = new Scanner({ fetchSnapshots: async () => [{ ...candidate, priceUsd: 0.3, observedAt: later.toISOString(), pairId: 'pair-2' }] }, config, entered.portfolio, () => later);
    const result = await scanner2.runOnce();

    const entryDecision = entered.decisions.find((decision) => decision.type === 'paper_entry');
    const exitDecision = result.decisions.find((decision) => decision.type === 'paper_exit');
    expect(entryDecision).toMatchObject({ tradeId: `mint-1:${now.toISOString()}`, learningSchemaVersion: 2, pairId: 'pair-1' });
    expect(exitDecision).toBeUndefined();
    expect(result.decisions).not.toEqual(expect.arrayContaining([expect.objectContaining({ type: 'paper_entry' })]));
    expect(result.portfolio.positions).toEqual([expect.objectContaining({ pairId: 'pair-1', mint: 'mint-1' })]);
  });

  it('accepts a provider quote timestamped after normal request latency and executes the stop loss', async () => {
    const entered = await new Scanner(
      { fetchSnapshots: async () => [candidate] },
      config,
      createPortfolio(config, now.toISOString()),
      () => now,
    ).runOnce();
    let currentTime = new Date(now.getTime() + 65_000);
    const scanner = new Scanner({
      fetchSnapshots: async () => [],
      fetchSnapshotsForPositions: async () => {
        currentTime = new Date(currentTime.getTime() + 20);
        return [{ ...candidate, priceUsd: 0.3, observedAt: currentTime.toISOString() }];
      },
    }, config, entered.portfolio, () => currentTime);

    const result = await scanner.runOnce();

    expect(result.decisions).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'paper_exit', mint: 'mint-1', reason: 'stop_loss' }),
    ]));
    expect(result.portfolio.positions).toEqual([]);
    expect(result.providerErrors).toEqual([]);
  });

  it('executes a fresh stop-loss quote without waiting for stalled discovery', async () => {
    const entered = await new Scanner(
      { fetchSnapshots: async () => [candidate] }, config,
      createPortfolio(config, now.toISOString()), () => now,
    ).runOnce();
    const later = new Date(now.getTime() + 65_000);
    let positionQuoteStarted = false;
    const scanner = new Scanner({
      fetchSnapshots: () => new Promise(() => {}),
      fetchSnapshotsForPositions: async () => {
        positionQuoteStarted = true;
        return [{ ...candidate, priceUsd: 0.3, observedAt: later.toISOString() }];
      },
    }, config, entered.portfolio, () => later, new Map(), 10);

    const outcome = await Promise.race([
      scanner.runOnce(),
      new Promise<'test-timeout'>((resolve) => setTimeout(() => resolve('test-timeout'), 100)),
    ]);

    expect(outcome).not.toBe('test-timeout');
    expect(positionQuoteStarted).toBe(true);
    expect(outcome).toEqual(expect.objectContaining({
      decisions: expect.arrayContaining([expect.objectContaining({ type: 'paper_exit', reason: 'stop_loss' })]),
      providerErrors: expect.arrayContaining(['discovery: deadline exceeded after 10ms']),
    }));
  });

  it('offers a late successful discovery result to the next scan instead of losing it', async () => {
    let resolveDiscovery!: (snapshots: typeof candidate[]) => void;
    let calls = 0;
    const provider = {
      fetchSnapshots: () => {
        calls += 1;
        return calls === 1
          ? new Promise<typeof candidate[]>((resolve) => { resolveDiscovery = resolve; })
          : Promise.resolve([]);
      },
    };
    const scanner = new Scanner(
      provider,
      config,
      createPortfolio(config, now.toISOString()),
      () => now,
      new Map(),
      10,
    );

    const timedOut = await scanner.runOnce();
    expect(timedOut.providerErrors).toContain('discovery: deadline exceeded after 10ms');
    expect(timedOut.snapshots).toEqual([]);

    resolveDiscovery([{ ...candidate, pairId: 'late-pair', mint: 'late-mint' }]);
    await Promise.resolve();
    await Promise.resolve();

    const recovered = await scanner.runOnce();
    expect(recovered.snapshots).toEqual([
      expect.objectContaining({ pairId: 'late-pair', mint: 'late-mint' }),
    ]);
    expect(recovered.decisions).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'paper_entry', pairId: 'late-pair' }),
    ]));
  });

  it('hard-caps buffered late discovery results', async () => {
    let resolveDiscovery!: (snapshots: typeof candidate[]) => void;
    const scanner = new Scanner({
      fetchSnapshots: () => new Promise<typeof candidate[]>((resolve) => { resolveDiscovery = resolve; }),
    }, config, createPortfolio(config, now.toISOString()), () => now, new Map(), 10);

    await scanner.runOnce();
    resolveDiscovery(Array.from({ length: 600 }, (_, index) => ({
      ...candidate,
      pairId: `late-pair-${index}`,
      mint: `late-mint-${index}`,
    })));
    await Promise.resolve();
    await Promise.resolve();

    expect((scanner as unknown as { lateDiscoverySnapshots: typeof candidate[] }).lateDiscoverySnapshots).toHaveLength(500);
    expect((scanner as unknown as { lateDiscoverySnapshots: typeof candidate[] }).lateDiscoverySnapshots[0]?.pairId).toBe('late-pair-100');
  });

  it('does not buffer a timed-out promise again when a following scan consumes that same promise', async () => {
    let resolveDiscovery!: (snapshots: typeof candidate[]) => void;
    const shared = new Promise<typeof candidate[]>((resolve) => { resolveDiscovery = resolve; });
    let calls = 0;
    const scanner = new Scanner({
      fetchSnapshots: () => ++calls <= 2 ? shared : Promise.resolve([]),
    }, config, createPortfolio(config, now.toISOString()), () => now, new Map(), 10);

    await scanner.runOnce();
    const secondPromise = scanner.runOnce();
    resolveDiscovery([{ ...candidate, pairId: 'shared-late-pair', mint: 'shared-late-mint' }]);
    const second = await secondPromise;
    expect(second.snapshots).toEqual([
      expect.objectContaining({ pairId: 'shared-late-pair', mint: 'shared-late-mint' }),
    ]);

    const third = await scanner.runOnce();
    expect(third.snapshots).toEqual([]);
  });

  it('uses an open-position quote even when new-launch discovery no longer returns that mint', async () => {
    const later = new Date(now.getTime() + 65_000);
    const scanner1 = new Scanner({ fetchSnapshots: async () => [candidate] }, config, createPortfolio(config, now.toISOString()), () => now);
    const entered = await scanner1.runOnce();
    expect(entered.decisions).toEqual(expect.arrayContaining([expect.objectContaining({ type: 'paper_entry' })]));

    const scanner2 = new Scanner({
      fetchSnapshots: async () => [],
      fetchSnapshotsForPositions: async () => [{ ...candidate, priceUsd: 0.3 }],
    }, config, entered.portfolio, () => later);
    const result = await scanner2.runOnce();

    expect(result.decisions).toEqual(expect.arrayContaining([expect.objectContaining({ type: 'paper_exit', reason: 'stop_loss', mint: 'mint-1' })]));
    expect(result.portfolio.positions).toHaveLength(0);
  });

  it.each([
    ['zero price', { priceUsd: 0 }],
    ['negative price', { priceUsd: -0.3 }],
    ['NaN price', { priceUsd: Number.NaN }],
    ['invalid timestamp', { priceUsd: 0.3, observedAt: 'not-a-date' }],
    ['stale timestamp', { priceUsd: 0.3, observedAt: new Date(now.getTime() - 3 * 60_000).toISOString() }],
    ['future timestamp', { priceUsd: 0.3, observedAt: new Date(now.getTime() + 125_000).toISOString() }],
  ])('holds an open position for an invalid, stale, or future dedicated quote: %s', async (_label, quotePatch) => {
    const later = new Date(now.getTime() + 65_000);
    const entered = await new Scanner(
      { fetchSnapshots: async () => [candidate] },
      config,
      createPortfolio(config, now.toISOString()),
      () => now,
    ).runOnce();
    const positionBefore = structuredClone(entered.portfolio.positions[0]);
    const scanner = new Scanner({
      fetchSnapshots: async () => [],
      fetchSnapshotsForPositions: async () => [{ ...candidate, observedAt: later.toISOString(), ...quotePatch }],
    }, config, entered.portfolio, () => later);

    const result = await scanner.runOnce();

    expect(result.portfolio.positions).toEqual([positionBefore]);
    expect(result.decisions).not.toEqual(expect.arrayContaining([expect.objectContaining({ type: 'paper_exit' })]));
    expect(result.providerErrors).toEqual(['position_quote: fresh quote unavailable; pair_id=pair-1; mint=mint-1']);
    expect(result.snapshots).toEqual([]);
  });

  it('reports one concrete position-quote failure and stays silent during following cooldown cycles', async () => {
    let currentTime = new Date(now.getTime() + 65_000);
    let diagnosticsCall = 0;
    const entered = await new Scanner(
      { fetchSnapshots: async () => [candidate] },
      config,
      createPortfolio(config, now.toISOString()),
      () => now,
    ).runOnce();
    const scanner = new Scanner({
      fetchSnapshots: async () => [],
      fetchSnapshotsForPositions: async () => [],
      drainDiagnostics: () => diagnosticsCall++ === 0
        ? ['position_quote: Birdeye HTTP 429; pair_id=pair-1; mint=mint-1; cooldown_ms=60000']
        : [],
    }, config, entered.portfolio, () => currentTime);

    const limited = await scanner.runOnce();
    currentTime = new Date(currentTime.getTime() + 5_000);
    const cooldown = await scanner.runOnce();

    expect(limited.providerErrors).toEqual([
      'position_quote: Birdeye HTTP 429; pair_id=pair-1; mint=mint-1; cooldown_ms=60000',
    ]);
    expect(cooldown.providerErrors).toEqual([]);
    expect(cooldown.portfolio.positions).toHaveLength(1);
  });

  it('holds an overdue open position when its fresh quote is unavailable', async () => {
    const later = new Date(now.getTime() + 46 * 60_000);
    const entered = await new Scanner(
      { fetchSnapshots: async () => [candidate] },
      config,
      createPortfolio(config, now.toISOString()),
      () => now,
    ).runOnce();
    const positionBefore = structuredClone(entered.portfolio.positions[0]);
    const scanner = new Scanner({
      fetchSnapshots: async () => [],
      fetchSnapshotsForPositions: async () => [],
    }, config, entered.portfolio, () => later);

    const result = await scanner.runOnce();

    expect(result.decisions).not.toEqual(expect.arrayContaining([expect.objectContaining({ type: 'paper_exit' })]));
    expect(result.portfolio.positions).toEqual([positionBefore]);
    expect(result.providerErrors).toEqual(['position_quote: fresh quote unavailable; pair_id=pair-1; mint=mint-1']);
  });

  it('records provider diagnostics after a degraded but completed discovery call', async () => {
    const scanner = new Scanner({
      fetchSnapshots: async () => [],
      drainDiagnostics: () => ['discovery: Birdeye HTTP 429; cooldown_ms=60000'],
    }, config, createPortfolio(config, now.toISOString()), () => now);

    const result = await scanner.runOnce();

    expect(result.providerErrors).toEqual(['discovery: Birdeye HTTP 429; cooldown_ms=60000']);
  });

  it('still evaluates open positions when discovery fails and reports the provider degradation', async () => {
    const later = new Date(now.getTime() + 65_000);
    const entered = await new Scanner(
      { fetchSnapshots: async () => [candidate] },
      config,
      createPortfolio(config, now.toISOString()),
      () => now,
    ).runOnce();
    const scanner = new Scanner({
      fetchSnapshots: async () => { throw new Error('discovery unavailable'); },
      fetchSnapshotsForPositions: async () => [{ ...candidate, priceUsd: 0.3, observedAt: later.toISOString() }],
    }, config, entered.portfolio, () => later);

    const result = await scanner.runOnce();

    expect(result.providerErrors).toEqual(['discovery: discovery unavailable']);
    expect(result.decisions).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'paper_exit', reason: 'stop_loss', mint: 'mint-1' }),
    ]));
    expect(result.portfolio.positions).toHaveLength(0);
  });
});