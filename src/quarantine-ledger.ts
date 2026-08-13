import type { PaperLedger } from './ledger.js';
import type { PaperLedgerStore } from './ledger.js';
import type { PaperPosition } from './portfolio.js';
import { REQUARANTINE_REASON_CODE } from './accounting.js';

/** Fase-QH: ledger-authoritative quarantine.
 *  De WAL/ledger is (en blijft) de enige authoritative source of truth. Quarantaine
 *  wordt doorgevoerd als append-only administratief WAL-event `position_quarantined`;
 *  de positie wordt UIT de portfolio-state verwijderd ZONDER paper_exit / exitprijs
 *  / realized-PnL. QuarantineStore mag enkel blijven bestaan als afgeleide projectie
 *  die volledig uit WAL-replay herbouwd kan worden. */

export const QUARANTINE_EVENT_TYPE = 'position_quarantined';
export const CAPITAL_ADJUSTMENT_EVENT_TYPE = 'administrative_capital_adjustment';

export type PositionQuarantinedEvent = {
  type: typeof QUARANTINE_EVENT_TYPE;
  at: string;
  migrationId: string;
  tradeId: string;
  reasonCode: string;
  accountingStatus: 'UNKNOWN';
  pricingStatus: 'UNPRICED';
  recoveryStatus: 'NONE' | 'RECOVERY_CANDIDATE';
  schemaVersion: number;
  evidence: string;
};

export type QuarantineOneResult =
  | { ok: true; ledger: PaperLedger; event: PositionQuarantinedEvent }
  | { ok: false; reason: string };

/** Quarantaine van één exacte positie via een WAL-event (idempotent). */
export async function quarantinePositionViaLedger(
  store: { save(ledger: PaperLedger, event?: PositionQuarantinedEvent): Promise<void> },
  ledger: PaperLedger,
  position: PaperPosition,
  at: string,
  migrationId: string,
  recoveryStatus: 'NONE' | 'RECOVERY_CANDIDATE',
): Promise<QuarantineOneResult> {
  if (!position?.tradeId) return { ok: false, reason: 'missing_trade_id' };
  // idempotent: afwezig in portfolio → niets te doen
  if (!ledger.portfolio.positions.some((p) => p.tradeId === position.tradeId)) {
    return { ok: false, reason: 'already_quarantined_or_absent' };
  }
  const nextPortfolio = {
    ...ledger.portfolio,
    positions: ledger.portfolio.positions.filter((p) => p.tradeId !== position.tradeId),
  };
  const nextLedger: PaperLedger = {
    ...ledger,
    schemaVersion: 1,
    portfolio: nextPortfolio,
    realizedPnlLamports: ledger.realizedPnlLamports, // géén realized PnL
    updatedAt: at,
  };
  const event: PositionQuarantinedEvent = {
    type: QUARANTINE_EVENT_TYPE,
    at,
    migrationId,
    tradeId: position.tradeId,
    reasonCode: REQUARANTINE_REASON_CODE,
    accountingStatus: 'UNKNOWN',
    pricingStatus: 'UNPRICED',
    recoveryStatus,
    schemaVersion: 1,
    evidence: 'no canonical pool/curve/market identity (gx:<mint>-only)',
  };
  await store.save(nextLedger, event);
  return { ok: true, ledger: nextLedger, event };
}

/** Projectie: bouw de quarantainemap volledig uit de WAL-event-stream (indien
 *  de store een replay/array van events exposeert). Indien de store dat niet
 *  heeft, wordt quarantine-herbouw gedekt via de afgeleide QuarantineStore die
 *  bij startup uit de huidige portfolio + migratie-markers werd opgebouwd. */
export type QuarantineProjection = Map<string, PositionQuarantinedEvent>;


/** Versiegebonden, idempotente migrator: quarantaineert de EXACTE legacy tradeIds
 *  via ledger-events (nooit mint-only). Reeds-afwezige/gequarantinede posities
 *  worden overgeslagen. */
export function buildLedgerQuarantineMigrator(store: { save(ledger: PaperLedger, event?: PositionQuarantinedEvent): Promise<void> }) {
  return {
    async quarantineOne(ledger: PaperLedger, position: PaperPosition, at: string, recoveryStatus: 'NONE' | 'RECOVERY_CANDIDATE' = 'NONE', migrationId = 'legacy-quarantine-2026-08-13-v1') {
      return quarantinePositionViaLedger(store, ledger, position, at, migrationId, recoveryStatus);
    },
  };
}

export function projectQuarantineFromEvents(events: readonly PositionQuarantinedEvent[]): QuarantineProjection {
  const map: QuarantineProjection = new Map();
  for (const e of events) if (e?.type === QUARANTINE_EVENT_TYPE && e.tradeId) map.set(e.tradeId, e);
  return map;
}
