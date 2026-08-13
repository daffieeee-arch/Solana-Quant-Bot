import type { PaperPosition } from './portfolio.js';

/** Fase-Q: quarantine/accounting — append-only administratieve state-transitie
 *  voor legacy posities zonder canonical market identity. GEEN fictieve exit,
 *  realizedPnL blijft UNKNOWN, uit actieve concurrency, idempotent. */

export const REQUARANTINE_REASON_CODE = 'MISSING_CANONICAL_MARKET_IDENTITY';

export type PricingStatus = 'LIVE_PRICED' | 'QUOTE_PRICED' | 'STALE_LAST_KNOWN' | 'UNPRICED' | 'UNROUTABLE' | 'STALE';
export type AccountingStatus = 'UNKNOWN' | 'REALIZED' | 'IMPAIRMENT';
export type RecoveryStatus = 'NONE' | 'HISTORICAL_RECOVERY_PENDING' | 'RECOVERY_CANDIDATE' | 'RECOVERED';

export type QuarantineRecord = {
  /** canonical trade/position ID = tradeId (immutable, uniek, stabiel) */
  tradeId: string;
  mint: string;
  symbol: string;
  pairId: string;
  openedAt: string;
  entryPriceUsd: number;
  allocatedLamports: number;
  entryCostLamports: number;
  quarantinedAt: string;
  reasonCode: string;
  evidence: string;
  pricingStatus: PricingStatus;
  accountingStatus: AccountingStatus;
  recoveryStatus: RecoveryStatus;
  schemaVersion: number;
};

export type QuarantineResult =
  | { ok: true; duplicate?: boolean }
  | { ok: false; reason: string };

/** Quarantaine — append-only, idempotent. Retourneert duplicate=true als al gedaan. */
export function quarantineLegacyPosition(
  records: Map<string, QuarantineRecord>,
  position: PaperPosition,
  quarantinedAt: string,
  recoveryStatus?: RecoveryStatus,
): QuarantineResult {
  if (!position?.tradeId) return { ok: false, reason: 'missing_trade_id' };
  if (records.has(position.tradeId)) return { ok: true, duplicate: true };
  const record: QuarantineRecord = {
    tradeId: position.tradeId,
    mint: position.mint,
    symbol: position.symbol,
    pairId: position.pairId,
    openedAt: position.openedAt,
    entryPriceUsd: position.entryPriceUsd,
    allocatedLamports: position.allocatedLamports,
    entryCostLamports: position.entryCostLamports,
    quarantinedAt,
    reasonCode: REQUARANTINE_REASON_CODE,
    evidence: 'no canonical pool/curve/vault identiteit in entry-record (gx:<mint>-only)',
    pricingStatus: 'UNPRICED',
    accountingStatus: 'UNKNOWN',
    recoveryStatus: recoveryStatus ?? 'NONE',
    schemaVersion: 1,
  };
  records.set(position.tradeId, record);
  return { ok: true };
}

export function isPositionQuarantined(records: Map<string, QuarantineRecord>, tradeId: string): boolean {
  return records.has(tradeId);
}

/** Filtere actieve (niet-gequarantined) posities voor concurrency-staat. */
export function activePositions(records: Map<string, QuarantineRecord>, positions: readonly PaperPosition[]): PaperPosition[] {
  return positions.filter((p) => !records.has(p.tradeId));
}
