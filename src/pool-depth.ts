import type { PoolDepth } from './scoring.js';

export const LAMPORTS_PER_SOL = 1_000_000_000;

/** Default fee when the pool depth does not expose one (0.25% / 10000). */
const DEFAULT_FEE_NUM = 25;
const DEFAULT_FEE_DEN = 10_000;
const U64_MAX = 18_446_744_073_709_551_615n;
const CANONICAL_RAW = /^(?:0|[1-9][0-9]{0,19})$/;

type RawAmount = number | string;

function rawBigInt(value: RawAmount): bigint | null {
  if (typeof value === 'number') {
    return Number.isSafeInteger(value) && value >= 0 ? BigInt(value) : null;
  }
  if (!CANONICAL_RAW.test(value)) return null;
  const parsed = BigInt(value);
  return parsed <= U64_MAX ? parsed : null;
}

function feeParts(depth: PoolDepth): { numerator: bigint; denominator: bigint } | null {
  const numerator = depth.feeNumerator;
  const denominator = depth.feeDenominator;
  if (numerator === undefined && denominator === undefined) {
    return { numerator: BigInt(DEFAULT_FEE_NUM), denominator: BigInt(DEFAULT_FEE_DEN) };
  }
  if (!Number.isSafeInteger(numerator) || !Number.isSafeInteger(denominator)
    || (numerator as number) < 0 || (denominator as number) <= 0
    || (numerator as number) >= (denominator as number)) return null;
  return { numerator: BigInt(numerator as number), denominator: BigInt(denominator as number) };
}

function normalizedDepth(depth: PoolDepth): {
  baseReserve: bigint;
  quoteReserve: bigint;
  feeNumerator: bigint;
  feeDenominator: bigint;
} | null {
  const baseReserve = rawBigInt(depth.baseReserve);
  const quoteReserve = rawBigInt(depth.quoteReserve);
  const fee = feeParts(depth);
  if (baseReserve === null || baseReserve <= 0n || quoteReserve === null || quoteReserve <= 0n || !fee
    || !Number.isInteger(depth.baseDecimals) || depth.baseDecimals < 0 || depth.baseDecimals > 18
    || !Number.isInteger(depth.quoteDecimals) || depth.quoteDecimals < 0 || depth.quoteDecimals > 18) return null;
  return {
    baseReserve,
    quoteReserve,
    feeNumerator: fee.numerator,
    feeDenominator: fee.denominator,
  };
}

/** Exact integer base-token output in canonical raw-unit decimal form. */
export function constantProductBuyQuoteRaw(depth: PoolDepth, quoteInRaw: RawAmount): string | null {
  const normalized = normalizedDepth(depth);
  const quoteIn = rawBigInt(quoteInRaw);
  if (!normalized || quoteIn === null || quoteIn <= 0n) return null;
  const netQuoteNumerator = quoteIn * (normalized.feeDenominator - normalized.feeNumerator);
  const denominator = normalized.quoteReserve * normalized.feeDenominator + netQuoteNumerator;
  if (denominator <= 0n) return null;
  const baseOut = normalized.baseReserve * netQuoteNumerator / denominator;
  return baseOut > 0n && baseOut <= U64_MAX ? baseOut.toString() : null;
}

/** Exact integer quote-token output in canonical raw-unit decimal form. */
export function constantProductSellQuoteRaw(depth: PoolDepth, baseInRaw: RawAmount): string | null {
  const normalized = normalizedDepth(depth);
  const baseIn = rawBigInt(baseInRaw);
  if (!normalized || baseIn === null || baseIn <= 0n) return null;
  const netBaseNumerator = baseIn * (normalized.feeDenominator - normalized.feeNumerator);
  const denominator = normalized.baseReserve * normalized.feeDenominator + netBaseNumerator;
  if (denominator <= 0n) return null;
  const quoteOut = normalized.quoteReserve * netBaseNumerator / denominator;
  return quoteOut > 0n && quoteOut <= U64_MAX ? quoteOut.toString() : null;
}

/**
 * Compute the constant-product execution price for buying quote raw units.
 * Exact pool arithmetic is performed in bigint; this number is reporting-only.
 */
export function constantProductBuyPrice(depth: PoolDepth, quoteInRaw: RawAmount): number | null {
  const quoteIn = rawBigInt(quoteInRaw);
  const baseOutText = constantProductBuyQuoteRaw(depth, quoteInRaw);
  if (quoteIn === null || !baseOutText) return null;
  const price = Number(quoteIn) / Number(BigInt(baseOutText));
  return Number.isFinite(price) && price > 0 ? price : null;
}

/** Reporting-only quote/base execution-price ratio for an exact integer sell. */
export function constantProductSellPrice(depth: PoolDepth, baseInRaw: RawAmount): number | null {
  const baseIn = rawBigInt(baseInRaw);
  const quoteOutText = constantProductSellQuoteRaw(depth, baseInRaw);
  if (baseIn === null || !quoteOutText) return null;
  const price = Number(BigInt(quoteOutText)) / Number(baseIn);
  return Number.isFinite(price) && price > 0 ? price : null;
}

/** Reporting-only raw quote/base mid-price after exact reserve validation. */
export function constantProductMidPriceRaw(depth: PoolDepth): number | null {
  const normalized = normalizedDepth(depth);
  if (!normalized) return null;
  const price = Number(normalized.quoteReserve) / Number(normalized.baseReserve);
  return Number.isFinite(price) && price > 0 ? price : null;
}

/** Convert a USD position size into raw quote units. */
export function usdToQuoteRaw(usd: number, quoteDecimals: number, usdPerQuoteUnit: number): number {
  return (usd / usdPerQuoteUnit) * 10 ** quoteDecimals;
}

/** Convert raw base token count into base-units (reporting only). */
export function rawBaseToUnits(raw: RawAmount, baseDecimals: number): number {
  return Number(raw) / 10 ** baseDecimals;
}

/** Convert a raw quote/base ratio to USD per whole base token. */
export function rawQuoteToUsd(
  rawQuotePerRawBase: number,
  baseDecimals: number,
  quoteDecimals: number,
  usdPerQuoteUnit: number,
): number {
  return rawQuotePerRawBase * 10 ** (baseDecimals - quoteDecimals) * usdPerQuoteUnit;
}
