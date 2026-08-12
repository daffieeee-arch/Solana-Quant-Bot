import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { loadConfig } from './config.js';
import { createDashboardServer, type DashboardDecision } from './dashboard.js';
import { PaperLedgerStore } from './ledger.js';
import { createPortfolio } from './portfolio.js';
import { DexScreenerProvider } from './providers/dexscreener.js';
import { MarketContextProvider, type MarketContext } from './providers/market-context.js';
import { Scanner } from './scanner.js';

const delay = (milliseconds: number) => new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

async function run(): Promise<void> {
  const config = loadConfig(process.env);
  const ledgerStore = new PaperLedgerStore(config.dataDir);
  let ledger = await ledgerStore.loadOrCreate(createPortfolio(config, new Date().toISOString()));
  let recentDecisions = await loadRecentDecisions(config.dataDir);
  let duplicateSuppressed = await loadDuplicateSuppressed(config.dataDir);
  let closedTrades = await loadClosedTrades(config.dataDir);
  let equityHistory = await loadEquityHistory(config.dataDir);
  let markPricesByMint: Record<string, number> = {};
  const marketContextProvider = new MarketContextProvider();
  let marketContext: MarketContext | undefined;
  try { marketContext = await marketContextProvider.get(); } catch (error) { console.warn(JSON.stringify({ event: 'market_context_unavailable', mode: 'paper', message: error instanceof Error ? error.message : String(error) })); }
  const scanner = new Scanner(new DexScreenerProvider(), config, ledger.portfolio);
  if (config.dashboardEnabled) {
    const dashboard = await createDashboardServer({
      port: config.dashboardPort,
      staticDir: resolve(fileURLToPath(new URL('../', import.meta.url)), 'frontend', 'dist'),
      getStatus: () => ({ mode: 'paper', updatedAt: ledger.updatedAt, availableLamports: ledger.portfolio.availableLamports, openPositions: ledger.portfolio.positions, realizedPnlLamports: ledger.realizedPnlLamports, recentDecisions, duplicateSuppressed, closedTrades, equityHistory, markPricesByMint, marketContext }),
    });
    console.log(JSON.stringify({ event: 'dashboard_started', mode: 'paper', port: dashboard.port }));
  }

  let cycle = 0;
  while (config.maxCycles === 0 || cycle < config.maxCycles) {
    const cycleStartedAt = Date.now();
    cycle += 1;
    try { marketContext = await marketContextProvider.get(); } catch (error) { console.warn(JSON.stringify({ event: 'market_context_refresh_unavailable', mode: 'paper', message: error instanceof Error ? error.message : String(error) })); }
    try {
      const result = await scanner.runOnce();
      markPricesByMint = Object.fromEntries(result.snapshots.map((snapshot) => [snapshot.mint, snapshot.priceUsd]));
      const realizedThisCycle = result.decisions.reduce((total, decision) => total + (decision.type === 'paper_exit' ? decision.pnlLamports : 0), 0);
      const latestPriceByMint = new Map(result.snapshots.map((snapshot) => [snapshot.mint, snapshot.priceUsd]));
      const estimatedEquityLamports = result.portfolio.availableLamports + result.portfolio.positions.reduce((total, position) => {
        const price = latestPriceByMint.get(position.mint);
        return total + (price ? Math.floor(position.allocatedLamports * (price / position.entryPriceUsd)) : position.allocatedLamports);
      }, 0);
      ledger = { schemaVersion: 1, portfolio: result.portfolio, realizedPnlLamports: ledger.realizedPnlLamports + realizedThisCycle, updatedAt: result.checkedAt };
      duplicateSuppressed += result.decisions.filter((decision) => decision.type === 'duplicate_suppressed').length;
      recentDecisions = [...recentDecisions, ...result.decisions.filter((decision) => decision.type !== 'duplicate_suppressed').map((decision) => ({ ...decision, at: result.checkedAt }))].slice(-500);
      closedTrades = [...closedTrades, ...result.decisions.filter((decision) => decision.type === 'paper_exit').map((decision) => ({ ...decision, at: result.checkedAt }))].slice(-200);
      equityHistory = [...equityHistory, { at: result.checkedAt, equityLamports: estimatedEquityLamports }].slice(-720);
      await ledgerStore.save(ledger, { type: 'scan_complete', at: result.checkedAt, cycle, decisions: result.decisions, snapshots: result.snapshots, equityLamports: estimatedEquityLamports });
      console.log(JSON.stringify({ event: 'scan_complete', cycle, mode: result.mode, coverage: 'best_effort_dexscreener_public_feed_not_all_solana_launches', checkedAt: result.checkedAt, decisions: result.decisions, openPositions: result.portfolio.positions.length, availableLamports: result.portfolio.availableLamports }));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await ledgerStore.save(ledger, { type: 'scan_error', at: new Date().toISOString(), cycle, message });
      console.error(JSON.stringify({ event: 'scan_error', cycle, mode: 'paper', message }));
    }
    if (config.maxCycles === 0 || cycle < config.maxCycles) await delay(Math.max(0, config.scanIntervalSeconds * 1000 - (Date.now() - cycleStartedAt)));
  }
}

async function loadRecentDecisions(dataDir: string): Promise<DashboardDecision[]> {
  try {
    const history = await readFile(`${dataDir}/events.ndjson`, 'utf8');
    const decisions: DashboardDecision[] = [];
    for (const line of history.trim().split('\n')) {
      if (!line) continue;
      const event: unknown = JSON.parse(line);
      if (!event || typeof event !== 'object') continue;
      const record = event as { at?: unknown; decisions?: unknown };
      if (!Array.isArray(record.decisions)) continue;
      for (const rawDecision of record.decisions) {
        if (!rawDecision || typeof rawDecision !== 'object') continue;
        const decision = rawDecision as DashboardDecision;
        if (typeof decision.type === 'string' && typeof decision.pairId === 'string') decisions.push({ ...decision, at: typeof record.at === 'string' ? record.at : undefined });
      }
    }
    return decisions.filter((decision) => decision.type !== 'duplicate_suppressed').slice(-500);
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    console.error(JSON.stringify({ event: 'dashboard_history_unavailable', mode: 'paper', message: error instanceof Error ? error.message : String(error) }));
    return [];
  }
}

async function loadDuplicateSuppressed(dataDir: string): Promise<number> { try { const history = await readFile(`${dataDir}/events.ndjson`, 'utf8'); let count = 0; for (const line of history.trim().split('\n')) { if (!line) continue; const event = JSON.parse(line) as { decisions?: Array<{ type?: unknown }> }; count += event.decisions?.filter((decision) => decision.type === 'duplicate_suppressed').length ?? 0; } return count; } catch (error: unknown) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return 0; console.error(JSON.stringify({ event: 'dashboard_duplicate_history_unavailable', mode: 'paper', message: error instanceof Error ? error.message : String(error) })); return 0; } }

async function loadClosedTrades(dataDir: string): Promise<DashboardDecision[]> {
  try {
    const history = await readFile(`${dataDir}/events.ndjson`, 'utf8');
    const trades: DashboardDecision[] = [];
    for (const line of history.trim().split('\n')) {
      if (!line) continue;
      const event: unknown = JSON.parse(line);
      if (!event || typeof event !== 'object') continue;
      const record = event as { at?: unknown; decisions?: unknown };
      if (!Array.isArray(record.decisions)) continue;
      for (const rawDecision of record.decisions) {
        if (!rawDecision || typeof rawDecision !== 'object') continue;
        const decision = rawDecision as DashboardDecision;
        if (decision.type === 'paper_exit' && typeof decision.pairId === 'string') trades.push({ ...decision, at: typeof record.at === 'string' ? record.at : undefined });
      }
    }
    return trades.slice(-200);
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    console.error(JSON.stringify({ event: 'dashboard_closed_trade_history_unavailable', mode: 'paper', message: error instanceof Error ? error.message : String(error) }));
    return [];
  }
}

async function loadEquityHistory(dataDir: string): Promise<Array<{ at: string; equityLamports: number }>> {
  try {
    const history = await readFile(`${dataDir}/events.ndjson`, 'utf8');
    const points: Array<{ at: string; equityLamports: number }> = [];
    for (const line of history.trim().split('\n')) {
      if (!line) continue;
      const event: unknown = JSON.parse(line);
      if (!event || typeof event !== 'object') continue;
      const record = event as { at?: unknown; equityLamports?: unknown };
      if (typeof record.at === 'string' && typeof record.equityLamports === 'number' && Number.isSafeInteger(record.equityLamports)) points.push({ at: record.at, equityLamports: record.equityLamports });
    }
    return points.slice(-720);
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    console.error(JSON.stringify({ event: 'dashboard_equity_history_unavailable', mode: 'paper', message: error instanceof Error ? error.message : String(error) }));
    return [];
  }
}

void run();
