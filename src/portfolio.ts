import type { PaperConfig } from './config.js';
import type { PoolDepth } from './scoring.js';
import { constantProductSellPrice, constantProductBuyPrice, usdToQuoteRaw, rawQuoteToUsd } from './pool-depth.js';

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
   * Base-tokens (USD-waarde na fees) bij entry — laat exits met echte pool-diepte
   * de constant-product impact rekenen i.p.v. het exit-model met vlakke slippage
   * (P&L was systematisch te optimistisch op shallow pools; T1-B3).
   * Optioneel: legacy-records zonder dit veld vallen terug op vlakke slippage.
   */
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
  | { ok: false; reason: 'duplicate_position' | 'max_concurrent_positions' | 'daily_loss_limit' | 'insufficient_balance' | 'invalid_price' | 'position_sized_to_zero' };

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
function computeImpactEntryUsd(
  depth: PoolDepth | undefined,
  quoteInLamports: number,
  midPriceUsd: number,
  solPriceUsd: number | undefined,
  config: PaperConfig,
): number | null {
  if (!depth) return null;
  if (!Number.isFinite(midPriceUsd) || midPriceUsd <= 0) return null;
  if (solPriceUsd === undefined || !Number.isFinite(solPriceUsd) || solPriceUsd <= 0) return null;
  if (depth.quoteDecimals !== 9) {
    // Only supports WSOL-quoted pools for direct lamport mapping.
    return null;
  }
  const execRawQuotePerBase = constantProductBuyPrice(depth, quoteInLamports);
  if (execRawQuotePerBase === null) return null;
  const execUsd = rawQuoteToUsd(execRawQuotePerBase, depth.quoteDecimals, solPriceUsd);
  if (!Number.isFinite(execUsd) || execUsd <= 0) return null;
  // Never allow the live-model fill to be better (tax-free) than the published mid
  // price, but permit the full impact (adverse to the buyer of the base token).
  return Math.min(execUsd, midPriceUsd * (1 + config.simulatedSlippageBps / 10_000));
}

/** Compute a live-market exit fill price using pool depth for a position whose
 * remaining base holdings are `baseInRaw` (base token raw units). Returns USD
 * price per base token received, or null to fall back to flat slippage.
 */
function computeImpactExitUsd(
  depth: PoolDepth | undefined,
  baseInRaw: number,
  _midPriceUsd: number,
  solPriceUsd: number | undefined,
): number | null {
  if (!depth) return null;
  if (solPriceUsd === undefined || !Number.isFinite(solPriceUsd) || solPriceUsd <= 0) return null;
  const execRawQuotePerBase = constantProductSellPrice(depth, baseInRaw);
  if (execRawQuotePerBase === null) return null;
  const execUsd = rawQuoteToUsd(execRawQuotePerBase, depth.quoteDecimals, solPriceUsd);
  return Number.isFinite(execUsd) && execUsd > 0 ? execUsd : null;
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

export function enterPaperPosition(portfolio: Portfolio, input: EntryInput, config: PaperConfig): EntryResult {
  const currentPortfolio = normalizeDailyLoss(portfolio, input.at);
  if (!Number.isFinite(input.priceUsd) || input.priceUsd <= 0) return { ok: false, reason: 'invalid_price' };
  if (currentPortfolio.positions.some((position) => position.mint === input.mint)) return { ok: false, reason: 'duplicate_position' };
  if (currentPortfolio.positions.length >= config.maxConcurrentPositions) return { ok: false, reason: 'max_concurrent_positions' };
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
  const impactEntryPriceUsd = computeImpactEntryUsd(
    input.poolDepth,
    allocatedLamports,
    input.priceUsd,
    solPriceUsd,
    config,
  ) ?? input.priceUsd * fillMultiplier(config.simulatedSlippageBps, true);
  // Base-tokens (USD-waarde na entry-fees): basis voor de exit-impact-berekening.
  const baseTokensUsd = allocatedLamports / 1_000_000_000 * impactEntryPriceUsd;

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
    baseTokensUsd,
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

function exitPosition(portfolio: Portfolio, position: PaperPosition, priceUsd: number, reason: 'stop_loss' | 'trailing_stop' | 'max_hold' | 'time_stop', at: string, config: PaperConfig, poolDepth?: PoolDepth): { portfolio: Portfolio; event: PositionEvent } {
  const currentPortfolio = normalizeDailyLoss(portfolio, at);
  // Live-market exit fill: wanneer we echte pool-diepte + baseTokens hebben,
  // reken de constant-product price impact van de verkoop (was: altíjd vlakke
  // slippage → P&L systematisch te optimistisch op shallow pools; T1-B3).
  // Fallback: legacy-posities zonder baseTokensUsd → bestaande vlakke slippage.
  let exitUsd = priceUsd;
  if (poolDepth && position.baseTokensUsd !== undefined && Number.isFinite(position.baseTokensUsd) && position.baseTokensUsd > 0) {
    // Base-tokens in raw units ≈ USD-waarde bij entry / entry-prijs × 1e6
    // (6-dec base-token aanname — de dominante pump/AMMv4-tokenconventie;
    // de exacte decimals ontbreken in het positierecord, 1e6 is conservatief
    // en maakt de impact berekenbaar die de slippage-vlakke exit mist).
    const baseInRaw = Math.max(1, Math.round(position.baseTokensUsd / Math.max(position.entryPriceUsd, 1e-12) * 1_000_000));
    const impact = computeImpactExitUsd(poolDepth, baseInRaw, priceUsd, config.solPriceUsd);
    if (impact !== null && Number.isFinite(impact) && impact > 0) {
      // Exit-impact is altijd advers (verkoper ontvangt minder) — nooit beter
      // dan de published mid.
      exitUsd = Math.min(priceUsd, impact);
    }
  }
  const executablePrice = exitUsd * fillMultiplier(config.simulatedSlippageBps, false) * fillMultiplier(config.simulatedFeeBps, false);
  const grossProceeds = Math.floor(position.allocatedLamports * (executablePrice / position.entryPriceUsd));
  const proceedsLamports = Math.max(0, grossProceeds);
  const pnlLamports = proceedsLamports - position.entryCostLamports;
  const realizedLoss = Math.max(0, -pnlLamports);
  const isLoss = pnlLamports < 0;
  return {
    portfolio: {
      ...currentPortfolio,
      availableLamports: currentPortfolio.availableLamports + proceedsLamports,
      dailyRealizedLossLamports: currentPortfolio.dailyRealizedLossLamports + realizedLoss,
      positions: currentPortfolio.positions.filter((candidate) => candidate.pairId !== position.pairId || candidate.mint !== position.mint),
      consecutiveLosses: isLoss ? currentPortfolio.consecutiveLosses + 1 : 0,
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
    return exitPosition(currentPortfolio, position, currentPriceUsd, 'time_stop', at, config, poolDepth);
  }

  // --- Max hold ---
  if (heldMinutes >= config.maxHoldMinutes) {
    return exitPosition(currentPortfolio, position, currentPriceUsd, 'max_hold', at, config, poolDepth);
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
    return exitPosition(currentPortfolio, position, currentPriceUsd, 'stop_loss', at, config, poolDepth);
  }

  // --- Trailing stop (activated after take-profit threshold) ---
  const trailingActivated = highPriceUsd >= entryPrice * (1 + config.takeProfitPercent / 100);
  if (trailingActivated && currentPriceUsd <= highPriceUsd * (1 - config.trailingStopPercent / 100)) {
    return exitPosition(currentPortfolio, position, currentPriceUsd, 'trailing_stop', at, config, poolDepth);
  }

  const updated = { ...position, highPriceUsd };
  return {
    portfolio: { ...currentPortfolio, positions: currentPortfolio.positions.map((candidate) => candidate.pairId === identity.pairId && candidate.mint === identity.mint ? updated : candidate) },
    event: { type: 'hold' },
  };
}