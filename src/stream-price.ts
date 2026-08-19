import type { PoolDepth } from './scoring.js';
import { constantProductMidPriceRaw } from './pool-depth.js';

/**
 * Streaming self-calculated price for pools discovered via Triton Vixen.
 *
 * For Raydium AMMv4/CPMM, the provider reads live pool reserves (via reserve vault
 * accounts) and computes the fair-value spot price here — entirely event-driven,
 * no REST/indexer dependency. The correct USD-per-quote-unit is applied per pool
 * based on its quote mint (WSOL => SOL/USD, USDC/USDT => 1.0).
 */

/** WSOL mint (Solana). */
export const WSOL_MINT = 'So11111111111111111111111111111111111111112';
/** USDC mint. */
export const USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';
/** USDT mint. */
export const USDT_MINT = 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB';

/**
 * Fair-value spot price (base token -> USD) from a PoolDepth, given a USD price
 * for one quote unit (SOL/USD, or 1.0 for USDC/USDT). No impact — the raw mid:
 *   price(quote per base) = quoteReserve / baseReserve   (decimals cancel)
 *   price(USD per base)   = that * usdPerQuoteUnit
 * Returns null on invalid depth/fee so callers fail closed.
 */
export function spotPriceUsd(depth: PoolDepth, usdPerQuoteUnit: number): number | null {
  if (!Number.isFinite(usdPerQuoteUnit) || usdPerQuoteUnit <= 0) return null;
  if (!Number.isInteger(depth.baseDecimals) || depth.baseDecimals < 0 || depth.baseDecimals > 18) return null;
  if (!Number.isInteger(depth.quoteDecimals) || depth.quoteDecimals < 0 || depth.quoteDecimals > 18) return null;
  // Raw-count ratio, scaled to quote-units per base-unit. Both reserves are raw
  // counts; the stored calories differ per token, so we compensate:
  //   price(baseUnits->quoteUnits) = (quoteReserve/10^quoteDecimals) / (baseReserve/10^baseDecimals)
  //                                 = (quoteReserve/baseReserve) * 10^(baseDecimals - quoteDecimals)
  const rawPerBase = constantProductMidPriceRaw(depth);
  if (rawPerBase === null) return null;
  const quotePerBase = rawPerBase * 10 ** (depth.baseDecimals - depth.quoteDecimals);
  return quotePerBase * usdPerQuoteUnit;
}

/** USD price of one quote unit for a known Solana quote mint: 1.0 for USDC/USDT,
 * else the caller-provided SOL/USD for WSOL, otherwise null (unknown). */
export function usdPerQuoteUnit(quoteMint: string, solPriceUsd: number): number | null {
  if (!quoteMint) return null;
  if (quoteMint === USDC_MINT) return 1;
  if (quoteMint === USDT_MINT) return 1;
  if (quoteMint === WSOL_MINT) {
    return Number.isFinite(solPriceUsd) && solPriceUsd > 0 ? solPriceUsd : null;
  }
  return null;
}
