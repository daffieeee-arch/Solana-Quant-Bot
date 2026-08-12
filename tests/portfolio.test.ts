import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { createPortfolio, enterPaperPosition, evaluateOpenPosition } from '../src/portfolio.js';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
  MIN_MOMENTUM_SCORE: '35',
  MIN_STOP_LOSS_PERCENT: '5', MAX_STOP_LOSS_PERCENT: '25', STOP_VOLATILITY_MULTIPLIER: '1.5',
  BREAKEVEN_TRIGGER_PERCENT: '8', MIN_PROFIT_FOR_CONTINUE_PERCENT: '3',
  POSITION_SCORE_DIVISOR: '100', LIQUIDITY_POSITION_FRACTION: '0.02',
  SOL_PRICE_USD: '100',
});

const entryInput50 = { pairId: 'pair-1', mint: 'mint-1', symbol: 'PUMP', priceUsd: 1, at: '2026-07-24T00:01:00.000Z', score: 50, liquidityUsd: 50_000, priceChangeM5Percent: 20 };

describe('paper portfolio', () => {
  it('deducts a conservative virtual entry cost and rejects a duplicate mint', () => {
    const initial = createPortfolio(config, '2026-07-24T00:00:00.000Z');
    const entered = enterPaperPosition(initial, entryInput50, config);
    expect(entered.ok).toBe(true);
    if (!entered.ok) return;
    expect(entered.portfolio.availableLamports).toBeLessThan(initial.availableLamports);
    expect(entered.position).toMatchObject({ pairId: 'pair-1', mint: 'mint-1' });
    expect(enterPaperPosition(entered.portfolio, { ...entryInput50, score: 50 }, config)).toEqual({
      ok: false, reason: 'duplicate_position',
    });
  });

  it('closes an open position at a conservative simulated stop-loss fill', () => {
    const entered = enterPaperPosition(createPortfolio(config, '2026-07-24T00:00:00.000Z'), entryInput50, config);
    if (!entered.ok) throw new Error('expected paper entry');
    const result = evaluateOpenPosition(entered.portfolio, { pairId: 'pair-1', mint: 'mint-1' }, 0.01, '2026-07-24T00:02:00.000Z', config);
    expect(result.event).toMatchObject({ type: 'exit', reason: 'stop_loss' });
    expect(result.portfolio.positions).toHaveLength(0);
    expect(result.portfolio.dailyRealizedLossLamports).toBeGreaterThan(0);
  });

  it('enforces the concurrent-position cap', () => {
    const one = enterPaperPosition(createPortfolio(config, '2026-07-24T00:00:00.000Z'), { ...entryInput50, mint: 'a', symbol: 'A' }, config);
    if (!one.ok) throw new Error('expected first entry');
    const two = enterPaperPosition(one.portfolio, { ...entryInput50, mint: 'b', symbol: 'B' }, config);
    if (!two.ok) throw new Error('expected second entry');
    expect(enterPaperPosition(two.portfolio, { ...entryInput50, mint: 'c', symbol: 'C' }, config)).toEqual({
      ok: false, reason: 'max_concurrent_positions',
    });
  });

  it('resets and persists the daily loss counter on the first event of a new UTC day', () => {
    const previousDay = {
      ...createPortfolio(config, '2026-07-24T00:00:00.000Z'),
      dailyRealizedLossLamports: 500_000_000,
      dailyLossDateUtc: '2026-07-24',
    };
    const entered = enterPaperPosition(previousDay, { ...entryInput50, at: '2026-07-25T00:01:00.000Z' }, config);
    if (!entered.ok) throw new Error('expected entry after UTC reset');
    expect(entered.portfolio.dailyRealizedLossLamports).toBe(0);
    expect(entered.portfolio.dailyLossDateUtc).toBe('2026-07-25');
  });

  it('caps position allocation using pool liquidity in USD converted by SOL/USD', () => {
    const liquidityConfig = { ...config, maxPositionSol: 1, positionScoreDivisor: 1, solPriceUsd: 100 };
    const entered = enterPaperPosition(createPortfolio(liquidityConfig, '2026-07-24T00:00:00.000Z'), {
      ...entryInput50,
      priceUsd: 0.0001,
      score: 100,
      liquidityUsd: 1_000,
    }, liquidityConfig);
    if (!entered.ok) throw new Error('expected entry');
    expect(entered.position.allocatedLamports).toBe(200_000_000); // 2% of $1,000 / $100 per SOL = 0.2 SOL
  });

  it('skips the liquidity cap when SOL/USD is unavailable (i.p.v. position_sized_to_zero — die verlamde de bot)', () => {
    const noSolPrice = { ...config, solPriceUsd: undefined };
    const result = enterPaperPosition(createPortfolio(noSolPrice, '2026-07-24T00:00:00.000Z'), entryInput50, noSolPrice);
    expect(result.ok).toBe(true);
    if (result.ok) {
      // Score-gewogen omvang zonder liquidity-cap: score 50/divisor 100 = 50% van max 0.25 SOL = 0.125 SOL
      expect(result.position.allocatedLamports).toBe(125_000_000);
    }
  });

  it('rejects entry with position_sized_to_zero when score is 0', () => {
    const portfolio = createPortfolio(config, '2026-07-24T00:00:00.000Z');
    expect(enterPaperPosition(portfolio, { ...entryInput50, score: 0 }, config)).toEqual({
      ok: false, reason: 'position_sized_to_zero',
    });
  });

  it('never widens a volatility-adjusted stop beyond the configured hard maximum', () => {
    const entered = enterPaperPosition(createPortfolio(config, '2026-07-24T00:00:00.000Z'), { ...entryInput50, priceChangeM5Percent: 22 }, config);
    if (!entered.ok) throw new Error('expected entry');
    expect(entered.position.dynamicStopPercent).toBe(config.maxStopLossPercent);
  });

  it('keeps the grace-period stop within the configured hard maximum', () => {
    const entered = enterPaperPosition(createPortfolio(config, '2026-07-24T00:00:00.000Z'), { ...entryInput50, priceChangeM5Percent: 100 }, config);
    if (!entered.ok) throw new Error('expected entry');
    const result = evaluateOpenPosition(entered.portfolio, { pairId: 'pair-1', mint: 'mint-1' }, 0.75, '2026-07-24T00:01:30.000Z', config);
    expect(result.event).toMatchObject({ type: 'exit', reason: 'stop_loss' });
  });

  it('uses the minimum dynamic stop for low-volatility entries', () => {
    const entered = enterPaperPosition(createPortfolio(config, '2026-07-24T00:00:00.000Z'), { ...entryInput50, priceChangeM5Percent: 2 }, config);
    if (!entered.ok) throw new Error('expected entry');
    if (!entered.ok) return;
    // volatility = 2, multiplier = 1.5 → dynamic = 3, score=50 → scoreMult=1.25, max=25*1.25=31.25, min=5 → 5
    expect(entered.position.dynamicStopPercent).toBe(5);
  });

  it('triggers a time_stop when a position shows no profit within maxHold/2', () => {
    const entered = enterPaperPosition(createPortfolio(config, '2026-07-24T00:00:00.000Z'), entryInput50, config);
    if (!entered.ok) throw new Error('expected entry');
    // After 23 min (>= maxHold/2 = 22.5) with <1% profit, time stop triggers
    const later = '2026-07-24T00:24:00.000Z'; // 23 min >= 45/2 = 22.5 min
    const result = evaluateOpenPosition(entered.portfolio, { pairId: 'pair-1', mint: 'mint-1' }, 1.009, later, config);
    expect(result.event).toMatchObject({ type: 'exit', reason: 'time_stop' });
  });

  it('caps the breakeven buffer at a sub-0.2% hard maximum', () => {
    const tightConfig = { ...config, minStopLossPercent: 0.05, maxStopLossPercent: 0.1, breakevenTriggerPercent: 1 };
    const entered = enterPaperPosition(createPortfolio(tightConfig, '2026-07-24T00:00:00.000Z'), entryInput50, tightConfig);
    if (!entered.ok) throw new Error('expected entry');
    const atHigh = evaluateOpenPosition(entered.portfolio, { pairId: 'pair-1', mint: 'mint-1' }, 1.03, '2026-07-24T00:02:00.000Z', tightConfig);
    const afterDrop = evaluateOpenPosition(atHigh.portfolio, { pairId: 'pair-1', mint: 'mint-1' }, 1.0135, '2026-07-24T00:03:00.000Z', tightConfig);
    expect(afterDrop.event).toMatchObject({ type: 'exit', reason: 'stop_loss' });
  });

  it('moves stop to breakeven after breakeven trigger price is hit, then exits on drop', () => {
    const entered = enterPaperPosition(createPortfolio(config, '2026-07-24T00:00:00.000Z'), entryInput50, config);
    if (!entered.ok) throw new Error('expected entry');
    // Entry with slippage: 1 * 1.015 = 1.015. Breakeven trigger at +8% = 1.0962.
    // Push high to 1.12 (10.3% above entry) → breakeven should trigger
    const atHigh = evaluateOpenPosition(entered.portfolio, { pairId: 'pair-1', mint: 'mint-1' }, 1.12, '2026-07-24T00:02:00.000Z', config);
    expect(atHigh.event).toMatchObject({ type: 'hold' });
    // Now drop below breakeven (0.998 * 1.015 = 1.01297). Price 0.99 should trigger stop.
    const afterDrop = evaluateOpenPosition(atHigh.portfolio, { pairId: 'pair-1', mint: 'mint-1' }, 0.99, '2026-07-24T00:03:00.000Z', config);
    expect(afterDrop.event).toMatchObject({ type: 'exit', reason: 'stop_loss' });
  });

  it('tracks consecutive losses and resets on win', () => {
    let portfolio = createPortfolio(config, '2026-07-24T00:00:00.000Z');
    expect(portfolio.consecutiveLosses).toBe(0);

    // Loss 1: drop to 0.5 → stop_loss
    const e1 = enterPaperPosition(portfolio, entryInput50, config);
    if (!e1.ok) throw new Error('entry 1');
    const r1 = evaluateOpenPosition(e1.portfolio, { pairId: 'pair-1', mint: 'mint-1' }, 0.4, '2026-07-24T00:02:00.000Z', config);
    expect(r1.event).toMatchObject({ type: 'exit', reason: 'stop_loss' });
    expect(r1.portfolio.consecutiveLosses).toBe(1);
    portfolio = r1.portfolio;

    // Loss 2
    const e2 = enterPaperPosition(portfolio, { ...entryInput50, mint: 'mint-2', symbol: 'COIN2' }, config);
    if (!e2.ok) throw new Error('entry 2');
    const r2 = evaluateOpenPosition(e2.portfolio, { pairId: 'pair-1', mint: 'mint-2' }, 0.4, '2026-07-24T00:04:00.000Z', config);
    expect(r2.portfolio.consecutiveLosses).toBe(2);
    portfolio = r2.portfolio;

    // Win: push price to 2.0 (above trailing activation at 1.015 * 1.30 = 1.32), then drop to 1.5
    const e3 = enterPaperPosition(portfolio, { ...entryInput50, mint: 'mint-3', symbol: 'COIN3' }, config);
    if (!e3.ok) throw new Error('entry 3');
    const atHigh = evaluateOpenPosition(e3.portfolio, { pairId: 'pair-1', mint: 'mint-3' }, 2.0, '2026-07-24T00:06:00.000Z', config);
    expect(atHigh.event).toMatchObject({ type: 'hold' });
    // Trailing stop: 2.0 * 0.85 = 1.70. Drop to 1.5 should trigger.
    const r3 = evaluateOpenPosition(atHigh.portfolio, { pairId: 'pair-1', mint: 'mint-3' }, 1.5, '2026-07-24T00:07:00.000Z', config);
    expect(r3.event).toMatchObject({ type: 'exit', reason: 'trailing_stop' });
    expect(r3.portfolio.consecutiveLosses).toBe(0); // Reset on win
  });
});