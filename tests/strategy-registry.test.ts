import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ComparisonEngine } from '../src/comparison-engine.js';
import type { PaperConfig } from '../src/config.js';
import type { OpportunityRecord } from '../src/opportunity-storage.js';
import { StrategyRegistry } from '../src/strategy-registry.js';

const MAX_FILE_BYTES = 1024 * 1024;
const config = {} as PaperConfig;

function strategy(index: number) {
  return {
    id: `strategy-${index}`,
    createdAt: '2026-07-30T00:00:00.000Z',
    config,
    status: index === 0 ? 'champion' : 'challenger',
  };
}

describe('legacy StrategyRegistry hard bounds', () => {
  it('rejects an existing file larger than one MiB before JSON parsing', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const path = join(directory, 'strategies.json');
    await writeFile(path, 'x'.repeat(MAX_FILE_BYTES + 1));

    expect(() => new StrategyRegistry(directory, 'strategy-0')).toThrow(/one MiB|1 MiB|1048576/i);
    expect((await readFile(path)).byteLength).toBe(MAX_FILE_BYTES + 1);
  });

  it('rejects more than 100 persisted strategies without rewriting the source file', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const path = join(directory, 'strategies.json');
    const original = JSON.stringify(Array.from({ length: 101 }, (_, index) => strategy(index)));
    await writeFile(path, original);

    expect(() => new StrategyRegistry(directory, 'strategy-0')).toThrow(/100/);
    expect(await readFile(path, 'utf8')).toBe(original);
  });

  it('refuses a 101st externally named strategy and preserves the persisted 100-entry state', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const path = join(directory, 'strategies.json');
    const original = JSON.stringify(Array.from({ length: 100 }, (_, index) => strategy(index)));
    await writeFile(path, original);
    const registry = new StrategyRegistry(directory, 'strategy-0');

    expect(() => registry.create('strategy-100', config)).toThrow(/100/);
    expect(registry.list()).toHaveLength(100);
    expect(await readFile(path, 'utf8')).toBe(original);
  });

  it('rejects an oversized next serialization before mutating memory or the state file', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const path = join(directory, 'strategies.json');
    const original = JSON.stringify([strategy(0)]);
    await writeFile(path, original);
    const registry = new StrategyRegistry(directory, 'strategy-0');

    expect(() => registry.create('huge-notes', config, 'strategy-0', 'x'.repeat(MAX_FILE_BYTES))).toThrow(/notes|one MiB|1 MiB|1048576/i);
    expect(registry.get('huge-notes')).toBeUndefined();
    expect(await readFile(path, 'utf8')).toBe(original);
  });

  it.each(['', 'unsafe/id', 'üñicode', 'x'.repeat(65)])('rejects unsafe or oversized strategy id %j', async (id) => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const registry = new StrategyRegistry(directory, 'strategy-0');
    expect(() => registry.create(id, config)).toThrow(/strategy id/i);
  });

  it('rejects duplicate ids and multiple champions without rewriting persisted bytes', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const path = join(directory, 'strategies.json');
    for (const records of [
      [strategy(0), strategy(0)],
      [strategy(0), { ...strategy(1), status: 'champion' }],
    ]) {
      const original = JSON.stringify(records);
      await writeFile(path, original);
      expect(() => new StrategyRegistry(directory, 'strategy-0')).toThrow(/duplicate|exactly one champion/i);
      expect(await readFile(path, 'utf8')).toBe(original);
    }
  });

  it('promotes exactly one champion and restores that champion after restart', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    await writeFile(join(directory, 'strategies.json'), JSON.stringify([strategy(0), strategy(1)]));
    const registry = new StrategyRegistry(directory, 'strategy-0');
    registry.promote('strategy-1');
    expect(registry.championId).toBe('strategy-1');
    expect(registry.list().filter((item) => item.status === 'champion').map((item) => item.id)).toEqual(['strategy-1']);

    const restarted = new StrategyRegistry(directory, 'strategy-0');
    expect(restarted.championId).toBe('strategy-1');
    expect(restarted.getChampion().id).toBe('strategy-1');
  });

  it('persists only tunable strategy fields and never endpoint, path, wallet, or hard-risk fields', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const registry = new StrategyRegistry(directory, 'strategy-0');
    const sensitive = {
      minPriceChangeM5Percent: 1.5,
      rpcHttpEndpoint: 'https://secret.invalid/?api-key=secret',
      rpcWsEndpoint: 'wss://secret.invalid/?api-key=secret',
      dataDir: '/secret/path', whaleWallets: ['secret-wallet'], maxDailyLossSol: 999,
    } as PaperConfig;
    registry.create('safe-tuning', sensitive);
    const persisted = await readFile(join(directory, 'strategies.json'), 'utf8');
    expect(persisted).toContain('minPriceChangeM5Percent');
    expect(persisted).not.toMatch(/api-key|rpcHttpEndpoint|rpcWsEndpoint|dataDir|whaleWallets|maxDailyLossSol/);
    // Ontdekking-poort (minAge/liquidity-band) is GEEN leerbaar veld meer.
    expect(persisted).not.toMatch(/minLiquidityUsd|minAgeMinutes/);
  });

  it('rejects semantically invalid persisted tuning without rewriting source bytes', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const path = join(directory, 'strategies.json');
    const original = JSON.stringify([{ ...strategy(0), config: { minPriceChangeM5Percent: -1 } }]);
    await writeFile(path, original);
    expect(() => new StrategyRegistry(directory, 'strategy-0')).toThrow(/minPriceChangeM5Percent|positive/i);
    expect(await readFile(path, 'utf8')).toBe(original);
  });

  it('requires an exact complete semantically valid performance record', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const path = join(directory, 'strategies.json');
    for (const performance of [
      { foo: 1 },
      { netReturn: 1, profitFactor: 1, sharpeRatio: 1, sortinoRatio: 1, maxDrawdown: 0.1, winRate: 101, avgWin: 1, avgLoss: 1, totalTrades: 1, expectancy: 1, medianTrade: 1, volatility: 0.1 },
    ]) {
      const original = JSON.stringify([{ ...strategy(0), performance }]);
      await writeFile(path, original);
      expect(() => new StrategyRegistry(directory, 'strategy-0')).toThrow(/performance|winRate|totalTrades/i);
      expect(await readFile(path, 'utf8')).toBe(original);
    }
  });

  it('accepts and persists the 0-to-100 win-rate scale produced by ComparisonEngine', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'strategy-registry-'));
    const registry = new StrategyRegistry(directory, 'strategy-0');
    const trades = Array.from({ length: 20 }, (_, index) => ({
      learningSchemaVersion: 2,
      decision: 'exit',
      pnlSol: index < 10 ? 1 : -0.5,
    })) as OpportunityRecord[];
    const performance = new ComparisonEngine().computePerformance(trades);

    expect(performance.winRate).toBe(50);
    expect(() => registry.updatePerformance('strategy-0', performance)).not.toThrow();
    expect(JSON.parse(await readFile(join(directory, 'strategies.json'), 'utf8'))[0].performance.winRate).toBe(50);
  });
});
