import { describe, expect, it } from 'vitest';
import { evaluateEntryShadow, type ShadowVerdict } from '../src/entry-shadow.js';
import { buildPumpIdentity } from '../src/market-identity2.js';

const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const MINT = 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump';

describe('entry shadow-mode', () => {
  it('WOULD_ACCEPT een geldige complete identity', () => {
    const id = buildPumpIdentity({ tradeId: 't1', mint: MINT, programId: PUMP, curve: 'C'.repeat(44), baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: new Date().toISOString(), entryPriceSource: 'STREAM', strategyVersion: 'contra', schemaVersion: 1 })!;
    const v: ShadowVerdict = evaluateEntryShadow({ identity: id, decimals: { base: 6, quote: 9 }, marketFreshMs: 0, nowMs: Date.now(), maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_ACCEPT');
    expect(v.reasonCode).toBeUndefined();
  });

  it('WOULD_REJECT een gx:<mint>-only / ontbrekende identity', () => {
    const v: ShadowVerdict = evaluateEntryShadow({ identity: undefined, decimals: { base: 6, quote: 9 }, marketFreshMs: 0, nowMs: Date.now(), maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
    expect(v.reasonCode).toBe('missing_canonical_market_identity');
  });

  it('WOULD_REJECT stale market data', () => {
    const id = buildPumpIdentity({ tradeId: 't2', mint: MINT, programId: PUMP, curve: 'C'.repeat(44), baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: new Date(Date.now() - 600_000).toISOString(), entryPriceSource: 'STREAM', strategyVersion: 'x', schemaVersion: 1 })!;
    const v: ShadowVerdict = evaluateEntryShadow({ identity: id, decimals: { base: 6, quote: 9 }, marketFreshMs: 600_000, nowMs: Date.now(), maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
    expect(v.reasonCode).toBe('stale_market_data');
  });

  it('WOULD_REJECT ontbrekende decimals', () => {
    const id = buildPumpIdentity({ tradeId: 't3', mint: MINT, programId: PUMP, curve: 'C'.repeat(44), baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: new Date().toISOString(), entryPriceSource: 'STREAM', strategyVersion: 'x', schemaVersion: 1 })!;
    const v: ShadowVerdict = evaluateEntryShadow({ identity: id, decimals: undefined, marketFreshMs: 0, nowMs: Date.now(), maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
    expect(v.reasonCode).toBe('missing_decimals');
  });
});
