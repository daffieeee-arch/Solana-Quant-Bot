import { describe, it, expect } from 'vitest';
import { spotPriceUsd, usdPerQuoteUnit, WSOL_MINT, USDC_MINT, USDT_MINT } from '../src/stream-price.js';
import type { PoolDepth } from '../src/scoring.js';

const SOL_PRICE = 150;

describe('spotPriceUsd', () => {
  it('berekent fair-value prijs uit reserves (constant-product ratio, geen impact)', () => {
    // 1e18 base tokens(9dec) = 1e9 token; 7.5e15 quote lamports(9dec) = 7.5e6 SOL
    // price = 7.5e6 SOL / 1e9 token = 0.0075 SOL/token → *150 = 1.125 USD/token
    const depth: PoolDepth = { baseReserve: 1e18, quoteReserve: 7.5e15, baseDecimals: 9, quoteDecimals: 9 };
    expect(spotPriceUsd(depth, SOL_PRICE)).toBeCloseTo(1.125, 6);
  });

  it('corrigeert voor gemengde decimalen: USDC(6dec) pool prijs in USD/token', () => {
    // 1.0 USDC quote (=1_000_000 raw 6dec) tegen 1.0 token base (=1e9 raw 9dec)
    // → prijs moet 1 USD/token zijn. Zonder decimalen-correctie zou dit 1e-3 zijn (fout).
    const depth: PoolDepth = { baseReserve: 1e9, quoteReserve: 1_000_000, baseDecimals: 9, quoteDecimals: 6 };
    expect(spotPriceUsd(depth, 1)).toBeCloseTo(1.0, 6);
  });

  it('quote-currency: usdPerQuoteUnit verschilt voor WSOL vs USDC vs USDT', () => {
    expect(usdPerQuoteUnit(WSOL_MINT, SOL_PRICE)).toBe(SOL_PRICE);
    expect(usdPerQuoteUnit(USDC_MINT, SOL_PRICE)).toBe(1);
    expect(usdPerQuoteUnit(USDT_MINT, SOL_PRICE)).toBe(1);
    expect(usdPerQuoteUnit('some-unknown-mint', SOL_PRICE)).toBeNull();
    expect(usdPerQuoteUnit(WSOL_MINT, 0)).toBeNull(); // solPrice onbekend → fail-closed
  });

  it('geeft null bij ongeldige reserves (fail-closed)', () => {
    expect(spotPriceUsd({ baseReserve: 0, quoteReserve: 5, baseDecimals: 9, quoteDecimals: 9 }, SOL_PRICE)).toBeNull();
    expect(spotPriceUsd({ baseReserve: 1, quoteReserve: -2, baseDecimals: 9, quoteDecimals: 9 }, SOL_PRICE)).toBeNull();
    expect(spotPriceUsd({ baseReserve: 1, quoteReserve: 5, baseDecimals: 9, quoteDecimals: 9 }, 0)).toBeNull();
  });
});
