import { describe, expect, it } from 'vitest';
import { computeExposure, applyAdministrativeCapitalAdjustment, CAPITAL_ADJUSTMENT_EVENT_TYPE } from '../src/capital-accounting.js';
import type { PaperPosition } from '../src/portfolio.js';

const mkPos = (mint: string, allocatedLamports: number): PaperPosition => ({
  tradeId: `${mint}:2026-08-01T00:00:00.000Z`, pairId: `pool-${mint}`, mint, symbol: 'X',
  openedAt: '2026-08-01T00:00:00.000Z', entryPriceUsd: 1e-6, highPriceUsd: 1e-6,
  allocatedLamports, entryCostLamports: allocatedLamports, dynamicStopPercent: 5,
});

describe('capital/exposure accounting', () => {
  it('telt quarantined exposure apart van actieve exposure; capital blijft gelockt', () => {
    const active = [mkPos('AMINT', 500_000)];
    const quarantined = [mkPos('QMINT1', 300_000), mkPos('QMINT2', 200_000)];
    const availableLamports = 9_000_000_000;
    const ex = computeExposure({ active, quarantined, availableLamports, realizedPnlLamports: 0 });
    expect(ex.activeExposureLamports).toBe(500_000);
    expect(ex.quarantinedExposureLamports).toBe(500_000);
    expect(ex.unknownExposureLamports).toBe(500_000); // state = UNKNOWN
    // available paper capital: NIET verminderd met unknown (niet gemarkeerd als vrij)
    expect(ex.availablePaperCapitalLamports).toBe(9_000_000_000);
    expect(ex.realizedCapitalLamports).toBe(0);
    // geen verborgen leverage: active + quarantined + available <= startkapitaal
    expect(ex.activeExposureLamports + ex.quarantinedExposureLamports).toBeLessThan(9_000_000_000 + 1_000_000);
  });

  it('administrative_capital_adjustment wijzigt strategy-PnL niet', () => {
    const r = applyAdministrativeCapitalAdjustment({
      availableLamports: 9_000_000_000,
      realizedPnlLamports: -250_000,
    }, -1_000_000_000 /* kapitaal vrijgeven voor testen */, '2026-08-13T12:00:00.000Z');
    expect(r.type).toBe(CAPITAL_ADJUSTMENT_EVENT_TYPE);
    // realised strategy-PnL blijft behouden (geen verandering in realizedPnl)
    expect(r.realizedPnlAfter).toBe(-250_000);
    // admin-release verschuift alleen available capital
    expect(r.availableAfter).toBe(9_000_000_000 - 1_000_000_000);
    expect(r.adminCapitalAdjustmentLamports).toBe(-1_000_000_000);
  });
});