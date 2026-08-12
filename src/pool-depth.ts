import type { PoolDepth } from './scoring.js';

export const LAMPORTS_PER_SOL = 1_000_000_000;

/** Default fee when the pool depth does not expose one (0.25% / 10000). */
const DEFAULT_FEE_NUM = 25;
const DEFAULT_FEE_DEN = 10_000;

/**
 * Compute the constant-product (k = x*y) execution price for buying `quoteAmountUsd`
 * worth of base tokens against a live pool, INCLUDING fee and price impact.
 *
 * For a constant-product pool with base reserve B and quote reserve Q (both in
 * raw units matching their decimals), buying an amount `quoteInRaw` shifts:
 *   Q' = Q + quoteInRaw                   (quote added to pool)
 *   B' = B * Q / Q'                       (base removed, constant product)
 *   tokenOutRaw = B - B' = B * quoteInRaw / (Q + quoteInRaw)
 *
 * Fee is applied to the input first (input_after_fee = input * (1 - fee)).
 * The resulting marginal execution price (quote per base) is:
 *   execPriceRaw = quoteInRaw / tokenOutRaw
 * returns USD price scaled to quote/base decimals.
 *
 * @returns the per-base-token execution price in the same units as quotePerBaseMid
 *   (i.e. quote-currency per 1 base token), or null if input is invalid.
 */
export function constantProductBuyPrice(depth: PoolDepth, quoteInRaw: number): number | null {
  if (!Number.isFinite(quoteInRaw) || quoteInRaw <= 0) return null;
  if (!isValidDepth(depth)) return null;

  const feeNum = Number.isFinite(depth.feeNumerator) && depth.feeDenominator
    ? depth.feeNumerator!
    : DEFAULT_FEE_NUM;
  const feeDen = Number.isFinite(depth.feeDenominator) ? depth.feeDenominator! : DEFAULT_FEE_DEN;
  if (feeDen <= 0) return null;

  const fee = feeNum / feeDen; // e.g. 0.0025
  const inputAfterFee = quoteInRaw * (1 - fee);
  if (inputAfterFee <= 0) return null;

  // Constant product
  const qAfter = depth.quoteReserve + inputAfterFee;
  if (qAfter <= 0) return null;
  const tokenOutRaw = depth.baseReserve * inputAfterFee / qAfter;
  if (tokenOutRaw <= 0) return null;

  // Execution price: quote spent (gross, incl fee) per base token received
  const execPriceRaw = quoteInRaw / tokenOutRaw;
  return execPriceRaw;
}

/**
 * Compute the constant-product execution price for selling `baseAmountRaw` of the
 * base token (returns quote), analogous to a pool exit. Used for paper exits so
 * slippage reflects live depth.
 */
export function constantProductSellPrice(depth: PoolDepth, baseInRaw: number): number | null {
  if (!Number.isFinite(baseInRaw) || baseInRaw <= 0) return null;
  if (!isValidDepth(depth)) return null;

  const feeNum = Number.isFinite(depth.feeNumerator) && depth.feeDenominator
    ? depth.feeNumerator!
    : DEFAULT_FEE_NUM;
  const feeDen = Number.isFinite(depth.feeDenominator) ? depth.feeDenominator! : DEFAULT_FEE_DEN;
  if (feeDen <= 0) return null;

  const fee = feeNum / feeDen;
  const inputAfterFee = baseInRaw * (1 - fee);
  if (inputAfterFee <= 0) return null;

  const bAfter = depth.baseReserve + inputAfterFee;
  if (bAfter <= 0) return null;
  const quoteOutRaw = depth.quoteReserve * inputAfterFee / bAfter;
  if (quoteOutRaw <= 0) return null;

  // Execution price: quote received per base token sold (gross input incl fee)
  return quoteOutRaw / baseInRaw;
}

/**
 * Convert a USD position size into raw quote units given the pool's quote
 * reserve decimals and a USD-per-quote-unit reference (e.g. SOL price in USD).
 */
export function usdToQuoteRaw(usd: number, quoteDecimals: number, usdPerQuoteUnit: number): number {
  return (usd / usdPerQuoteUnit) * 10 ** quoteDecimals;
}

/**
 * Convert raw base token count into base-units (for reporting position size).
 */
export function rawBaseToUnits(raw: number, baseDecimals: number): number {
  return raw / 10 ** baseDecimals;
}

/** Convert an execution price in quote-units-per-base-token (ratio cancels both
 * decimals) to a USD price for the base token. `usdPerQuoteUnit` is e.g. SOL/USD. */
export function rawQuoteToUsd(quotePerBase: number, _quoteDecimals: number, usdPerQuoteUnit: number): number {
  return quotePerBase * usdPerQuoteUnit;
}

function isValidDepth(depth: PoolDepth): boolean {
  return Number.isFinite(depth.baseReserve) && depth.baseReserve > 0
    && Number.isFinite(depth.quoteReserve) && depth.quoteReserve > 0
    && Number.isInteger(depth.baseDecimals) && depth.baseDecimals >= 0 && depth.baseDecimals <= 18
    && Number.isInteger(depth.quoteDecimals) && depth.quoteDecimals >= 0 && depth.quoteDecimals <= 18;
}
