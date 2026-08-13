import { describe, expect, it } from 'vitest';
import { buildPumpIdentity } from '../src/market-identity2.js';
import { evaluateEntryShadow } from '../src/entry-shadow.js';
import { buildIdentityFromGeneric } from '../src/market-identity-upstream.js';

const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const MINT = 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump';
const CURVE = 'C'.repeat(44);
const NOW = Date.now();
const TS = () => new Date(NOW).toISOString();

describe('MarketIdentity serialization/persistence + evaluator-integratie', () => {
  it('serieel/persist-eert de identity intact (JSON round-trip + kind behouden)', () => {
    const id = buildPumpIdentity({ tradeId: 't1', mint: MINT, programId: PUMP, curve: CURVE, baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS(), entryPriceSource: 'STREAM', strategyVersion: 'contra', schemaVersion: 1 })!;
    const roundtrip = JSON.parse(JSON.stringify(id));
    expect(roundtrip.kind).toBe('pump_bonding_curve');
    expect(roundtrip.marketId).toBe(CURVE);
    expect(roundtrip.baseDecimals).toBe(6);
    expect(roundtrip.quoteDecimals).toBe(9);
  });

  it('identity blijft intact door normalisation (niet herleid uit alleen mint/gx)', () => {
    const id = buildPumpIdentity({ tradeId: 't2', mint: MINT, programId: PUMP, curve: CURVE, baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS(), entryPriceSource: 'STREAM', strategyVersion: 'x', schemaVersion: 1 })!;
    // de identity draagt canonical marketId (curve), NIET gx:<mint>
    expect(id.marketId).toBe(CURVE);
    expect(id.marketId.toLowerCase()).not.toBe(`gx:${MINT.toLowerCase()}`);
    // generic zonder curve valt NIET terug naar gx:mint
    const gen = buildIdentityFromGeneric({ tradeId: 't3', mint: MINT, curve: undefined, programId: '', baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS(), entryPriceSource: 'STREAM' });
    expect((gen as { reason?: string }).reason).toBe('no_canonical_pool_identity_in_event');
  });

  it('evaluator gebruikt de identity + decimals (WOULD_ACCEPT) en weigert missende velden fail-closed', () => {
    const id = buildPumpIdentity({ tradeId: 't4', mint: MINT, programId: PUMP, curve: CURVE, baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS(), entryPriceSource: 'STREAM', strategyVersion: 'x', schemaVersion: 1 })!;
    const ok = evaluateEntryShadow({ identity: id, decimals: { base: 6, quote: 9 }, marketFreshMs: 0, nowMs: NOW, maxAgeMs: 120_000 });
    expect(ok.verdict).toBe('WOULD_ACCEPT');
    // missende decimals → fail-closed reject
    const noDec = evaluateEntryShadow({ identity: id, decimals: undefined, marketFreshMs: 0, nowMs: NOW, maxAgeMs: 120_000 });
    expect(noDec).toEqual({ verdict: 'WOULD_REJECT', reasonCode: 'missing_decimals' });
  });

  it('decimals blijven intact door de identity-objecten (niet herleid/niet verloren)', () => {
    const id = buildPumpIdentity({ tradeId: 't5', mint: MINT, programId: PUMP, curve: CURVE, baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS(), entryPriceSource: 'STREAM', strategyVersion: 'x', schemaVersion: 1 })!;
    expect(id.baseDecimals).toBe(6);
    expect(id.quoteDecimals).toBe(9);
  });
});
