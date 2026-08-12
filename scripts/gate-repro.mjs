import { loadConfig } from '../dist/config.js';
import { evaluateMarketGate } from '../dist/scoring.js';

const env = {
  MODE: 'paper',
  PAPER_STARTING_SOL: '10',
  MAX_POSITION_SOL: '0.25',
  MAX_DAILY_LOSS_SOL: '0.5',
  ENTRY_MODE: 'contra',
  MIN_AGE_MINUTES: '20',
  MAX_AGE_MINUTES: '259200',
  MAX_LIQUIDITY_USD: '50000000',
  MIN_LIQUIDITY_USD: '10000',
  MIN_VOLUME_M5_USD: '1000',
  MIN_BUY_SURGE_COUNT: '10',
  MIN_BUY_PRESSURE: '0.45',
  MIN_PRICE_CHANGE_M5_PERCENT: '1.5',
  MIN_MOMENTUM_SCORE: '50',
  STOP_LOSS_PERCENT: '12',
  TAKE_PROFIT_PERCENT: '8',
  SOL_PRICE_USD: '74',
  TRAILING_STOP_PERCENT: '5',
  MAX_HOLD_MINUTES: '15',
  SIMULATED_SLIPPAGE_BPS: '150',
  SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30',
  MIN_STOP_LOSS_PERCENT: '5',
  MAX_STOP_LOSS_PERCENT: '15',
  STOP_VOLATILITY_MULTIPLIER: '1',
  BREAKEVEN_TRIGGER_PERCENT: '8',
  MIN_PROFIT_FOR_CONTINUE_PERCENT: '2',
  POSITION_SCORE_DIVISOR: '80',
  LIQUIDITY_POSITION_FRACTION: '0.02',
  MIN_WHALE_TX_SOL: '1',
  WHALE_WALLETS: '',
  MAX_CONCURRENT_POSITIONS: '4',
  MAX_CYCLES: '0',
  STRICT_RISK_MODE: 'false',
};
const cfg = loadConfig(env);
console.log('entryMode:', cfg.entryMode, '| minAge:', cfg.minAgeMinutes, '| maxLiq:', cfg.maxLiquidityUsd, '| minLiq:', cfg.minLiquidityUsd);

// 1) Verse 4-min-oude pool → verwacht age_outside_window (geen liquidity_above_maximum)
const snap = {
  pairId: 'x', mint: 'y', symbol: 'Alon', source: 'triton_vixen_pumpfun',
  pairCreatedAt: '2026-08-11T18:01:29.361Z', observedAt: '2026-08-11T18:05:32.558Z',
  liquidityUsd: 17000, volumeM5Usd: 5000, priceChangeM5Percent: 2, buysM5: 8, sellsM5: 4,
};
const now = new Date('2026-08-11T18:06:03.393Z');
console.log('test1 (4min, liq 17k):', JSON.stringify(evaluateMarketGate(snap, cfg, now)));

// 2) Oude pool (>20min), liq 88M → liquidity_above_maximum
const snap2 = { ...snap, pairCreatedAt: '2026-08-11T17:00:00.000Z', liquidityUsd: 88_000_000 };
console.log('test2 (66min, liq 88M):', JSON.stringify(evaluateMarketGate(snap2, cfg, now)));

// 3) Oude pool, liq 17k, lage buy-druk → contra accepted
const snap3 = { ...snap, pairCreatedAt: '2026-08-11T17:00:00.000Z', buysM5: 3, sellsM5: 8, priceChangeM5Percent: -2 };
console.log('test3 (66min, dip):', JSON.stringify(evaluateMarketGate(snap3, cfg, now)));

// 4) Ongeprijsde oude pool met dip-flow → contra accepted (geen invalid_market_data meer)
const snap4 = { ...snap, pairCreatedAt: '2026-08-11T17:00:00.000Z', priceUsd: 0, buysM5: 2, sellsM5: 6, priceChangeM5Percent: -3 };
console.log('test4 (onge prijs, dip):', JSON.stringify(evaluateMarketGate(snap4, cfg, now)));