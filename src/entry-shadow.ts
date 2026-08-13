import type { MarketIdentity } from './market-identity2.js';
import { isCompleteIdentity } from './market-identity2.js';
import type { Decimals } from './decimals-resolver.js';

/** Fase-QH: entry-gate in shadow-modus.
 *  Evalueert een kandidaat tegen de volledige fail-closed-entry-contract maar
 *  OPENT GEEN positie. Rapporteert WOULD_ACCEPT / WOULD_REJECT + reasonCode. */

export type ShadowVerdict =
  | { verdict: 'WOULD_ACCEPT'; reasonCode?: undefined }
  | { verdict: 'WOULD_REJECT'; reasonCode: string };

export type ShadowEvaluation = {
  identity?: MarketIdentity;
  decimals?: Decimals;
  /** ouderdom van de market-data (ms) */
  marketFreshMs: number;
  nowMs: number;
  maxAgeMs: number;
};

export function evaluateEntryShadow(e: ShadowEvaluation): ShadowVerdict {
  // 1. canonical market identity (protocol-specifiek, nooit gx:<mint>-only)
  if (!e.identity) return { verdict: 'WOULD_REJECT', reasonCode: 'missing_canonical_market_identity' };
  if (!isCompleteIdentity(e.identity)) return { verdict: 'WOULD_REJECT', reasonCode: 'invalid_market_identity' };
  // 2. decimals beschikbaar
  if (!e.decimals || !e.decimals.base || !e.decimals.quote) return { verdict: 'WOULD_REJECT', reasonCode: 'missing_decimals' };
  // 3. market activity voldoende recent
  if (e.marketFreshMs > e.maxAgeMs) return { verdict: 'WOULD_REJECT', reasonCode: 'stale_market_data' };
  // 4. entry-pricebron/timestamp aanwezig
  if (!e.identity.sourceTimestamp || !e.identity.entryPriceSource) return { verdict: 'WOULD_REJECT', reasonCode: 'missing_entry_price_source' };
  // 5. ongoing mark-source + bounded exit-fallback
  if (!e.identity.markPriceSource) return { verdict: 'WOULD_REJECT', reasonCode: 'missing_mark_price_source' };
  if (!(e.identity as { boundedExitFallback?: boolean }).boundedExitFallback) return { verdict: 'WOULD_REJECT', reasonCode: 'missing_exit_fallback' };
  return { verdict: 'WOULD_ACCEPT' };
}
