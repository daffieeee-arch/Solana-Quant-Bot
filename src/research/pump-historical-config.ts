import { validatePaperConfig, type PaperConfig } from '../config.js';

type JsonObject = Record<string, unknown>;

const REQUIRED_KEYS: ReadonlyArray<keyof PaperConfig> = [
  'mode', 'paperStartingSol', 'maxPositionSol', 'maxConcurrentPositions', 'maxEntriesPerScan',
  'maxDailyLossSol', 'minLiquidityUsd', 'maxLiquidityUsd', 'minAgeMinutes', 'maxAgeMinutes',
  'minPriceChangeM5Percent', 'minVolumeM5Usd', 'minMomentumScore', 'minBuySurgeCount',
  'minBuyPressure', 'contraMaxBuyPressure', 'entryMode', 'entryShadowMode', 'stopLossPercent',
  'takeProfitPercent', 'trailingStopPercent', 'maxHoldMinutes', 'simulatedSlippageBps',
  'simulatedFeeBps', 'scanIntervalSeconds', 'maxCycles', 'strictRiskMode', 'dataDir',
  'dashboardEnabled', 'dashboardPort', 'minStopLossPercent', 'maxStopLossPercent',
  'stopVolatilityMultiplier', 'breakevenTriggerPercent', 'minProfitForContinuePercent',
  'positionScoreDivisor', 'liquidityPositionFraction', 'whaleWallets', 'minWhaleTxSol',
];
const OPTIONAL_KEYS: ReadonlyArray<keyof PaperConfig> = ['solPriceUsd', 'rpcHttpEndpoint', 'rpcWsEndpoint'];
const ALLOWED_KEYS = new Set<string>([...REQUIRED_KEYS, ...OPTIONAL_KEYS]);

const isObject = (value: unknown): value is JsonObject => typeof value === 'object' && value !== null && !Array.isArray(value);

function numberIn(
  object: JsonObject,
  key: keyof PaperConfig,
  options: { min: number; max: number; integer?: boolean },
): number {
  const value = object[key];
  if (typeof value !== 'number' || !Number.isFinite(value)
    || value < options.min || value > options.max
    || (options.integer === true && !Number.isInteger(value))) {
    throw new Error(`${String(key)} is outside the research-safe numeric contract`);
  }
  return value;
}

function booleanField(object: JsonObject, key: keyof PaperConfig): boolean {
  const value = object[key];
  if (typeof value !== 'boolean') throw new Error(`${String(key)} must be boolean`);
  return value;
}

/**
 * Strict runtime decoder for the file-only research CLI. The production config
 * validator intentionally accepts an already-typed PaperConfig; this decoder is
 * the untrusted JSON boundary and therefore checks every consumed field, rejects
 * provider endpoints, and caps values before lamport arithmetic.
 */
export function decodePumpResearchConfig(value: unknown): PaperConfig {
  if (!isObject(value)) throw new Error('config input must be a JSON object');
  for (const key of Object.keys(value)) {
    if (!ALLOWED_KEYS.has(key)) throw new Error('unsupported research config field');
  }
  for (const key of REQUIRED_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) throw new Error(`missing research config field: ${String(key)}`);
  }

  if (value.mode !== 'paper') throw new Error('mode must remain paper');
  if (value.rpcHttpEndpoint !== undefined || value.rpcWsEndpoint !== undefined) {
    throw new Error('provider endpoints are forbidden in the offline research config');
  }
  if (value.entryMode !== 'surge' && value.entryMode !== 'contra') throw new Error('entryMode must be surge or contra');
  if (typeof value.dataDir !== 'string' || value.dataDir.length === 0 || value.dataDir.length > 4096) {
    throw new Error('dataDir must be a bounded non-empty string');
  }
  booleanField(value, 'entryShadowMode');
  booleanField(value, 'strictRiskMode');
  booleanField(value, 'dashboardEnabled');

  for (const key of ['paperStartingSol', 'maxPositionSol', 'maxDailyLossSol', 'minWhaleTxSol'] as const) {
    numberIn(value, key, { min: Number.EPSILON, max: 1_000_000 });
  }
  for (const key of ['minLiquidityUsd', 'maxLiquidityUsd', 'minVolumeM5Usd'] as const) {
    numberIn(value, key, { min: Number.EPSILON, max: 1_000_000_000_000_000 });
  }
  for (const key of ['minAgeMinutes', 'maxAgeMinutes', 'maxHoldMinutes'] as const) {
    numberIn(value, key, { min: Number.EPSILON, max: 10_000_000 });
  }
  for (const key of [
    'minPriceChangeM5Percent', 'stopLossPercent', 'takeProfitPercent', 'trailingStopPercent',
    'minStopLossPercent', 'maxStopLossPercent', 'stopVolatilityMultiplier',
    'breakevenTriggerPercent', 'minProfitForContinuePercent', 'positionScoreDivisor',
  ] as const) {
    numberIn(value, key, { min: Number.EPSILON, max: 10_000 });
  }
  numberIn(value, 'minMomentumScore', { min: Number.EPSILON, max: 100 });
  numberIn(value, 'minBuySurgeCount', { min: 0, max: 1_000_000_000, integer: true });
  numberIn(value, 'minBuyPressure', { min: 0, max: 1 });
  numberIn(value, 'contraMaxBuyPressure', { min: 0, max: 1 });
  numberIn(value, 'liquidityPositionFraction', { min: Number.EPSILON, max: 1 });
  numberIn(value, 'maxConcurrentPositions', { min: 1, max: 10_000, integer: true });
  numberIn(value, 'maxEntriesPerScan', { min: 1, max: 10_000, integer: true });
  numberIn(value, 'simulatedSlippageBps', { min: 0, max: 9_999, integer: true });
  numberIn(value, 'simulatedFeeBps', { min: 0, max: 9_999, integer: true });
  numberIn(value, 'scanIntervalSeconds', { min: 1, max: 86_400, integer: true });
  numberIn(value, 'maxCycles', { min: 0, max: 1_000_000_000, integer: true });
  numberIn(value, 'dashboardPort', { min: 1, max: 65_535, integer: true });
  if (value.solPriceUsd !== undefined) numberIn(value, 'solPriceUsd', { min: Number.EPSILON, max: 1_000_000_000 });

  if (!Array.isArray(value.whaleWallets)) throw new Error('whaleWallets must be an array');
  if ((value.maxEntriesPerScan as number) > (value.maxConcurrentPositions as number)) {
    throw new Error('maxEntriesPerScan cannot exceed maxConcurrentPositions');
  }

  const decoded = Object.fromEntries(
    [...REQUIRED_KEYS, ...OPTIONAL_KEYS]
      .filter((key) => Object.prototype.hasOwnProperty.call(value, key))
      .map((key) => [key, value[key]]),
  ) as PaperConfig;
  return validatePaperConfig(decoded);
}
