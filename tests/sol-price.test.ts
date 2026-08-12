import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { withFreshSolPrice } from '../src/sol-price.js';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data', SOL_PRICE_USD: '99',
});
const now = new Date('2026-07-28T12:00:00.000Z');

describe('withFreshSolPrice', () => {
  it('applies a finite positive SOL price from fresh market context', () => {
    const result = withFreshSolPrice(config, {
      updatedAt: '2026-07-28T11:59:00.000Z', ticker: [{ symbol: 'SOL', name: 'Solana', priceUsd: 123, change24hPercent: 0 }], news: [],
    }, now);
    expect(result.solPriceUsd).toBe(123);
  });

  it.each([
    ['missing context', undefined],
    ['stale context', { updatedAt: '2026-07-28T11:54:59.000Z', ticker: [{ symbol: 'SOL', name: 'Solana', priceUsd: 123, change24hPercent: 0 }], news: [] }],
    ['invalid price', { updatedAt: '2026-07-28T11:59:00.000Z', ticker: [{ symbol: 'SOL', name: 'Solana', priceUsd: 0, change24hPercent: 0 }], news: [] }],
  ])('clears a previously cached rate for %s', (_label, context) => {
    expect(withFreshSolPrice(config, context, now).solPriceUsd).toBeUndefined();
  });
});
