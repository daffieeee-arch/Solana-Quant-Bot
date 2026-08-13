import { describe, expect, it } from 'vitest';
import { mkdtempSync, existsSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { QuarantineStore } from '../src/quarantine-store.js';

const TIDS = {
  GE: 'GEfxobQ71cNWJ15zhZ3NSmPQu1ppcgNTGNiuKRXopump:2026-08-12T20:48:05.571Z',
  PR: '4LLbsb5ReP3yEtYzmXewyGjcir5uXtKFURtaEUVC2AHs:2026-08-13T08:31:57.965Z',
};
const mk = (tid: string) => ({ tradeId: tid, pairId: 'x', mint: tid.split(':')[0], symbol: 'X', openedAt: '2026-08-12T00:00:00Z', entryPriceUsd: 1e-6, highPriceUsd: 1e-6, allocatedLamports: 1, entryCostLamports: 1, dynamicStopPercent: 5 });

describe('QuarantineStore', () => {
  it('quarantineert + persisted, en bewaart de originele histonen', () => {
    const dir = mkdtempSync(join(tmpdir(), 'qtest'));
    const store = new QuarantineStore(join(dir, 'q.json'));
    expect(store.quarantine(mk(TIDS.GE), 'now')).toBe(true);
    expect(store.isQuarantined(TIDS.GE)).toBe(true);
    expect(existsSync(join(dir, 'q.json'))).toBe(true);
    expect(JSON.parse(readFileSync(join(dir, 'q.json'), 'utf8')).length).toBe(1);
    rmSync(dir, { recursive: true, force: true });
  });

  it('filtert gequarantined uit actieve concurrency; herstart is idempotent', () => {
    const dir = mkdtempSync(join(tmpdir(), 'qtest2'));
    const store = new QuarantineStore(join(dir, 'q.json'));
    store.quarantine(mk(TIDS.GE), 'now');
    store.quarantine(mk(TIDS.PR), 'now');
    const all = [mk(TIDS.GE), mk(TIDS.PR)];
    expect(store.active(all).length).toBe(0);
    // herstart: nieuwe store leest hetzelfde bestand
    const store2 = new QuarantineStore(join(dir, 'q.json'));
    expect(store2.count()).toBe(2);
    expect(store2.isQuarantined(TIDS.GE)).toBe(true);
    // idempotent: opnieuw quarantinen voegt niets toe
    expect(store2.quarantine(mk(TIDS.GE), 'now')).toBe(false); // duplicate → save niet
    expect(store2.count()).toBe(2);
    // actieve assets: een andere niet-gequarantined positie blijft actief
    const withLive = [...all, mk('LIVE:2026-08-13T00:00:00Z')];
    expect(store2.active(withLive).length).toBe(1);
    rmSync(dir, { recursive: true, force: true });
  });

  it('laat geldige history behouden (append-only bestand, geen delete van oud)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'qtest3'));
    const store = new QuarantineStore(join(dir, 'q.json'));
    store.quarantine(mk(TIDS.GE), 'now');
    store.quarantine(mk(TIDS.PR), 'now');
    const raw = JSON.parse(readFileSync(join(dir, 'q.json'), 'utf8'));
    expect(raw.map((r: { tradeId: string }) => r.tradeId)).toContain(TIDS.GE);
    expect(raw.map((r: { tradeId: string }) => r.tradeId)).toContain(TIDS.PR);
    rmSync(dir, { recursive: true, force: true });
  });
});
