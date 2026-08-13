import type { PaperPosition } from './portfolio.js';
import { REQUARANTINE_REASON_CODE } from './accounting.js';

/** Fase-Q: versiegebonden, idempotente legacy-migratie voor de exacte 4 open
 *  posities ZONDER canonical market identity. Filtert ALTIJD op volledige tradeId
 *  (nooit mint-only), met migrationId/appliedAt/alreadyApplied-tracking zodat een
 *  herstart de migratie niet opnieuw of breder toegepast. */

export const LEGACY_MIGRATION_ID = 'legacy-quarantine-2026-08-13-v1';

/** De exacte vier bevestigde legacy tradeIds (authoritative, immutable). */
export const LEGACY_LEGACY_TRADE_IDS: readonly string[] = [
  'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump:2026-08-12T20:48:05.571Z',
  'AfwyNJG2ZHsjvFViUfuaPVK4fSheY9xMm7tyR6VEpump:2026-08-12T21:13:12.653Z',
  '7pkqvfHe6WREhvZ1ergfXtz3F6MQfXCfcAZiumCt6Ene:2026-08-13T01:31:57.684Z',
  '4LLbsb5ReP3yEtYzmXewyGjcir5uXtKFURtaEUVC2AHs:2026-08-13T08:31:57.965Z',
];

/** Recovery-candidate target (PRCL — heeft multi-DEX historische data in CH). */
const RECOVERY_CANDIDATE_PREFIX = '4LLbsb5ReP3yEtYzmXewyGjcir5uXtKFURtaEUVC2AHs:';

export type MigrationApplied = { id: string; appliedAt: string; tradeIds: string[] };

export type LegacyMigrationResult = {
  /* true als deze migratie nog niet eerder is toegepast */
  newlyApplied: boolean;
  applied: Array<PaperPosition>;
  skippedMissing: string[];
};

/** Versiegebonden, idempotente migratie. alreadyApplied-detectie via migration-file.
 *  Target alleen de exacte 4 tradeIds; andere posities worden nooit geraakt. */
export function runLegacyQuarantineMigration(
  positions: readonly PaperPosition[],
  nowIso: string,
  previouslyApplied: boolean,
): LegacyMigrationResult {
  const targetSet = new Set(LEGACY_LEGACY_TRADE_IDS);
  const applied: PaperPosition[] = [];
  const skippedMissing: string[] = [];
  // Niet opnieuw toepassen als deze migratie al eerder is doorgevoerd.
  if (previouslyApplied) {
    for (const tid of LEGACY_LEGACY_TRADE_IDS) {
      if (!positions.some((p) => p.tradeId === tid)) skippedMissing.push(tid);
    }
    return { newlyApplied: false, applied, skippedMissing };
  }
  for (const p of positions) {
    // exacte tradeId-match — NOOIT mint-only; niet-legacy posities blijven onaangetast
    if (!targetSet.has(p.tradeId)) continue;
    applied.push(p);
  }
  for (const tid of LEGACY_LEGACY_TRADE_IDS) {
    if (!positions.some((p) => p.tradeId === tid)) skippedMissing.push(tid);
  }
  return { newlyApplied: applied.length > 0, applied, skippedMissing };
}

/** Recovery-status voor een gequarantinede tradeId. */
export function recoveryStatusFor(tradeId: string): 'NONE' | 'RECOVERY_CANDIDATE' {
  return tradeId.startsWith(RECOVERY_CANDIDATE_PREFIX) ? 'RECOVERY_CANDIDATE' : 'NONE';
}
