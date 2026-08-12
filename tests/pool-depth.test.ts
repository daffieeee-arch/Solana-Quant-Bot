import { describe, it, expect } from 'vitest';
import {
  constantProductBuyPrice,
  constantProductSellPrice,
  usdToQuoteRaw,
  rawBaseToUnits,
  rawQuoteToUsd,
} from '../src/pool-depth.js';
import type { PoolDepth } from '../src/scoring.js';

// Raydium AMMv4 pool: base token 9 decimals, quote WSOL 9 decimals.
// quoteReserve=150e9 (150 SOL), baseReserve=1e18 (1e9 tokens).
const depth: PoolDepth = {
  baseReserve: 1_000_000_000 * 10 ** 9, // 1e18 raw = 1e9 tokens
  quoteReserve: 150 * 1e9,               // 1.5e11 raw = 150 SOL
  baseDecimals: 9,
  quoteDecimals: 9,
  feeNumerator: 25,
  feeDenominator: 10_000,
};
const SOL = 150;
// Mid = quoteReserve/baseReserve (decimals cancel) = 1.5e-7 SOL/token
const MID_SOL_PER_TOKEN = depth.quoteReserve / depth.baseReserve;

describe('constantProductBuyPrice', () => {
  it('returns the mid price for an infinitesimal buy', () => {
    const execRaw = constantProductBuyPrice(depth, 1)!; // 1 lamport
    expect(execRaw).toBeCloseTo(MID_SOL_PER_TOKEN, 4);
  });

  it('yields a worse (higher) price for a large buy due to slippage + fee', () => {
    const execRaw = constantProductBuyPrice(depth, 5 * 1e9)!; // 5 SOL
    expect(execRaw).toBeGreaterThan(MID_SOL_PER_TOKEN);
  });

  it('scales impact with size: bigger buy → higher exec price', () => {
    const small = constantProductBuyPrice(depth, 1 * 1e9)!;
    const big = constantProductBuyPrice(depth, 20 * 1e9)!;
    expect(big).toBeGreaterThan(small);
  });

  it('returns null on invalid depth or zero input', () => {
    expect(constantProductBuyPrice(depth, 0)).toBeNull();
    expect(constantProductBuyPrice(depth, -1)).toBeNull();
    expect(constantProductBuyPrice({ ...depth, baseReserve: 0 }, 1e9)).toBeNull();
    expect(constantProductBuyPrice({ ...depth, quoteReserve: -5 }, 1e9)).toBeNull();
  });
});

describe('constantProductSellPrice', () => {
  it('returns a lower (worse) exec price for a large sell of base tokens', () => {
    const baseInRaw = 20_000_000 * 1e9; // 20M base tokens
    const execRaw = constantProductSellPrice(depth, baseInRaw)!;
    expect(execRaw).toBeLessThan(MID_SOL_PER_TOKEN); // adverse for seller
  });
});

describe('conversion helpers', () => {
  it('usdToQuoteRaw converts USD to WSOL raw lamports', () => {
    const sol = usdToQuoteRaw(30, 9, SOL); // $30 / $150 = 0.2 SOL = 2e8 lamports
    expect(sol).toBeCloseTo(0.2 * 1e9, 4);
  });

  it('rawQuoteToUsd converts quote-per-base ratio to USD price', () => {
    // 1.2e-7 SOL/token * 150 = 1.8e-5 USD/token
    expect(rawQuoteToUsd(1.2e-7, 9, SOL)).toBeCloseTo(1.8e-5, 8);
  });

  it('rawBaseToUnits scales raw base by decimals', () => {
    expect(rawBaseToUnits(2_000_000 * 1e9, 9)).toBe(2_000_000);
  });
});

describe('end-to-end price impact realism (real captured pump.fun pool)', () => {
  it('a $30 entry into a real thin pump pool registers material slippage', () => {
    // REAL Pump.fun bonding-curve state captured live 2026-08-06:
    //   virtualTokenReserves=421077467918487, virtualSolReserves=76446741519
    const thin: PoolDepth = {
      baseReserve: 421_077_467_918_487,
      quoteReserve: 76_446_741_519,
      baseDecimals: 9,
      quoteDecimals: 9,
      bondingCurve: true,
      feeNumerator: 25,
      feeDenominator: 10_000,
    };
    const solUsd = 150;
    const quoteInRaw = usdToQuoteRaw(30, 9, solUsd); // $30 → 2e8 lamports
    const midUsd = rawQuoteToUsd(thin.quoteReserve / thin.baseReserve, 9, solUsd);
    const execRaw = constantProductBuyPrice(thin, quoteInRaw)!;
    const execUsd = rawQuoteToUsd(execRaw, 9, solUsd);
    expect(execUsd).toBeGreaterThan(midUsd);
    const impactPct = ((execUsd - midUsd) / midUsd) * 100;
    expect(impactPct).toBeGreaterThan(0.01); // real slippage in thin pool
  });
});
