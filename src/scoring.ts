import type { PaperConfig } from './config.js';

export type DiscoveryProvenance = {
  signature: string;
  slot?: number;
  programId: string;
  instructionLocation: 'top_level' | 'inner';
  instructionIndex: number;
  parentInstructionIndex?: number;
  receiptAt: string;
};

export type MarketSnapshot = {
  pairId: string;
  mint: string;
  symbol: string;
  source: string;
  discovery?: DiscoveryProvenance;
  firstSeenAt?: string;
  observedAt: string;
  pairCreatedAt: string;
  priceUsd: number;
  liquidityUsd?: number;
  volumeM5Usd?: number;
  priceChangeM5Percent?: number;
  buysM5?: number;
  sellsM5?: number;
  whaleInterestAt?: string;
  /**
   * Live on-chain pool depth used to compute realistic price-impact fills.
   * Populated by Triton Vixen stream updates (e.g. Pump.fun bonding-curve
   * virtual reserves, Raydium AMM vault reserves). When present, the paper
   * fill price reflects the actual k-value / constant-product impact of the
   * position size instead of a fixed slippage %. Absent → flat slippage fallback.
   */
  poolDepth?: PoolDepth;
  /** Rug/honeypot assessment (Triton on-chain), when available. */
  rugRisk?: {
    className: 'clean' | 'moderate' | 'high';
    score: number;
    flags: string[];
  };
};

export type PoolDepth = {
  /** Base (traded token) reserve in its own base units, scaled by 10^baseDecimals. */
  baseReserve: number;
  /** Quote (SOL or stablecoin) reserve in quote units, scaled by 10^quoteDecimals. */
  quoteReserve: number;
  baseDecimals: number;
  quoteDecimals: number;
  /** Constant-product fee numerator/denominator if known (default 0.25%). */
  feeNumerator?: number;
  feeDenominator?: number;
  /** True for Pump.fun bonding curves (virtual reserves, before migration). */
  bondingCurve?: boolean;
  /** True wanneer de depth synthetisch is afgeleid uit txn-balances (balance-pricing). */
  syntheticPriceLamportsPerToken?: number;
};

export type MarketGate =
  | { accepted: true }
  | {
      accepted: false;
      reason:
        | 'stale_market_data'
        | 'future_market_data'
        | 'invalid_market_data'
        | 'age_outside_window'
        | 'liquidity_below_minimum'
        | 'liquidity_above_maximum'
        | 'volume_below_minimum'
        | 'momentum_below_minimum'
        | 'buy_pressure_insufficient'
        | 'buy_surge_insufficient'
        | 'rug_risk_high'
        | 'contra_high_buy_pressure'
        | 'contra_whale_recent'
        | 'contra_momentum_high';
    };

const MAX_OBSERVATION_AGE_MS = 2 * 60_000;

function hasInvalidNumericMarketData(snapshot: MarketSnapshot, allowUnpriced: boolean): boolean {
  if (allowUnpriced) {
    // Contra-mode (dip-buy): een ontbrekende prijs is NIET invalid — de dip wordt
    // beoordeeld op flow (buys/sells, whale, momentum). Alleen NaN/negatief is hard-invalid.
    if (snapshot.priceUsd !== undefined && (!Number.isFinite(snapshot.priceUsd) || snapshot.priceUsd < 0)) return true;
  } else if (!Number.isFinite(snapshot.priceUsd) || snapshot.priceUsd <= 0) {
    // Surge-mode: een prijs is vereist om momentum/kans te beoordelen.
    return true;
  }
  if (snapshot.priceChangeM5Percent !== undefined && !Number.isFinite(snapshot.priceChangeM5Percent)) return true;
  return [snapshot.liquidityUsd, snapshot.volumeM5Usd, snapshot.buysM5, snapshot.sellsM5]
    .some((value) => value !== undefined && (!Number.isFinite(value) || value < 0));
}

export function evaluateMarketGate(
  snapshot: MarketSnapshot,
  config: PaperConfig,
  now: Date = new Date(),
): MarketGate {
  const observedAt = Date.parse(snapshot.observedAt);
  const pairCreatedAt = Date.parse(snapshot.pairCreatedAt);
  const allowUnpriced = config.entryMode === 'contra';
  if (!Number.isFinite(observedAt) || !Number.isFinite(pairCreatedAt) || hasInvalidNumericMarketData(snapshot, allowUnpriced)) {
    return { accepted: false, reason: 'invalid_market_data' };
  }
  if (observedAt > now.getTime()) {
    return { accepted: false, reason: 'future_market_data' };
  }
  if (now.getTime() - observedAt > MAX_OBSERVATION_AGE_MS) {
    return { accepted: false, reason: 'stale_market_data' };
  }
  const ageMinutes = (now.getTime() - pairCreatedAt) / 60_000;
  if (ageMinutes < config.minAgeMinutes || ageMinutes > config.maxAgeMinutes) {
    return { accepted: false, reason: 'age_outside_window' };
  }
  if (snapshot.liquidityUsd === undefined || snapshot.liquidityUsd < config.minLiquidityUsd) {
    return { accepted: false, reason: 'liquidity_below_minimum' };
  }
  if (snapshot.liquidityUsd > config.maxLiquidityUsd) {
    return { accepted: false, reason: 'liquidity_above_maximum' };
  }
  // WS-detected pools may lack volume/momentum data — skip those gates and rely on scoreMomentum
  const isWsDetected = (snapshot.source ?? '').startsWith('solana_rpc_ws');
  if (!isWsDetected || snapshot.volumeM5Usd !== undefined) {
    if (snapshot.volumeM5Usd === undefined || snapshot.volumeM5Usd < config.minVolumeM5Usd) {
      // Triton-first: discovery-pools zonder externe volume-data (Birdeye/Gecko uit)
      // worden NIET op 'volume_below_minimum' afgewezen — de live buy-surge/flow
      // (Laag B, uit Vixen) is het volume-signaal. Alleen rejecten wanneer er wél
      // een opgegeven volume is dat onder de drempel zit.
      if (snapshot.volumeM5Usd !== undefined) {
        return { accepted: false, reason: 'volume_below_minimum' };
      }
    }
  }
  // Contra-mode: momentum_below_minimum is omgekeerd — we zoeken juist de dip
  // (negatief/laag momentum). Deze gate alleen in surge-mode.
  if (config.entryMode !== 'contra' && (!isWsDetected || snapshot.priceChangeM5Percent !== undefined)) {
    if (snapshot.priceChangeM5Percent === undefined || snapshot.priceChangeM5Percent < config.minPriceChangeM5Percent) {
      // Idem: zonder externe 5-min prijs-change (Triton-only mode) geen momentum-reject;
      // de score-beloning voor buy-surge + koopdruk is dan het entry-signaal.
      if (snapshot.priceChangeM5Percent !== undefined) {
        return { accepted: false, reason: 'momentum_below_minimum' };
      }
    }
  }
  const buys = snapshot.buysM5 ?? 0;
  const sells = snapshot.sellsM5 ?? 0;
  if (config.entryMode === 'contra') {
    // ─── CONTRA-MOMENTUM regime (backtest 179-mint/72u bevestigd) ───
    // Koop de DIP, niet de surge: lage buy-druk, geen whale, voldoende leeftijd.
    // Overerving: alle eerdere gates (liquidity/age/rug) zijn al gepasseerd.
    // Leeftijd: versoepel naar >=20 min (verse pumps dumpen eerst). De age-gate
    // hierboven (minAgeMinutes) blijft, dus alleen versterken als nodig.
    if (buys + sells > 0 && buys / (buys + sells) > config.contraMaxBuyPressure) {
      // hoge retail-buy-druk = extase nabij de top → overslaan.
      // Drempel uit backtest (3-mnd): bp<=0.55 maximeert expectancy.
      return { accepted: false, reason: 'contra_high_buy_pressure' };
    }
    // whale recent = dump-signaal (whale verkoop), geen entry
    if (snapshot.whaleInterestAt) {
      const age = now.getTime() - Date.parse(snapshot.whaleInterestAt);
      if (Number.isFinite(age) && age > 0 && age <= 30 * 60 * 1000) {
        return { accepted: false, reason: 'contra_whale_recent' };
      }
    }
    // forces: een coin die net (>+X%) in 5 min is gebombeerd = nabij top → skip
    if (snapshot.priceChangeM5Percent !== undefined && snapshot.priceChangeM5Percent > config.minPriceChangeM5Percent) {
      return { accepted: false, reason: 'contra_momentum_high' };
    }
    // Rug/honeypot safety gate (Triton on-chain): reject high-risk tokens — ook
    // in contra-mode (was alleen in de surge-tak; een contra-entry mag NOOIT een
    // high-rug coin kopen, dat is een honeypot-verlies).
    if (snapshot.rugRisk?.className === 'high') {
      return { accepted: false, reason: 'rug_risk_high' };
    }
    return { accepted: true };
  }
  // ─── SURGE regime (historisch, anti-edge op backtest) ───
  if (buys + sells > 0 && buys <= sells) {
    return { accepted: false, reason: 'buy_pressure_insufficient' };
  }
  // Pomp-surge gate: "honderden kopers in korte tijd" — absolute buy count must clear
  // the surge floor (e.g. 50+ buys), and buyers must dominate by the configured ratio.
  if (buys < config.minBuySurgeCount) {
    return { accepted: false, reason: 'buy_surge_insufficient' };
  }
  if (buys + sells > 0 && buys / (buys + sells) < config.minBuyPressure) {
    return { accepted: false, reason: 'buy_pressure_insufficient' };
  }
  // Rug/honeypot safety gate (Triton on-chain): reject high-risk tokens outright.
  if (snapshot.rugRisk?.className === 'high') {
    return { accepted: false, reason: 'rug_risk_high' };
  }
  return { accepted: true };
}

export function scoreMomentum(snapshot: MarketSnapshot, config: PaperConfig, now = new Date()): number {
  if (hasInvalidNumericMarketData(snapshot, config.entryMode === 'contra')) return 0;
  const momentum = snapshot.priceChangeM5Percent ?? 0;
  const priceRatio = Math.max(0, momentum / config.minPriceChangeM5Percent);
  const priceScore = Math.min(45, priceRatio * 12);

  const volumeRatio = Math.max(0, (snapshot.volumeM5Usd ?? 0) / config.minVolumeM5Usd);
  const volumeScore = Math.min(35, Math.log2(Math.max(1, volumeRatio)) * 10);

  const buys = snapshot.buysM5 ?? 0;
  const sells = snapshot.sellsM5 ?? 0;
  const buyPressureScore = buys + sells === 0
    ? 0
    : Math.max(-10, Math.min(10, ((buys - sells) / (buys + sells)) * 10));
  // Pomp-surge: reward absolute buyer count — "honderden kopers in korte tijd".
  // Log-scaled so 50 buys ≈ +6, 500 ≈ +12, 5000+ ≈ +20.
  const surgeScore = buys <= 0 ? 0 : Math.min(20, Math.log10(Math.max(1, buys)) * 5);
  let flowScore = priceScore + volumeScore + buyPressureScore + surgeScore;

  // Whale activity boost: +15 if a known whale bought this token within 30 minutes.
  // Guard `age > 0`: een toekomstige/negatieve leeftijd (klok-scheefstand, parse-fout)
  // mag GEEN boost geven — alleen echte recente whale-activiteit (zelfde guard als
  // de contra-gate 'contra_whale_recent').
  const whaleBoost = (() => {
    if (!snapshot.whaleInterestAt) return 0;
    const age = now.getTime() - Date.parse(snapshot.whaleInterestAt);
    if (!Number.isFinite(age) || age <= 0 || age > 30 * 60 * 1000) return 0;
    return 15;
  })();
  flowScore += whaleBoost;

  // Een score rankt momentum. Sterke stroom zónder (of sub-drempel) 5-min prijs-beweging
  // is voor een VERS-gedetecteerde pool wél entry-grade: de jonge pool heeft nog geen
  // externe 5-min prijs-change, maar de live buy/sell-stroom (Laag B) is het signaal.
  // → Leid de entry-grade op de buy-pressure als er flow is; behoud prijs-gate voor
  //   pools mét externe prijs-bewegingsdata.
  const hasPriceMomentum = snapshot.priceChangeM5Percent !== undefined
    && Number.isFinite(snapshot.priceChangeM5Percent)
    && snapshot.priceChangeM5Percent >= config.minPriceChangeM5Percent;
  const hasFlow = buys + sells > 0;
  let entryScore: number;
  if (hasPriceMomentum || !hasFlow) {
    // bewezen momentum, of geen flow (dan klassieke momentum-gate)
    entryScore = momentum < config.minPriceChangeM5Percent
      ? Math.min(flowScore, config.minMomentumScore - 0.01)
      : flowScore;
  } else {
    // vers-pool met live flow: entry-grade wordt geleid door buy-pressure + volume,
    // zonder dat het ontbreken van externe prijs-change de score saboteert.
    entryScore = flowScore;
  }
  return Number(Math.max(0, Math.min(100, entryScore)).toFixed(2));
}

/**
 * CONTRA-mode score (dip kopen). Beloont precies wat de contra-strategie zoekt:
 *  - LAGE buy-druk (sellers domineren) → hoe lager de bp, hoe hoger de score.
 *    (Dit is het OMGEKEERDE van surge-score: daar is hoge buy-druk goed.)
 *  - Diepe/levendige daling: negatief 5-min momentum = koopje, geen extase.
 *  - Voldoende flow (buys+sells) — een dip mét volume is een echte dip, geen
 *    dode munt.
 *  - Verse munt (jonge leeftijd = meer upside, zie backtest 5-15min +30%).
 * Schaal 0-100; drempel via config.minMomentumScore (default 50).
 */
export function scoreContraMomentum(snapshot: MarketSnapshot, config: PaperConfig, now = new Date()): number {
  if (hasInvalidNumericMarketData(snapshot, true)) return 0;

  const buys = snapshot.buysM5 ?? 0;
  const sells = snapshot.sellsM5 ?? 0;
  const total = buys + sells;

  // 1) Dip-diepte via buy-pressure: bp 0→1. Hoe lager bp, hoe dieper de dip.
  //    bp = 0.5 (neutraal) → 0 punten; bp = 0.1 → ~36; bp = 0.9 → -40 (extase).
  const bp = total > 0 ? buys / total : 0.5;
  const dipScore = total > 0 ? Math.max(-40, Math.min(45, (0.55 - bp) * 90)) : 0;

  // 2) Momentum: negatief/laag momentum = koopje; sterk positief = extase-skip.
  //    -5% → +25; 0% → +10; +5% → -10; +10% → -30.
  const momentum = snapshot.priceChangeM5Percent ?? 0;
  const momScore = Math.max(-30, Math.min(25, 10 - momentum * 4));

  // 3) Flow-kwaliteit: een dip met volume is beter dan een stille munt.
  //    Log-schaal: 10 events → +8; 100 → +16; 1000+ → +24.
  const flowScore = total <= 0 ? 0 : Math.min(24, Math.log10(Math.max(1, total)) * 8);

  // 4) Leeftijd: jonge munt (5-60 min) = meeste upside (backtest: 5-15min +30%).
  //    30 min → +20; 2u → +10; 6h+ → -15 (backtest: 4h+ verliezend).
  const ageMinutes = (now.getTime() - Date.parse(snapshot.pairCreatedAt)) / 60_000;
  let ageScore = 0;
  if (Number.isFinite(ageMinutes)) {
    if (ageMinutes < 60) ageScore = Math.max(0, 20 - ageMinutes * 0.15);
    else if (ageMinutes < 240) ageScore = Math.max(-5, 10 - (ageMinutes - 60) * 0.06);
    else ageScore = -15;
  }

  // 5) Whale-recent (dump) = negatief; geen data = neutraal.
  const whaleScore = snapshot.whaleInterestAt ? -10 : 0;

  // 6) Liquiditeit: minimale drempel al gepasseerd; hogere liq = iets veiliger.
  const liqScore = (snapshot.liquidityUsd ?? 0) > 0
    ? Math.min(8, Math.log10(Math.max(1, snapshot.liquidityUsd ?? 0)) * 1.2)
    : 0;

  const totalScore = dipScore + momScore + flowScore + ageScore + whaleScore + liqScore;
  return Number(Math.max(0, Math.min(100, totalScore)).toFixed(2));
}
