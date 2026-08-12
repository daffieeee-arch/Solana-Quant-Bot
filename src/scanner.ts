import type { PaperConfig } from './config.js';
import { canPaperEnter } from './risk.js';
import { enterPaperPosition, evaluateOpenPosition, computePositionSize, type Portfolio } from './portfolio.js';
import { evaluateMarketGate, scoreMomentum, scoreContraMomentum, type DiscoveryProvenance, type MarketSnapshot } from './scoring.js';

export type MarketProvider = {
  fetchSnapshots(): Promise<MarketSnapshot[]>;
  fetchSnapshotsForPositions?(positions: readonly PositionQuoteIdentity[]): Promise<MarketSnapshot[]>;
  drainDiagnostics?(): string[];
  /**
   * Optional: fetch a live Titan route-quoted fill price (USD) for a mint at a
   * planned position size. When present and the entry gate passes, the scanner
   * may override the snapshot price with this real aggregated fill price.
   */
  fetchTitanFillUsd?(mint: string, amountLamports: number, solPriceUsd: number): Promise<number | undefined>;
  /**
   * Optional: Triton on-chain rug/honeypot assessment (getAsset, gecached 7 dagen).
   * De scanner roept dit ALLEEN aan wanneer de market-gate gepasseerd is en een
   * entry reëel dreigt — zo kost een pool die op age/liquidity/flow afvalt nooit
   * een betaalde getAsset-call (credits-besparing).
   */
  assessRugRisk?(mint: string): Promise<MarketSnapshot['rugRisk'] | undefined>;
  /**
   * Optional: haal een verse positie-snapshot op (prijs + symbol) voor een
   * bekende open positie die niet (meer) in de actieve discovery-flow zit.
   * Gebruikt door fetchSnapshotsForPositions om mark-prijzen live te houden.
   */
  fetchPositionSnapshot?(mint: string): Promise<{ symbol: string; priceUsd: number } | undefined>;
};

export type PositionQuoteIdentity = { pairId: string; mint: string };

type MarketDecisionContext = {
  pairCreatedAt: string;
  firstSeenAt: string;
  observedAt: string;
  evaluatedAt: string;
  detectionDelayMs?: number;
  discovery?: DiscoveryProvenance;
};

export type ScanDecision =
  | ({ type: 'paper_entry'; tradeId: string; learningSchemaVersion: 2; pairId: string; mint: string; symbol: string; score: number; source: string; coverage: 'best_effort'; openedAt: string; entryPriceUsd: number } & MarketDecisionContext)
  | { type: 'paper_exit'; tradeId: string; learningSchemaVersion?: 2; pairId: string; mint: string; symbol: string; reason: 'stop_loss' | 'trailing_stop' | 'max_hold' | 'time_stop'; pnlLamports: number; source: string; openedAt: string; exitPriceUsd: number }
  | ({ type: 'rejected'; pairId: string; mint: string; symbol: string; reason: string; rejectionClass: 'market' | 'risk'; score: number; source: string } & MarketDecisionContext)
  | { type: 'duplicate_suppressed'; pairId: string };

export type ScanResult = { mode: 'paper'; portfolio: Portfolio; decisions: ScanDecision[]; snapshots: MarketSnapshot[]; checkedAt: string; providerErrors: string[] };

const MAX_POSITION_QUOTE_AGE_MS = 2 * 60_000;
const MAX_PAIR_STATES = 10_000;
const PAIR_STATE_IDLE_TTL_MS = 6 * 60 * 60_000;
const MAX_LATE_DISCOVERY_SNAPSHOTS = 500;

class DiscoveryDeadlineError extends Error {
  constructor(deadlineMs: number) {
    super(`deadline exceeded after ${deadlineMs}ms`);
    this.name = 'DiscoveryDeadlineError';
  }
}

function withDeadline<T>(promise: Promise<T>, deadlineMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new DiscoveryDeadlineError(deadlineMs)), deadlineMs);
    void promise.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

type PairState = { firstSeenAt: string; lastObservedAtMs?: number; touchedAtMs: number };
/** Laag A: pools wachten tot ze het min-leeftijdsvenster bereiken i.p.v. direct afgewezen te worden. */
type RipeningEntry = { pairId: string; mint: string; symbol: string; source: string; firstSeenAt: string; observedAt: string; pairCreatedAt: string; sinceMs: number };
type DiscoveryPromiseState = { activeConsumers: number; captureInstalled: boolean };

function marketDecisionContext(snapshot: MarketSnapshot, firstSeenAt: string, evaluatedAt: string): MarketDecisionContext {
  const firstSeenMs = Date.parse(firstSeenAt);
  const createdMs = Date.parse(snapshot.pairCreatedAt);
  const delay = firstSeenMs - createdMs;
  return {
    pairCreatedAt: snapshot.pairCreatedAt,
    firstSeenAt,
    observedAt: snapshot.observedAt,
    evaluatedAt,
    ...(snapshot.discovery ? { discovery: snapshot.discovery } : {}),
    ...(Number.isFinite(delay) ? { detectionDelayMs: Math.max(0, delay) } : {}),
  };
}

function isFreshPositionQuote(snapshot: MarketSnapshot, now: Date): boolean {
  const observedAt = Date.parse(snapshot.observedAt);
  const ageMs = now.getTime() - observedAt;
  return Number.isFinite(snapshot.priceUsd)
    && snapshot.priceUsd > 0
    && Number.isFinite(observedAt)
    && ageMs >= 0
    && ageMs <= MAX_POSITION_QUOTE_AGE_MS;
}

function positionKey(identity: PositionQuoteIdentity): string {
  return `${identity.pairId}\0${identity.mint}`;
}

export class Scanner {
  private readonly pairStates = new Map<string, PairState>();
  private readonly unavailablePositionKeys = new Set<string>();
  private lateDiscoverySnapshots: MarketSnapshot[] = [];
  private readonly discoveryPromiseStates = new WeakMap<Promise<MarketSnapshot[]>, DiscoveryPromiseState>();
  /** Laag A: pools die rijpen (te jong) wordt vastgehouden i.p.v. afgewezen. */
  private readonly ripening = new Map<string, RipeningEntry>();
  private readonly MAX_RIPENING = 2_000;

  constructor(
    private readonly provider: MarketProvider,
    private config: PaperConfig,
    private portfolio: Portfolio,
    private readonly clock: () => Date = () => new Date(),
    initialFirstSeenByPair: ReadonlyMap<string, string> = new Map(),
    private readonly discoveryDeadlineMs = 4_000,
  ) {
    if (!Number.isInteger(discoveryDeadlineMs) || discoveryDeadlineMs <= 0) {
      throw new Error('discovery deadline must be a positive integer');
    }
    const touchedAtMs = this.clock().getTime();
    for (const [pairId, firstSeenAt] of initialFirstSeenByPair) {
      if (this.pairStates.size >= MAX_PAIR_STATES) break;
      if (!pairId || !Number.isFinite(Date.parse(firstSeenAt))) continue;
      this.pairStates.set(pairId, { firstSeenAt, touchedAtMs });
    }
  }

  private ensurePairStateCapacity(nowMs: number): void {
    if (this.pairStates.size < MAX_PAIR_STATES) return;
    for (const [pairId, state] of this.pairStates) {
      if (nowMs - state.touchedAtMs > PAIR_STATE_IDLE_TTL_MS) this.pairStates.delete(pairId);
    }
    while (this.pairStates.size >= MAX_PAIR_STATES) {
      const oldestPairId = this.pairStates.keys().next().value as string | undefined;
      if (!oldestPairId) break;
      this.pairStates.delete(oldestPairId);
    }
  }

  /** Update runtime config after a strategy promotion */
  updateConfig(newConfig: PaperConfig): void {
    this.config = newConfig;
  }

  /** Laag A: houd een te-jonge pool vast zodat hij na rijping ('age>=minAge') kan worden her-waardeed. */
  private registerRipening(entry: RipeningEntry): void {
    this.ripening.delete(entry.pairId);
    this.ripening.set(entry.pairId, entry);
    while (this.ripening.size > this.MAX_RIPENING) {
      const oldest = this.ripening.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.ripening.delete(oldest);
    }
  }

  /** Restore durable financial state after a scan cycle was not committed. */
  restorePortfolio(committedPortfolio: Portfolio): void {
    this.portfolio = committedPortfolio;
  }

  private discoveryPromiseState(promise: Promise<MarketSnapshot[]>): DiscoveryPromiseState {
    const existing = this.discoveryPromiseStates.get(promise);
    if (existing) return existing;
    const created = { activeConsumers: 0, captureInstalled: false };
    this.discoveryPromiseStates.set(promise, created);
    return created;
  }

  private captureLateDiscovery(promise: Promise<MarketSnapshot[]>): void {
    const state = this.discoveryPromiseState(promise);
    if (state.captureInstalled) return;
    state.captureInstalled = true;
    void promise.then((snapshots) => {
      if (state.activeConsumers > 0) return;
      this.lateDiscoverySnapshots = [...this.lateDiscoverySnapshots, ...snapshots]
        .slice(-MAX_LATE_DISCOVERY_SNAPSHOTS);
    }, () => {});
  }

  private takeLateDiscovery(): MarketSnapshot[] {
    const snapshots = this.lateDiscoverySnapshots;
    this.lateDiscoverySnapshots = [];
    return snapshots;
  }

  async runOnce(): Promise<ScanResult> {
    const providerErrors: string[] = [];
    const openPositions = this.portfolio.positions.flatMap((position) => (
      typeof position.pairId === 'string' && position.pairId.length > 0 && position.mint.length > 0
        ? [{ pairId: position.pairId, mint: position.mint }]
        : []
    ));
    const openPositionKeys = new Set(openPositions.map(positionKey));
    const carriedDiscovery = this.takeLateDiscovery();
    const discoveryPromise = this.provider.fetchSnapshots();
    const positionPromise = this.provider.fetchSnapshotsForPositions && openPositions.length > 0
      ? this.provider.fetchSnapshotsForPositions(openPositions)
      : undefined;
    const discoveryState = this.discoveryPromiseState(discoveryPromise);
    discoveryState.activeConsumers += 1;
    let snapshots: MarketSnapshot[];
    try {
      snapshots = [...carriedDiscovery, ...await withDeadline(discoveryPromise, this.discoveryDeadlineMs)];
    } catch (error) {
      if (this.portfolio.positions.length === 0 && !(error instanceof DiscoveryDeadlineError)) throw error;
      if (error instanceof DiscoveryDeadlineError) this.captureLateDiscovery(discoveryPromise);
      snapshots = carriedDiscovery;
      providerErrors.push(`discovery: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      discoveryState.activeConsumers -= 1;
    }
    const positionSnapshots = positionPromise ? await positionPromise : snapshots;
    const providerDiagnostics = this.provider.drainDiagnostics?.() ?? [];
    providerErrors.push(...providerDiagnostics);
    const diagnosedPositionKeys = new Set(providerDiagnostics.flatMap((diagnostic) => {
      if (!diagnostic.startsWith('position_quote:')) return [];
      const mint = /(?:^|;) mint=([^;]+)/.exec(diagnostic)?.[1];
      const pairId = /(?:^|;) pair_id=([^;]+)/.exec(diagnostic)?.[1];
      return mint && pairId ? [positionKey({ pairId, mint })] : [];
    }));
    const now = this.clock();
    const freshPositionSnapshots = positionSnapshots.filter((snapshot) => isFreshPositionQuote(snapshot, now)
      && openPositionKeys.has(positionKey(snapshot)));
    const quotedPositionKeys = new Set(freshPositionSnapshots.map(positionKey));
    for (const position of openPositions) {
      const key = positionKey(position);
      if (quotedPositionKeys.has(key)) {
        this.unavailablePositionKeys.delete(key);
        continue;
      }
      if (diagnosedPositionKeys.has(key)) {
        this.unavailablePositionKeys.add(key);
        continue;
      }
      if (!this.unavailablePositionKeys.has(key)) {
        providerErrors.push(`position_quote: fresh quote unavailable; pair_id=${position.pairId}; mint=${position.mint}`);
        this.unavailablePositionKeys.add(key);
      }
    }
    const decisions: ScanDecision[] = [];
    const evaluatedAt = now.toISOString();
    const exitedMintsThisCycle = new Set<string>();

    for (const snapshot of freshPositionSnapshots) {
      const openPosition = this.portfolio.positions.find((position) => position.pairId === snapshot.pairId && position.mint === snapshot.mint);
      if (!openPosition) continue;
      const evaluation = evaluateOpenPosition(this.portfolio, { pairId: snapshot.pairId, mint: snapshot.mint }, snapshot.priceUsd, now.toISOString(), this.config, snapshot.poolDepth);
      this.portfolio = evaluation.portfolio;
      if (evaluation.event.type === 'exit') {
        exitedMintsThisCycle.add(snapshot.mint);
        decisions.push({
          type: 'paper_exit',
          tradeId: openPosition.tradeId,
          learningSchemaVersion: openPosition.learningSchemaVersion,
          pairId: snapshot.pairId,
          mint: snapshot.mint,
          symbol: openPosition.symbol,
          openedAt: openPosition.openedAt,
          exitPriceUsd: snapshot.priceUsd,
          reason: evaluation.event.reason,
          pnlLamports: evaluation.event.pnlLamports,
          source: snapshot.source,
        });
      }
    }

    // Top-N entry-kandidaten: gate+score-passende snapshots worden verzameld en
    // na de loop gerankt op score; alleen de beste maxEntriesPerScan worden geopend.
    const entryCandidates: Array<{ score: number; snapshot: typeof snapshots[number]; entryPriceUsd: number; context: ReturnType<typeof marketDecisionContext> }> = [];

    for (const snapshot of snapshots) {
      const nowMs = now.getTime();
      const observedAtMs = Date.parse(snapshot.observedAt);
      const previousState = this.pairStates.get(snapshot.pairId);
      const providerFirstSeenAtMs = snapshot.firstSeenAt ? Date.parse(snapshot.firstSeenAt) : Number.NaN;
      const providerFirstSeenAt = Number.isFinite(providerFirstSeenAtMs) && providerFirstSeenAtMs <= nowMs
        ? snapshot.firstSeenAt : undefined;
      const firstSeenAt = previousState?.firstSeenAt ?? providerFirstSeenAt ?? evaluatedAt;
      const context = marketDecisionContext(snapshot, firstSeenAt, evaluatedAt);
      const score = this.config.entryMode === 'contra'
        ? scoreContraMomentum(snapshot, this.config, now)
        : scoreMomentum(snapshot, this.config, now);
      const marketGate = evaluateMarketGate(snapshot, this.config, now);

      // Invalid/future timestamps and invalid market payloads never advance the
      // monotone observation watermark, so a later valid quote cannot be poisoned.
      if (!Number.isFinite(observedAtMs)
        || marketGate.accepted === false && (marketGate.reason === 'invalid_market_data' || marketGate.reason === 'future_market_data')) {
        if (previousState) previousState.touchedAtMs = nowMs;
        else {
          this.ensurePairStateCapacity(nowMs);
          this.pairStates.set(snapshot.pairId, { firstSeenAt, touchedAtMs: nowMs });
        }
        // Laag A: een te-jonge, (nog) ongeprijsde discovery pool rijpt i.p.v. te
        // worden afgewezen — zodra hij ≥ minAge is én geprijsd, wordt hij her-waardeed.
        if (marketGate.accepted === false && marketGate.reason === 'invalid_market_data') {
          const ageMs = nowMs - Date.parse(snapshot.pairCreatedAt);
          const belowMin = Number.isFinite(ageMs) ? ageMs < this.config.minAgeMinutes * 60_000 : false;
          if (belowMin) {
            this.registerRipening({ pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol, source: snapshot.source, firstSeenAt, observedAt: snapshot.observedAt, pairCreatedAt: snapshot.pairCreatedAt, sinceMs: nowMs });
            continue;
          }
        }
        const reason = marketGate.accepted === false ? marketGate.reason : 'invalid_market_data';
        decisions.push({ type: 'rejected', pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol, reason, rejectionClass: 'market', score, source: snapshot.source, ...context });
        continue;
      }

      if (previousState?.lastObservedAtMs !== undefined && observedAtMs <= previousState.lastObservedAtMs) {
        previousState.touchedAtMs = nowMs;
        decisions.push({ type: 'duplicate_suppressed', pairId: snapshot.pairId });
        continue;
      }
      if (!previousState && this.pairStates.size >= MAX_PAIR_STATES) {
        this.ensurePairStateCapacity(nowMs);
      }
      this.pairStates.set(snapshot.pairId, { firstSeenAt, lastObservedAtMs: observedAtMs, touchedAtMs: nowMs });
      if (marketGate.accepted === false) {
        // Laag A (rijping): een te-jonge pool (< minAge) wordt NIET afgewezen maar
        // vastgehouden tot hij rijpt — ongeacht of hij al externe data heeft, want de
        // live flow-telemetrie (Laag B) vult buys/sells later aan. Te-oude pools (>max)
        // worden wél afgewezen.
        if (marketGate.reason === 'age_outside_window') {
          const ageMs = nowMs - Date.parse(snapshot.pairCreatedAt);
          const belowMin = Number.isFinite(ageMs) ? ageMs < this.config.minAgeMinutes * 60_000 : false;
          if (belowMin) {
            this.registerRipening({ pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol, source: snapshot.source, firstSeenAt, observedAt: snapshot.observedAt, pairCreatedAt: snapshot.pairCreatedAt, sinceMs: nowMs });
            continue; // skip this cycle; pool rijpt, wordt later her-waardeed
          }
        }
        decisions.push({ type: 'rejected', pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol, reason: marketGate.reason, rejectionClass: 'market', score, source: snapshot.source, ...context });
        continue;
      }
      if (score < this.config.minMomentumScore) {
        decisions.push({ type: 'rejected', pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol, reason: 'score_below_minimum', rejectionClass: 'market', score, source: snapshot.source, ...context });
        continue;
      }
      if (exitedMintsThisCycle.has(snapshot.mint)) {
        decisions.push({ type: 'rejected', pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol, reason: 'same_cycle_reentry', rejectionClass: 'risk', score, source: snapshot.source, ...context });
        continue;
      }
      // Risk-status afleiden uit de Triton on-chain rug-assessment: 'high' → flagged,
      // 'clean'/'moderate' → clear, afwezig → unknown. (Voorheen hardcoded 'unknown':
      // STRICT_RISK_MODE blokkeerde daardoor élke entry — per constructie.)
      // ZUINIGHEID: een ontbrekende rugRisk wordt hier pas opgehaald (getAsset,
      // 7d-cache) — ALLEEN wanneer de market-gate al gepasseerd is en een entry
      // daadwerkelijk dreigt. Pools die op age/liquidity/flow afvallen kosten zo
      // nooit een betaalde DAS-call.
      let rugClass = snapshot.rugRisk?.className;
      if (rugClass === undefined && this.provider.assessRugRisk) {
        const assessed = await this.provider.assessRugRisk(snapshot.mint);
        if (assessed !== undefined) rugClass = assessed.className;
      }
      const riskStatus = rugClass === 'high' ? 'flagged' : (rugClass === 'clean' || rugClass === 'moderate') ? 'clear' : 'unknown';
      const riskGate = canPaperEnter(riskStatus, this.config.strictRiskMode);
      if (riskGate.allowed === false) {
        decisions.push({ type: 'rejected', pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol, reason: riskGate.reason, rejectionClass: 'risk', score, source: snapshot.source, ...context });
        continue;
      }
      // Live Titan route-quoted fill price (real aggregated multi-venue): use it to
      // override the pair-derived price for the actual position size. Fail-closed:
      // if Titan is unavailable, keep the snapshot price unchanged.
      // Zuinig + correct: quote de WERKELIJKE score-gewogen omvang (zelfde logica
      // als enterPaperPosition) i.p.v. een volledige max-positie — een grotere
      // probe levert systematisch slechtere (hogere-slippage) quote op.
      let entryPriceUsd = snapshot.priceUsd;
      if (this.provider.fetchTitanFillUsd && this.config.solPriceUsd) {
        const consecutiveLosses = this.portfolio.consecutiveLosses ?? 0;
        const plannedSol = computePositionSize(score, snapshot.liquidityUsd, this.config.solPriceUsd, this.config, consecutiveLosses);
        const probeLamports = Math.max(1, Math.round(Math.min(plannedSol, this.config.maxPositionSol) * 1_000_000_000));
        try {
          const titanUsd = await this.provider.fetchTitanFillUsd(snapshot.mint, probeLamports, this.config.solPriceUsd);
          if (titanUsd !== undefined && Number.isFinite(titanUsd) && titanUsd > 0) {
            entryPriceUsd = titanUsd;
          }
        } catch (error) {
          // fail-closed: keep pair price on Titan error
        }
      }
      // Top-N entry: verzamel deze entry-grade kandidaat i.p.v. direct te openen.
      // Na de scan-loop worden de kandidaten gerankt op score en worden alleen de
      // beste `maxEntriesPerScan` geopend (voorkomt dat scan-volgorde de keuze
      // bepaalt en dat alle slots in één cycle opengaan).
      entryCandidates.push({ score, snapshot, entryPriceUsd, context });
    }
    // ── Top-N ranked entry (fase 2) ──
    // Rangschik de gate+score-passende kandidaten op score (hoogste eerst) en open
    // hooguit maxEntriesPerScan (binnen het gelijkwaardige portfolio-budget).
    entryCandidates.sort((a, b) => b.score - a.score);
    const entryBudget = Math.min(this.config.maxEntriesPerScan, entryCandidates.length);
    for (let idx = 0; idx < entryBudget; idx++) {
      const { score, snapshot, entryPriceUsd, context } = entryCandidates[idx];
      const nowForEntry = now;
      const entry = enterPaperPosition(this.portfolio, {
        pairId: snapshot.pairId,
        mint: snapshot.mint,
        symbol: snapshot.symbol,
        priceUsd: entryPriceUsd,
        at: nowForEntry.toISOString(),
        score,
        liquidityUsd: snapshot.liquidityUsd,
        priceChangeM5Percent: snapshot.priceChangeM5Percent,
        poolDepth: snapshot.poolDepth,
        solPriceUsd: this.config.solPriceUsd,
      }, this.config);
      if (entry.ok === false) {
        decisions.push({ type: 'rejected', pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol, reason: entry.reason, rejectionClass: 'risk', score, source: snapshot.source, ...context });
        continue;
      }
      this.portfolio = entry.portfolio;
      decisions.push({
        type: 'paper_entry', tradeId: entry.position.tradeId, learningSchemaVersion: 2, pairId: snapshot.pairId, mint: snapshot.mint, symbol: snapshot.symbol,
        score, source: snapshot.source, coverage: 'best_effort', openedAt: evaluatedAt, entryPriceUsd: entry.position.entryPriceUsd, ...context,
      });
    }
    const safeSnapshots = [...snapshots, ...freshPositionSnapshots]
      .filter((snapshot) => isFreshPositionQuote(snapshot, now));
    return { mode: 'paper', portfolio: this.portfolio, decisions, snapshots: Array.from(new Map(safeSnapshots.map((snapshot) => [snapshot.pairId, snapshot])).values()), checkedAt: now.toISOString(), providerErrors };
  }
}
