import type { PaperConfig } from './config.js';
import type { PoolDepth } from './scoring.js';
import {
  constantProductBuyPrice, constantProductBuyQuoteRaw, constantProductSellQuoteRaw,
  usdToQuoteRaw, rawQuoteToUsd,
} from './pool-depth.js';

const LAMPORTS_PER_SOL = 1_000_000_000;

export type PaperPosition = {
  tradeId: string;
  learningSchemaVersion?: 2;
  pairId: string;
  mint: string;
  symbol: string;
  openedAt: string;
  entryPriceUsd: number;
  highPriceUsd: number;
  allocatedLamports: number;
  entryCostLamports: number;
  /** Volatility-adjusted stop loss percent at entry time */
  dynamicStopPercent: number;
  /**
   * Actual base-token quantity received from the depth-aware entry, in raw units,
   * with its decimal domain pinned for exact depth-aware exits. Legacy positions
   * omit both fields and retain the flat-slippage fallback.
   */
  baseAmountRaw?: string | number;
  baseDecimals?: number;
  /** USD notional of the actual base quantity at the recorded entry fill. */
  baseTokensUsd?: number;
};

export type Portfolio = {
  createdAt: string;
  availableLamports: number;
  dailyRealizedLossLamports: number;
  dailyLossDateUtc: string;
  positions: PaperPosition[];
  /** Consecutive losing paper trades (reset on win) */
  consecutiveLosses: number;
};

export function makeTradeId(mint: string, openedAt: string): string {
  return `${mint}:${openedAt}`;
}

export type PositionEvent =
  | { type: 'hold' }
  | { type: 'exit'; reason: 'stop_loss' | 'trailing_stop' | 'max_hold' | 'time_stop'; proceedsLamports: number; pnlLamports: number };

type EntryInput = { pairId: string; mint: string; symbol: string; priceUsd: number; at: string; score: number; liquidityUsd?: number; priceChangeM5Percent?: number; poolDepth?: PoolDepth; solPriceUsd?: number };
type EntryResult =
  | { ok: true; portfolio: Portfolio; position: PaperPosition }
  | { ok: false; reason: 'duplicate_position' | 'max_concurrent_positions' | 'daily_loss_limit' | 'insufficient_balance' | 'invalid_price' | 'invalid_pool_depth' | 'position_sized_to_zero' };

function toLamports(sol: number): number {
  return Math.round(sol * LAMPORTS_PER_SOL);
}
function toSol(lamports: number): number {
  return lamports / LAMPORTS_PER_SOL;
}

function utcDay(value: string): string | undefined {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString().slice(0, 10) : undefined;
}

function normalizeDailyLoss(portfolio: Portfolio, at: string): Portfolio {
  const eventDay = utcDay(at);
  if (!eventDay) return portfolio;
  const storedDay = portfolio.dailyLossDateUtc ?? utcDay(portfolio.createdAt);
  if (storedDay === eventDay && portfolio.dailyLossDateUtc === eventDay) return portfolio;
  return {
    ...portfolio,
    dailyRealizedLossLamports: storedDay === eventDay ? portfolio.dailyRealizedLossLamports : 0,
    dailyLossDateUtc: eventDay,
  };
}

function fillMultiplier(bps: number, adverse: boolean): number {
  return adverse ? 1 + bps / 10_000 : 1 - bps / 10_000;
}

/**
 * Compute a live-market entry fill price using the pool's constant-product depth.
 * `quoteInLamports` is the SOL position size (WSOL-quoted pools have 9-decimal
 * quoteReserve, so quoteInRaw == lamports). Returns the impact-adjusted USD entry
 * price for the base token, or null if depth/price inputs are invalid (caller
 * falls back to flat slippage).
 */
function computeImpactEntry(
  depth: PoolDepth | undefined,
  quoteInLamports: number,
  midPriceUsd: number,
  solPriceUsd: number | undefined,
): { priceUsd: number; baseAmountRaw: string; baseDecimals: number } | null {
  if (!depth) return null;
  if (!Number.isFinite(midPriceUsd) || midPriceUsd <= 0) return null;
  if (solPriceUsd === undefined || !Number.isFinite(solPriceUsd) || solPriceUsd <= 0) return null;
  if (depth.quoteDecimals !== 9) return null;
  const execRawQuotePerBase = constantProductBuyPrice(depth, quoteInLamports);
  const baseAmountRaw = constantProductBuyQuoteRaw(depth, quoteInLamports);
  if (execRawQuotePerBase === null || baseAmountRaw === null) return null;
  const execUsd = rawQuoteToUsd(
    execRawQuotePerBase,
    depth.baseDecimals,
    depth.quoteDecimals,
    solPriceUsd,
  );
  if (!Number.isFinite(execUsd) || execUsd <= 0 || BigInt(baseAmountRaw) <= 0n) return null;
  return {
    // A depth-aware fill can never beat the published mid, while genuine adverse
    // constant-product impact is never capped by the flat fallback slippage.
    priceUsd: Math.max(execUsd, midPriceUsd),
    baseAmountRaw,
    baseDecimals: depth.baseDecimals,
  };
}

/** Compute exact integer raw WSOL proceeds for the stored raw base quantity. */
function computeImpactExitQuoteRaw(
  depth: PoolDepth | undefined,
  baseInRaw: string | number,
  baseDecimals: number,
): number | null {
  if (!depth || depth.quoteDecimals !== 9 || depth.baseDecimals !== baseDecimals) return null;
  const quoteOutRaw = constantProductSellQuoteRaw(depth, baseInRaw);
  if (quoteOutRaw === null) return null;
  const raw = BigInt(quoteOutRaw);
  return raw > 0n && raw <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(raw) : null;
}

function validStoredRawAmount(value: string | number): boolean {
  if (typeof value === 'number') return Number.isSafeInteger(value) && value > 0;
  return /^(?:[1-9][0-9]{0,19})$/.test(value) && BigInt(value) <= 18_446_744_073_709_551_615n;
}

function applyFeeBpsFloor(rawAmount: number, feeBps: number): number | null {
  if (!Number.isSafeInteger(rawAmount) || rawAmount <= 0
    || !Number.isSafeInteger(feeBps) || feeBps < 0 || feeBps >= 10_000) return null;
  const result = BigInt(rawAmount) * BigInt(10_000 - feeBps) / 10_000n;
  return result <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(result) : null;
}

/**
 * Compute a volatility-adjusted stop loss percent.
 * Wider stop for volatile pairs, tighter for stable ones.
 * Clamped to [minStopLossPercent, maxStopLossPercent].
 */
export function computeDynamicStop(priceChangeM5Percent: number | undefined, _score: number, config: PaperConfig): number {
  if (priceChangeM5Percent === undefined) return config.minStopLossPercent;
  const volatility = Math.abs(priceChangeM5Percent);
  const dynamic = Math.max(config.minStopLossPercent, volatility * config.stopVolatilityMultiplier);
  return Math.min(dynamic, config.maxStopLossPercent);
}

/**
 * Score-weighted position size with liquidity cap and consecutive-loss reduction.
 */
export function computePositionSize(
  score: number,
  liquidityUsd: number | undefined,
  solPriceUsd: number | undefined,
  config: PaperConfig,
  consecutiveLosses: number,
): number {
  const scoreFraction = Math.min(1, score / config.positionScoreDivisor);
  let baseSize = scoreFraction * config.maxPositionSol;

  // Liquidity cap: position value <= fraction of pool liquidity. Bij ontbrekende
  // SOL/USD-prijs de cap OVERSLAAN (sizen op score×max) i.p.v. 0 retourneren —
  // 0 verlamde de hele bot stil (elke entry werd position_sized_to_zero zodra
  // liquidityUsd bekend was en solPriceUsd tijdelijk niet opgehaald kon worden).
  if (liquidityUsd !== undefined) {
    if (solPriceUsd !== undefined && Number.isFinite(solPriceUsd) && solPriceUsd > 0) {
      const liquidityCapSol = (liquidityUsd * config.liquidityPositionFraction) / solPriceUsd;
      baseSize = Math.min(baseSize, liquidityCapSol);
    }
  }

  // Consecutive loss reduction (softer: -25% at 3, -50% at 5 instead of -50%/-75%)
  if (consecutiveLosses >= 5) baseSize *= 0.5;
  else if (consecutiveLosses >= 3) baseSize *= 0.75;

  return Math.max(0, Math.min(baseSize, config.maxPositionSol));
}

export function createPortfolio(config: PaperConfig, createdAt: string): Portfolio {
  return {
    createdAt,
    availableLamports: toLamports(config.paperStartingSol),
    dailyRealizedLossLamports: 0,
    dailyLossDateUtc: utcDay(createdAt) ?? new Date(0).toISOString().slice(0, 10),
    positions: [],
    consecutiveLosses: 0,
  };
}

export function enterPaperPosition(portfolio: Portfolio, input: EntryInput, config: PaperConfig, quarantinedTradeIds?: ReadonlySet<string>): EntryResult {
  const currentPortfolio = normalizeDailyLoss(portfolio, input.at);
  if (!Number.isFinite(input.priceUsd) || input.priceUsd <= 0) return { ok: false, reason: 'invalid_price' };
  if (currentPortfolio.positions.some((position) => position.mint === input.mint)) return { ok: false, reason: 'duplicate_position' };
  // Fase-Q: concurrency-cap telt alleen ACTIEVE posities; gequarantinede legacy
  // posities (zonder canonical market identity) nemen geen slots in beslag.
  const activeCount = quarantinedTradeIds && quarantinedTradeIds.size > 0
    ? currentPortfolio.positions.filter((p) => !quarantinedTradeIds.has(p.tradeId)).length
    : currentPortfolio.positions.length;
  if (activeCount >= config.maxConcurrentPositions) return { ok: false, reason: 'max_concurrent_positions' };
  if (currentPortfolio.dailyRealizedLossLamports >= toLamports(config.maxDailyLossSol)) return { ok: false, reason: 'daily_loss_limit' };

  const allocatedLamports = toLamports(computePositionSize(input.score, input.liquidityUsd, config.solPriceUsd, config, currentPortfolio.consecutiveLosses));
  if (allocatedLamports <= 0) return { ok: false, reason: 'position_sized_to_zero' };

  const entryCostLamports = Math.ceil(allocatedLamports * fillMultiplier(config.simulatedFeeBps, true));
  if (currentPortfolio.availableLamports < entryCostLamports) return { ok: false, reason: 'insufficient_balance' };

  // Live-market entry fill: when we have real pool depth, compute the actual
  // constant-product price impact of the position size instead of a flat slippage.
  // The position is quoted in SOL lamports; a SOL-quoted pool depth has quoteReserve
  // in WSOL raw units (9 decimals), so quoteInRaw == allocatedLamports.
  const solPriceUsd = input.solPriceUsd ?? config.solPriceUsd;
  const impactEntry = computeImpactEntry(
    input.poolDepth,
    allocatedLamports,
    input.priceUsd,
    solPriceUsd,
  );
  if (input.poolDepth && !impactEntry) return { ok: false, reason: 'invalid_pool_depth' };
  const impactEntryPriceUsd = impactEntry?.priceUsd
    ?? input.priceUsd * fillMultiplier(config.simulatedSlippageBps, true);
  const baseTokensUsd = impactEntry
    ? Number(impactEntry.baseAmountRaw) / 10 ** impactEntry.baseDecimals * impactEntryPriceUsd
    : solPriceUsd === undefined ? undefined : allocatedLamports / LAMPORTS_PER_SOL * solPriceUsd;

  const dynamicStop = computeDynamicStop(input.priceChangeM5Percent, input.score, config);
  const position: PaperPosition = {
    tradeId: makeTradeId(input.mint, input.at),
    learningSchemaVersion: 2,
    pairId: input.pairId,
    mint: input.mint,
    symbol: input.symbol,
    openedAt: input.at,
    entryPriceUsd: impactEntryPriceUsd,
    highPriceUsd: impactEntryPriceUsd,
    allocatedLamports,
    entryCostLamports,
    dynamicStopPercent: dynamicStop,
    ...(impactEntry ? {
      baseAmountRaw: impactEntry.baseAmountRaw,
      baseDecimals: impactEntry.baseDecimals,
    } : {}),
    ...(baseTokensUsd === undefined ? {} : { baseTokensUsd }),
  };
  return {
    ok: true,
    position,
    portfolio: {
      ...currentPortfolio,
      availableLamports: currentPortfolio.availableLamports - entryCostLamports,
      positions: [...currentPortfolio.positions, position],
    },
  };
}

function exitPosition(portfolio: Portfolio, position: PaperPosition, priceUsd: number, reason: 'stop_loss' | 'trailing_stop' | 'max_hold' | 'time_stop', at: string, config: PaperConfig, poolDepth: PoolDepth | undefined, holdPortfolio: Portfolio): { portfolio: Portfolio; event: PositionEvent } {
  const currentPortfolio = normalizeDailyLoss(portfolio, at);
  const hasDepthAwareIdentity = position.baseAmountRaw !== undefined || position.baseDecimals !== undefined;
  const depthQuoteOutRaw = position.baseAmountRaw !== undefined
    && validStoredRawAmount(position.baseAmountRaw)
    && position.baseDecimals !== undefined
    && Number.isInteger(position.baseDecimals) && position.baseDecimals >= 0 && position.baseDecimals <= 18
    ? computeImpactExitQuoteRaw(poolDepth, position.baseAmountRaw, position.baseDecimals)
    : null;
  // A persisted raw-token identity may only exit against compatible executable
  // reserves. Missing, malformed, or decimal-mismatched depth cannot be replaced by
  // legacy price-ratio accounting without fabricating economic proceeds.
  if (hasDepthAwareIdentity && depthQuoteOutRaw === null) {
    return { portfolio: holdPortfolio, event: { type: 'hold' } };
  }
  // Pool execution already includes its configured fee and full price impact. Apply
  // only the separate simulated execution fee; do not add the flat slippage fallback
  // a second time. Legacy positions without pinned raw quantity retain ratio pricing.
  const legacyGrossProceeds = depthQuoteOutRaw === null
    ? position.allocatedLamports * (
      priceUsd
      * fillMultiplier(config.simulatedSlippageBps, false)
      * fillMultiplier(config.simulatedFeeBps, false)
      / position.entryPriceUsd
    )
    : null;
  const exactDepthProceeds = depthQuoteOutRaw === null ? null : applyFeeBpsFloor(depthQuoteOutRaw, config.simulatedFeeBps);
  if (depthQuoteOutRaw !== null && exactDepthProceeds === null) {
    return { portfolio: holdPortfolio, event: { type: 'hold' } };
  }
  const proceedsLamports = exactDepthProceeds ?? Math.max(0, Math.floor(legacyGrossProceeds as number));
  const pnlLamports = proceedsLamports - position.entryCostLamports;
  const realizedLoss = Math.max(0, -pnlLamports);
  const isLoss = pnlLamports < 0;
  const nextAvailableLamports = currentPortfolio.availableLamports + proceedsLamports;
  const nextDailyRealizedLossLamports = currentPortfolio.dailyRealizedLossLamports + realizedLoss;
  const nextConsecutiveLosses = isLoss ? currentPortfolio.consecutiveLosses + 1 : 0;
  if (!Number.isSafeInteger(proceedsLamports) || proceedsLamports < 0
    || !Number.isSafeInteger(pnlLamports)
    || !Number.isSafeInteger(nextAvailableLamports) || nextAvailableLamports < 0
    || !Number.isSafeInteger(nextDailyRealizedLossLamports) || nextDailyRealizedLossLamports < 0
    || !Number.isSafeInteger(nextConsecutiveLosses) || nextConsecutiveLosses < 0) {
    return { portfolio: holdPortfolio, event: { type: 'hold' } };
  }
  return {
    portfolio: {
      ...currentPortfolio,
      availableLamports: nextAvailableLamports,
      dailyRealizedLossLamports: nextDailyRealizedLossLamports,
      positions: currentPortfolio.positions.filter((candidate) => candidate.pairId !== position.pairId || candidate.mint !== position.mint),
      consecutiveLosses: nextConsecutiveLosses,
    },
    event: { type: 'exit', reason, proceedsLamports, pnlLamports },
  };
}

export function evaluateOpenPosition(portfolio: Portfolio, identity: Pick<PaperPosition, 'pairId' | 'mint'>, currentPriceUsd: number, at: string, config: PaperConfig, poolDepth?: PoolDepth): { portfolio: Portfolio; event: PositionEvent } {
  const currentPortfolio = normalizeDailyLoss(portfolio, at);
  const position = currentPortfolio.positions.find((candidate) => candidate.pairId === identity.pairId && candidate.mint === identity.mint);
  if (!position || !Number.isFinite(currentPriceUsd) || currentPriceUsd <= 0) return { portfolio: currentPortfolio, event: { type: 'hold' } };

  const heldMs = Date.parse(at) - Date.parse(position.openedAt);
  const heldMinutes = heldMs / 60_000;
  const entryPrice = position.entryPriceUsd;
  const changePercent = ((currentPriceUsd - entryPrice) / entryPrice) * 100;

  // --- Time stop: exit if no meaningful profit after half of max hold ---
  if (heldMinutes >= config.maxHoldMinutes / 2 && changePercent < config.minProfitForContinuePercent / 2) {
    return exitPosition(currentPortfolio, position, currentPriceUsd, 'time_stop', at, config, poolDepth, portfolio);
  }

  // --- Max hold ---
  if (heldMinutes >= config.maxHoldMinutes) {
    return exitPosition(currentPortfolio, position, currentPriceUsd, 'max_hold', at, config, poolDepth, portfolio);
  }

  const highPriceUsd = Math.max(position.highPriceUsd, currentPriceUsd);
  const breakevenTriggered = highPriceUsd >= entryPrice * (1 + config.breakevenTriggerPercent / 100);

  // --- Dynamic stop loss ---
  const stopPercent = breakevenTriggered ? 0 : position.dynamicStopPercent;
  // Grace period may widen a tight stop, but never beyond the configured hard maximum.
  const graceMultiplier = heldMinutes < 1.5 ? 2.0 : 1.0;
  const effectiveStopPercent = Math.min(config.maxStopLossPercent, stopPercent * graceMultiplier);
  // The breakeven buffer avoids an instant trigger but remains inside the absolute hard maximum.
  const breakevenBufferPercent = Math.min(0.2, config.maxStopLossPercent);
  const effectiveStopPrice = breakevenTriggered
    ? entryPrice * (1 - breakevenBufferPercent / 100)
    : entryPrice * (1 - effectiveStopPercent / 100);

  if (currentPriceUsd <= effectiveStopPrice) {
    return exitPosition(currentPortfolio, position, currentPriceUsd, 'stop_loss', at, config, poolDepth, portfolio);
  }

  // --- Trailing stop (activated after take-profit threshold) ---
  const trailingActivated = highPriceUsd >= entryPrice * (1 + config.takeProfitPercent / 100);
  if (trailingActivated && currentPriceUsd <= highPriceUsd * (1 - config.trailingStopPercent / 100)) {
    return exitPosition(currentPortfolio, position, currentPriceUsd, 'trailing_stop', at, config, poolDepth, portfolio);
  }

  const updated = { ...position, highPriceUsd };
  return {
    portfolio: { ...currentPortfolio, positions: currentPortfolio.positions.map((candidate) => candidate.pairId === identity.pairId && candidate.mint === identity.mint ? updated : candidate) },
    event: { type: 'hold' },
  };
}