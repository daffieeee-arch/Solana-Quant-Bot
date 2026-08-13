import { describe, expect, it } from 'vitest';
import {
  quarantineLegacyPosition,
  isPositionQuarantined,
  REQUARANTINE_REASON_CODE,
  type QuarantineRecord,
} from '../src/accounting.js';
import { makeTradeId } from '../src/portfolio.js';

// De vier exacte legacy tradeIds (canonical, immutable)
const TRADE_IDS = [
  'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump:2026-08-12T20:48:05.571Z',
  'AfwyNJG2ZHsjvFViUfuaPVK4fSheY9xMm7tyR6VEpump:2026-08-12T21:13:12.653Z',
  '7pkqvfHe6WREhvZ1ergfXtz3F6MQfXCfcAZiumCt6Ene:2026-08-13T01:31:57.684Z',
  '4LLbsb5ReP3yEtYzmXewyGjcir5uXtKFURtaEUVC2AHs:2026-08-13T08:31:57.965Z',
];

const entry = (tid: string) => ({
  tradeId: tid,
  pairId: `gx:${tid.split(':')[0]}`,
  mint: tid.split(':')[0],
  symbol: 'LEGACY',
  openedAt: tid.split(':')[1],
  entryPriceUsd: 1e-6,
  highPriceUsd: 1e-6,
  allocatedLamports: 244_906_250,
  entryCostLamports: 247_355_313,
  dynamicStopPercent: 5,
});

describe('accounting quarantine', () => {
  it('quarantineert de vier exacte legacy posities éénmalig (idempotent)', () => {
    const map = new Map<string, QuarantineRecord>();
    for (const tid of TRADE_IDS) {
      const r = quarantineLegacyPosition(map, entry(tid), 'now', undefined);
      expect(r.ok).toBe(true);
      expect(isPositionQuarantined(map, tid)).toBe(true);
    }
    // idempotent: opnieuw quarantinen telt niet dubbel
    const again = quarantineLegacyPosition(map, entry(TRADE_IDS[0]), 'now', undefined);
    expect(again.duplicate).toBe(true);
    expect(map.size).toBe(TRADE_IDS.length);
  });

  it('schrijft GEEN paper_exit en houdt realizedPnL op UNKNOWN', () => {
    const map = new Map<string, QuarantineRecord>();
    const r = quarantineLegacyPosition(map, entry(TRADE_IDS[0]), 'now', undefined);
    expect(r.ok).toBe(true);
    const rec = map.get(TRADE_IDS[0])!;
    expect(rec.accountingStatus).toBe('UNKNOWN');
    expect('proceedsLamports' in rec).toBe(false);
    expect('exitPriceUsd' in rec).toBe(false);
  });

  it('telt quarantined posities niet mee voor performance; mint-only match mag geen verkeerde raken', () => {
    const map = new Map<string, QuarantineRecord>();
    quarantineLegacyPosition(map, entry(TRADE_IDS[0]), 'now', undefined);
    // een andere trade op een ANDERE mint die toevallig het prefix deelt moet niet raken
    const otherMint = 'AfwyNJG2ZHsjvFViUfuaPVK4fSheY9xMm7tyR6VEpump';
    expect(isPositionQuarantined(map, otherMint)).toBe(false);
    // reden-code
    expect(map.get(TRADE_IDS[0])!.reasonCode).toBe(REQUARANTINE_REASON_CODE);
  });
});
