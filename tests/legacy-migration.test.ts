import { describe, expect, it } from 'vitest';
import { LEGACY_LEGACY_TRADE_IDS, runLegacyQuarantineMigration, recoveryStatusFor, LEGACY_MIGRATION_ID } from '../src/legacy-migration.js';
import type { PaperPosition } from '../src/portfolio.js';

const mk = (mint: string, at: string): PaperPosition => ({ tradeId: `${mint}:${at}`, pairId: `gx:${mint}`, mint, symbol: 'X', openedAt: at, entryPriceUsd: 1e-6, highPriceUsd: 1e-6, allocatedLamports: 1, entryCostLamports: 1, dynamicStopPercent: 5 });
// tradeId = `<mint>:<openedAt`; de timestamp bevat zelf colons, dus splits op de EERSTE `:`
const tradeOf = (tid: string) => {
  const idx = tid.indexOf(':');
  return { mint: tid.slice(0, idx), at: tid.slice(idx + 1) };
};

describe('legacy migration', () => {
  it('target alleen de exacte 4 tradeIds; andere posities blijven onaangetast', () => {
    const legacy = LEGACY_LEGACY_TRADE_IDS.map((tid) => { const { mint, at } = tradeOf(tid); return mk(mint, at); });
    const normal = mk('MINT9pump', '2026-08-01T00:00:00.000Z');
    const r = runLegacyQuarantineMigration([...legacy, normal], 'now', false);
    expect(r.applied.length).toBe(4); // alleen de 4
    expect(r.applied.map((p) => p.tradeId)).toEqual(LEGACY_LEGACY_TRADE_IDS);
    // normal niet geraakt (staat niet in applied)
    expect(r.applied.some((p) => p.tradeId === normal.tradeId)).toBe(false);
  });

  it('mint-only matching kan geen andere trade raken (zelfde mint, ander timingslot)', () => {
    const t0 = LEGACY_LEGACY_TRADE_IDS[0]!;
    const mint = t0.split(':')[0]!;
    const origAt = t0.split(':')[1]!;
    // een ANDERE trade op dezelfde mint (ander openedAt) — mag NIET gemigreerd worden
    const otherSlot = mk(mint, '2026-08-11T00:00:00.000Z');
    const r = runLegacyQuarantineMigration([otherSlot], 'now', false);
    expect(r.applied.length).toBe(0); // niet de exacte tradeId → niet aangeraakt
  });

  it('is idempotent via alreadyApplied-detectie; skip meldt ontbrekende', () => {
    const legacy = LEGACY_LEGACY_TRADE_IDS.map((tid) => { const { mint, at } = tradeOf(tid); return mk(mint, at); });
    const first = runLegacyQuarantineMigration(legacy, 'now', false);
    expect(first.newlyApplied).toBe(true);
    // al toegepast → niet opnieuw
    const second = runLegacyQuarantineMigration(legacy, 'later', true);
    expect(second.newlyApplied).toBe(false);
    expect(second.applied.length).toBe(0);
    // ontbrekende tradeIds worden gerapporteerd
    const missing = runLegacyQuarantineMigration(legacy.slice(0, 2), 'now', true);
    expect(missing.skippedMissing.length).toBe(2);
  });

  it('PRCL krijgt RECOVERY_CANDIDATE-status', () => {
    const prcl = LEGACY_LEGACY_TRADE_IDS.find((t) => t.startsWith('4LLbsb'))!;
    expect(recoveryStatusFor(prcl)).toBe('RECOVERY_CANDIDATE');
    expect(recoveryStatusFor(LEGACY_LEGACY_TRADE_IDS[0]!)).toBe('NONE');
  });

  it('exposeert een stabiel migrationId', () => {
    expect(LEGACY_MIGRATION_ID).toMatch(/legacy-quarantine-.+-v1/);
  });
});
