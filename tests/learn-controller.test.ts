import { describe, expect, it } from 'vitest';
import { mkdtemp, readFile, readdir, readlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadConfig } from '../src/config.js';
import { LearnController } from '../src/learn-controller.js';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
  SOL_PRICE_USD: '100', RPC_HTTP_ENDPOINT: 'https://provider.invalid/?api-key=must-not-persist',
});

async function seedChampion(directory: string, tuning: Record<string, unknown>): Promise<void> {
  await writeFile(join(directory, 'strategies-v2.json'), JSON.stringify([{
    id: 'v1.0', createdAt: '2026-07-28T00:00:00.000Z', config: tuning, status: 'champion', notes: 'fixture',
  }]));
}

async function descriptorsResolvingUnder(directory: string): Promise<string[]> {
  const descriptors = await readdir('/proc/self/fd');
  const targets = await Promise.all(descriptors.map(async (descriptor) => {
    try {
      return await readlink(`/proc/self/fd/${descriptor}`);
    } catch {
      return undefined;
    }
  }));
  return targets.filter((target): target is string => target === directory || target?.startsWith(`${directory}/`) === true);
}

describe('LearnController runtime config safety', () => {
  it('closes opportunity audit and directory descriptors when corrupt strategy loading throws', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-corrupt-cleanup-'));
    await writeFile(join(directory, 'strategies-v2.json'), '{corrupt-json');

    expect(() => new LearnController(directory, config)).toThrow(/JSON/i);

    expect(await descriptorsResolvingUnder(directory)).toEqual([]);
  });

  it('resolves a partial champion tuning into a complete validated runtime config without persisting endpoints', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    await seedChampion(directory, { minMomentumScore: 55 });
    const controller = new LearnController(directory, config);

    expect(controller.getChampionRuntimeConfig()).toMatchObject({
      mode: 'paper', minMomentumScore: 55, maxStopLossPercent: config.maxStopLossPercent, solPriceUsd: 100,
    });
    const persisted = await readFile(join(directory, 'strategies-v2.json'), 'utf8');
    expect(persisted).not.toContain('must-not-persist');
    expect(persisted).not.toContain('rpcHttpEndpoint');
  });

  it('restores the strategy marked champion instead of silently reverting to v1.0', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    await writeFile(join(directory, 'strategies-v2.json'), JSON.stringify([
      { id: 'v1.0', createdAt: '2026-07-27T00:00:00.000Z', config: { minMomentumScore: 50 }, status: 'archived' },
      { id: 'v2.0', createdAt: '2026-07-28T00:00:00.000Z', config: { minMomentumScore: 60 }, status: 'champion' },
    ]));
    const controller = new LearnController(directory, config);
    expect(controller.championId).toBe('v2.0');
    expect(controller.getChampionRuntimeConfig().minMomentumScore).toBe(60);
  });

  it('refuses to activate an invalid persisted champion tuning', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    await seedChampion(directory, { stopLossPercent: -1 });
    expect(() => new LearnController(directory, config)).toThrow(/stopLossPercent/i);
  });

  it('never lets persisted strategy state loosen the deployment daily-loss ceiling', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    await seedChampion(directory, { minMomentumScore: 55, maxDailyLossSol: 5 });

    const controller = new LearnController(directory, config);

    expect(controller.getChampionRuntimeConfig().maxDailyLossSol).toBe(config.maxDailyLossSol);
    expect(await readFile(join(directory, 'strategies-v2.json'), 'utf8')).not.toContain('maxDailyLossSol');
  });

  it('never restores hard position, concurrency, stop-loss, or liquidity-exposure ceilings from strategy state', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    await seedChampion(directory, {
      maxPositionSol: config.maxPositionSol * 2,
      maxConcurrentPositions: config.maxConcurrentPositions * 2,
      maxStopLossPercent: config.maxStopLossPercent * 2,
      liquidityPositionFraction: Math.min(1, config.liquidityPositionFraction * 2),
    });

    const controller = new LearnController(directory, config);
    const runtime = controller.getChampionRuntimeConfig();
    expect(runtime.maxPositionSol).toBe(config.maxPositionSol);
    expect(runtime.maxConcurrentPositions).toBe(config.maxConcurrentPositions);
    expect(runtime.maxStopLossPercent).toBe(config.maxStopLossPercent);
    expect(runtime.liquidityPositionFraction).toBe(config.liquidityPositionFraction);

    const persisted = await readFile(join(directory, 'strategies-v2.json'), 'utf8');
    expect(persisted).not.toMatch(/maxPositionSol|maxConcurrentPositions|maxStopLossPercent|liquidityPositionFraction/);
  });

  it('fails closed on duplicate persisted strategy IDs without rewriting the registry', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    const duplicated = JSON.stringify([
      { id: 'v1.0', createdAt: '2026-07-27T00:00:00.000Z', config: { minMomentumScore: 40 }, status: 'champion' },
      { id: 'v1.0', createdAt: '2026-07-28T00:00:00.000Z', config: { minMomentumScore: 80 }, status: 'challenger' },
    ]);
    const path = join(directory, 'strategies-v2.json');
    await writeFile(path, duplicated);
    expect(() => new LearnController(directory, config)).toThrow(/duplicate persisted strategy ID: v1\.0/i);
    expect(await readFile(path, 'utf8')).toBe(duplicated);
  });

  it('fails closed when a persisted registry exceeds 100 strategies', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-strategy-count-'));
    const strategies = Array.from({ length: 101 }, (_, index) => ({
      id: `v${index}.0`, createdAt: '2026-07-28T00:00:00.000Z', config: {},
      status: index === 0 ? 'champion' : 'archived',
    }));
    const original = JSON.stringify(strategies);
    const path = join(directory, 'strategies-v2.json');
    await writeFile(path, original);

    expect(() => new LearnController(directory, config)).toThrow(/strategy registry.*100/i);
    expect(await readFile(path, 'utf8')).toBe(original);
  });

  it('checks the persisted strategy byte cap before parsing or rewriting it', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-strategy-bytes-'));
    const oversized = JSON.stringify([{
      id: 'v1.0', createdAt: '2026-07-28T00:00:00.000Z', config: {}, status: 'champion',
      notes: 'x'.repeat(1_048_576),
    }]);
    const path = join(directory, 'strategies-v2.json');
    await writeFile(path, oversized);

    expect(() => new LearnController(directory, config)).toThrow(/strategy registry.*byte/i);
    expect(await readFile(path, 'utf8')).toBe(oversized);
  });

  it('leaves legacy files untouched and atomically replaces one bounded v2 latest report', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    const legacy = JSON.stringify([{ id: 'legacy', status: 'champion', config: { rpcHttpEndpoint: 'legacy-value' } }]);
    const day = new Date().toISOString().split('T')[0];
    const legacyReport = '{"legacy":true}';
    await writeFile(join(directory, 'strategies.json'), legacy);
    await writeFile(join(directory, `learn-report-${day}.json`), legacyReport);
    const controller = new LearnController(directory, config);
    controller.recordObservation({
      at: new Date().toISOString(), strategyId: 'v1.0', token: { mint: 'none', symbol: 'NONE', pairId: 'none' },
      dex: 'fixture', source: 'fixture', priceUsd: 1, decision: 'rejected', rejectReason: 'fixture',
    });
    await controller.analyzeAndImprove();
    expect(await readFile(join(directory, 'strategies.json'), 'utf8')).toBe(legacy);
    expect(await readFile(join(directory, `learn-report-${day}.json`), 'utf8')).toBe(legacyReport);
    expect(await readFile(join(directory, 'strategies-v2.json'), 'utf8')).not.toContain('legacy-value');
    expect(JSON.parse(await readFile(join(directory, 'learn-report-v2-latest.json'), 'utf8'))).toMatchObject({ action: 'skip', totalTrades: 0 });
  });

  it('analyzes canonical matched v2 exits instead of filtering the completed cohort to zero', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    const controller = new LearnController(directory, config);
    for (let index = 0; index < 20; index += 1) {
      const tradeId = `mint-${index}:2026-07-28T10:00:00.000Z`;
      const token = { mint: `mint-${index}`, symbol: `T${index}`, pairId: `pair-${index}` };
      controller.recordObservation({
        learningSchemaVersion: 2,
        at: '2026-07-28T10:00:00.000Z', strategyId: 'ignored', tradeId, token,
        dex: 'fixture', source: 'fixture', priceUsd: 1, entryPriceUsd: 1, score: 80, decision: 'entry',
      });
      controller.recordObservation({
        learningSchemaVersion: 2,
        at: '2026-07-28T10:10:00.000Z', strategyId: 'ignored', tradeId, token,
        dex: 'fixture', source: 'fixture', priceUsd: 1.1, exitPriceUsd: 1.1, score: 80,
        decision: 'exit', pnlSol: 0.1, exitReason: 'take_profit', holdingMinutes: 10,
      });
    }

    const restarted = new LearnController(directory, config);
    const report = await restarted.analyzeAndImprove();
    expect(report).toMatchObject({ action: 'hold', totalTrades: 20, championPerf: { totalTrades: 20 } });
    expect(report.championPerf.netReturn).toBeCloseTo(2);
  });

  it('preserves an absent learning schema marker on legacy exits', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    const controller = new LearnController(directory, config);

    controller.recordObservation({
      at: '2026-07-28T10:10:00.000Z', strategyId: controller.championId,
      tradeId: 'legacy-mint:2026-07-28T10:00:00.000Z',
      token: { mint: 'legacy-mint', symbol: 'LEGACY', pairId: 'legacy-pair' },
      dex: 'fixture', source: 'fixture', priceUsd: 0.8, exitPriceUsd: 0.8,
      decision: 'exit', pnlSol: -0.1, exitReason: 'stop_loss', holdingMinutes: 10,
    });

    const records = (await readFile(join(directory, 'opportunities.ndjson'), 'utf8')).trim().split('\n').map((line) => JSON.parse(line));
    expect(records).toHaveLength(1);
    expect(records[0]).not.toHaveProperty('learningSchemaVersion');
    expect(controller.storage.completedCount).toBe(0);
  });

  it('exposes no strategy mutation or promotion surface while deterministic replay is unavailable', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    const controller = new LearnController(directory, config);
    expect(controller).not.toHaveProperty('promote');
    expect(controller).not.toHaveProperty('createStrategy');
    expect(controller).not.toHaveProperty('nextStrategyId');
  });

  it('fails closed without deterministic quote-path replay and leaves champion runtime and registry untouched', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-controller-'));
    const controller = new LearnController(directory, config);
    for (let index = 0; index < 20; index += 1) {
      const openedAt = `2026-07-28T10:${String(index).padStart(2, '0')}:00.000Z`;
      const tradeId = `loss-${index}:${openedAt}`;
      const token = { mint: `loss-${index}`, symbol: `L${index}`, pairId: `loss-pair-${index}` };
      controller.recordObservation({
        learningSchemaVersion: 2, at: openedAt, strategyId: controller.championId, tradeId, token,
        dex: 'fixture', source: 'fixture', priceUsd: 1, entryPriceUsd: 1, score: 80, decision: 'entry',
      });
      controller.recordObservation({
        learningSchemaVersion: 2, at: '2026-07-28T11:00:00.000Z', strategyId: controller.championId, tradeId, token,
        dex: 'fixture', source: 'fixture', priceUsd: 0.8, exitPriceUsd: 0.8,
        decision: 'exit', pnlSol: -0.1, exitReason: 'stop_loss', holdingMinutes: 10,
      });
    }
    const registryPath = join(directory, 'strategies-v2.json');
    const registryBefore = await readFile(registryPath, 'utf8');
    const championConfigBefore = controller.getChampionRuntimeConfig();

    const report = await controller.analyzeAndImprove();

    expect(report).toMatchObject({
      action: 'hold',
      reason: 'Automatic strategy promotion disabled: deterministic quote-path replay unavailable',
      totalTrades: 20,
      candidatesTested: 0,
    });
    expect(controller.championId).toBe('v1.0');
    expect(controller.getChampionRuntimeConfig()).toEqual(championConfigBefore);
    expect(await readFile(registryPath, 'utf8')).toBe(registryBefore);
  });
});
