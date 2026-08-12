import { writeFileSync, mkdirSync, readFileSync, renameSync, statSync } from 'fs';
import { join } from 'path';
import { validatePaperConfig, type PaperConfig } from './config.js';
import type { StrategyId, StrategyPerformance } from './strategy-registry.js';
import { OpportunityStorage, type OpportunityRecord } from './opportunity-storage.js';
import { ComparisonEngine } from './comparison-engine.js';

const TUNABLE_CONFIG_KEYS = [
  // Ontdekking-poort is GEEN leerbare parameter — het contra-regime (minAge=
  // 20 min rijping, liquiditeits-band) is een bewuste strategiekeuze van de
  // operator via env. De champion overschreef deze met 'Initial baseline'
  // (minAge 3, maxLiq 2M) → verse pools kwamen door de age-gate en alle
  // liquiditeit leek boven de cap. Alleen exit/score-parameters zijn leerdeel.
  'minPriceChangeM5Percent', 'minVolumeM5Usd', 'minMomentumScore',
  'stopLossPercent', 'takeProfitPercent', 'trailingStopPercent', 'maxHoldMinutes', 'minStopLossPercent',
  'stopVolatilityMultiplier', 'breakevenTriggerPercent', 'minProfitForContinuePercent',
  'positionScoreDivisor',
] as const;
type TunableKey = typeof TUNABLE_CONFIG_KEYS[number];
type StrategyTuningConfig = Partial<Pick<PaperConfig, TunableKey>>;
const MAX_PERSISTED_STRATEGIES = 100;
const MAX_STRATEGY_REGISTRY_BYTES = 1024 * 1024;
const MAX_LEARN_REPORT_BYTES = 1024 * 1024;

function pickTuningConfig(config: Partial<PaperConfig> | undefined): StrategyTuningConfig {
  const picked: Partial<Record<TunableKey, number>> = {};
  for (const key of TUNABLE_CONFIG_KEYS) {
    const value = config?.[key];
    if (typeof value === 'number') picked[key] = value;
  }
  return picked as StrategyTuningConfig;
}

function writeJsonAtomic(path: string, value: unknown, maxBytes: number, label: string): void {
  const serialized = JSON.stringify(value, null, 2);
  if (Buffer.byteLength(serialized, 'utf8') > maxBytes) throw new Error(`${label} exceeds its byte limit`);
  const temporaryPath = `${path}.tmp`;
  writeFileSync(temporaryPath, serialized, { mode: 0o600 });
  renameSync(temporaryPath, path);
}

/** LearnController — full self-learning cycle */
export class LearnController {
  readonly storage: OpportunityStorage;
  private readonly comparison = new ComparisonEngine();
  private readonly dataDir: string;
  private readonly strategiesFile: string;
  private readonly baseConfig: PaperConfig;
  private strategies: Map<string, Strategy> = new Map();
  public championId: StrategyId;

  constructor(dataDir: string, initialConfig: PaperConfig) {
    this.dataDir = dataDir;
    this.baseConfig = validatePaperConfig(initialConfig);
    mkdirSync(dataDir, { recursive: true });
    this.storage = new OpportunityStorage(dataDir);
    try {
      this.strategiesFile = join(dataDir, 'strategies-v2.json');
      this.championId = 'v1.0';
      this.load();
      if (!this.strategies.has(this.championId)) {
        this.strategies.set(this.championId, {
          id: this.championId, createdAt: new Date().toISOString(), config: pickTuningConfig(initialConfig),
          status: 'champion', notes: 'Initial baseline',
        });
      }
      for (const strategy of this.strategies.values()) strategy.config = pickTuningConfig(strategy.config);
      this.getChampionRuntimeConfig();
      this.persist();
    } catch (error) {
      try {
        this.storage.close();
      } catch (closeError) {
        throw new AggregateError([error, closeError], 'LearnController initialization and opportunity storage cleanup failed');
      }
      throw error;
    }
  }

  getChampionRuntimeConfig(): PaperConfig {
    const champion = this.strategies.get(this.championId);
    if (!champion) throw new Error(`Champion ${this.championId} not found`);
    return validatePaperConfig({ ...this.baseConfig, ...pickTuningConfig(champion.config) });
  }

  recordObservation(record: OpportunityRecord): void {
    this.storage.log({ ...record, strategyId: this.championId });
  }

  async analyzeAndImprove(): Promise<AnalysisReport> {
    const trades = await this.collectCompletedTrades(this.championId);
    if (trades.length < 20) {
      const report: AnalysisReport = { action: 'skip', reason: `Only ${trades.length} trades — need 20+`, championPerf: this.comparison.computePerformance(trades), totalTrades: trades.length };
      this.writeReport(report);
      return report;
    }

    const championPerf = this.comparison.computePerformance(trades);
    const analysis = this.analyzePatterns(trades);
    const report: AnalysisReport = {
      action: 'hold',
      reason: 'Automatic strategy promotion disabled: deterministic quote-path replay unavailable',
      championPerf,
      totalTrades: trades.length,
      analysis,
      candidatesTested: 0,
    };
    this.writeReport(report);
    return report;
  }

  private async collectCompletedTrades(strategyId: StrategyId): Promise<OpportunityRecord[]> {
    return this.storage.loadCompletedTrades(strategyId, 5_000);
  }

  private analyzePatterns(trades: OpportunityRecord[]): AnalysisResult {
    const wins = trades.filter((t) => (t.pnlSol ?? 0) > 0);
    const losses = trades.filter((t) => (t.pnlSol ?? 0) <= 0);
    const exitReasons = new Map<string, number>();
    for (const t of trades) exitReasons.set(t.exitReason ?? 'unknown', (exitReasons.get(t.exitReason ?? 'unknown') ?? 0) + 1);

    const stopLosses = exitReasons.get('stop_loss') ?? 0;
    const trailingStops = exitReasons.get('trailing_stop') ?? 0;
    const totalExits = stopLosses + trailingStops + (exitReasons.get('time_stop') ?? 0) + (exitReasons.get('max_hold') ?? 0);
    const stopLossRatio = totalExits > 0 ? stopLosses / totalExits : 0;

    const recommendations: string[] = [];
    if (stopLossRatio > 0.6) recommendations.push('stop_loss_too_tight');
    if (trailingStops === 0 && totalExits > 10) recommendations.push('no_trailing_exits');
    if (losses.length > wins.length * 3) recommendations.push('low_win_rate');
    if (wins.length > 0 && losses.length > 0) {
      const avgWin = wins.reduce((s, t) => s + (t.pnlSol ?? 0), 0) / wins.length;
      const avgLoss = Math.abs(losses.reduce((s, t) => s + (t.pnlSol ?? 0), 0) / losses.length);
      if (avgWin < avgLoss) recommendations.push('wins_smaller_than_losses');
    }

    return { winCount: wins.length, lossCount: losses.length, stopLossRatio, recommendations, exitReasons: Object.fromEntries(exitReasons) };
  }

  private writeReport(report: AnalysisReport): void {
    const path = join(this.dataDir, 'learn-report-v2-latest.json');
    writeJsonAtomic(path, report, MAX_LEARN_REPORT_BYTES, 'Learning report');
  }

  private load(): void {
    try {
      if (statSync(this.strategiesFile).size > MAX_STRATEGY_REGISTRY_BYTES) {
        throw new Error('Persisted strategy registry exceeds its byte limit');
      }
      const data = JSON.parse(readFileSync(this.strategiesFile, 'utf-8')) as Strategy[];
      if (!Array.isArray(data)) throw new Error('Persisted strategy registry must be an array');
      if (data.length > MAX_PERSISTED_STRATEGIES) {
        throw new Error(`Persisted strategy registry exceeds ${MAX_PERSISTED_STRATEGIES} strategies`);
      }
      const loaded = new Map<string, Strategy>();
      for (const strategy of data) {
        if (!strategy || typeof strategy.id !== 'string' || strategy.id.length === 0) throw new Error('Persisted strategy has an invalid ID');
        if (loaded.has(strategy.id)) throw new Error(`Duplicate persisted strategy ID: ${strategy.id}`);
        loaded.set(strategy.id, strategy);
      }
      if (data.length > 0) {
        const champions = Array.from(loaded.values()).filter((strategy) => strategy.status === 'champion');
        if (champions.length !== 1) throw new Error(`Expected exactly one persisted champion, found ${champions.length}`);
        this.championId = champions[0].id;
      }
      this.strategies = loaded;
    } catch (error) {
      if (typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT') return;
      throw error;
    }
  }

  private persist(): void {
    mkdirSync(this.strategiesFile.replace(/\/[^/]+$/, ''), { recursive: true });
    writeJsonAtomic(
      this.strategiesFile,
      Array.from(this.strategies.values()),
      MAX_STRATEGY_REGISTRY_BYTES,
      'Persisted strategy registry',
    );
  }
}

export interface AnalysisResult {
  winCount: number; lossCount: number; stopLossRatio: number;
  recommendations: string[]; exitReasons: Record<string, number>;
}

export interface AnalysisReport {
  action: 'skip' | 'hold';
  reason: string;
  championPerf?: StrategyPerformance;
  totalTrades?: number;
  candidatesTested?: number;
  analysis?: AnalysisResult;
}

interface Strategy {
  id: StrategyId; parent?: StrategyId; createdAt: string; config: StrategyTuningConfig;
  status: 'champion' | 'challenger' | 'archived'; performance?: StrategyPerformance; notes?: string;
}
