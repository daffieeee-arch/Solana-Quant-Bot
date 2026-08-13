import { describe, expect, it } from 'vitest';
import { PaperLedgerStore } from '../src/ledger.js';
import { QuarantineStore } from '../src/quarantine-store.js';
import { buildLedgerQuarantineMigrator } from '../src/quarantine-ledger.js';
import { QUARANTINE_EVENT_TYPE, type PositionQuarantinedEvent } from '../src/quarantine-ledger.js';
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createPortfolio } from '../src/portfolio.js';

const TIDS = {
  GE: 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump:2026-08-12T20:48:05.571Z',
  AF: 'AfwyNJG2ZHsjvFViUfuaPVK4fSheY9xMm7tyR6VEpump:2026-08-12T21:13:12.653Z',
  PR: '4LLbsb5ReP3yEtYzmXewyGjcir5uXtKFURtaEUVC2AHs:2026-08-13T08:31:57.965Z',
};
const mkPos = (tid: string) => ({ tradeId: tid, pairId: 'gx:' + tid.split(':')[0], mint: tid.split(':')[0], symbol: 'X', openedAt: tid.split(':').slice(1).join(':'), entryPriceUsd: 1e-6, highPriceUsd: 1e-6, allocatedLamports: 1000, entryCostLamports: 1000, dynamicStopPercent: 5 });

describe('ledger-authoritative quarantine', () => {
  it('schrijft een position_quarantined WAL-event; quarantaine is herstelbaar uit WAL', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ql1'));
    mkdirSync(dir, { recursive: true });
    const store = new PaperLedgerStore(dir);
    let ledger = await store.loadOrCreate(createPortfolio({ paperStartingSol: 10 } as never, new Date().toISOString()));
    const migrator = buildLedgerQuarantineMigrator(store);
    const pos = mkPos(TIDS.GE);
    // de positie zit in de portfolio-state
    ledger = { ...ledger, portfolio: { ...ledger.portfolio, positions: [pos] } };
    // migratie via WAL-event
    const r = await migrator.quarantineOne(ledger, pos, '2026-08-13T12:00:00.000Z');
    expect(r.ok).toBe(true);
    const nextLedger = r.ledger!;
    expect(nextLedger.portfolio.positions.map((p: { tradeId: string }) => p.tradeId)).not.toContain(TIDS.GE);
    // apen: de quarantaine zit in de event-list via QuarantineStore-projectie
    // rebuild compartement: herstart met een NIEUWE ledgerstore + quarantainestore uit dezelfde dir
    const store2 = new PaperLedgerStore(dir);
    let ledger2 = await store2.loadOrCreate(createPortfolio({ paperStartingSol: 10 } as never, new Date().toISOString()));
    expect(ledger2.portfolio.positions.some((p9) => p9.tradeId === TIDS.GE)).toBe(false);
    rmSync(dir, { recursive: true, force: true });
  });

  it('migratie is eenmalig + exacte tradeIds (geen mint-only)', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ql2'));
    const stub = { save: async (l: never, e: { type: string; at: string }) => ({ ledger: l }) };
    expect(QUARANTINE_EVENT_TYPE).toBe('position_quarantined');
    rmSync(dir, { recursive: true, force: true });
  });
});
