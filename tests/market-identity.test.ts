import { describe, expect, it } from 'vitest';
import type { MarketIdentity } from '../src/market-identity.js';
import {
  buildMarketIdentity,
  validateMarketIdentity,
  hasUsableExitRoute,
  EntryGateResult,
  requireCanonicalMarketIdentity,
  requireRecentMarketActivity,
} from '../src/market-identity.js';

const PUMP_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const MINT = 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump';

describe('MarketIdentity + fail-closed entry contract', () => {
  it('accepteert een geldige Pump.fun bonding-curve identity', () => {
    const id: MarketIdentity = {
      tradeId: 't1',
      mint: MINT,
      protocol: 'pump',
      programId: PUMP_PROGRAM,
      marketType: 'pump_bonding_curve',
      marketId: 'gx-bondingcurve:' + 'C'.repeat(44),
      bondCurve: 'C'.repeat(44),
      baseMint: MINT,
      quoteMint: 'So11111111111111111111111111111111111111112',
      baseDecimals: 6,
      quoteDecimals: 9,
      entryTimestamp: new Date().toISOString(),
      entryPriceSource: 'STREAM',
      strategyVersion: 'contra-audit15',
      schemaVersion: 1,
    };
    expect(validateMarketIdentity(id)).toBe(true);
    expect(hasUsableExitRoute(id)).toBe(true);
  });

  it('weigert een gx:<mint>-only identity (geen canonical market/curve)', () => {
    const bad: MarketIdentity = {
      tradeId: 't2',
      mint: MINT,
      protocol: 'pump',
      programId: PUMP_PROGRAM,
      marketType: 'pump_bonding_curve',
      marketId: `gx:${MINT}`, // mint-only, geen curve
      baseMint: MINT,
      quoteMint: 'So11111111111111111111111111111111111111112',
      baseDecimals: 6,
      quoteDecimals: 9,
      entryTimestamp: new Date().toISOString(),
      entryPriceSource: 'STREAM',
      strategyVersion: 'x',
      schemaVersion: 1,
    };
    expect(validateMarketIdentity(bad)).toBe(false);
  });

  it('weigert entry zonder decimals', () => {
    const noDec: MarketIdentity = {
      tradeId: 't3', mint: MINT, protocol: 'pump', programId: PUMP_PROGRAM, marketType: 'pump_bonding_curve',
      marketId: 'C'.repeat(44), bondCurve: 'C'.repeat(44),
      baseMint: MINT, quoteMint: 'So...', baseDecimals: 0, quoteDecimals: 9,
      entryTimestamp: new Date().toISOString(), entryPriceSource: 'STREAM', strategyVersion: 'x', schemaVersion: 1,
    };
    expect(validateMarketIdentity(noDec)).toBe(false);
  });

  it('weigert entry bij stale market data (activity ouder dan max age)', () => {
    const id = buildMarketIdentity({ tradeId: 't4', mint: MINT, protocol: 'pump', programId: PUMP_PROGRAM, marketType: 'pump_bonding_curve', marketId: 'C'.repeat(44), bondCurve: 'C'.repeat(44), baseMint: MINT, quoteMint: 'So11111111111111111111111111111111111111112', baseDecimals: 6, quoteDecimals: 9, entryTimestamp: new Date().toISOString(), marketEventTimestamp: new Date(Date.now() - 600_000).toISOString() }) as MarketIdentity;
    const now = Date.now();
    const gate: EntryGateResult = requireRecentMarketActivity(id, now, 120_000);
    expect(gate.ok).toBe(false);
  });

  it('fail-closed entry contract: ontbrekende canonical identity blokkeert', () => {
    const res = requireCanonicalMarketIdentity({ tradeId: 'x', mint: MINT, protocol: 'pump' } as MarketIdentity);
    expect(res.ok).toBe(false);
  });
});
