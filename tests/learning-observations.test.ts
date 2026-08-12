import { describe, expect, it } from 'vitest';
import { buildLearningObservations } from '../src/learning-observations.js';
import type { ScanDecision } from '../src/scanner.js';
import type { MarketSnapshot } from '../src/scoring.js';

const at = '2026-07-29T10:10:00.000Z';
const snapshots: MarketSnapshot[] = [{
  pairId: 'entry-pair', mint: 'entry-mint', symbol: 'ENTRY', source: 'birdeye',
  observedAt: at, pairCreatedAt: '2026-07-29T10:00:00.000Z', priceUsd: 1, liquidityUsd: 50_000, volumeM5Usd: 10_000,
  priceChangeM5Percent: 10, buysM5: 20, sellsM5: 5,
}, {
  pairId: 'legacy-pair', mint: 'legacy-mint', symbol: 'LEGACY', source: 'birdeye',
  observedAt: at, pairCreatedAt: '2026-07-29T09:00:00.000Z', priceUsd: 0.8, liquidityUsd: 10_000, volumeM5Usd: 2_000,
  priceChangeM5Percent: -5, buysM5: 5, sellsM5: 10,
}];

describe('learning observation provenance', () => {
  it('preserves the scanner schema marker on current entries and legacy exits', () => {
    const decisions: ScanDecision[] = [
      {
        type: 'paper_entry', tradeId: `entry-mint:${at}`, learningSchemaVersion: 2,
        pairId: 'entry-pair', mint: 'entry-mint', symbol: 'ENTRY', score: 80,
        source: 'birdeye', coverage: 'best_effort', openedAt: at, entryPriceUsd: 1.01,
        pairCreatedAt: '2026-07-29T10:00:00.000Z', firstSeenAt: at, observedAt: at, evaluatedAt: at,
      },
      {
        type: 'paper_exit', tradeId: 'legacy-mint:2026-07-29T10:00:00.000Z',
        pairId: 'legacy-pair', mint: 'legacy-mint', symbol: 'LEGACY', reason: 'stop_loss',
        pnlLamports: -100_000_000, source: 'birdeye', openedAt: '2026-07-29T10:00:00.000Z',
        exitPriceUsd: 0.8,
      },
    ];

    const records = buildLearningObservations('v1.0', snapshots, decisions, at);
    const entry = records.find((record) => record.decision === 'entry');
    const legacyExit = records.find((record) => record.decision === 'exit');

    expect(entry).toMatchObject({ strategyId: 'v1.0' });
    expect(legacyExit).toMatchObject({ strategyId: 'v1.0' });
    expect(entry?.decision).toBe('entry');
    expect(legacyExit?.decision).toBe('exit');
  });
});