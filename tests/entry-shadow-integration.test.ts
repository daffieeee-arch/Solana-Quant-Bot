import { describe, expect, it } from 'vitest';
import { evaluateEntryShadow } from '../src/entry-shadow.js';
import { buildPumpIdentity, buildAmmIdentity, buildClmmIdentity } from '../src/market-identity2.js';

const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const AMM = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const CLMM = 'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK';
const MINT = 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump';
const NOW = Date.now();
const TS = () => new Date(NOW).toISOString();

// geldige fixtures, verse market-data
const pumpId = buildPumpIdentity({ tradeId: 'p1', mint: MINT, programId: PUMP, curve: 'C'.repeat(44), baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS(), entryPriceSource: 'STREAM', strategyVersion: 'contra', schemaVersion: 1 })!;
const ammId = buildAmmIdentity({ tradeId: 'a1', mint: MINT, programId: AMM, marketId: 'P'.repeat(44), baseVault: 'B'.repeat(44), quoteVault: 'Q'.repeat(44), baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS(), entryPriceSource: 'STREAM', strategyVersion: 'contra', schemaVersion: 1 })!;
const clmmId = buildClmmIdentity({ tradeId: 'c1', mint: MINT, programId: CLMM, marketId: 'C2'.repeat(22), tokenVaultA: 'A'.repeat(44), tokenVaultB: 'B'.repeat(44), tickState: 'T'.repeat(44), baseDecimals: 6, quoteDecimals: 6, sourceTimestamp: TS(), entryPriceSource: 'STREAM', strategyVersion: 'contra', schemaVersion: 1 })!;

describe('entry-shadow runtime-integratie (protocol-fixtures)', () => {
  it.each([
    ['pump_bonding_curve', pumpId],
    ['amm_cpmm', ammId],
    ['clmm', clmmId],
  ])('WOULD_ACCEPT geldige %s identity', (_kind, id) => {
    const v = evaluateEntryShadow({ identity: id, decimals: { base: 6, quote: 9 }, marketFreshMs: 0, nowMs: NOW, maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_ACCEPT');
  });

  it('WOULD_REJECT gx:<mint>-only', () => {
    const bad = { ...pumpId, marketId: `gx:${MINT}`, bondCurve: `gx:${MINT}` };
    const v = evaluateEntryShadow({ identity: bad, decimals: { base: 6, quote: 9 }, marketFreshMs: 0, nowMs: NOW, maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
    expect(v.reasonCode).toBe('invalid_market_identity');
  });

  it('WOULD_REJECT ontbrekende decimals', () => {
    const v = evaluateEntryShadow({ identity: pumpId, decimals: undefined, marketFreshMs: 0, nowMs: NOW, maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
    expect(v.reasonCode).toBe('missing_decimals');
  });

  it('WOULD_REJECT stale market-data', () => {
    const stalePump = buildPumpIdentity({ tradeId: 'p2', mint: MINT, programId: PUMP, curve: 'C'.repeat(44), baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: new Date(NOW - 600_000).toISOString(), entryPriceSource: 'STREAM', strategyVersion: 'x', schemaVersion: 1 })!;
    const v = evaluateEntryShadow({ identity: stalePump, decimals: { base: 6, quote: 9 }, marketFreshMs: 600_000, nowMs: NOW, maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
    expect(v.reasonCode).toBe('stale_market_data');
  });

  it('WOULD_REJECT ontbrekende canonical identity', () => {
    const v = evaluateEntryShadow({ identity: undefined, decimals: { base: 6, quote: 9 }, marketFreshMs: 0, nowMs: NOW, maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
    expect(v.reasonCode).toBe('missing_canonical_market_identity');
  });
});
