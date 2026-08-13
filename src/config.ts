import { closeSync, fstatSync, openSync, readSync } from 'node:fs';

const MAX_ENDPOINT_SECRET_BYTES = 8192;
const MAX_WHALE_WALLETS = 100;

export type PaperConfig = {
  mode: 'paper';
  paperStartingSol: number;
  maxPositionSol: number;
  maxConcurrentPositions: number;
  /** Top-N entry: maximum aantal entries dat één scan-cycle mag openen. Na het
   *  verzamelen van alle gate-passende kandidaten wordt gerankt op score en
   *  alleen de beste N geopend (i.p.v. één-voor-één in scan-volgorde). */
  maxEntriesPerScan: number;
  maxDailyLossSol: number;
  minLiquidityUsd: number;
  maxLiquidityUsd: number;
  minAgeMinutes: number;
  maxAgeMinutes: number;
  minPriceChangeM5Percent: number;
  minVolumeM5Usd: number;
  minMomentumScore: number;
  /** Minimum absolute buy count in the window (the "hundreds of buyers in a short time" surge). */
  minBuySurgeCount: number;
  /** Minimum buy pressure ratio buys/(buys+sells) — only enter when buyers dominate. */
  minBuyPressure: number;
  /**
   * Contra-mode: maximale buy-pressure ratio buys/(buys+sells) — in de dip
   * willen we LAGE buy-druk (sellers domineren). Backtest 3-mnd: bp<=0.55
   * geeft de beste expectancy (+23.4%/trade). Waarden > deze drempel = te
   * veel retail-extase nabij de top → skip.
   */
  contraMaxBuyPressure: number;
  /**
   * Entry regime:
   *  - 'surge'  (default, historisch): koop bij hoge buy-druk + momentum + surge
   *             (absolute buy-count). Backtest 179-mint/72u liet zien dat dit
   *             ANTI-edge is (koopt nabij top van retail-extase).
   *  - 'contra': koop dip (laag momentum / lage buy-druk / geen whale / oudere
   *             coin ≥20-23min). Dit had op dezelfde data de robuust positieve
   *             expectancy (+~4-7%/trade op het laag-druk kwartiel).
   */
  entryMode: 'surge' | 'contra';
  /** Fase-QH: entry-gate in shadow-modus (false = enforced; true = shadow-evalueer
   *  en rapporteer WOULD_ACCEPT/WOULD_REJECT zonder de entry-beslissing te wijzigen). */
  entryShadowMode: boolean;
  stopLossPercent: number;
  takeProfitPercent: number;
  trailingStopPercent: number;
  maxHoldMinutes: number;
  simulatedSlippageBps: number;
  simulatedFeeBps: number;
  scanIntervalSeconds: number;
  maxCycles: number;
  strictRiskMode: boolean;
  dataDir: string;
  dashboardEnabled: boolean;
  dashboardPort: number;
  // Quant strategy parameters
  minStopLossPercent: number;
  maxStopLossPercent: number;
  stopVolatilityMultiplier: number;
  breakevenTriggerPercent: number;
  minProfitForContinuePercent: number;
  positionScoreDivisor: number;
  liquidityPositionFraction: number;
  /** Live SOL/USD reference used only to convert USD pool-liquidity caps into SOL. */
  solPriceUsd?: number;
  // Whale tracking parameters
  whaleWallets: string[];
  minWhaleTxSol: number;
  rpcHttpEndpoint?: string;
  rpcWsEndpoint?: string;
};

const POSITIVE_CONFIG_KEYS: Array<keyof PaperConfig> = [
  'paperStartingSol', 'maxPositionSol', 'maxConcurrentPositions', 'maxDailyLossSol',
  'minLiquidityUsd', 'maxLiquidityUsd', 'minAgeMinutes', 'maxAgeMinutes',
  'minPriceChangeM5Percent', 'minVolumeM5Usd', 'minMomentumScore', 'stopLossPercent',
  'takeProfitPercent', 'trailingStopPercent', 'maxHoldMinutes', 'scanIntervalSeconds',
  'dashboardPort', 'minStopLossPercent', 'maxStopLossPercent', 'stopVolatilityMultiplier',
  'breakevenTriggerPercent', 'minProfitForContinuePercent', 'positionScoreDivisor',
  'liquidityPositionFraction', 'minWhaleTxSol',
];

export function validatePaperConfig(config: PaperConfig): PaperConfig {
  if (config.mode !== 'paper') throw new Error('mode must remain paper');
  for (const key of POSITIVE_CONFIG_KEYS) {
    const value = config[key];
    if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) throw new Error(`${String(key)} must be a positive number`);
  }
  // Surge/pressure are disabled at 0 (strategy off) but must not be negative.
  for (const key of ['minBuySurgeCount', 'minBuyPressure'] as const) {
    if (typeof config[key] !== 'number' || !Number.isFinite(config[key]) || config[key] < 0) throw new Error(`${key} must be a non-negative number`);
  }
  for (const key of ['simulatedSlippageBps', 'simulatedFeeBps', 'maxCycles'] as const) {
    const value = config[key];
    if (!Number.isInteger(value) || value < 0) throw new Error(`${key} must be a non-negative integer`);
  }
  if (!Number.isInteger(config.maxConcurrentPositions)) throw new Error('maxConcurrentPositions must be an integer');
  if (config.solPriceUsd !== undefined && (!Number.isFinite(config.solPriceUsd) || config.solPriceUsd <= 0)) throw new Error('solPriceUsd must be a positive number when provided');
  if (config.minMomentumScore > 100) throw new Error('minMomentumScore cannot exceed 100');
  if (config.maxStopLossPercent > 100) throw new Error('maxStopLossPercent cannot exceed 100');
  if (config.liquidityPositionFraction > 1) throw new Error('liquidityPositionFraction cannot exceed 1');
  if (!Array.isArray(config.whaleWallets)) throw new Error('whaleWallets must be an array');
  if (config.whaleWallets.length > MAX_WHALE_WALLETS) throw new Error(`whaleWallets cannot exceed ${MAX_WHALE_WALLETS}`);
  const uniqueWhaleWallets = new Set<string>();
  for (const wallet of config.whaleWallets) {
    if (typeof wallet !== 'string' || !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(wallet)) throw new Error('whale wallet must be a canonical Solana base58 address');
    if (uniqueWhaleWallets.has(wallet)) throw new Error(`duplicate whale wallet: ${wallet}`);
    uniqueWhaleWallets.add(wallet);
  }
  if (config.maxLiquidityUsd <= config.minLiquidityUsd) throw new Error('maxLiquidityUsd must be greater than minLiquidityUsd');
  if (config.maxAgeMinutes <= config.minAgeMinutes) throw new Error('maxAgeMinutes must be greater than minAgeMinutes');
  if (config.maxPositionSol > config.paperStartingSol) throw new Error('maxPositionSol cannot exceed paperStartingSol');
  if (config.minStopLossPercent > config.maxStopLossPercent) throw new Error('minStopLossPercent cannot exceed maxStopLossPercent');
  return { ...config, whaleWallets: [...config.whaleWallets] };
}

type Env = Record<string, string | undefined>;

function positiveNumber(env: Env, key: string): number {
  const value = Number(env[key]);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${key} must be a positive number`);
  }
  return value;
}

function nonNegativeInteger(env: Env, key: string): number {
  const value = Number(env[key]);
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`${key} must be a non-negative integer`);
  }
  return value;
}

function boolean(env: Env, key: string): boolean {
  const value = env[key];
  if (value === 'true') return true;
  if (value === 'false') return false;
  throw new Error(`${key} must be true or false`);
}

function validateEndpoint(value: string, protocol: 'https:' | 'wss:', key: string): string {
  if (Buffer.byteLength(value, 'utf8') > MAX_ENDPOINT_SECRET_BYTES) throw new Error(`${key} exceeds ${MAX_ENDPOINT_SECRET_BYTES} bytes`);
  if (value.includes('\0') || value.includes('\n') || value.includes('\r')) throw new Error(`${key} must contain one line`);
  let parsed: URL;
  try { parsed = new URL(value); } catch { throw new Error(`${key} must be a valid ${protocol.slice(0, -1)} URL`); }
  if (parsed.protocol !== protocol) throw new Error(`${key} must use ${protocol.slice(0, -1)}://`);
  if (parsed.username || parsed.password) throw new Error(`${key} must not contain URL userinfo`);
  return value;
}

function readSecretFile(filePath: string, fileKey: string): string {
  let descriptor: number;
  try { descriptor = openSync(filePath, 'r'); } catch { throw new Error(`${fileKey} could not be read`); }
  try {
    const stats = fstatSync(descriptor);
    if (!stats.isFile()) throw new Error(`${fileKey} must be a regular file`);
    if (stats.size > MAX_ENDPOINT_SECRET_BYTES) throw new Error(`${fileKey} exceeds ${MAX_ENDPOINT_SECRET_BYTES} bytes`);
    const buffer = Buffer.alloc(MAX_ENDPOINT_SECRET_BYTES + 1);
    let total = 0;
    while (total < buffer.length) {
      const count = readSync(descriptor, buffer, total, buffer.length - total, null);
      if (count === 0) break;
      total += count;
    }
    if (total > MAX_ENDPOINT_SECRET_BYTES) throw new Error(`${fileKey} exceeds ${MAX_ENDPOINT_SECRET_BYTES} bytes`);
    let value = buffer.subarray(0, total).toString('utf8');
    if (value.endsWith('\r\n')) value = value.slice(0, -2);
    else if (value.endsWith('\n')) value = value.slice(0, -1);
    if (!value || value !== value.trim()) throw new Error(`${fileKey} must not be empty or padded`);
    if (value.includes('\0') || value.includes('\n') || value.includes('\r')) throw new Error(`${fileKey} must contain one line`);
    return value;
  } finally {
    closeSync(descriptor);
  }
}

function valueOrSecretFile(env: Env, valueKey: string, fileKey: string, protocol: 'https:' | 'wss:'): string | undefined {
  const directValue = env[valueKey]?.trim();
  const filePath = env[fileKey]?.trim();
  if (directValue && filePath) throw new Error(`Cannot configure both ${valueKey} and ${fileKey}`);
  const value = filePath ? readSecretFile(filePath, fileKey) : directValue;
  return value ? validateEndpoint(value, protocol, filePath ? fileKey : valueKey) : undefined;
}

export function loadConfig(env: Env = process.env): PaperConfig {
  if (env.MODE !== 'paper') {
    throw new Error('This build is paper-only; live execution is not implemented.');
  }

  const config: PaperConfig = {
    mode: 'paper',
    paperStartingSol: positiveNumber(env, 'PAPER_STARTING_SOL'),
    maxPositionSol: positiveNumber(env, 'MAX_POSITION_SOL'),
    maxConcurrentPositions: positiveNumber(env, 'MAX_CONCURRENT_POSITIONS'),
    maxEntriesPerScan: env.MAX_ENTRIES_PER_SCAN === undefined ? 1 : positiveNumber(env, 'MAX_ENTRIES_PER_SCAN'),
    maxDailyLossSol: positiveNumber(env, 'MAX_DAILY_LOSS_SOL'),
    minLiquidityUsd: positiveNumber(env, 'MIN_LIQUIDITY_USD'),
    maxLiquidityUsd: positiveNumber(env, 'MAX_LIQUIDITY_USD'),
    minAgeMinutes: positiveNumber(env, 'MIN_AGE_MINUTES'),
    maxAgeMinutes: positiveNumber(env, 'MAX_AGE_MINUTES'),
    minPriceChangeM5Percent: positiveNumber(env, 'MIN_PRICE_CHANGE_M5_PERCENT'),
    minVolumeM5Usd: positiveNumber(env, 'MIN_VOLUME_M5_USD'),
    minMomentumScore: env.MIN_MOMENTUM_SCORE === undefined ? 35 : positiveNumber(env, 'MIN_MOMENTUM_SCORE'),
    minBuySurgeCount: env.MIN_BUY_SURGE_COUNT === undefined ? 0 : positiveNumber(env, 'MIN_BUY_SURGE_COUNT'),
    minBuyPressure: env.MIN_BUY_PRESSURE === undefined ? 0 : positiveNumber(env, 'MIN_BUY_PRESSURE'),
    contraMaxBuyPressure: env.CONTRA_MAX_BUY_PRESSURE === undefined ? 0.62 : positiveNumber(env, 'CONTRA_MAX_BUY_PRESSURE'),
    entryMode: env.ENTRY_MODE === 'contra' ? 'contra' : 'surge',
    entryShadowMode: env.ENTRY_SHADOW_MODE === '1' || env.ENTRY_SHADOW_MODE === 'true',
    stopLossPercent: positiveNumber(env, 'STOP_LOSS_PERCENT'),
    takeProfitPercent: positiveNumber(env, 'TAKE_PROFIT_PERCENT'),
    trailingStopPercent: positiveNumber(env, 'TRAILING_STOP_PERCENT'),
    maxHoldMinutes: positiveNumber(env, 'MAX_HOLD_MINUTES'),
    simulatedSlippageBps: nonNegativeInteger(env, 'SIMULATED_SLIPPAGE_BPS'),
    simulatedFeeBps: nonNegativeInteger(env, 'SIMULATED_FEE_BPS'),
    scanIntervalSeconds: positiveNumber(env, 'SCAN_INTERVAL_SECONDS'),
    maxCycles: nonNegativeInteger(env, 'MAX_CYCLES'),
    strictRiskMode: boolean(env, 'STRICT_RISK_MODE'),
    dataDir: env.DATA_DIR?.trim() || './data',
    dashboardEnabled: env.DASHBOARD_ENABLED === 'true',
    dashboardPort: env.DASHBOARD_PORT ? positiveNumber(env, 'DASHBOARD_PORT') : 3000,
    minStopLossPercent: env.MIN_STOP_LOSS_PERCENT === undefined ? 5 : positiveNumber(env, 'MIN_STOP_LOSS_PERCENT'),
    maxStopLossPercent: env.MAX_STOP_LOSS_PERCENT === undefined ? 25 : positiveNumber(env, 'MAX_STOP_LOSS_PERCENT'),
    stopVolatilityMultiplier: env.STOP_VOLATILITY_MULTIPLIER === undefined ? 1.5 : positiveNumber(env, 'STOP_VOLATILITY_MULTIPLIER'),
    breakevenTriggerPercent: env.BREAKEVEN_TRIGGER_PERCENT === undefined ? 8 : positiveNumber(env, 'BREAKEVEN_TRIGGER_PERCENT'),
    minProfitForContinuePercent: env.MIN_PROFIT_FOR_CONTINUE_PERCENT === undefined ? 3 : positiveNumber(env, 'MIN_PROFIT_FOR_CONTINUE_PERCENT'),
    positionScoreDivisor: env.POSITION_SCORE_DIVISOR === undefined ? 100 : positiveNumber(env, 'POSITION_SCORE_DIVISOR'),
    liquidityPositionFraction: env.LIQUIDITY_POSITION_FRACTION === undefined ? 0.02 : positiveNumber(env, 'LIQUIDITY_POSITION_FRACTION'),
    solPriceUsd: env.SOL_PRICE_USD === undefined ? undefined : positiveNumber(env, 'SOL_PRICE_USD'),
    whaleWallets: env.WHALE_WALLETS ? env.WHALE_WALLETS.split(',').map((w) => w.trim()).filter((w) => w.length > 0) : [],
    minWhaleTxSol: env.MIN_WHALE_TX_SOL === undefined ? 5 : positiveNumber(env, 'MIN_WHALE_TX_SOL'),
    rpcHttpEndpoint: valueOrSecretFile(env, 'RPC_HTTP_ENDPOINT', 'RPC_HTTP_ENDPOINT_FILE', 'https:'),
    rpcWsEndpoint: valueOrSecretFile(env, 'RPC_WS_ENDPOINT', 'RPC_WS_ENDPOINT_FILE', 'wss:'),
  };

  if (config.maxLiquidityUsd <= config.minLiquidityUsd) {
    throw new Error('MAX_LIQUIDITY_USD must be greater than MIN_LIQUIDITY_USD');
  }
  if (config.maxAgeMinutes <= config.minAgeMinutes) {
    throw new Error('MAX_AGE_MINUTES must be greater than MIN_AGE_MINUTES');
  }
  if (config.maxPositionSol > config.paperStartingSol) {
    throw new Error('MAX_POSITION_SOL cannot exceed PAPER_STARTING_SOL');
  }
  return validatePaperConfig(config);
}
