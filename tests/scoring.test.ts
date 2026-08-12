import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { evaluateMarketGate, scoreMomentum, scoreContraMomentum } from '../src/scoring.js';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
});

const now = new Date('2026-07-24T23:00:00.000Z');
const validSnapshot = {
  pairId: 'pair-1', mint: 'mint-1', symbol: 'PUMP', source: 'birdeye',
  observedAt: now.toISOString(), pairCreatedAt: new Date(now.getTime() - 10 * 60_000).toISOString(),
  priceUsd: 0.02, liquidityUsd: 50_000, volumeM5Usd: 15_000, priceChangeM5Percent: 22,
  buysM5: 80, sellsM5: 30,
};

describe('market scoring', () => {
  it('rejects a pair below the liquidity floor with a stable reason code', () => {
    expect(evaluateMarketGate({ ...validSnapshot, liquidityUsd: 24_999 }, config, now)).toEqual({
      accepted: false, reason: 'liquidity_below_minimum',
    });
  });

  it('accepts a fresh liquid momentum candidate and returns an explainable positive score', () => {
    expect(evaluateMarketGate(validSnapshot, config, now)).toEqual({ accepted: true });
    expect(scoreMomentum(validSnapshot, config)).toBeGreaterThan(0);
  });

  it('rejects when buy surge is too low (few buyers in the window)', () => {
    const surgeConfig = { ...config, minBuySurgeCount: 50, minBuyPressure: 0.55 };
    const fewBuyers = { ...validSnapshot, buysM5: 10, sellsM5: 2 };
    expect(evaluateMarketGate(fewBuyers, surgeConfig, now)).toEqual({ accepted: false, reason: 'buy_surge_insufficient' });
  });

  it('accepts a high-liquidity pool with a strong buy surge (the "hundreds of buyers" setup)', () => {
    const surgeConfig = { ...config, minBuySurgeCount: 50, minBuyPressure: 0.55 };
    const surge = {
      ...validSnapshot,
      liquidityUsd: 50_000,
      buysM5: 800, sellsM5: 120, volumeM5Usd: 80_000, priceChangeM5Percent: 25,
    };
    expect(evaluateMarketGate(surge, surgeConfig, now)).toEqual({ accepted: true });
    expect(scoreMomentum(surge, surgeConfig)).toBeGreaterThan(50);
  });

  it('rejects when buyers do not dominate by the configured pressure ratio', () => {
    const surgeConfig = { ...config, minBuySurgeCount: 50, minBuyPressure: 0.55 };
    const weakPressure = { ...validSnapshot, buysM5: 300, sellsM5: 280 };
    // pressure = 300/580 = 0.517 < 0.55
    expect(evaluateMarketGate(weakPressure, surgeConfig, now)).toEqual({ accepted: false, reason: 'buy_pressure_insufficient' });
  });

  it('rejects a high rug/honeypot risk token outright', () => {
    const ruggy = { ...validSnapshot, rugRisk: { className: 'high' as const, score: 0.9, flags: ['full_authority_1'] } };
    expect(evaluateMarketGate(ruggy, config, now)).toEqual({ accepted: false, reason: 'rug_risk_high' });
  });

  it('accepts a clean-rug token (moderate/clean passes the hard gate)', () => {
    const clean = { ...validSnapshot, rugRisk: { className: 'clean' as const, score: 0.05, flags: ['supply_burnt'] } };
    expect(evaluateMarketGate(clean, config, now)).toEqual({ accepted: true });
  });

  it('lets a fresh pool with live flow score entry-grade despite no external price-change', () => {
    // Jonge pool: geen externe priceChangeM5, maar wel flow (Laag B) + volume.
    const freshFlow = {
      ...validSnapshot,
      priceChangeM5Percent: 0,        // geen externe 5-min beweging
      volumeM5Usd: 60_000,            // sterke flow-volume
      buysM5: 200, sellsM5: 20,       // sterke buy-pressure
    };
    const score = scoreMomentum(freshFlow, config);
    expect(score).toBeGreaterThanOrEqual(config.minMomentumScore);
  });

  it('still caps sub-threshold price-change pools without flow below entry-grade', () => {
    const strictConfig = { ...config, minMomentumScore: 55 };
    const noFlow = { ...validSnapshot, priceChangeM5Percent: 1, buysM5: 0, sellsM5: 0, volumeM5Usd: 60_000 };
    // prijs-change 1 < min 8 → klassieke momentum-gate capped onder 55
    expect(scoreMomentum(noFlow, strictConfig)).toBeLessThan(strictConfig.minMomentumScore);
  });

  it('caps the normalized entry score at 100 even for extreme volume', () => {
    const extreme = { ...validSnapshot, volumeM5Usd: 100_000_000, priceChangeM5Percent: 100, buysM5: 10_000, sellsM5: 1 };
    expect(scoreMomentum(extreme, config)).toBeLessThanOrEqual(100);
  });

  it('does not let extreme volume alone make threshold-level momentum entry-grade (feeble buy edge)', () => {
    const surgeConfig = { ...config, minBuySurgeCount: 50, minBuyPressure: 0.55, minMomentumScore: 50 };
    // buys 51/sells 49 → pressure 0.51 < 0.55: the gate rejects it outright regardless of volume.
    const volumeDominated = { ...validSnapshot, priceChangeM5Percent: 8.1, volumeM5Usd: 100_000_000, buysM5: 51, sellsM5: 49 };
    expect(evaluateMarketGate(volumeDominated, surgeConfig, now)).toEqual({ accepted: false, reason: 'buy_pressure_insufficient' });
  });

  it('does not let volume alone (no buy edge) create entry-grade score', () => {
    const strictConfig = { ...config, minMomentumScore: 55 };
    const flatFlow = { ...validSnapshot, priceChangeM5Percent: 0, volumeM5Usd: 100_000_000, buysM5: 1000, sellsM5: 900 };
    // gebalanceerde flow zonder buy-edge → ver onder 55
    expect(scoreMomentum(flatFlow, strictConfig)).toBeLessThan(strictConfig.minMomentumScore);
  });

  it('does not let moderate flow against negative 5m price reach entry-grade without a strong buy edge', () => {
    const strictConfig = { ...config, minMomentumScore: 55 };
    const reversalFlow = { ...validSnapshot, priceChangeM5Percent: -9.13, volumeM5Usd: 39_197.11, buysM5: 545, sellsM5: 88 };
    // flowScore ~36.9 blijft ver onder 55
    expect(scoreMomentum(reversalFlow, strictConfig)).toBeLessThan(strictConfig.minMomentumScore);
  });

  it('lets overwhelming buy-flow vs sells reach entry-grade even without external price change', () => {
    const strongEdge = { ...validSnapshot, priceChangeM5Percent: 0, volumeM5Usd: 400_000, buysM5: 900, sellsM5: 30 };
    // buyPressure ~9.7 + volumeScore(min35) = ~44,7 → boven de standaard 35-drempel
    expect(scoreMomentum(strongEdge, config)).toBeGreaterThanOrEqual(config.minMomentumScore);
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY])('rejects non-finite market inputs fail-closed (%s)', (invalidValue) => {
    const invalid = { ...validSnapshot, priceChangeM5Percent: invalidValue };
    expect(evaluateMarketGate(invalid, config, now)).toEqual({ accepted: false, reason: 'invalid_market_data' });
    expect(scoreMomentum(invalid, config)).toBe(0);
  });

  it('rejects non-finite transaction counts fail-closed', () => {
    const invalid = { ...validSnapshot, buysM5: Number.POSITIVE_INFINITY };
    expect(evaluateMarketGate(invalid, config, now)).toEqual({ accepted: false, reason: 'invalid_market_data' });
    expect(scoreMomentum(invalid, config)).toBe(0);
  });

  it('rejects a stale market observation', () => {
    expect(evaluateMarketGate({ ...validSnapshot, observedAt: '2026-07-24T22:50:00.000Z' }, config, now)).toEqual({
      accepted: false, reason: 'stale_market_data',
    });
  });

  it('rejects a future market observation fail-closed', () => {
    expect(evaluateMarketGate({ ...validSnapshot, observedAt: '2026-07-24T23:00:00.001Z' }, config, now)).toEqual({
      accepted: false, reason: 'future_market_data',
    });
  });
});

describe('contra-momentum entry mode', () => {
  const contra = { ...config, entryMode: 'contra' as const };

  it('rejects hoge retail-buy-druk (extase) in contra mode', () => {
    // buys 80 / sells 30 → buy-druk 0.73 > 0.62 → skip
    expect(evaluateMarketGate({ ...validSnapshot, buysM5: 80, sellsM5: 30 }, contra, now))
      .toEqual({ accepted: false, reason: 'contra_high_buy_pressure' });
  });

  it('accepteert lage buy-druk (dip) in contra mode', () => {
    // buys 30 / sells 40 → 0.43 <= 0.62 → pas
    const snap = { ...validSnapshot, buysM5: 30, sellsM5: 40, priceChangeM5Percent: -5 };
    expect(evaluateMarketGate(snap, contra, now)).toEqual({ accepted: true });
  });

  it('verwerpt whale recent in contra mode (dump-signaal)', () => {
    const snap = {
      ...validSnapshot, buysM5: 20, sellsM5: 25, priceChangeM5Percent: -5,
      // whaleInterestAt recent tov de geïnjecteerde `now`-klok (niet Date.now())
      whaleInterestAt: new Date(now.getTime() - 5 * 60_000).toISOString(),
    };
    expect(evaluateMarketGate(snap, contra, now)).toEqual({ accepted: false, reason: 'contra_whale_recent' });
  });

  it('verwerpt sterke 5-min momentum-stijging (near top) in contra mode', () => {
    const snap = { ...validSnapshot, buysM5: 20, sellsM5: 25, priceChangeM5Percent: 25 };
    expect(evaluateMarketGate(snap, contra, now)).toEqual({ accepted: false, reason: 'contra_momentum_high' });
  });

  it('beoordeelt een ongeprijsde dip-pool op flow i.p.v. invalid_market_data (Triton-first)', () => {
    // Essentiële contra-fix: verse/dumpy discovery-pools hebben vaak geen priceUsd
    // (prijs komt pas via curve-calc/Titan). In contra-mode is dat GEEN invalid data —
    // de dip wordt beoordeeld op buys/sells-flow.
    const snap = { ...validSnapshot, priceUsd: 0, buysM5: 12, sellsM5: 30, priceChangeM5Percent: -4 };
    expect(evaluateMarketGate(snap, contra, now)).toEqual({ accepted: true });
  });

  it('houdt prijs-vereiste in surge-mode: ongeprijsde pool blijft invalid_market_data', () => {
    const snap = { ...validSnapshot, priceUsd: 0, buysM5: 20, sellsM5: 30 };
    expect(evaluateMarketGate(snap, config, now)).toEqual({ accepted: false, reason: 'invalid_market_data' });
  });

  it('contra-score beloont de DIP: lage buy-druk + negatief momentum > hoge buy-druk', () => {
    const contraConfig = { ...config, entryMode: 'contra' as const, contraMaxBuyPressure: 0.55 };
    const dip = { ...validSnapshot, buysM5: 10, sellsM5: 90, priceChangeM5Percent: -6 };
    const surge = { ...validSnapshot, buysM5: 90, sellsM5: 10, priceChangeM5Percent: +22 };
    expect(scoreContraMomentum(dip, contraConfig, now)).toBeGreaterThan(scoreContraMomentum(surge, contraConfig, now));
  });

  it('contra-score geeft hoge score aan een levendige verse dip (entry-grade > 50)', () => {
    const contraConfig = { ...config, entryMode: 'contra' as const, contraMaxBuyPressure: 0.55 };
    const freshDip = {
      ...validSnapshot,
      pairCreatedAt: new Date(now.getTime() - 8 * 60_000).toISOString(), // 8 min oud (beste venster)
      buysM5: 25, sellsM5: 95, priceChangeM5Percent: -5, volumeM5Usd: 30_000, liquidityUsd: 60_000,
    };
    const score = scoreContraMomentum(freshDip, contraConfig, now);
    expect(score).toBeGreaterThan(50);
  });

  it('contra-gate reject hoge buy-druk boven contraMaxBuyPressure (0.55)', () => {
    const contraConfig = { ...config, entryMode: 'contra' as const, contraMaxBuyPressure: 0.55 };
    const extase = { ...validSnapshot, buysM5: 80, sellsM5: 20 }; // bp = 0.8 > 0.55
    expect(evaluateMarketGate(extase, contraConfig, now)).toEqual({ accepted: false, reason: 'contra_high_buy_pressure' });
  });

  it('contra-gate accepteert een dip onder de drempel (bp <= 0.55)', () => {
    const contraConfig = { ...config, entryMode: 'contra' as const, contraMaxBuyPressure: 0.55 };
    const dip = { ...validSnapshot, buysM5: 20, sellsM5: 60, priceChangeM5Percent: -4 }; // bp = 0.25
    expect(evaluateMarketGate(dip, contraConfig, now)).toEqual({ accepted: true });
  });
});
