import type { PaperConfig } from './config.js';
import type { MarketContext } from './providers/market-context.js';

export const MAX_SOL_PRICE_AGE_MS = 5 * 60_000;

export function withFreshSolPrice(
  config: PaperConfig,
  context: MarketContext | undefined,
  now: Date = new Date(),
): PaperConfig {
  const updatedAt = context ? Date.parse(context.updatedAt) : Number.NaN;
  const ageMs = now.getTime() - updatedAt;
  const livePrice = context?.ticker.find((ticker) => ticker.symbol === 'SOL')?.priceUsd;
  const valid = Number.isFinite(updatedAt)
    && ageMs >= 0
    && ageMs <= MAX_SOL_PRICE_AGE_MS
    && Number.isFinite(livePrice)
    && (livePrice ?? 0) > 0;
  return { ...config, solPriceUsd: valid ? livePrice : undefined };
}
