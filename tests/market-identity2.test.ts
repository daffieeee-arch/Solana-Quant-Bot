import { describe, expect, it } from 'vitest';
import { buildPumpIdentity, buildAmmIdentity, buildClmmIdentity, isCompleteIdentity } from '../src/market-identity2.js';

const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const MINT = 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump';

describe('protocol-specifieke MarketIdentity (discriminated)', () => {
  it('bouwt een complete Pump.fun bonding-curve identity', () => {
    const id = buildPumpIdentity({
      tradeId: 't1', mint: MINT, programId: PUMP, curve: 'C'.repeat(44),
      baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: '2026-08-13T12:00:00.000Z',
      entryPriceSource: 'STREAM', strategyVersion: 'contra', schemaVersion: 1,
    });
    expect(id!).not.toBeNull();
    expect(id!.kind).toBe('pump_bonding_curve');
    expect(isCompleteIdentity(id!)).toBe(true);
    // gx:<mint>-only is GEEN complete identity
    expect(isCompleteIdentity({ ...id!, marketId: `gx:${MINT}` } as never)).toBe(false);
  });

  it('bouwt een complete AMM/CPMM identity met vaults', () => {
    const id = buildAmmIdentity({
      tradeId: 't2', mint: MINT, programId: '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8',
      marketId: 'P'.repeat(44), baseVault: 'B'.repeat(44), quoteVault: 'Q'.repeat(44),
      baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: '2026-08-13T12:00:00.000Z',
      entryPriceSource: 'STREAM', strategyVersion: 'contra', schemaVersion: 1,
    });
    expect(id).not.toBeNull();
    expect(id!.kind).toBe('amm_cpmm');
    expect(isCompleteIdentity(id!)).toBe(true);
  });

  it('bouwt een complete CLMM identity met tick-state', () => {
    const id = buildClmmIdentity({
      tradeId: 't3', mint: MINT, programId: 'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK',
      marketId: 'C2'.repeat(22), tokenVaultA: 'A'.repeat(44), tokenVaultB: 'B'.repeat(44),
      tickState: 'T'.repeat(44), baseDecimals: 6, quoteDecimals: 6,
      sourceTimestamp: '2026-08-13T12:00:00.000Z', entryPriceSource: 'STREAM', strategyVersion: 'contra', schemaVersion: 1,
    });
    expect(id).not.toBeNull();
    expect(id!.kind).toBe('clmm');
    expect(isCompleteIdentity(id!)).toBe(true);
  });

  it('weigert onvolledige identity (ontbrekende curve/vault)', () => {
    const incomplete = buildPumpIdentity({
      tradeId: 't4', mint: MINT, programId: PUMP, curve: '', baseDecimals: 6, quoteDecimals: 9,
      sourceTimestamp: '2026-08-13T12:00:00.000Z', entryPriceSource: 'STREAM', strategyVersion: 'x', schemaVersion: 1,
    });
    expect(incomplete).toBeNull();
  });
});