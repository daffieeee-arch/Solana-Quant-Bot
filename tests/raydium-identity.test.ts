import { describe, expect, it } from 'vitest';
import { buildAmmIdentityFromDecode } from '../src/market-identity-upstream.js';

const AMMv4 = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const CPMM = 'CPMMoo8L3Fn4zpT5D8iJZK8c1mCQMTaBQYc7ZcfcRU4';
const MINT = 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump';
const TS = '2026-08-13T12:00:00.000Z';

describe('Raydium canonical pool identity (marketId = pool-state, niet lpMint)', () => {
  it('AMMv4: marketId = echt AmmInfo/pool-account, lpMint blijft apart', () => {
    // Het pool/AMM-account is een echte pool-state-account (niet de LP-mint)
    const poolId = 'AmmInfoV4PoolAccountXXXXXXXXXXXXXXXXXXXXXXXXX';
    const lpMint = 'LpMintXXXXXXXXXXXXXXTokenXXXXXXXXXXXXXXXX';
    const id = buildAmmIdentityFromDecode({
      tradeId: 'a1', mint: MINT, programId: AMMv4, marketId: poolId, lpMint,
      baseVault: 'B'.repeat(44), quoteVault: 'Q'.repeat(44), baseDecimals: 6, quoteDecimals: 9,
      sourceTimestamp: TS, entryPriceSource: 'STREAM',
    });
    expect(id).not.toBeNull();
    expect(id!.kind).toBe('amm_cpmm');
    expect(id!.marketId).toBe(poolId);
    expect(id!.marketId).not.toBe(lpMint); // NOT lpMint
  });

  it('CPMM: marketId = PoolState-account, lpMint apart', () => {
    const poolState = 'PoolStateVAAAXXXXXXXXXXXXPoolAccount';
    const lpMint = 'CPMMLpTokenMintYYYYYYYYYYYYYYYYYYYY';
    const id = buildAmmIdentityFromDecode({
      tradeId: 'c1', mint: MINT, programId: CPMM, marketId: poolState, lpMint,
      baseVault: 'V0VaultAAAAAAAAAAAA', quoteVault: 'V1VaultBBBBBBBBBBBB', baseDecimals: 6, quoteDecimals: 6,
      sourceTimestamp: TS, entryPriceSource: 'STREAM',
    });
    expect(id).not.toBeNull();
    expect(id!.marketId).toBe(poolState);
    expect(id!.marketId).not.toBe(lpMint);
  });
});