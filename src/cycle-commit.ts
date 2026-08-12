import type { DashboardDecision } from './dashboard.js';
import type { PaperLedger, PaperLedgerEvent } from './ledger.js';
import type { ScanResult } from './scanner.js';

export type CycleCommitStore = {
  save(ledger: PaperLedger, event?: PaperLedgerEvent): Promise<void>;
};

export type CommittedCycleState = {
  ledger: PaperLedger;
  markPricesByMint: Record<string, number>;
  recentDecisions: DashboardDecision[];
  duplicateSuppressed: number;
  closedTrades: DashboardDecision[];
  equityHistory: Array<{ at: string; equityLamports: number }>;
};

export async function commitScanCycle(input: {
  store: CycleCommitStore;
  ledger: PaperLedger;
  result: ScanResult;
  cycle: number;
  recentDecisions: DashboardDecision[];
  duplicateSuppressed: number;
  closedTrades: DashboardDecision[];
  equityHistory: Array<{ at: string; equityLamports: number }>;
}): Promise<CommittedCycleState> {
  const { store, ledger, result, cycle } = input;
  const markPricesByMint = Object.fromEntries(result.snapshots.map((snapshot) => [snapshot.mint, snapshot.priceUsd]));
  const realizedThisCycle = result.decisions.reduce(
    (total, decision) => total + (decision.type === 'paper_exit' ? decision.pnlLamports : 0),
    0,
  );
  const latestPriceByMint = new Map(result.snapshots.map((snapshot) => [snapshot.mint, snapshot.priceUsd]));
  const estimatedEquityLamports = result.portfolio.availableLamports + result.portfolio.positions.reduce((total, position) => {
    const price = latestPriceByMint.get(position.mint);
    return total + (price ? Math.floor(position.allocatedLamports * (price / position.entryPriceUsd)) : position.allocatedLamports);
  }, 0);
  const nextLedger: PaperLedger = {
    schemaVersion: 1,
    portfolio: result.portfolio,
    realizedPnlLamports: ledger.realizedPnlLamports + realizedThisCycle,
    updatedAt: result.checkedAt,
  };
  const duplicateSuppressed = input.duplicateSuppressed
    + result.decisions.filter((decision) => decision.type === 'duplicate_suppressed').length;
  const recentDecisions = [
    ...input.recentDecisions,
    ...result.decisions
      .filter((decision) => decision.type !== 'duplicate_suppressed')
      .map((decision) => ({ ...decision, at: result.checkedAt })),
  ].slice(-500) as DashboardDecision[];
  const closedTrades = [
    ...input.closedTrades,
    ...result.decisions
      .filter((decision) => decision.type === 'paper_exit')
      .map((decision) => ({ ...decision, at: result.checkedAt })),
  ].slice(-500) as DashboardDecision[];
  const equityHistory = cycle % 6 === 0
    ? [...input.equityHistory, { at: result.checkedAt, equityLamports: estimatedEquityLamports }].slice(-525600)
    : input.equityHistory;

  await store.save(nextLedger, {
    type: 'scan_complete',
    at: result.checkedAt,
    cycle,
    decisions: result.decisions,
    // Exclude ephemeral poolDepth (raw vault reserves can exceed Number.MAX_SAFE_INTEGER
    // and break the ledger integer validator). Persist only the price/mint identity —
    // impact depth is per-scan data, not ledger state.
    snapshots: result.snapshots.map(({ poolDepth: _omit, ...snapshot }) => snapshot),
    equityLamports: estimatedEquityLamports,
    providerErrors: result.providerErrors,
  });

  return { ledger: nextLedger, markPricesByMint, recentDecisions, duplicateSuppressed, closedTrades, equityHistory };
}
