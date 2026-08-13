import { describe, expect, it } from 'vitest';
import { buildPumpIdentityFromDecode, buildAmmIdentityFromDecode, buildIdentityFromGeneric } from '../src/market-identity-upstream.js';

const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const AMM = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const CPMM = 'CPMMoo8L3Fn4zpT5D8iJZK8c1mCQMTaBQYc7ZcfcRU4';
const MINT = 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump';
const CURVE = 'C'.repeat(44);
const TS = '2026-08-13T12:00:00.000Z';

describe('MarketIdentity upstream wiring', () => {
  it('decoded Pump.fun event (curve) → complete pump_bonding_curve identity', () => {
    const id = buildPumpIdentityFromDecode({ tradeId: 'p1', mint: MINT, programId: PUMP, curve: CURVE, baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS, entryPriceSource: 'STREAM' });
    expect(id).not.toBeNull();
    expect(id!.kind).toBe('pump_bonding_curve');
    expect(id!.bondCurve).toBe(CURVE);
    expect(id!.marketId).toBe(CURVE);
  });

  it('decoded AMMv4 event (vaults) → complete amm_cpmm identity', () => {
    const id = buildAmmIdentityFromDecode({
      tradeId: 'a1', mint: MINT, programId: AMM, marketId: 'P'.repeat(44),
      baseVault: 'B'.repeat(44), quoteVault: 'Q'.repeat(44), baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS, entryPriceSource: 'STREAM',
    });
    expect(id).not.toBeNull();
    expect(id!.kind).toBe('amm_cpmm');
    expect(id!.baseVault).toBe('B'.repeat(44));
    expect(id!.quoteVault).toBe('Q'.repeat(44));
  });

  it('decoded CPMM event (vaultA/vaultB) → complete amm_cpmm identity', () => {
    const id = buildAmmIdentityFromDecode({
      tradeId: 'a2', mint: MINT, programId: CPMM, marketId: 'L'.repeat(44),
      baseVault: 'B'.repeat(44), quoteVault: 'Q'.repeat(44), baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS, entryPriceSource: 'STREAM',
    });
    expect(id).not.toBeNull();
    expect(id!.kind).toBe('amm_cpmm');
  });

  it('missende curve/vault voert NOOIT terug naar gx:<mint> (identity = null)', () => {
    const pumpNoCurve = buildPumpIdentityFromDecode({ tradeId: 'p2', mint: MINT, programId: PUMP, curve: '', baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS, entryPriceSource: 'STREAM' });
    expect(pumpNoCurve).toBeNull();
    const ammNoVault = buildAmmIdentityFromDecode({ tradeId: 'a3', mint: MINT, programId: AMM, marketId: 'P'.repeat(44), baseVault: '', quoteVault: '', baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS, entryPriceSource: 'STREAM' });
    expect(ammNoVault).toBeNull();
  });

  it('generic multidex zonder curve/vault → identity undefined (geen canonical pool) + markering', () => {
    const r = buildIdentityFromGeneric({ tradeId: 'x', mint: MINT, curve: undefined, programId: '', baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: TS, entryPriceSource: 'STREAM' });
    // geen canonical market identity in generieke txn → undefined; dit is het CLMM/onbekende-veld-geval
    expect(r.identity).toBeUndefined();
    expect(r.reason).toBe('no_canonical_pool_identity_in_event');
  });
});
