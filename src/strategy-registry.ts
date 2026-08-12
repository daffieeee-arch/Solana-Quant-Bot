import {
  closeSync, fstatSync, fsyncSync, mkdirSync, openSync, readSync, renameSync, writeFileSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import type { PaperConfig } from './config.js';

const MAX_STRATEGY_FILE_BYTES = 1024 * 1024;
const MAX_STRATEGIES = 100;
const MAX_STRATEGY_ID_BYTES = 64;
const MAX_NOTES_BYTES = 4096;
const TUNABLE_CONFIG_KEYS = [
  // Ontdekking-poort is GEEN leerbare parameter (zie learn-controller.ts):
  // minAge/liquidity-band zijn operator-keuzes via env (contra-regime), geen
  // leerbaar veld — de champion mocht ze niet overschrijven.
  'minPriceChangeM5Percent', 'minVolumeM5Usd', 'minMomentumScore',
  'stopLossPercent', 'takeProfitPercent', 'trailingStopPercent', 'maxHoldMinutes', 'minStopLossPercent',
  'stopVolatilityMultiplier', 'breakevenTriggerPercent', 'minProfitForContinuePercent',
  'positionScoreDivisor',
] as const;
const PERFORMANCE_KEYS = [
  'netReturn', 'profitFactor', 'sharpeRatio', 'sortinoRatio', 'maxDrawdown', 'winRate',
  'avgWin', 'avgLoss', 'totalTrades', 'expectancy', 'medianTrade', 'volatility',
] as const;

type TunableKey = typeof TUNABLE_CONFIG_KEYS[number];
export type StrategyTuningConfig = Partial<Pick<PaperConfig, TunableKey>>;
export type StrategyId = string;

export interface Strategy {
  id: StrategyId;
  parent?: StrategyId;
  createdAt: string;
  config: StrategyTuningConfig;
  status: 'champion' | 'challenger' | 'archived';
  performance?: StrategyPerformance;
  notes?: string;
}

export interface StrategyPerformance {
  netReturn: number;
  profitFactor: number;
  sharpeRatio: number;
  sortinoRatio: number;
  maxDrawdown: number;
  winRate: number;
  avgWin: number;
  avgLoss: number;
  totalTrades: number;
  expectancy: number;
  medianTrade: number;
  volatility: number;
}

function validateStrategyId(id: unknown): asserts id is StrategyId {
  if (typeof id !== 'string'
    || Buffer.byteLength(id, 'utf8') > MAX_STRATEGY_ID_BYTES
    || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(id)) {
    throw new Error('strategy id must be 1..64 safe ASCII characters');
  }
}

function pickTuningConfig(config: Partial<PaperConfig> | undefined): StrategyTuningConfig {
  if (config === undefined) return {};
  if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('strategy config must be an object');
  const picked: Partial<Record<TunableKey, number>> = {};
  for (const key of TUNABLE_CONFIG_KEYS) {
    const value = config[key];
    if (value === undefined) continue;
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`strategy config ${key} must be finite`);
    picked[key] = value;
  }
  const positiveKeys: readonly TunableKey[] = [
    'minPriceChangeM5Percent', 'minVolumeM5Usd',
    'stopLossPercent', 'takeProfitPercent', 'trailingStopPercent', 'maxHoldMinutes',
    'minStopLossPercent', 'stopVolatilityMultiplier', 'breakevenTriggerPercent',
    'minProfitForContinuePercent', 'positionScoreDivisor',
  ];
  for (const key of positiveKeys) {
    if (picked[key] !== undefined && picked[key]! <= 0) throw new Error(`strategy config ${key} must be positive`);
  }
  if (picked.minMomentumScore !== undefined && (picked.minMomentumScore < 0 || picked.minMomentumScore > 100)) throw new Error('strategy config minMomentumScore must be between 0 and 100');
  for (const key of ['stopLossPercent', 'trailingStopPercent', 'minStopLossPercent', 'breakevenTriggerPercent'] as const) {
    if (picked[key] !== undefined && picked[key]! > 100) throw new Error(`strategy config ${key} cannot exceed 100`);
  }
  if (picked.minStopLossPercent !== undefined && picked.stopLossPercent !== undefined && picked.minStopLossPercent > picked.stopLossPercent) throw new Error('strategy config minStopLossPercent cannot exceed stopLossPercent');
  return picked as StrategyTuningConfig;
}

function cloneStrategy(strategy: Strategy): Strategy {
  return {
    ...strategy,
    config: { ...strategy.config },
    ...(strategy.performance ? { performance: { ...strategy.performance } } : {}),
  };
}

function canonicalTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString() === value;
}

function validatePerformance(value: unknown): StrategyPerformance | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('strategy performance must be an object');
  const keys = Object.keys(value).sort();
  const expectedKeys = [...PERFORMANCE_KEYS].sort();
  if (JSON.stringify(keys) !== JSON.stringify(expectedKeys)) throw new Error('strategy performance must contain exactly the required metrics');
  for (const [key, metric] of Object.entries(value)) {
    if (typeof metric !== 'number' || !Number.isFinite(metric)) throw new Error(`strategy performance ${key} must be finite`);
  }
  const performance = value as StrategyPerformance;
  if (!Number.isSafeInteger(performance.totalTrades) || performance.totalTrades < 0) throw new Error('strategy performance totalTrades must be a non-negative safe integer');
  if (performance.winRate < 0 || performance.winRate > 100) throw new Error('strategy performance winRate must be between 0 and 100');
  if (performance.maxDrawdown < 0) throw new Error('strategy performance maxDrawdown cannot be negative');
  if (performance.volatility < 0) throw new Error('strategy performance volatility cannot be negative');
  if (performance.profitFactor < 0) throw new Error('strategy performance profitFactor cannot be negative');
  return { ...performance };
}

function validateRegistry(records: unknown): { strategies: Map<StrategyId, Strategy>; championId: StrategyId } {
  if (!Array.isArray(records)) throw new Error('strategies.json must contain an array');
  if (records.length === 0 || records.length > MAX_STRATEGIES) throw new Error(`strategy count must be 1..${MAX_STRATEGIES}`);
  const strategies = new Map<StrategyId, Strategy>();
  for (const candidate of records) {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) throw new Error('strategy entry must be an object');
    const raw = candidate as Partial<Strategy> & { config?: Partial<PaperConfig> };
    validateStrategyId(raw.id);
    if (strategies.has(raw.id)) throw new Error(`duplicate strategy id ${raw.id}`);
    if (!canonicalTimestamp(raw.createdAt)) throw new Error(`strategy ${raw.id} has an invalid timestamp`);
    if (raw.status !== 'champion' && raw.status !== 'challenger' && raw.status !== 'archived') {
      throw new Error(`strategy ${raw.id} has an invalid status`);
    }
    if (raw.parent !== undefined) validateStrategyId(raw.parent);
    if (raw.notes !== undefined && (typeof raw.notes !== 'string' || Buffer.byteLength(raw.notes, 'utf8') > MAX_NOTES_BYTES)) {
      throw new Error(`strategy ${raw.id} notes exceed ${MAX_NOTES_BYTES} bytes`);
    }
    strategies.set(raw.id, {
      id: raw.id,
      ...(raw.parent === undefined ? {} : { parent: raw.parent }),
      createdAt: raw.createdAt,
      config: pickTuningConfig(raw.config),
      status: raw.status,
      ...(raw.performance === undefined ? {} : { performance: validatePerformance(raw.performance)! }),
      ...(raw.notes === undefined ? {} : { notes: raw.notes }),
    });
  }
  for (const strategy of strategies.values()) {
    if (strategy.parent !== undefined && (strategy.parent === strategy.id || !strategies.has(strategy.parent))) {
      throw new Error(`strategy ${strategy.id} has an unknown parent`);
    }
  }
  const champions = Array.from(strategies.values()).filter((strategy) => strategy.status === 'champion');
  if (champions.length !== 1) throw new Error('strategy registry must contain exactly one champion');
  return { strategies, championId: champions[0].id };
}

export class StrategyRegistry {
  private strategies: Map<StrategyId, Strategy> = new Map();
  private readonly filePath: string;
  public championId: StrategyId;

  constructor(dataDir: string, bootstrapChampionId: StrategyId) {
    validateStrategyId(bootstrapChampionId);
    this.filePath = join(dataDir, 'strategies.json');
    this.championId = bootstrapChampionId;
    this.load(bootstrapChampionId);
  }

  get(strategyId: StrategyId): Strategy | undefined {
    const strategy = this.strategies.get(strategyId);
    return strategy ? cloneStrategy(strategy) : undefined;
  }

  getChampion(): Strategy {
    const champion = this.strategies.get(this.championId);
    if (!champion || champion.status !== 'champion') throw new Error(`Champion strategy ${this.championId} not found`);
    return cloneStrategy(champion);
  }

  getChallengers(): Strategy[] {
    return Array.from(this.strategies.values()).filter((strategy) => strategy.status === 'challenger').map(cloneStrategy);
  }

  create(id: StrategyId, config: PaperConfig, parent?: StrategyId, notes?: string): Strategy {
    validateStrategyId(id);
    if (parent !== undefined) {
      validateStrategyId(parent);
      if (!this.strategies.has(parent)) throw new Error(`parent strategy ${parent} not found`);
    }
    if (this.strategies.has(id)) throw new Error(`Strategy ${id} already exists`);
    if (this.strategies.size >= MAX_STRATEGIES) throw new Error(`strategy count exceeds ${MAX_STRATEGIES}`);
    const strategy: Strategy = {
      id, ...(parent === undefined ? {} : { parent }), createdAt: new Date().toISOString(),
      config: pickTuningConfig(config), status: 'challenger', ...(notes === undefined ? {} : { notes }),
    };
    const next = new Map(this.strategies);
    next.set(id, strategy);
    this.commit(next);
    return cloneStrategy(this.strategies.get(id)!);
  }

  promote(challengerId: StrategyId): void {
    validateStrategyId(challengerId);
    const challenger = this.strategies.get(challengerId);
    if (!challenger) throw new Error(`Strategy ${challengerId} not found`);
    const next = new Map(Array.from(this.strategies, ([id, strategy]) => [id, cloneStrategy(strategy)]));
    for (const strategy of next.values()) if (strategy.status === 'champion') strategy.status = 'archived';
    next.get(challengerId)!.status = 'champion';
    const previous = this.championId;
    this.commit(next);
    console.log(JSON.stringify({ event: 'strategy_promoted', from: previous, to: challengerId }));
  }

  updatePerformance(strategyId: StrategyId, performance: StrategyPerformance): void {
    validateStrategyId(strategyId);
    if (!this.strategies.has(strategyId)) throw new Error(`Strategy ${strategyId} not found`);
    const next = new Map(Array.from(this.strategies, ([id, strategy]) => [id, cloneStrategy(strategy)]));
    next.get(strategyId)!.performance = validatePerformance(performance)!;
    this.commit(next);
  }

  list(): Strategy[] {
    return Array.from(this.strategies.values()).map(cloneStrategy)
      .sort((left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime());
  }

  private load(bootstrapChampionId: StrategyId): void {
    let descriptor: number;
    try {
      descriptor = openSync(this.filePath, 'r');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        this.strategies = new Map([[bootstrapChampionId, {
          id: bootstrapChampionId, createdAt: new Date().toISOString(), config: {}, status: 'champion',
        }]]);
        this.championId = bootstrapChampionId;
        return;
      }
      throw error;
    }

    let raw: Buffer;
    try {
      const stats = fstatSync(descriptor);
      if (!stats.isFile()) throw new Error('strategies.json must be a regular file');
      if (stats.size > MAX_STRATEGY_FILE_BYTES) throw new Error(`strategies.json exceeds one MiB (${MAX_STRATEGY_FILE_BYTES} bytes)`);
      const buffer = Buffer.alloc(MAX_STRATEGY_FILE_BYTES + 1);
      let total = 0;
      while (total < buffer.length) {
        const count = readSync(descriptor, buffer, total, buffer.length - total, null);
        if (count === 0) break;
        total += count;
      }
      if (total > MAX_STRATEGY_FILE_BYTES) throw new Error(`strategies.json exceeds one MiB (${MAX_STRATEGY_FILE_BYTES} bytes)`);
      raw = buffer.subarray(0, total);
    } finally {
      closeSync(descriptor);
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(raw.toString('utf8'));
    } catch (error) {
      throw new Error(`strategies.json is invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
    }
    const validated = validateRegistry(parsed);
    this.strategies = validated.strategies;
    this.championId = validated.championId;
  }

  private commit(next: ReadonlyMap<StrategyId, Strategy>): void {
    const validated = validateRegistry(Array.from(next.values()));
    const serialized = JSON.stringify(Array.from(validated.strategies.values()), null, 2);
    if (Buffer.byteLength(serialized, 'utf8') > MAX_STRATEGY_FILE_BYTES) {
      throw new Error(`strategies.json exceeds one MiB (${MAX_STRATEGY_FILE_BYTES} bytes)`);
    }
    mkdirSync(dirname(this.filePath), { recursive: true });
    const temporaryPath = `${this.filePath}.tmp`;
    const descriptor = openSync(temporaryPath, 'wx', 0o600);
    try {
      writeFileSync(descriptor, serialized, { encoding: 'utf8' });
      fsyncSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    renameSync(temporaryPath, this.filePath);
    const directoryDescriptor = openSync(dirname(this.filePath), 'r');
    try { fsyncSync(directoryDescriptor); } finally { closeSync(directoryDescriptor); }
    this.strategies = validated.strategies;
    this.championId = validated.championId;
  }
}
