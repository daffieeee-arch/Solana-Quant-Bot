import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, resolve } from 'node:path';
import { loadConfig } from './config.js';
import { createDashboardServer, summarizeProviderHealth } from './dashboard.js';
import { EngineControl } from './engine-control.js';
import { loadScannerHistory } from './history.js';
import { PaperLedgerPersistenceError, PaperLedgerStore, type PaperLedger } from './ledger.js';
import { QuarantineStore } from './quarantine-store.js';
import { LEGACY_LEGACY_TRADE_IDS, recoveryStatusFor } from './legacy-migration.js';
import { quarantinePositionViaLedger, type PositionQuarantinedEvent } from './quarantine-ledger.js';
import { LEGACY_MIGRATION_ID, runLegacyQuarantineMigration } from './legacy-migration.js';
import { REQUARANTINE_REASON_CODE } from './accounting.js';
import { commitScanCycle } from './cycle-commit.js';
import { createPortfolio } from './portfolio.js';
import { CompositeProvider } from './providers/composite.js';
import { TitanQuoteProvider } from './providers/titan.js';
import { TritonProvider } from './providers/triton.js';
import { createVixenClientFactory } from './providers/triton-sdk.js';
import { createGeyserClientFactory } from './providers/triton-geyser.js';
import { TritonReserveReader } from './providers/triton-reserves.js';
import { MarketContextProvider, type MarketContext } from './providers/market-context.js';
import { Scanner } from './scanner.js';
import { LearnController } from './learn-controller.js';
import { buildLearningObservations } from './learning-observations.js';
import { withFreshSolPrice } from './sol-price.js';
import { PersistentLearnCycleGate } from './learn-cycle-gate.js';
import { withRuntimeLifecycle } from './runtime-lifecycle.js';

const delay = (milliseconds: number) => new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

/** Read an optional secret from a direct env value or a _FILE secret mount path.
 * Returns undefined when neither is set; trims. Never exposes the value. */
function readOptionalSecret(direct: string | undefined, filePath: string | undefined): string | undefined {
  if (direct && filePath) throw new Error('Cannot configure both direct value and _FILE for the same secret');
  if (filePath) {
    try {
      return readFileSync(filePath, 'utf8').trim() || undefined;
    } catch {
      return undefined; // missing/unreadable mount → Triton simply stays disabled
    }
  }
  return direct?.trim() || undefined;
}

async function run(): Promise<void> {
  const config = loadConfig(process.env);
  // Fase-W/Provenance: immutable Git SHA uit de image (SOURCE_GIT_SHA build-arg).
  const provenance = { gitSha: process.env.SOURCE_GIT_SHA ?? 'unknown', freeze: process.env.SOURCE_FREEZE_SHA256 ?? 'unknown' };
  await withRuntimeLifecycle(async (resources) => {
    // Runtime config is refreshed with market context; automatic strategy promotion is disabled.
    let runtimeConfig = config;
    const ledgerStore = new PaperLedgerStore(config.dataDir);
    resources.ledgerStore = ledgerStore;
    let ledger = await ledgerStore.loadOrCreate(createPortfolio(config, new Date().toISOString()));
    // Fase-QH: LEDGER-AUTHORITATIVE legacy-migratie. De WAL/ledger is de enige
    // authoritative source of truth: de 4 legacy posities worden via een echt
    // 'position_quarantined' WAL-event UIT de portfolio-ledger verwijderd (via de
    // crash-safe store.save-weg). De quarantaine-status is herleidbaar uit WAL-
    // replay; geen sidecar-bestand (quarantine.json / migration-marker) als autoriteit.
    const nowIso = new Date().toISOString();
    const migratedTradeIds = new Set<string>();
    let quarantinedNew = 0;
    for (const p of ledger.portfolio.positions) {
      // filter op EXACTE legacy tradeIds (nooit mint-only, nooit alle posities)
      if (!LEGACY_LEGACY_TRADE_IDS.includes(p.tradeId)) continue;
      const rec = recoveryStatusFor(p.tradeId);
      const r = await quarantinePositionViaLedger(
        { save: (l: PaperLedger, e?: PositionQuarantinedEvent) => ledgerStore.save(l, e as never) },
        ledger, p, nowIso, LEGACY_MIGRATION_ID, rec,
      );
      if (r.ok) { ledger = r.ledger; migratedTradeIds.add(p.tradeId); quarantinedNew += 1; }
    }
    // quarantaine-status voor scanner-filter/status-exposure (afgeleide projectie)
    const quarantine: {
      isQuarantined(tid: string): boolean;
      active<T extends { tradeId: string }>(positions: readonly T[]): T[];
      getAll(): Array<{ tradeId: string; reasonCode: string; accountingStatus: 'UNKNOWN'; pricingStatus: 'UNPRICED' }>;
      count(): number;
    } = {
      isQuarantined: (tid: string) => migratedTradeIds.has(tid),
      active: <T extends { tradeId: string }>(positions: readonly T[]): T[] => positions.filter((p) => !migratedTradeIds.has(p.tradeId)),
      getAll: () => Array.from(migratedTradeIds).map((tradeId) => ({ tradeId, reasonCode: REQUARANTINE_REASON_CODE, accountingStatus: 'UNKNOWN' as const, pricingStatus: 'UNPRICED' as const })),
      count: () => migratedTradeIds.size,
    };
    console.log(JSON.stringify({ event: 'quarantine_initialize', mode: 'paper', migrationId: LEGACY_MIGRATION_ID, newlyApplied: quarantinedNew, missing: LEGACY_LEGACY_TRADE_IDS.filter((t) => !migratedTradeIds.has(t)).length, total: migratedTradeIds.size }));
    const history = await loadScannerHistory(config.dataDir);
    let recentDecisions = history.recentDecisions;
    let duplicateSuppressed = history.duplicateSuppressed;
    let closedTrades = history.closedTrades;
    let equityHistory = history.equityHistory;
    let markPricesByMint: Record<string, number> = {};
    let lastProviderErrors: string[] = [];
    const engine = new EngineControl();
    const marketContextProvider = new MarketContextProvider();
    let marketContext: MarketContext | undefined;
    try {
      marketContext = await marketContextProvider.get();
    } catch (error) {
      console.warn(JSON.stringify({ event: 'market_context_unavailable', mode: 'paper', message: error instanceof Error ? error.message : String(error) }));
    }
    // Use the live SOL/USD price from market context for Titan (Metis/Pump) route-quote
    // conversion. Falls back to env if the context is unavailable.
    if (config.solPriceUsd === undefined && marketContext) {
      const sol = marketContext.ticker.find((t) => t.symbol.toUpperCase() === 'SOL');
      if (sol && Number.isFinite(sol.priceUsd) && sol.priceUsd > 0) config.solPriceUsd = sol.priceUsd;
    }
    // Optional Triton Vixen discovery: enabled only when both the endpoint and
    // token are provided (via direct env or a _FILE secret mount, so the secret
    // never lands in compose env or the image).
    // TRITON_STREAM=geyser gebruikt de raw Dragon's Mouth gRPC stream (auto-reconnect,
    // ~400ms sneller); default vixen = de geparsede program-stream (stabiel).
    const streamMode = (process.env.TRITON_STREAM ?? 'vixen').toLowerCase();
    const tritonClientFactory = streamMode === 'geyser' ? createGeyserClientFactory() : createVixenClientFactory();
    const tritonEndpoint = readOptionalSecret(process.env.TRITON_ENDPOINT, process.env.TRITON_ENDPOINT_FILE);
    const tritonToken = readOptionalSecret(process.env.TRITON_TOKEN, process.env.TRITON_TOKEN_FILE);
    const tritonEnabled = Boolean(tritonEndpoint && tritonToken);
    const tritonHost = tritonEndpoint ? tritonEndpoint.split('/')[0] ?? tritonEndpoint : undefined;
    console.log(JSON.stringify({ event: 'triton_config', mode: 'paper', enabled: tritonEnabled, endpoint_file: process.env.TRITON_ENDPOINT_FILE ?? null, token_file: process.env.TRITON_TOKEN_FILE ?? null, endpoint_host: tritonHost, token_len: tritonToken ? tritonToken.length : 0 }));
    const triton = tritonEnabled
      ? new TritonProvider(
          tritonEndpoint as string,
          tritonToken as string,
          tritonClientFactory,
          undefined,
          new TritonReserveReader(tritonEndpoint as string, tritonToken as string),
          { solPriceUsd: config.solPriceUsd },
        )
      : undefined;

    const provider = new CompositeProvider({
      maxTokens: 30,
      // TRITON-ONLY (hard): géén useSolanaWs → de Solana-WS-lane wordt niet
      // geïnstantieerd; alle discovery/pricing loopt via Triton (Vixen/geyser,
      // curve self-calc, Titan route-quotes). whaleWallets/rpc-endpoints zijn
      // daardoor hier niet meer actief (bewust, conform TRITON-ONLY-keuze).
      minAgeMinutes: config.minAgeMinutes,
      maxAgeMinutes: config.maxAgeMinutes,
      solPriceUsd: config.solPriceUsd,
      triton,
      // Titan live route-quote: same endpoint+token, gives real aggregated
      // multi-venue fill prices. Optional; disabled if Triton env is absent.
      titan: tritonEndpoint && tritonToken
        ? new TitanQuoteProvider(tritonEndpoint, tritonToken)
        : undefined,
    });
    resources.provider = provider;
    const applyProviderToggles = () => {
      const state = engine.state().providers;
      for (const name of Object.keys(state)) provider.setProviderEnabled(name, state[name]);
    };
    // TRITON-ONLY: synchroniseer de zichtbare providers met de werkelijke set.
    engine.syncProviders(provider.getProviderEnabled());
    applyProviderToggles();
    runtimeConfig = withFreshSolPrice(runtimeConfig, marketContext);
    // Fase-W: bij startup de position-watches reconstruct uit de open-positie
    // WAL-state (authoritatief) — zodat open posities direct verse stream-marks
    // ontvangen (onderdeel 2: restart-recovery).
    if (provider.setPositionWatches) {
      provider.setPositionWatches(ledger.portfolio.positions.map((p) => p.mint).filter(Boolean));
    }
    const scanner = new Scanner(provider, runtimeConfig, ledger.portfolio, undefined, history.firstSeenByPair, 4_000, (tid: string) => quarantine.isQuarantined(tid));
    const learnController = new LearnController(config.dataDir, runtimeConfig);
    resources.opportunityStorage = learnController.storage;

    const reconcileLearningPending = () => learnController.storage.reconcilePending(new Set(
      ledger.portfolio.positions
        .filter((position) => position.learningSchemaVersion === 2 && typeof position.tradeId === 'string')
        .map((position) => position.tradeId as string),
    ));
    reconcileLearningPending();
    runtimeConfig = withFreshSolPrice(learnController.getChampionRuntimeConfig(), marketContext);
    scanner.updateConfig(runtimeConfig);
    if (config.dashboardEnabled) {
      const dashboard = await createDashboardServer({
        port: config.dashboardPort,
        staticDir: resolve(fileURLToPath(new URL('../', import.meta.url)), 'frontend', 'dist'),
        controlToken: process.env.DASHBOARD_CONTROL_TOKEN || undefined,
        getStatus: () => ({ mode: 'paper', updatedAt: ledger.updatedAt, availableLamports: ledger.portfolio.availableLamports, openPositions: quarantine.active(ledger.portfolio.positions), realizedPnlLamports: ledger.realizedPnlLamports, recentDecisions, duplicateSuppressed, closedTrades, equityHistory, markPricesByMint, marketContext, whaleInterestMints: Array.from(provider.whaleActivity.keys()), providerHealth: summarizeProviderHealth(lastProviderErrors), build: provenance, quarantine: quarantine.getAll() }),
        controls: {
          getEngineState: () => engine.state(),
          setScannerRunning: (running: boolean) => engine.setScannerRunning(running),
          setProviderEnabled: (name: string, enabled: boolean) => { engine.setProviderEnabled(name, enabled); applyProviderToggles(); },
          getProviderLatency: () => provider.getProviderLatency(),
        },
        getDebug: () => (provider as unknown as { debugInfo?: () => Record<string, unknown> }).debugInfo?.() ?? {},
      });
      resources.dashboard = dashboard;
      console.log(JSON.stringify({ event: 'dashboard_started', mode: 'paper', port: dashboard.port }));
    }

    const learnCycleGate = new PersistentLearnCycleGate(config.dataDir, 100, async () => {
      const report = await learnController.analyzeAndImprove();
      console.log(JSON.stringify({ event: 'learn_cycle', ...report }));
    });
    let cycle = 0;
    void learnCycleGate.observeCompleted(learnController.storage.completedCount).catch((error) => {
      console.error(JSON.stringify({ event: 'learn_cycle_startup_error', mode: 'paper', message: error instanceof Error ? error.message : String(error) }));
    });
    while (config.maxCycles === 0 || cycle < config.maxCycles) {
      // Pause support: while the scanner is stopped (dashboard control), idle
      // instead of scanning — keeps market/ledger state intact.
      if (!engine.isScannerRunning()) {
        await delay(1_000);
        continue;
      }
      const cycleStartedAt = Date.now();
      cycle += 1;
      applyProviderToggles();
      engine.setScannerState('scanning');
      try {
        marketContext = await marketContextProvider.get();
        runtimeConfig = withFreshSolPrice(runtimeConfig, marketContext);
        scanner.updateConfig(runtimeConfig);
      } catch (error) {
        runtimeConfig = withFreshSolPrice(runtimeConfig, undefined);
        scanner.updateConfig(runtimeConfig);
        console.warn(JSON.stringify({ event: 'market_context_refresh_unavailable', mode: 'paper', message: error instanceof Error ? error.message : String(error) }));
      }
      try {
        reconcileLearningPending();
        const result = await scanner.runOnce();
        const committed = await commitScanCycle({
          store: ledgerStore, ledger, result, cycle, recentDecisions, duplicateSuppressed, closedTrades, equityHistory,
        });
        ledger = committed.ledger;
        markPricesByMint = committed.markPricesByMint;
        recentDecisions = committed.recentDecisions;
        duplicateSuppressed = committed.duplicateSuppressed;
        closedTrades = committed.closedTrades;
        equityHistory = committed.equityHistory;
        // Learning/audit records are committed only after the portfolio ledger succeeds.
        for (const observation of buildLearningObservations(
          learnController.championId,
          result.snapshots,
          result.decisions,
          result.checkedAt,
        )) {
          learnController.recordObservation(observation);
        }
        void learnCycleGate.observeCompleted(learnController.storage.completedCount).catch((error) => {
          console.error(JSON.stringify({ event: 'learn_cycle_error', mode: 'paper', message: error instanceof Error ? error.message : String(error) }));
        });
        const discoveryDebug = (resources.provider as unknown as { discoveryDebug?: () => string }).discoveryDebug?.() ?? '';
        console.log(JSON.stringify({ event: 'scan_complete', cycle, mode: result.mode, coverage: 'triton_first', checkedAt: result.checkedAt, decisions: result.decisions, providerErrors: result.providerErrors, discoveryDebug, openPositions: result.portfolio.positions.length, availableLamports: result.portfolio.availableLamports }));
        // Fase-O: `discoveryDebug` is status-TELEMETRIE (bevat 'triton=', 'streams='...),
        // geen provider-error — NIET in de health-error-array zetten (anders degradeerde
        // TRITON elke scan door de eigen status-string). Alleen echte providerErrors
        // (Triton/Titan RPC/diagnostic-fouten) voeden de provider-health.
        lastProviderErrors = result.providerErrors; // voor dashboard provider-health
        engine.setScannerState('idle');
        engine.noteScan(result.checkedAt);
      } catch (error) {
        if (error instanceof PaperLedgerPersistenceError) throw error;
        const message = error instanceof Error ? error.message : String(error);
        scanner.restorePortfolio(ledger.portfolio);
        await ledgerStore.save(ledger, { type: 'scan_error', at: new Date().toISOString(), cycle, message });
        console.error(JSON.stringify({ event: 'scan_error', cycle, mode: 'paper', message }));
        // T3-B6: ook na een scan-error terug naar 'idle' — anders toont het
        // dashboard 'scanning' terwijl er niets draait (tot de volgende cyclus).
        engine.setScannerState('idle');
      }
      if (config.maxCycles === 0 || cycle < config.maxCycles) {
        await delay(Math.max(0, config.scanIntervalSeconds * 1000 - (Date.now() - cycleStartedAt)));
      }
    }
  });
}

void run();
