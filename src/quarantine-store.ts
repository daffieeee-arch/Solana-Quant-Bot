import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { quarantineLegacyPosition, type QuarantineRecord } from './accounting.js';
import type { PaperPosition } from './portfolio.js';

/** Fase-Q: persistente quarantaine-registry (append-only bestand, idempotent).
 *  Houdt de 4 legacy posities vast zodat ze uit actieve concurrency blijven +
 *  niet door normale risk-exits worden geraakt, en overleeft restart/replay. */

export class QuarantineStore {
  private records = new Map<string, QuarantineRecord>();
  constructor(private readonly filePath: string) {
    this.load();
  }
  private load(): void {
    if (!existsSync(this.filePath)) return;
    try {
      const arr = JSON.parse(readFileSync(this.filePath, 'utf8')) as QuarantineRecord[];
      for (const r of arr) this.records.set(r.tradeId, r);
    } catch {
      // corrupt bestand → leeg; nieuwe records overschrijven bij save
    }
  }
  private save(): void {
    mkdirSync(dirname(this.filePath), { recursive: true });
    writeFileSync(this.filePath, JSON.stringify(Array.from(this.records.values()), null, 2));
  }
  getAll(): Map<string, QuarantineRecord> {
    return this.records;
  }
  isQuarantined(tradeId: string): boolean {
    return this.records.has(tradeId);
  }
  /** Idempotent quarantaine: true als NIEUW gequarantined + persisted; false als al gedaan. */
  quarantine(position: PaperPosition, at: string, recoveryStatus?: 'NONE' | 'HISTORICAL_RECOVERY_PENDING' | 'RECOVERY_CANDIDATE' | 'RECOVERED'): boolean {
    if (this.records.has(position.tradeId)) return false;
    const r = quarantineLegacyPosition(this.records, position, at, recoveryStatus);
    if (r.ok && !r.duplicate) this.save();
    return r.ok && !r.duplicate;
  }
  /** Actieve (niet-gequarantined) posities — concurrency + risk geldt alleen hierop. */
  active(positions: readonly PaperPosition[]): PaperPosition[] {
    return positions.filter((p) => !this.records.has(p.tradeId));
  }
  count(): number {
    return this.records.size;
  }
}
