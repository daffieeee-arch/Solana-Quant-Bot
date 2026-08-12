import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildLearningObservations } from '../src/learning-observations.js';
import { OpportunityStorage } from '../src/opportunity-storage.js';
import type { ScanDecision } from '../src/scanner.js';
import type { MarketSnapshot } from '../src/scoring.js';

const at = '2026-07-30T10:05:00.000Z';

describe('learning observation producer → strict opportunity logger', () => {
  it('commits the normal current-v2 paper entry and exit produced by the scanner path', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-producer-logger-'));
    const openedAt = '2026-07-30T10:00:00.000Z';
    const tradeId = `mint-current:${openedAt}`;
    const snapshots: MarketSnapshot[] = [{
      pairId: 'pair-current',
      mint: 'mint-current',
      symbol: 'CURRENT',
      source: 'raydium-live',
      observedAt: at,
      pairCreatedAt: '2026-07-30T09:55:00.000Z',
      priceUsd: 1.2,
      liquidityUsd: 50_000,
      volumeM5Usd: 10_000,
      priceChangeM5Percent: 10,
      buysM5: 20,
      sellsM5: 5,
    }];
    const decisions: ScanDecision[] = [
      {
        type: 'paper_entry',
        tradeId,
        learningSchemaVersion: 2,
        pairId: 'pair-current',
        mint: 'mint-current',
        symbol: 'CURRENT',
        score: 80,
        source: 'raydium-live',
        coverage: 'best_effort',
        openedAt,
        entryPriceUsd: 1.01,
      },
      {
        type: 'paper_exit',
        tradeId,
        learningSchemaVersion: 2,
        pairId: 'pair-current',
        mint: 'mint-current',
        symbol: 'CURRENT',
        reason: 'trailing_stop',
        pnlLamports: 100_000_000,
        source: 'raydium-live',
        openedAt,
        exitPriceUsd: 1.2,
      },
    ];

    const observations = buildLearningObservations('v1.0', snapshots, decisions, at);
    expect(observations[1]).toMatchObject({ dex: 'raydium', source: 'raydium-live' });

    const storage = new OpportunityStorage(directory);
    expect(observations.map((record) => storage.log(record))).toEqual([false, true]);
    expect(storage.completedCount).toBe(1);
  });
});