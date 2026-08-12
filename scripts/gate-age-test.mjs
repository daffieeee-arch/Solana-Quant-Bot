import { loadConfig } from '../dist/config.js';
import { evaluateMarketGate } from '../dist/scoring.js';

const env = {
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '4',
  MIN_LIQUIDITY_USD: '10000', MAX_LIQUIDITY_USD: '50000000', MIN_AGE_MINUTES: '20', MAX_AGE_MINUTES: '259200',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
  MIN_MOMENTUM_SCORE: '50', MIN_STOP_LOSS_PERCENT: '5', MAX_STOP_LOSS_PERCENT: '25',
  STOP_VOLATILITY_MULTIPLIER: '1.5', BREAKEVEN_TRIGGER_PERCENT: '8', MIN_PROFIT_FOR_CONTINUE_PERCENT: '3',
  POSITION_SCORE_DIVISOR: '100', LIQUIDITY_POSITION_FRACTION: '0.02', SOL_PRICE_USD: '74',
  ENTRY_MODE: 'contra', MIN_BUY_SURGE_COUNT: '10', MIN_BUY_PRESSURE: '0.45', MAX_DAILY_LOSS_SOL: '0.5',
};
const cfg = loadConfig(env);
console.log('minAgeMinutes:', cfg.minAgeMinutes, '| maxLiquidity:', cfg.maxLiquidityUsd, '| entryMode:', cfg.entryMode);

// 3.3-min oude pool zoals de verse decisions
const now = new Date('2026-08-11T22:06:16.000Z');
const snap = {
  pairId: 'gx:X', mint: 'Xpump', symbol: 'TEST', source: 'triton_geyser_generic_multidex',
  observedAt: '2026-08-11T22:06:15.000Z',
  pairCreatedAt: '2026-08-11T22:02:56.000Z', // 3.3 min oud
  priceUsd: 0.000005,
  liquidityUsd: 50000000, // boven max
  buysM5: 5, sellsM5: 3,
};
const gate = evaluateMarketGate(snap, cfg, now);
console.log('gate voor 3.3-min pool met liq 50M:', JSON.stringify(gate));