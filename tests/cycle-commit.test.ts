import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { commitScanCycle } from '../src/cycle-commit.js';
import { PaperLedgerPersistenceError, type PaperLedger } from '../src/ledger.js';
import { createPortfolio } from '../src/portfolio.js';
import type { ScanResult } from '../src/scanner.js';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
});

function fixture() {
  const ledger: PaperLedger = {
    schemaVersion: 1,
    portfolio: createPortfolio(config, '2026-07-30T00:00:00.000Z'),
    realizedPnlLamports: 10,
    updatedAt: '2026-07-30T00:00:00.000Z',
  };
  const result: ScanResult = {
    mode: 'paper',
    portfolio: { ...ledger.portfolio, availableLamports: ledger.portfolio.availableLamports - 100 },
    decisions: [{
      type: 'rejected', pairId: 'pair-1', mint: 'mint-1', symbol: 'ONE', reason: 'score_below_minimum',
      rejectionClass: 'market', score: 10, source: 'fixture', pairCreatedAt: undefined,
      firstSeenAt: '2026-07-30T00:00:00.000Z', observedAt: '2026-07-30T00:01:00.000Z',
      evaluatedAt: '2026-07-30T00:01:00.000Z', detectionDelayMs: 60_000,
    }],
    snapshots: [], checkedAt: '2026-07-30T00:01:00.000Z', providerErrors: [],
  };
  return { ledger, result };
}

describe('commitScanCycle', () => {
  it('does not publish candidate state when persistence rejects', async () => {
    const { ledger, result } = fixture();
    const recentDecisions = [];
    const equityHistory = [{ at: ledger.updatedAt, equityLamports: ledger.portfolio.availableLamports }];
    const store = { save: async () => { throw new PaperLedgerPersistenceError('forced persistence failure'); } };

    await expect(commitScanCycle({
      store, ledger, result, cycle: 6, recentDecisions, duplicateSuppressed: 2, closedTrades: [], equityHistory,
    })).rejects.toBeInstanceOf(PaperLedgerPersistenceError);
    expect(ledger.updatedAt).toBe('2026-07-30T00:00:00.000Z');
    expect(recentDecisions).toEqual([]);
    expect(equityHistory).toHaveLength(1);
  });

  it('returns staged state only after the durable save resolves', async () => {
    const { ledger, result } = fixture();
    let saveResolved = false;
    const store = { save: async () => { saveResolved = true; } };
    const committed = await commitScanCycle({
      store, ledger, result, cycle: 6, recentDecisions: [], duplicateSuppressed: 2, closedTrades: [], equityHistory: [],
    });

    expect(saveResolved).toBe(true);
    expect(committed.ledger.updatedAt).toBe(result.checkedAt);
    expect(committed.duplicateSuppressed).toBe(2);
    expect(committed.recentDecisions).toEqual([expect.objectContaining({ pairId: 'pair-1', at: result.checkedAt })]);
    expect(committed.equityHistory).toEqual([{ at: result.checkedAt, equityLamports: result.portfolio.availableLamports }]);
  });

  it('strips ephemeral poolDepth (raw reserves > MAX_SAFE_INTEGER) from ledger snapshots', async () => {
    const { ledger, result } = fixture();
    // snapshot met een unsafe reserve (raw vault-balans > 2^53)
    result.snapshots = [{
      pairId: 'pool-x', mint: 'mint-x', symbol: 'X', source: 'triton_vixen_raydium_ammv4',
      observedAt: result.checkedAt, pairCreatedAt: result.checkedAt, priceUsd: 0.001,
      poolDepth: { baseReserve: 157_211_287_178_488_420, quoteReserve: 495_812_846_495_263_040, baseDecimals: 9, quoteDecimals: 9, feeNumerator: 25, feeDenominator: 10_000 },
    }];
    let saved: unknown;
    const store = { save: async (l: PaperLedger, ev: unknown) => { saved = ev; } };
    await commitScanCycle({
      store, ledger, result, cycle: 6, recentDecisions: [], duplicateSuppressed: 0, closedTrades: [], equityHistory: [],
    });
    const event = saved as { snapshots: Array<{ poolDepth?: unknown }> };
    expect(event.snapshots).toHaveLength(1);
    expect(event.snapshots[0].poolDepth).toBeUndefined(); // unsafe reserve niet in ledger
  });
});
