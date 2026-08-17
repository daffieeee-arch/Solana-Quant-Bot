export const MAX_POSITION_QUOTE_AGE_MS = 2 * 60_000;

export type PositionQuoteFreshnessInput = {
  priceUsd: number;
  observedAt: string;
};

/** Shared production/research rule for position marks. */
export function isFreshPositionQuote(snapshot: PositionQuoteFreshnessInput, now: Date): boolean {
  const observedAt = Date.parse(snapshot.observedAt);
  const ageMs = now.getTime() - observedAt;
  return Number.isFinite(snapshot.priceUsd)
    && snapshot.priceUsd > 0
    && Number.isFinite(observedAt)
    && ageMs >= 0
    && ageMs <= MAX_POSITION_QUOTE_AGE_MS;
}
