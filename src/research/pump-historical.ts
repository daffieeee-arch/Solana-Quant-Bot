import { createHash } from 'node:crypto';
import type { PaperConfig } from '../config.js';
import { isCompleteIdentity, type MarketIdentity } from '../market-identity2.js';
import {
  createPortfolio,
  enterPaperPosition,
  evaluateOpenPosition,
} from '../portfolio.js';
import { deriveBondingCurve, PUMP_PROGRAM_ID as CANONICAL_PUMP_PROGRAM_ID } from '../pump-address.js';
import { isFreshPositionQuote } from '../quote-freshness.js';
import {
  evaluateMarketGate,
  scoreContraMomentum,
  scoreMomentum,
  type MarketSnapshot,
} from '../scoring.js';
import { decodePumpResearchConfig } from './pump-historical-config.js';

export const PUMP_PROGRAM_ID = CANONICAL_PUMP_PROGRAM_ID;
export const PUMP_RESEARCH_SOURCE_CONTRACT = 'PUMP_SNAPSHOT_V2';
const WSOL_MINT = 'So11111111111111111111111111111111111111112';
const CANONICAL_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const MAX_TEXT_BYTES = 4_096;
const MAX_PRICE_USD = 1_000_000_000;
const MAX_SOL_PRICE_USD = 1_000_000_000;
const MAX_USD_SNAPSHOT = 1_000_000_000_000_000;
const MAX_EVENT_COUNT = 1_000_000_000;
const MAX_ABS_PERCENT = 1_000_000;

const REQUIRED_CAPABILITIES = {
  structuralPumpIdentity: 'missing_structural_pump_identity',
  nativeSolBalanceDeltas: 'missing_native_sol_balance_deltas',
  innerInstructions: 'missing_inner_instructions',
  loadedAddresses: 'missing_loaded_addresses',
  canonicalLaunchTimestamp: 'missing_canonical_launch_timestamp',
  transactionOrder: 'missing_transaction_order',
  exactPriceUnits: 'missing_exact_price_units',
  causalFeatureWindows: 'missing_causal_feature_windows',
  scanCycleIdentity: 'missing_scan_cycle_identity',
  boundedMarkCadence: 'missing_bounded_mark_cadence',
} as const;

const LIVE_GATE_CAPABILITIES = [
  'historicalSolUsd',
  'liquiditySnapshots',
  'rugRiskSnapshots',
  'whaleFlowSnapshots',
] as const;

/**
 * Real v2 exports are HOLD until their parser/query pair has a separate review.
 * Future approvals must add one exact tuple here in a reviewed commit; arbitrary
 * manifest booleans and hashes can never self-authorize.
 */
const APPROVED_REAL_PROVENANCE: ReadonlyArray<{
  provenanceId: string;
  parserGitSha: string;
  exportQuerySha256: string;
}> = [];

/** Fixed synthetic tuple for lifecycle regression tests only. It can produce a
 * `SYNTHETIC_TEST_ONLY` artifact, never READY and never CLI exit 0. */
const APPROVED_SYNTHETIC_PROVENANCE = [{
  provenanceId: 'synthetic-test-fixture',
  parserGitSha: 'a'.repeat(40),
  exportQuerySha256: 'b'.repeat(64),
}] as const;

type ProvenanceClass = 'real' | 'synthetic_test_only' | undefined;

export type PumpResearchCapabilities = {
  [K in keyof typeof REQUIRED_CAPABILITIES]: boolean;
} & {
  [K in (typeof LIVE_GATE_CAPABILITIES)[number]]: boolean;
};

export type PumpResearchManifest = {
  schemaVersion: 2;
  sourceContract: typeof PUMP_RESEARCH_SOURCE_CONTRACT;
  provenanceId: string;
  parserGitSha: string;
  exportQuerySha256: string;
  rowsSha256: string;
  generatedAt: string;
  window: {
    startInclusive: string;
    endExclusive: string;
  };
  split: {
    trainEndExclusive: string;
    validationEndExclusive: string;
    embargoSeconds: number;
  };
  samplingIntervalSeconds: number;
  capabilities: PumpResearchCapabilities;
};

export type PumpResearchRecord = Omit<MarketSnapshot, 'whaleInterestAt' | 'poolDepth'> & {
  sampleId: string;
  signature: string;
  slot: number;
  transactionIndex: number;
  instructionLocation: 'top_level' | 'inner';
  instructionIndex: number;
  parentInstructionIndex?: number;
  scanCycleId: string;
  scanCycleStartedAt: string;
  programId: typeof PUMP_PROGRAM_ID;
  solPriceUsd: number;
  liquidityUsd: number;
  volumeM5Usd: number;
  priceChangeM5Percent: number;
  buysM5: number;
  sellsM5: number;
  rugRisk: NonNullable<MarketSnapshot['rugRisk']>;
  whaleInterestAt: string | null;
  poolDepth?: never;
};

export type PumpSuitabilityReport = {
  ready: boolean;
  blockers: string[];
  warnings: string[];
  recordCount: number;
  mintCount: number;
};

export type PumpResearchSplit = {
  train: PumpResearchRecord[];
  validation: PumpResearchRecord[];
  test: PumpResearchRecord[];
};

export type ClosedResearchTrade = {
  tradeId: string;
  mint: string;
  entryAt: string;
  exitAt: string;
  allocatedLamports: number;
  entryPriceObservedUsd: number;
  entrySolUsd: number;
  entryPriceExecutableSolPerToken: number;
  exitPriceObservedUsd: number;
  exitSolUsd: number;
  exitReason: 'stop_loss' | 'trailing_stop' | 'max_hold' | 'time_stop';
  pnlLamports: number;
};

export type CensoredResearchPosition = {
  tradeId: string;
  mint: string;
  openedAt: string;
  allocatedLamports: number;
  entryPriceObservedUsd: number;
  entrySolUsd: number;
  entryPriceExecutableSolPerToken: number;
  lastObservedAt: string;
  lastObservedPriceUsd: number;
};

export type PumpSplitResult = {
  split: 'train' | 'validation' | 'test';
  records: number;
  mints: number;
  closedTrades: ClosedResearchTrade[];
  censoredOpenPositions: CensoredResearchPosition[];
  realizedPnlLamports: number;
  endingAvailableLamports: number;
};

type JsonObject = { [key: string]: unknown };
type SplitName = keyof PumpResearchSplit;
type ParsedManifestBoundaries = {
  start: number;
  end: number;
  trainEnd: number;
  validationEnd: number;
  embargoMs: number;
};

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function textBytes(value: string): number {
  return Buffer.byteLength(value, 'utf8');
}

function isBoundedText(value: unknown, maximum = MAX_TEXT_BYTES): value is string {
  return typeof value === 'string' && value.length > 0 && textBytes(value) <= maximum;
}

function base58DecodedLength(value: unknown): number | undefined {
  if (typeof value !== 'string' || value.length === 0 || value.length > 128) return undefined;
  let leadingZeros = 0;
  while (leadingZeros < value.length && value[leadingZeros] === '1') leadingZeros += 1;
  let decoded = 0n;
  for (const character of value) {
    const index = BASE58_ALPHABET.indexOf(character);
    if (index < 0) return undefined;
    decoded = decoded * 58n + BigInt(index);
  }
  let payloadBytes = 0;
  while (decoded > 0n) {
    payloadBytes += 1;
    decoded >>= 8n;
  }
  return leadingZeros + payloadBytes;
}

function isCanonicalPublicKey(value: unknown): value is string {
  return base58DecodedLength(value) === 32;
}

function isCanonicalSignature(value: unknown): value is string {
  return base58DecodedLength(value) === 64;
}

function canonicalTimestamp(value: unknown): number | undefined {
  if (typeof value !== 'string' || !CANONICAL_TIMESTAMP.test(value)) return undefined;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return undefined;
  return new Date(timestamp).toISOString() === value ? timestamp : undefined;
}

function compareText(a: string, b: string): number {
  return a === b ? 0 : a < b ? -1 : 1;
}

function isValidPumpIdentity(value: unknown, row: PumpResearchRecord): boolean {
  if (!isObject(value)
    || !isCanonicalPublicKey(row.mint)
    || value.kind !== 'pump_bonding_curve'
    || !isBoundedText(value.tradeId)
    || !isCanonicalPublicKey(value.marketId)
    || !isCanonicalPublicKey(value.bondCurve)
    || !isCanonicalPublicKey(value.mint)
    || !isCanonicalPublicKey(value.baseMint)
    || !isCanonicalPublicKey(value.quoteMint)
    || value.programId !== PUMP_PROGRAM_ID
    || value.protocol !== 'pump'
    || canonicalTimestamp(value.sourceTimestamp) === undefined
    || value.entryPriceSource !== 'LOCAL_STATE'
    || value.markPriceSource !== 'LOCAL_STATE'
    || value.boundedExitFallback !== true
    || value.strategyVersion !== 'pump-snapshot-v2'
    || value.schemaVersion !== 2) return false;
  const identity = value as unknown as MarketIdentity;
  let derivedCurve: string;
  try { derivedCurve = deriveBondingCurve(row.mint); } catch { return false; }
  return isCompleteIdentity(identity)
    && identity.kind === 'pump_bonding_curve'
    && identity.mint === row.mint
    && identity.baseMint === row.mint
    && identity.marketId === row.pairId
    && identity.bondCurve === row.pairId
    && identity.marketId === derivedCurve
    && identity.baseDecimals === 6
    && identity.quoteDecimals === 9
    && identity.quoteMint === WSOL_MINT
    && identity.sourceTimestamp === row.observedAt;
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (!isObject(value)) return value;
  const out = Object.create(null) as JsonObject;
  for (const key of Object.keys(value).sort(compareText)) {
    if (value[key] !== undefined) Object.defineProperty(out, key, {
      value: canonicalize(value[key]), enumerable: true, configurable: true, writable: true,
    });
  }
  return out;
}

export function stableResearchJson(value: unknown): string {
  assertFiniteTree(value);
  const serialized = JSON.stringify(canonicalize(value), null, 2);
  if (serialized === undefined) throw new Error('research output is not JSON serializable');
  return `${serialized}\n`;
}

export function hashPumpRecords(records: readonly unknown[]): string {
  return createHash('sha256').update(stableResearchJson(records), 'utf8').digest('hex');
}

function finiteNumberIn(value: unknown, minimum: number, maximum: number): boolean {
  return typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum;
}

function uniqueSorted(values: string[]): string[] {
  return [...new Set(values)].sort(compareText);
}

function normalizeRecord(value: unknown): PumpResearchRecord | undefined {
  return isObject(value) ? value as PumpResearchRecord : undefined;
}

function provenanceClass(manifest: JsonObject): ProvenanceClass {
  if (APPROVED_REAL_PROVENANCE.some((approved) => approved.provenanceId === manifest.provenanceId
    && approved.parserGitSha === manifest.parserGitSha
    && approved.exportQuerySha256 === manifest.exportQuerySha256)) return 'real';
  if (APPROVED_SYNTHETIC_PROVENANCE.some((approved) => approved.provenanceId === manifest.provenanceId
    && approved.parserGitSha === manifest.parserGitSha
    && approved.exportQuerySha256 === manifest.exportQuerySha256)) return 'synthetic_test_only';
  return undefined;
}

function parseManifestBoundaries(manifest: JsonObject): ParsedManifestBoundaries | undefined {
  const window = isObject(manifest.window) ? manifest.window : undefined;
  const split = isObject(manifest.split) ? manifest.split : undefined;
  if (!window || !split) return undefined;
  const start = canonicalTimestamp(window.startInclusive);
  const end = canonicalTimestamp(window.endExclusive);
  const trainEnd = canonicalTimestamp(split.trainEndExclusive);
  const validationEnd = canonicalTimestamp(split.validationEndExclusive);
  const embargoSeconds = split.embargoSeconds;
  if (start === undefined || end === undefined || trainEnd === undefined || validationEnd === undefined
    || !Number.isSafeInteger(embargoSeconds) || (embargoSeconds as number) <= 0
    || !(start < trainEnd && trainEnd < validationEnd && validationEnd < end)) return undefined;
  return { start, end, trainEnd, validationEnd, embargoMs: (embargoSeconds as number) * 1_000 };
}

function classifyLaunch(launched: number, boundaries: ParsedManifestBoundaries): SplitName | 'embargo' | undefined {
  if (launched < boundaries.start || launched >= boundaries.end) return undefined;
  if (launched < boundaries.trainEnd - boundaries.embargoMs) return 'train';
  if (launched < boundaries.trainEnd) return 'embargo';
  if (launched < boundaries.validationEnd - boundaries.embargoMs) return 'validation';
  if (launched < boundaries.validationEnd) return 'embargo';
  if (launched < boundaries.end - boundaries.embargoMs) return 'test';
  return 'embargo';
}

function splitObservationEnd(split: SplitName, boundaries: ParsedManifestBoundaries): number {
  if (split === 'train') return boundaries.trainEnd;
  if (split === 'validation') return boundaries.validationEnd;
  return boundaries.end;
}

function scanCycleDecisionMs(row: PumpResearchRecord, samplingIntervalSeconds: number): number | undefined {
  const started = canonicalTimestamp(row.scanCycleStartedAt);
  return started === undefined ? undefined : started + samplingIntervalSeconds * 1_000;
}

export function assessPumpResearchDataset(
  manifestValue: unknown,
  recordValues: readonly unknown[],
): PumpSuitabilityReport {
  const blockers: string[] = [];
  const warnings: string[] = [];
  const manifest = isObject(manifestValue) ? manifestValue : undefined;
  const records = recordValues.map(normalizeRecord).filter((row): row is PumpResearchRecord => row !== undefined);
  let boundaries: ParsedManifestBoundaries | undefined;
  let samplingIntervalSeconds: number | undefined;

  if (!manifest) {
    blockers.push('invalid_manifest');
  } else {
    if (manifest.schemaVersion !== 2) blockers.push('unsupported_manifest_schema');
    if (manifest.sourceContract !== PUMP_RESEARCH_SOURCE_CONTRACT) blockers.push('unsupported_source_contract');
    if (!isBoundedText(manifest.provenanceId, 256)) blockers.push('invalid_provenance_id');
    if (typeof manifest.parserGitSha !== 'string' || !/^[0-9a-f]{40}$/.test(manifest.parserGitSha)) blockers.push('invalid_parser_git_sha');
    if (typeof manifest.exportQuerySha256 !== 'string' || !/^[0-9a-f]{64}$/.test(manifest.exportQuerySha256)) blockers.push('invalid_export_query_hash');
    if (typeof manifest.rowsSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(manifest.rowsSha256)) blockers.push('invalid_rows_hash');
    else {
      try {
        if (manifest.rowsSha256 !== hashPumpRecords(recordValues)) blockers.push('rows_hash_mismatch');
      } catch {
        blockers.push('invalid_record_hash_content');
      }
    }
    if (canonicalTimestamp(manifest.generatedAt) === undefined) blockers.push('invalid_generated_at');
    const provenance = provenanceClass(manifest);
    if (provenance === undefined) blockers.push('unreviewed_provenance');
    else if (provenance === 'synthetic_test_only') blockers.push('synthetic_provenance_not_evidence');

    const capabilities = isObject(manifest.capabilities) ? manifest.capabilities : {};
    for (const [capability, blocker] of Object.entries(REQUIRED_CAPABILITIES)) {
      if (capabilities[capability] !== true) blockers.push(blocker);
    }
    if (LIVE_GATE_CAPABILITIES.some((capability) => capabilities[capability] !== true)) blockers.push('missing_live_gate_snapshots');

    boundaries = parseManifestBoundaries(manifest);
    if (!boundaries) blockers.push('invalid_chronological_boundaries');
    if (!Number.isSafeInteger(manifest.samplingIntervalSeconds)
      || (manifest.samplingIntervalSeconds as number) < 1
      || (manifest.samplingIntervalSeconds as number) > 86_400) {
      blockers.push('invalid_sampling_interval');
    } else {
      samplingIntervalSeconds = manifest.samplingIntervalSeconds as number;
    }
  }

  if (records.length === 0) blockers.push('empty_dataset');
  if (records.length !== recordValues.length) blockers.push('invalid_record');

  const seenSamples = new Set<string>();
  const seenEvents = new Set<string>();
  const seenCycleMints = new Set<string>();
  const signatureByTransaction = new Map<string, string>();
  const transactionBySignature = new Map<string, string>();
  const launchByMint = new Map<string, number>();
  const curveByMint = new Map<string, string>();
  const mintByCurve = new Map<string, string>();
  const cycleStartById = new Map<string, number>();
  const distinctCycleStarts = new Set<number>();
  const mints = new Set<string>();
  const eligibleMints: Record<SplitName, Set<string>> = { train: new Set(), validation: new Set(), test: new Set() };

  for (const row of records) {
    if (!isBoundedText(row.sampleId)) blockers.push('invalid_sample_id');
    else if (seenSamples.has(row.sampleId)) blockers.push('duplicate_sample_id');
    else seenSamples.add(row.sampleId);

    const mintValid = isCanonicalPublicKey(row.mint);
    if (!mintValid) blockers.push('invalid_mint');
    else mints.add(row.mint);
    if (row.programId !== PUMP_PROGRAM_ID) blockers.push('non_pump_record');
    if (row.source !== 'old_faithful_pump_snapshot_v2') blockers.push('invalid_research_source');
    if (!isCanonicalPublicKey(row.pairId) || row.pairId === row.mint) blockers.push('noncanonical_pair_id');
    if (!isBoundedText(row.symbol, 256)) blockers.push('invalid_symbol');
    if (!isCanonicalSignature(row.signature)) blockers.push('invalid_signature');
    if (!Number.isSafeInteger(row.slot) || row.slot < 0
      || !Number.isSafeInteger(row.transactionIndex) || row.transactionIndex < 0
      || !Number.isSafeInteger(row.instructionIndex) || row.instructionIndex < 0) {
      blockers.push('invalid_transaction_order');
    }
    const locationValid = row.instructionLocation === 'top_level' || row.instructionLocation === 'inner';
    if (!locationValid
      || (row.instructionLocation === 'inner' && (!Number.isSafeInteger(row.parentInstructionIndex) || (row.parentInstructionIndex as number) < 0))
      || (row.instructionLocation === 'top_level' && row.parentInstructionIndex !== undefined)) {
      blockers.push('invalid_instruction_location');
    }

    if (isCanonicalSignature(row.signature)
      && Number.isSafeInteger(row.slot) && row.slot >= 0
      && Number.isSafeInteger(row.transactionIndex) && row.transactionIndex >= 0
      && Number.isSafeInteger(row.instructionIndex) && row.instructionIndex >= 0
      && locationValid) {
      const transaction = `${row.slot}:${row.transactionIndex}`;
      const priorSignature = signatureByTransaction.get(transaction);
      if (priorSignature !== undefined && priorSignature !== row.signature) blockers.push('inconsistent_transaction_signature');
      else signatureByTransaction.set(transaction, row.signature);
      const priorTransaction = transactionBySignature.get(row.signature);
      if (priorTransaction !== undefined && priorTransaction !== transaction) blockers.push('inconsistent_signature_coordinates');
      else transactionBySignature.set(row.signature, transaction);
      const parent = row.instructionLocation === 'inner' ? row.parentInstructionIndex : '-';
      const event = `${transaction}:${row.instructionLocation}:${parent}:${row.instructionIndex}`;
      if (seenEvents.has(event)) blockers.push('duplicate_transaction_event');
      else seenEvents.add(event);
    }

    const observed = canonicalTimestamp(row.observedAt);
    const launched = canonicalTimestamp(row.pairCreatedAt);
    const cycleStarted = canonicalTimestamp(row.scanCycleStartedAt);
    if (observed === undefined || launched === undefined || launched > observed) blockers.push('invalid_record_timestamp');
    if (!isBoundedText(row.scanCycleId, 256) || cycleStarted === undefined
      || row.scanCycleId !== row.scanCycleStartedAt
      || observed === undefined || samplingIntervalSeconds === undefined
      || observed < cycleStarted || observed >= cycleStarted + samplingIntervalSeconds * 1_000) {
      blockers.push('invalid_scan_cycle');
    } else {
      distinctCycleStarts.add(cycleStarted);
      if (boundaries && (cycleStarted - boundaries.start) % (samplingIntervalSeconds * 1_000) !== 0) {
        blockers.push('misaligned_scan_cycle');
      }
      const priorCycleStart = cycleStartById.get(row.scanCycleId);
      if (priorCycleStart !== undefined && priorCycleStart !== cycleStarted) blockers.push('inconsistent_scan_cycle');
      else cycleStartById.set(row.scanCycleId, cycleStarted);
      if (mintValid) {
        const cycleMint = `${row.scanCycleId}:${row.mint}`;
        if (seenCycleMints.has(cycleMint)) blockers.push('duplicate_cycle_mint_snapshot');
        else seenCycleMints.add(cycleMint);
      }
    }

    if (boundaries && (observed === undefined || launched === undefined
      || launched < boundaries.start || observed < boundaries.start
      || launched >= boundaries.end || observed >= boundaries.end)) blockers.push('record_outside_manifest_window');

    if (mintValid && launched !== undefined) {
      const knownLaunch = launchByMint.get(row.mint);
      if (knownLaunch !== undefined && knownLaunch !== launched) blockers.push('inconsistent_launch_time');
      else launchByMint.set(row.mint, launched);
      const knownCurve = curveByMint.get(row.mint);
      if (knownCurve !== undefined && knownCurve !== row.pairId) blockers.push('inconsistent_mint_curve');
      else curveByMint.set(row.mint, row.pairId);
      if (isCanonicalPublicKey(row.pairId)) {
        const knownMint = mintByCurve.get(row.pairId);
        if (knownMint !== undefined && knownMint !== row.mint) blockers.push('inconsistent_curve_mint');
        else mintByCurve.set(row.pairId, row.mint);
      }
    }

    if (!isValidPumpIdentity(row.marketIdentity, row)) blockers.push('invalid_pump_market_identity');

    if (!finiteNumberIn(row.priceUsd, Number.MIN_VALUE, MAX_PRICE_USD)
      || !finiteNumberIn(row.solPriceUsd, Number.MIN_VALUE, MAX_SOL_PRICE_USD)
      || !finiteNumberIn(row.liquidityUsd, 0, MAX_USD_SNAPSHOT)
      || !finiteNumberIn(row.volumeM5Usd, 0, MAX_USD_SNAPSHOT)
      || !finiteNumberIn(row.buysM5, 0, MAX_EVENT_COUNT) || !Number.isInteger(row.buysM5)
      || !finiteNumberIn(row.sellsM5, 0, MAX_EVENT_COUNT) || !Number.isInteger(row.sellsM5)
      || !finiteNumberIn(row.priceChangeM5Percent, -MAX_ABS_PERCENT, MAX_ABS_PERCENT)) {
      blockers.push('invalid_market_snapshot');
      if ([row.priceUsd, row.solPriceUsd, row.liquidityUsd, row.volumeM5Usd, row.buysM5, row.sellsM5, row.priceChangeM5Percent]
        .some((number) => typeof number === 'number' && Number.isFinite(number) && Math.abs(number) > MAX_USD_SNAPSHOT)) {
        blockers.push('numeric_out_of_range');
      }
    }
    if (!finiteNumberIn(row.solPriceUsd, Number.MIN_VALUE, MAX_SOL_PRICE_USD)) blockers.push('missing_historical_sol_usd');

    if (!Object.prototype.hasOwnProperty.call(row, 'whaleInterestAt')) blockers.push('missing_whale_flow_snapshot');
    else if (row.whaleInterestAt !== null) {
      const whaleAt = canonicalTimestamp(row.whaleInterestAt);
      if (whaleAt === undefined || observed === undefined || whaleAt > observed) blockers.push('invalid_whale_flow_snapshot');
    }
    if (!isObject(row.rugRisk)
      || !['clean', 'moderate', 'high'].includes(String(row.rugRisk.className))
      || !finiteNumberIn(row.rugRisk.score, 0, 100)
      || !Array.isArray(row.rugRisk.flags)
      || row.rugRisk.flags.length > 1_000
      || row.rugRisk.flags.some((flag) => !isBoundedText(flag, 512))) blockers.push('invalid_rug_snapshot');
    if (Object.prototype.hasOwnProperty.call(row, 'poolDepth') && row.poolDepth !== undefined) blockers.push('unsupported_pool_depth_snapshot');

    if (boundaries && launched !== undefined) {
      const cohort = classifyLaunch(launched, boundaries);
      if (cohort === 'embargo') blockers.push('record_in_split_embargo');
      else if (cohort && observed !== undefined) {
        const observationEnd = splitObservationEnd(cohort, boundaries);
        const decision = samplingIntervalSeconds === undefined ? undefined : scanCycleDecisionMs(row, samplingIntervalSeconds);
        if (decision !== undefined && decision < observationEnd) eligibleMints[cohort].add(row.mint);
        else warnings.push('observations_right_censored_at_split_boundary');
      }
    }
  }

  if (samplingIntervalSeconds !== undefined) {
    const starts = [...distinctCycleStarts].sort((a, b) => a - b);
    if (starts.some((start, index) => index > 0 && start < starts[index - 1] + samplingIntervalSeconds * 1_000)) {
      blockers.push('overlapping_scan_cycles');
    }
  }

  if (records.length > 0 && boundaries) {
    if (eligibleMints.train.size === 0) blockers.push('empty_train_split');
    if (eligibleMints.validation.size === 0) blockers.push('empty_validation_split');
    if (eligibleMints.test.size === 0) blockers.push('empty_test_split');
  }

  return {
    ready: blockers.length === 0,
    blockers: uniqueSorted(blockers),
    warnings: uniqueSorted(warnings),
    recordCount: records.length,
    mintCount: mints.size,
  };
}

function requireManifest(value: unknown): PumpResearchManifest {
  if (!isObject(value)) throw new Error('invalid Pump research manifest');
  return value as PumpResearchManifest;
}

function requireRecords(values: readonly unknown[]): PumpResearchRecord[] {
  const records = values.map(normalizeRecord);
  if (records.some((row) => row === undefined)) throw new Error('invalid Pump research record');
  return records as PumpResearchRecord[];
}

function compareRecords(a: PumpResearchRecord, b: PumpResearchRecord): number {
  return (canonicalTimestamp(a.observedAt) ?? Number.MAX_SAFE_INTEGER) - (canonicalTimestamp(b.observedAt) ?? Number.MAX_SAFE_INTEGER)
    || a.slot - b.slot
    || a.transactionIndex - b.transactionIndex
    || a.instructionIndex - b.instructionIndex
    || compareText(a.sampleId, b.sampleId);
}

function splitPumpRecords(recordValues: readonly unknown[], manifestValue: unknown): PumpResearchSplit {
  const manifest = requireManifest(manifestValue);
  const boundaries = parseManifestBoundaries(manifest as unknown as JsonObject);
  if (!boundaries) throw new Error('invalid Pump research split boundaries');
  if (!Number.isSafeInteger(manifest.samplingIntervalSeconds) || manifest.samplingIntervalSeconds <= 0) {
    throw new Error('invalid Pump research sampling interval');
  }
  const records = requireRecords(recordValues);
  const result: PumpResearchSplit = { train: [], validation: [], test: [] };
  for (const row of records) {
    const launched = canonicalTimestamp(row.pairCreatedAt);
    const observed = canonicalTimestamp(row.observedAt);
    if (launched === undefined || observed === undefined) continue;
    const cohort = classifyLaunch(launched, boundaries);
    if (cohort === 'train' || cohort === 'validation' || cohort === 'test') {
      const decision = scanCycleDecisionMs(row, manifest.samplingIntervalSeconds);
      if (decision !== undefined && decision < splitObservationEnd(cohort, boundaries)) result[cohort].push(row);
    }
  }
  result.train.sort(compareRecords);
  result.validation.sort(compareRecords);
  result.test.sort(compareRecords);
  return result;
}

function snapshotFromRecord(row: PumpResearchRecord): MarketSnapshot {
  return {
    pairId: row.pairId,
    mint: row.mint,
    symbol: row.symbol,
    source: row.source,
    firstSeenAt: row.firstSeenAt,
    observedAt: row.observedAt,
    pairCreatedAt: row.pairCreatedAt,
    priceUsd: row.priceUsd,
    liquidityUsd: row.liquidityUsd,
    volumeM5Usd: row.volumeM5Usd,
    priceChangeM5Percent: row.priceChangeM5Percent,
    buysM5: row.buysM5,
    sellsM5: row.sellsM5,
    whaleInterestAt: row.whaleInterestAt ?? undefined,
    rugRisk: row.rugRisk,
    marketIdentity: row.marketIdentity,
  };
}

function assertFiniteTree(value: unknown, path = 'report'): void {
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error(`non-finite research output at ${path}`);
    if (/lamports$/i.test(path) && !Number.isSafeInteger(value)) {
      throw new Error(`safe integer lamport value required at ${path}`);
    }
  }
  if (Array.isArray(value)) value.forEach((entry, index) => assertFiniteTree(entry, `${path}[${index}]`));
  else if (isObject(value)) for (const [key, entry] of Object.entries(value)) assertFiniteTree(entry, `${path}.${key}`);
}

function simulatePumpSplit(
  recordValues: readonly unknown[],
  configValue: PaperConfig,
  split: SplitName,
): PumpSplitResult {
  const config = decodePumpResearchConfig(configValue);
  const records = requireRecords(recordValues).sort(compareRecords);
  if (records.some((row) => Object.prototype.hasOwnProperty.call(row, 'poolDepth') && row.poolDepth !== undefined)) {
    throw new Error('pool depth is unsupported by Pump historical research until production decimal math is corrected');
  }
  const createdAt = records[0]?.observedAt ?? new Date(0).toISOString();
  let portfolio = createPortfolio(config, createdAt);
  assertFiniteTree(portfolio, 'portfolio');
  const entryByTradeId = new Map<string, PumpResearchRecord>();
  const completedMints = new Set<string>();
  const latestByMint = new Map<string, PumpResearchRecord>();
  const closedTrades: ClosedResearchTrade[] = [];

  const groups = new Map<string, PumpResearchRecord[]>();
  for (const row of records) {
    const group = groups.get(row.scanCycleId) ?? [];
    group.push(row);
    groups.set(row.scanCycleId, group);
  }
  const orderedGroups = [...groups.entries()].sort(([, a], [, b]) => compareText(a[0].scanCycleStartedAt, b[0].scanCycleStartedAt));
  for (let index = 1; index < orderedGroups.length; index += 1) {
    const previous = canonicalTimestamp(orderedGroups[index - 1][1][0].scanCycleStartedAt);
    const current = canonicalTimestamp(orderedGroups[index][1][0].scanCycleStartedAt);
    if (previous === undefined || current === undefined
      || current < previous + config.scanIntervalSeconds * 1_000) {
      throw new Error('overlapping scan cycles are forbidden');
    }
  }

  for (const [, group] of orderedGroups) {
    group.sort(compareRecords);
    const cycleStartedMs = canonicalTimestamp(group[0].scanCycleStartedAt);
    if (cycleStartedMs === undefined) throw new Error('invalid scan cycle timestamp');
    const atMs = cycleStartedMs + config.scanIntervalSeconds * 1_000;
    const at = new Date(atMs).toISOString();

    for (const row of group) {
      const position = portfolio.positions.find((candidate) => candidate.mint === row.mint && candidate.pairId === row.pairId);
      if (!position) continue;
      const openedMs = canonicalTimestamp(position.openedAt);
      if (openedMs === undefined) throw new Error('invalid position timestamp');
      const latestAllowedMark = openedMs + config.maxHoldMinutes * 60_000 + config.scanIntervalSeconds * 1_000;
      if (atMs > latestAllowedMark) continue;
      if (!isFreshPositionQuote({ priceUsd: row.priceUsd, observedAt: row.observedAt }, new Date(at))) continue;
      latestByMint.set(row.mint, row);
      const currentSolPerToken = row.priceUsd / row.solPriceUsd;
      if (!Number.isFinite(currentSolPerToken) || currentSolPerToken <= 0) throw new Error('invalid SOL-normalized mark price');
      const evaluated = evaluateOpenPosition(
        portfolio,
        { pairId: row.pairId, mint: row.mint },
        currentSolPerToken,
        at,
        { ...config, solPriceUsd: row.solPriceUsd },
      );
      assertFiniteTree(evaluated, 'positionEvaluation');
      portfolio = evaluated.portfolio;
      if (evaluated.event.type === 'exit') {
        const entry = entryByTradeId.get(position.tradeId);
        if (!entry) throw new Error(`missing entry provenance for ${position.tradeId}`);
        closedTrades.push({
          tradeId: position.tradeId,
          mint: position.mint,
          entryAt: position.openedAt,
          exitAt: at,
          allocatedLamports: position.allocatedLamports,
          entryPriceObservedUsd: entry.priceUsd,
          entrySolUsd: entry.solPriceUsd,
          entryPriceExecutableSolPerToken: position.entryPriceUsd,
          exitPriceObservedUsd: row.priceUsd,
          exitSolUsd: row.solPriceUsd,
          exitReason: evaluated.event.reason,
          pnlLamports: evaluated.event.pnlLamports,
        });
        entryByTradeId.delete(position.tradeId);
        completedMints.add(position.mint);
      }
    }

    const candidates = group
      .filter((row) => !completedMints.has(row.mint)
        && !portfolio.positions.some((position) => position.mint === row.mint))
      .map((row) => {
        const snapshot = snapshotFromRecord(row);
        const gate = evaluateMarketGate(snapshot, config, new Date(at));
        const score = config.entryMode === 'contra'
          ? scoreContraMomentum(snapshot, config, new Date(at))
          : scoreMomentum(snapshot, config, new Date(at));
        return { row, gate, score };
      })
      .filter((candidate) => candidate.gate.accepted && candidate.score >= config.minMomentumScore)
      .sort((a, b) => b.score - a.score || compareText(a.row.sampleId, b.row.sampleId))
      .slice(0, config.maxEntriesPerScan);

    for (const candidate of candidates) {
      const row = candidate.row;
      const entrySolPerToken = row.priceUsd / row.solPriceUsd;
      if (!Number.isFinite(entrySolPerToken) || entrySolPerToken <= 0) throw new Error('invalid SOL-normalized entry price');
      const entered = enterPaperPosition(portfolio, {
        pairId: row.pairId,
        mint: row.mint,
        symbol: row.symbol,
        priceUsd: entrySolPerToken,
        at,
        score: candidate.score,
        liquidityUsd: row.liquidityUsd,
        priceChangeM5Percent: row.priceChangeM5Percent,
        solPriceUsd: row.solPriceUsd,
      }, { ...config, solPriceUsd: row.solPriceUsd });
      if (!entered.ok) continue;
      assertFiniteTree(entered, 'positionEntry');
      portfolio = entered.portfolio;
      entryByTradeId.set(entered.position.tradeId, row);
      latestByMint.set(row.mint, row);
    }
  }

  const censoredOpenPositions = portfolio.positions
    .map((position): CensoredResearchPosition => {
      const entry = entryByTradeId.get(position.tradeId);
      const latest = latestByMint.get(position.mint) ?? entry;
      if (!entry || !latest) throw new Error(`missing observation provenance for ${position.tradeId}`);
      return {
        tradeId: position.tradeId,
        mint: position.mint,
        openedAt: position.openedAt,
        allocatedLamports: position.allocatedLamports,
        entryPriceObservedUsd: entry.priceUsd,
        entrySolUsd: entry.solPriceUsd,
        entryPriceExecutableSolPerToken: position.entryPriceUsd,
        lastObservedAt: latest.observedAt,
        lastObservedPriceUsd: latest.priceUsd,
      };
    })
    .sort((a, b) => compareText(a.tradeId, b.tradeId));

  const result: PumpSplitResult = {
    split,
    records: records.length,
    mints: new Set(records.map((row) => row.mint)).size,
    closedTrades,
    censoredOpenPositions,
    realizedPnlLamports: closedTrades.reduce((sum, trade) => sum + trade.pnlLamports, 0),
    endingAvailableLamports: portfolio.availableLamports,
  };
  assertFiniteTree(result);
  return result;
}

function addSuitabilityBlockers(report: PumpSuitabilityReport, blockers: string[]): PumpSuitabilityReport {
  const merged = uniqueSorted([...report.blockers, ...blockers]);
  return { ...report, ready: merged.length === 0, blockers: merged };
}

function canonicalSha256(value: unknown): string | null {
  try {
    return createHash('sha256').update(stableResearchJson(value), 'utf8').digest('hex');
  } catch {
    return null;
  }
}

function researchBindings(manifestValue: unknown, recordValues: readonly unknown[], config: PaperConfig): unknown {
  const manifest = isObject(manifestValue) ? manifestValue : undefined;
  return {
    schemaVersion: 1,
    manifestSha256: canonicalSha256(manifestValue),
    recordsSha256: canonicalSha256(recordValues),
    configSha256: canonicalSha256(config),
    provenance: {
      provenanceId: manifest && typeof manifest.provenanceId === 'string' ? manifest.provenanceId : null,
      parserGitSha: manifest && typeof manifest.parserGitSha === 'string' ? manifest.parserGitSha : null,
      exportQuerySha256: manifest && typeof manifest.exportQuerySha256 === 'string' ? manifest.exportQuerySha256 : null,
      class: manifest ? provenanceClass(manifest) ?? 'unreviewed' : 'unreviewed',
      approved: manifest ? provenanceClass(manifest) === 'real' : false,
    },
  };
}

export function runPumpResearch(
  manifestValue: unknown,
  recordValues: readonly unknown[],
  configValue: PaperConfig,
): unknown {
  const config = decodePumpResearchConfig(configValue);
  const bindings = researchBindings(manifestValue, recordValues, config);
  let suitability = assessPumpResearchDataset(manifestValue, recordValues);
  const manifest = isObject(manifestValue) ? manifestValue : undefined;
  const split = manifest && isObject(manifest.split) ? manifest.split : undefined;
  const configBlockers: string[] = [];
  if (!manifest || manifest.samplingIntervalSeconds !== config.scanIntervalSeconds) configBlockers.push('config_sampling_interval_mismatch');
  const minimumEmbargo = config.maxHoldMinutes * 60 + config.scanIntervalSeconds;
  if (!split || !Number.isSafeInteger(split.embargoSeconds) || (split.embargoSeconds as number) < minimumEmbargo) {
    configBlockers.push('embargo_shorter_than_hold_horizon');
  }
  suitability = addSuitabilityBlockers(suitability, configBlockers);
  const provenance = manifest ? provenanceClass(manifest) : undefined;
  const syntheticOnly = provenance === 'synthetic_test_only'
    && suitability.blockers.length === 1
    && suitability.blockers[0] === 'synthetic_provenance_not_evidence';
  if (!suitability.ready && !syntheticOnly) return { schemaVersion: 2, status: 'BLOCKED', bindings, suitability };

  try {
    const records = splitPumpRecords(recordValues, manifestValue);
    const report = {
      schemaVersion: 2,
      status: provenance === 'real' ? 'READY' : 'SYNTHETIC_TEST_ONLY',
      bindings,
      suitability,
      results: {
        train: simulatePumpSplit(records.train, config, 'train'),
        validation: simulatePumpSplit(records.validation, config, 'validation'),
        test: simulatePumpSplit(records.test, config, 'test'),
      },
    };
    assertFiniteTree(report);
    return report;
  } catch {
    return {
      schemaVersion: 2,
      status: 'BLOCKED',
      bindings,
      suitability: addSuitabilityBlockers(suitability, ['non_finite_or_invalid_simulation_output']),
    };
  }
}
