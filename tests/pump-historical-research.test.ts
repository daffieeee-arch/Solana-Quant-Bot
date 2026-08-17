import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Keypair } from '@solana/web3.js';
import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { deriveBondingCurve } from '../src/pump-parser.js';
import { decodePumpResearchConfig } from '../src/research/pump-historical-config.js';
import * as pumpHistorical from '../src/research/pump-historical.js';
import {
  PUMP_PROGRAM_ID,
  assessPumpResearchDataset,
  hashPumpRecords,
  runPumpResearch,
  stableResearchJson,
} from '../src/research/pump-historical.js';

const BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const encodeBase58 = (bytes: Uint8Array): string => {
  let leadingZeros = 0;
  while (leadingZeros < bytes.length && bytes[leadingZeros] === 0) leadingZeros += 1;
  const hex = Buffer.from(bytes).toString('hex');
  let value = hex.length === 0 ? 0n : BigInt(`0x${hex}`);
  let encoded = '';
  while (value > 0n) {
    encoded = BASE58[Number(value % 58n)] + encoded;
    value /= 58n;
  }
  return '1'.repeat(leadingZeros) + encoded;
};
const mintFor = (label: string): string => Keypair.fromSeed(
  createHash('sha256').update(`mint:${label}`).digest(),
).publicKey.toBase58();
const signatureFor = (label: string): string => encodeBase58(
  createHash('sha512').update(`signature:${label}`).digest(),
);
const scanStart = (observedAt: string): string => {
  const ms = Date.parse(observedAt);
  return new Date(Math.floor(ms / 30_000) * 30_000).toISOString();
};

const allCapabilities = {
  structuralPumpIdentity: true,
  nativeSolBalanceDeltas: true,
  innerInstructions: true,
  loadedAddresses: true,
  canonicalLaunchTimestamp: true,
  transactionOrder: true,
  exactPriceUnits: true,
  causalFeatureWindows: true,
  scanCycleIdentity: true,
  boundedMarkCadence: true,
  historicalSolUsd: true,
  liquiditySnapshots: true,
  rugRiskSnapshots: true,
  whaleFlowSnapshots: true,
} as const;

const baseManifest = {
  schemaVersion: 2 as const,
  sourceContract: 'PUMP_SNAPSHOT_V2' as const,
  provenanceId: 'unreviewed-test-fixture',
  parserGitSha: 'a'.repeat(40),
  exportQuerySha256: 'b'.repeat(64),
  rowsSha256: '',
  generatedAt: '2026-08-16T20:00:00.000Z',
  window: {
    startInclusive: '2026-01-01T00:00:00.000Z',
    endExclusive: '2026-01-11T00:00:00.000Z',
  },
  split: {
    trainEndExclusive: '2026-01-04T00:00:00.000Z',
    validationEndExclusive: '2026-01-07T00:00:00.000Z',
    embargoSeconds: 630,
  },
  samplingIntervalSeconds: 30,
  capabilities: allCapabilities,
};

function record(
  label: string,
  observedAt: string,
  pairCreatedAt: string,
  priceUsd = 1,
  overrides: Record<string, unknown> = {},
) {
  const mint = mintFor(label);
  const pairId = deriveBondingCurve(mint);
  const cycleStartedAt = scanStart(observedAt);
  const eventLabel = `${label}:${observedAt}`;
  return {
    sampleId: eventLabel,
    signature: signatureFor(eventLabel),
    slot: Math.floor(Date.parse(observedAt) / 1_000),
    transactionIndex: createHash('sha256').update(label).digest().readUInt16BE(0),
    instructionLocation: 'top_level' as const,
    instructionIndex: 0,
    scanCycleId: cycleStartedAt,
    scanCycleStartedAt: cycleStartedAt,
    programId: PUMP_PROGRAM_ID,
    pairId,
    mint,
    symbol: label,
    source: 'old_faithful_pump_snapshot_v2',
    observedAt,
    pairCreatedAt,
    priceUsd,
    solPriceUsd: 100,
    liquidityUsd: 100_000,
    volumeM5Usd: 10_000,
    priceChangeM5Percent: -2,
    buysM5: 3,
    sellsM5: 7,
    whaleInterestAt: null,
    rugRisk: { className: 'clean' as const, score: 0, flags: [] },
    marketIdentity: {
      kind: 'pump_bonding_curve' as const,
      tradeId: `research-${eventLabel}`,
      mint,
      protocol: 'pump' as const,
      programId: PUMP_PROGRAM_ID,
      marketId: pairId,
      bondCurve: pairId,
      baseMint: mint,
      quoteMint: 'So11111111111111111111111111111111111111112',
      baseDecimals: 6,
      quoteDecimals: 9,
      sourceTimestamp: observedAt,
      entryPriceSource: 'LOCAL_STATE' as const,
      markPriceSource: 'LOCAL_STATE' as const,
      boundedExitFallback: true,
      strategyVersion: 'pump-snapshot-v2',
      schemaVersion: 2,
    },
    ...overrides,
  };
}

function manifestFor(records: readonly unknown[]) {
  return { ...baseManifest, rowsSha256: hashPumpRecords(records) };
}

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '1', MAX_CONCURRENT_POSITIONS: '2', MAX_ENTRIES_PER_SCAN: '1', MAX_DAILY_LOSS_SOL: '5',
  MIN_LIQUIDITY_USD: '100', MAX_LIQUIDITY_USD: '1000000', MIN_AGE_MINUTES: '1', MAX_AGE_MINUTES: '60',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '1', MIN_MOMENTUM_SCORE: '1',
  MIN_BUY_SURGE_COUNT: '1', MIN_BUY_PRESSURE: '0.1', CONTRA_MAX_BUY_PRESSURE: '0.55', ENTRY_MODE: 'contra', ENTRY_SHADOW_MODE: 'true',
  STOP_LOSS_PERCENT: '12', TAKE_PROFIT_PERCENT: '8', TRAILING_STOP_PERCENT: '5', MAX_HOLD_MINUTES: '10',
  SIMULATED_SLIPPAGE_BPS: '100', SIMULATED_FEE_BPS: '100', SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
  MIN_STOP_LOSS_PERCENT: '5', MAX_STOP_LOSS_PERCENT: '25', STOP_VOLATILITY_MULTIPLIER: '1.5',
  BREAKEVEN_TRIGGER_PERCENT: '8', MIN_PROFIT_FOR_CONTINUE_PERCENT: '3', POSITION_SCORE_DIVISOR: '100',
  LIQUIDITY_POSITION_FRACTION: '1', SOL_PRICE_USD: '100', MIN_WHALE_TX_SOL: '5',
});

const cohortRows = () => [
  record('train', '2026-01-02T00:02:00.000Z', '2026-01-02T00:00:00.000Z'),
  record('validation', '2026-01-05T00:02:00.000Z', '2026-01-05T00:00:00.000Z'),
  record('test', '2026-01-08T00:02:00.000Z', '2026-01-08T00:00:00.000Z'),
];

const structuralBlockers = (rows: readonly unknown[]) => assessPumpResearchDataset(manifestFor(rows), rows).blockers
  .filter((blocker) => blocker !== 'unreviewed_provenance');

function syntheticManifestFor(records: readonly unknown[], researchConfig = config) {
  return {
    ...manifestFor(records),
    provenanceId: 'synthetic-test-fixture',
    samplingIntervalSeconds: researchConfig.scanIntervalSeconds,
    split: {
      ...baseManifest.split,
      embargoSeconds: researchConfig.maxHoldMinutes * 60 + researchConfig.scanIntervalSeconds,
    },
  };
}

function splitFillers(researchConfig = config) {
  const make = (label: string, day: string) => {
    const startedAt = `${day}T00:00:00.000Z`;
    const observedAt = new Date(Date.parse(startedAt) + researchConfig.scanIntervalSeconds * 1_000 - 1).toISOString();
    return record(label, observedAt, startedAt, 1, { scanCycleId: startedAt, scanCycleStartedAt: startedAt });
  };
  return [make('synthetic-validation-filler', '2026-01-05'), make('synthetic-test-filler', '2026-01-08')];
}

function runSyntheticReport(rows: readonly unknown[], researchConfig = config): any {
  return runPumpResearch(syntheticManifestFor(rows, researchConfig), rows, researchConfig);
}

function simulateSyntheticTrain(trainRows: readonly unknown[], researchConfig = config): any {
  const rows = [...trainRows, ...splitFillers(researchConfig)];
  const report = runSyntheticReport(rows, researchConfig);
  expect(report.status).toBe('SYNTHETIC_TEST_ONLY');
  expect(report.bindings.provenance).toMatchObject({ class: 'synthetic_test_only', approved: false });
  return report.results.train;
}

describe('Pump historical integrity and suitability', () => {
  it('blocks transaction-net v1 and every unreviewed v2 provenance tuple', () => {
    const v1 = { ...baseManifest, schemaVersion: 1, sourceContract: 'TRANSACTION_NET_SWAP_V1', rowsSha256: hashPumpRecords([]), capabilities: {} };
    expect(assessPumpResearchDataset(v1, []).blockers).toEqual(expect.arrayContaining([
      'unsupported_source_contract', 'missing_native_sol_balance_deltas', 'empty_dataset', 'unreviewed_provenance',
    ]));
    const rows = cohortRows();
    const report = assessPumpResearchDataset(manifestFor(rows), rows);
    expect(report.ready).toBe(false);
    expect(report.blockers).toEqual(['unreviewed_provenance']);
  });

  it('preserves every own JSON key in canonical hashes, including prototype-like keys', () => {
    const ordinary = JSON.parse('[{"x":1}]');
    for (const key of ['__proto__', 'constructor', 'prototype']) {
      const adversarial = JSON.parse(`[{"x":1,"${key}":{"hidden":true}}]`);
      expect(hashPumpRecords(adversarial)).not.toBe(hashPumpRecords(ordinary));
      expect(stableResearchJson(adversarial)).toContain(`"${key}"`);
    }
  });

  it('requires canonical base58 keys, exact Pump PDA and complete stable identity', () => {
    const rows = cohortRows();
    expect(structuralBlockers(rows)).toEqual([]);
    const synthetic = [{ ...rows[0], mint: 'not-base58', pairId: 'fake-curve', marketIdentity: { kind: 'pump_bonding_curve', marketId: 'fake-curve' } }];
    expect(structuralBlockers(synthetic)).toEqual(expect.arrayContaining(['invalid_mint', 'invalid_pump_market_identity']));

    const wrongCurve = deriveBondingCurve(mintFor('other'));
    const mismatch = [{ ...rows[0], pairId: wrongCurve, marketIdentity: { ...rows[0].marketIdentity, marketId: wrongCurve, bondCurve: wrongCurve } }];
    expect(structuralBlockers(mismatch)).toContain('invalid_pump_market_identity');

    const changed = [rows[0], { ...rows[0], sampleId: 'changed', signature: signatureFor('changed'), slot: rows[0].slot + 1, pairId: wrongCurve, marketIdentity: { ...rows[0].marketIdentity, marketId: wrongCurve, bondCurve: wrongCurve } }];
    expect(structuralBlockers(changed)).toEqual(expect.arrayContaining(['inconsistent_mint_curve', 'invalid_pump_market_identity']));
  });

  it('requires canonical UTC timestamps and deterministic code-unit ordering', () => {
    const offsetless = cohortRows();
    offsetless[0] = record('train', '2026-01-02T00:02:00', '2026-01-02T00:00:00.000Z');
    expect(structuralBlockers(offsetless)).toContain('invalid_record_timestamp');

    expect(stableResearchJson({ ä: 1, z: 2 })).toBe('{\n  "z": 2,\n  "ä": 1\n}\n');
  });

  it('validates event coordinates, scan-cycle identity, snapshots and numeric bounds without throwing', () => {
    const rows = cohortRows();
    const duplicateCoordinates = [rows[0], { ...rows[0], sampleId: 'other', signature: signatureFor('other') }];
    expect(structuralBlockers(duplicateCoordinates)).toContain('duplicate_transaction_event');

    const malformed = [{
      ...rows[0],
      signature: { toString: 1, valueOf: 1 },
      instructionLocation: 'inner',
      parentInstructionIndex: undefined,
      marketIdentity: { kind: 'pump_bonding_curve', marketId: 7 },
      rugRisk: { className: 'clean', score: -1, flags: 'bad' },
      poolDepth: { baseReserve: 1, quoteReserve: 1, baseDecimals: 6, quoteDecimals: 9, bondingCurve: true },
      priceUsd: 1.79e308,
      buysM5: 1.5,
    }];
    expect(() => assessPumpResearchDataset(manifestFor(malformed), malformed)).not.toThrow();
    expect(structuralBlockers(malformed)).toEqual(expect.arrayContaining([
      'invalid_signature', 'invalid_instruction_location', 'invalid_pump_market_identity',
      'invalid_rug_snapshot', 'unsupported_pool_depth_snapshot', 'numeric_out_of_range', 'invalid_market_snapshot',
    ]));

    const nonFinite = [{ ...rows[0], priceUsd: Number.POSITIVE_INFINITY }];
    const nonFiniteManifest = { ...baseManifest, rowsSha256: 'f'.repeat(64) };
    expect(() => assessPumpResearchDataset(nonFiniteManifest, nonFinite)).not.toThrow();
    expect(assessPumpResearchDataset(nonFiniteManifest, nonFinite).blockers).toContain('invalid_record_hash_content');
    expect(() => stableResearchJson({ result: Number.NaN })).toThrow(/non-finite/);
  });

  it('requires non-empty purged splits and right-censors observations at their boundary', () => {
    const rows = cohortRows();
    rows.push(record('train', '2026-01-08T00:02:00.000Z', '2026-01-02T00:00:00.000Z', 100));
    rows.push(record('validation', '2026-01-09T00:02:00.000Z', '2026-01-05T00:00:00.000Z', 100));
    const report = runSyntheticReport(rows);
    expect(report.status).toBe('SYNTHETIC_TEST_ONLY');
    expect(report.results.train.records).toBe(1);
    expect(report.results.validation.records).toBe(1);

    const embargoMint = record('embargo', '2026-01-03T23:55:00.000Z', '2026-01-03T23:55:00.000Z');
    expect(structuralBlockers([...rows, embargoMint])).toContain('record_in_split_embargo');
    expect(structuralBlockers([rows[0]])).toEqual(expect.arrayContaining(['empty_validation_split', 'empty_test_split']));
  });

  it('treats train, validation, and window end boundaries as strictly exclusive decision cutoffs', () => {
    const boundaryRows = [
      record('train-boundary', '2026-01-03T23:59:59.000Z', '2026-01-02T00:00:00.000Z', 1, {
        scanCycleId: '2026-01-03T23:59:30.000Z', scanCycleStartedAt: '2026-01-03T23:59:30.000Z',
      }),
      record('validation-boundary', '2026-01-06T23:59:59.000Z', '2026-01-05T00:00:00.000Z', 1, {
        scanCycleId: '2026-01-06T23:59:30.000Z', scanCycleStartedAt: '2026-01-06T23:59:30.000Z',
      }),
      record('test-boundary', '2026-01-10T23:59:59.000Z', '2026-01-08T00:00:00.000Z', 1, {
        scanCycleId: '2026-01-10T23:59:30.000Z', scanCycleStartedAt: '2026-01-10T23:59:30.000Z',
      }),
    ];
    const report = assessPumpResearchDataset(manifestFor(boundaryRows), boundaryRows);
    expect(report.warnings).toContain('observations_right_censored_at_split_boundary');
    expect(report.blockers).toEqual(expect.arrayContaining(['empty_train_split', 'empty_validation_split', 'empty_test_split']));
  });

  it('rejects globally overlapping or cadence-misaligned scan cycles', () => {
    const launch = '2026-01-02T00:00:00.000Z';
    const overlapping = [
      record('cycle-a', '2026-01-02T00:02:29.000Z', launch, 1, {
        scanCycleId: '2026-01-02T00:02:00.000Z', scanCycleStartedAt: '2026-01-02T00:02:00.000Z',
      }),
      record('cycle-b', '2026-01-02T00:02:29.000Z', launch, 1, {
        scanCycleId: '2026-01-02T00:02:01.000Z', scanCycleStartedAt: '2026-01-02T00:02:01.000Z',
      }),
    ];
    expect(structuralBlockers(overlapping)).toEqual(expect.arrayContaining([
      'misaligned_scan_cycle', 'overlapping_scan_cycles',
    ]));
  });
});

describe('Pump historical bounded simulation', () => {
  it('does not export raw split/simulation bypasses and blocks invalid public-path inputs', () => {
    expect('simulatePumpSplit' in pumpHistorical).toBe(false);
    expect('splitPumpRecords' in pumpHistorical).toBe(false);
    const valid = record('direct-invalid', '2026-01-02T00:02:00.000Z', '2026-01-02T00:00:00.000Z');
    const fabricated = {
      ...valid,
      programId: 'fake-program',
      source: 'fabricated-source',
      pairId: `gx:${valid.mint}`,
      marketIdentity: { kind: 'pump_bonding_curve', marketId: `gx:${valid.mint}` },
    };
    expect(runPumpResearch(manifestFor([fabricated]), [fabricated], config)).toMatchObject({ status: 'BLOCKED' });
    const nonCanonical = { ...valid, observedAt: '2026-01-02T00:02:00' };
    expect(runPumpResearch(manifestFor([nonCanonical]), [nonCanonical], config)).toMatchObject({ status: 'BLOCKED' });
    const concealedOverlap = [
      valid,
      record('direct-overlap', '2026-01-02T00:02:29.000Z', '2026-01-02T00:00:00.000Z', 1, {
        scanCycleId: valid.scanCycleId,
        scanCycleStartedAt: '2026-01-02T00:02:01.000Z',
      }),
    ];
    expect(runPumpResearch(manifestFor(concealedOverlap), concealedOverlap, config)).toMatchObject({ status: 'BLOCKED' });
  });

  it('groups candidates by provenance scan cycle and enforces Top-N once per cycle', () => {
    const launch = '2026-01-02T00:00:00.000Z';
    const rows = [
      record('candidate-a', '2026-01-02T00:02:00.000Z', launch),
      record('candidate-b', '2026-01-02T00:02:01.000Z', launch, 1, {
        scanCycleId: '2026-01-02T00:02:00.000Z', scanCycleStartedAt: '2026-01-02T00:02:00.000Z',
      }),
    ];
    const result = simulateSyntheticTrain(rows, config);
    expect(result.censoredOpenPositions).toHaveLength(1);
  });

  it('exits at an on-cadence max-hold mark but censors an arbitrarily late mark', () => {
    const launch = '2026-01-02T00:00:00.000Z';
    const onCadence = simulateSyntheticTrain([
      record('on-time', '2026-01-02T00:02:00.000Z', launch, 1),
      record('on-time', '2026-01-02T00:12:00.000Z', launch, 1.05),
      record('on-time', '2026-01-02T01:00:00.000Z', launch, 100),
    ], config);
    expect(onCadence.closedTrades).toHaveLength(1);
    expect(onCadence.closedTrades[0].exitPriceObservedUsd).toBe(1.05);

    const late = simulateSyntheticTrain([
      record('late', '2026-01-02T00:02:00.000Z', launch, 1),
      record('late', '2026-01-02T01:00:00.000Z', launch, 100),
    ], config);
    expect(late.closedTrades).toEqual([]);
    expect(late.censoredOpenPositions).toHaveLength(1);
    expect(late.realizedPnlLamports).toBe(0);
  });

  it('normalizes token USD prices by each observation SOL/USD before lamport PnL', () => {
    const launch = '2026-01-02T00:00:00.000Z';
    const stableSol = simulateSyntheticTrain([
      record('stable-sol', '2026-01-02T00:02:00.000Z', launch, 1, { solPriceUsd: 100 }),
      record('stable-sol', '2026-01-02T00:12:00.000Z', launch, 1.1, { solPriceUsd: 100 }),
    ], config);
    const doubledSol = simulateSyntheticTrain([
      record('double-sol', '2026-01-02T00:02:00.000Z', launch, 1, { solPriceUsd: 100 }),
      record('double-sol', '2026-01-02T00:12:00.000Z', launch, 1.1, { solPriceUsd: 200 }),
    ], config);
    expect(stableSol.closedTrades[0].pnlLamports).toBeGreaterThan(0);
    expect(doubledSol.closedTrades[0].pnlLamports).toBeLessThan(0);
  });

  it('rejects pool depth until production 6/9-decimal math is independently corrected', () => {
    const row = record('depth', '2026-01-02T00:02:00.000Z', '2026-01-02T00:00:00.000Z', 1, {
      poolDepth: { baseReserve: 1e12, quoteReserve: 1e11, baseDecimals: 6, quoteDecimals: 9, bondingCurve: true },
    });
    expect(structuralBlockers([row])).toContain('unsupported_pool_depth_snapshot');
    expect(runSyntheticReport([row, ...splitFillers()])).toMatchObject({ status: 'BLOCKED' });
  });

  it('does not evaluate an exit from a mark older than the production two-minute freshness bound', () => {
    const launch = '2026-01-02T00:00:00.000Z';
    const slowConfig = { ...config, scanIntervalSeconds: 300 };
    const result = simulateSyntheticTrain([
      record('stale-mark', '2026-01-02T00:04:59.000Z', launch, 1, {
        scanCycleId: '2026-01-02T00:00:00.000Z', scanCycleStartedAt: '2026-01-02T00:00:00.000Z',
      }),
      record('stale-mark', '2026-01-02T00:10:00.000Z', launch, 20, {
        scanCycleId: '2026-01-02T00:10:00.000Z', scanCycleStartedAt: '2026-01-02T00:10:00.000Z',
      }),
    ], slowConfig);
    expect(result.closedTrades).toEqual([]);
    expect(result.censoredOpenPositions).toHaveLength(1);
  });

  it('fails closed before returning unsafe-integer lamport accounting', () => {
    const launch = '2026-01-02T00:00:00.000Z';
    const largeConfig = {
      ...config,
      paperStartingSol: 1_000_000,
      maxPositionSol: 900_000,
      maxDailyLossSol: 1_000_000,
      maxLiquidityUsd: 1_000_000_000_000_000,
      positionScoreDivisor: 1,
    };
    const rows = [
      record('unsafe-lamports', '2026-01-02T00:02:00.000Z', launch, 1, { liquidityUsd: 1_000_000_000_000_000 }),
      record('unsafe-lamports', '2026-01-02T00:12:00.000Z', launch, 20, { liquidityUsd: 1_000_000_000_000_000 }),
    ];
    const report = runSyntheticReport([...rows, ...splitFillers(largeConfig)], largeConfig);
    expect(report).toMatchObject({
      status: 'BLOCKED',
      suitability: { blockers: expect.arrayContaining(['non_finite_or_invalid_simulation_output']) },
    });
  });
});

describe('Pump research CLI and config boundary', () => {
  it('strictly decodes every research config field and rejects unsafe values', () => {
    expect(decodePumpResearchConfig(config)).toEqual(config);
    for (const mutation of [
      { maxEntriesPerScan: undefined },
      { maxEntriesPerScan: '1' },
      { maxEntriesPerScan: 1.5 },
      { contraMaxBuyPressure: null },
      { entryMode: 'invalid' },
      { entryShadowMode: 'true' },
      { simulatedFeeBps: 10_000 },
      { paperStartingSol: 1e308 },
      { rpcHttpEndpoint: 'https://example.invalid' },
    ]) {
      expect(() => decodePumpResearchConfig({ ...config, ...mutation })).toThrow();
    }
  });

  it('keeps the tracked v1 fixture blocked with deterministic exit code 2', () => {
    const fixtureRoot = join(process.cwd(), 'tests/fixtures/pump-research');
    const result = spawnSync(join(process.cwd(), 'node_modules/.bin/tsx'), [
      'src/research/pump-historical-cli.ts', '--manifest', join(fixtureRoot, 'v1-manifest.json'),
      '--records', join(fixtureRoot, 'v1-records.json'), '--config', join(fixtureRoot, 'paper-config.json'),
    ], { cwd: process.cwd(), encoding: 'utf8' });
    expect(result.status).toBe(2);
    expect(result.stderr).toBe('');
    expect(JSON.parse(result.stdout).status).toBe('BLOCKED');
  });

  it('rejects malformed config with exit 1 and never echoes embedded values', () => {
    const directory = mkdtempSync(join(tmpdir(), 'pump-research-cli-'));
    try {
      const rows = cohortRows();
      const manifestPath = join(directory, 'manifest.json');
      const recordsPath = join(directory, 'records.json');
      const configPath = join(directory, 'config.json');
      writeFileSync(manifestPath, JSON.stringify(manifestFor(rows)));
      writeFileSync(recordsPath, JSON.stringify(rows));
      writeFileSync(configPath, JSON.stringify({ ...config, maxEntriesPerScan: 'SECRET_TEST_VALUE' }));
      const result = spawnSync(join(process.cwd(), 'node_modules/.bin/tsx'), [
        'src/research/pump-historical-cli.ts', '--manifest', manifestPath,
        '--records', recordsPath, '--config', configPath,
      ], { cwd: process.cwd(), encoding: 'utf8' });
      expect(result.status).toBe(1);
      expect(result.stdout).toBe('');
      expect(result.stderr).toContain('pump historical research failed');
      expect(result.stderr).not.toContain('SECRET_TEST_VALUE');
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it('redacts malformed JSON bytes from source CLI stderr', () => {
    const directory = mkdtempSync(join(tmpdir(), 'pump-research-json-redaction-'));
    const canary = 'S3CR3T42';
    try {
      const rows = cohortRows();
      const manifestPath = join(directory, 'manifest.json');
      const recordsPath = join(directory, 'records.json');
      const configPath = join(directory, 'config.json');
      writeFileSync(manifestPath, JSON.stringify(manifestFor(rows)));
      writeFileSync(recordsPath, JSON.stringify(rows));
      writeFileSync(configPath, `{"broken":"${canary}`);
      const result = spawnSync(join(process.cwd(), 'node_modules/.bin/tsx'), [
        'src/research/pump-historical-cli.ts', '--manifest', manifestPath,
        '--records', recordsPath, '--config', configPath,
      ], { cwd: process.cwd(), encoding: 'utf8' });
      expect(result.status).toBe(1);
      expect(result.stderr).toContain('invalid config JSON');
      expect(result.stderr).not.toContain(canary);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it('rejects descriptor growth after fstat instead of reading a stale valid prefix', () => {
    const directory = mkdtempSync(join(tmpdir(), 'pump-research-growth-'));
    try {
      const rows = cohortRows();
      const manifestPath = join(directory, 'manifest.json');
      const recordsPath = join(directory, 'records.json');
      const configPath = join(directory, 'config.json');
      const hookPath = join(directory, 'grow-after-fstat.cjs');
      writeFileSync(manifestPath, JSON.stringify(manifestFor(rows)));
      writeFileSync(recordsPath, JSON.stringify(rows));
      writeFileSync(configPath, JSON.stringify(config));
      writeFileSync(hookPath, `
const fs = require('node:fs');
const { syncBuiltinESMExports } = require('node:module');
const original = fs.fstatSync;
let grown = false;
fs.fstatSync = function(fd, ...args) {
  const result = original.call(this, fd, ...args);
  if (!grown) {
    let actual = '';
    try { actual = fs.realpathSync('/proc/self/fd/' + fd); } catch {}
    if (actual === process.env.GROW_TARGET) {
      grown = true;
      fs.appendFileSync(actual, ' ' + 'X'.repeat(1024 * 1024 + 1));
    }
  }
  return result;
};
syncBuiltinESMExports();
`);
      const result = spawnSync(join(process.cwd(), 'node_modules/.bin/tsx'), [
        'src/research/pump-historical-cli.ts', '--manifest', manifestPath,
        '--records', recordsPath, '--config', configPath,
      ], {
        cwd: process.cwd(), encoding: 'utf8',
        env: { ...process.env, NODE_OPTIONS: `--require=${hookPath}`, GROW_TARGET: manifestPath },
      });
      expect(result.status).toBe(1);
      expect(result.stdout).toBe('');
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it('rejects bounded growth and truncation immediately after the initial fstat', () => {
    for (const mode of ['grow', 'truncate'] as const) {
      const directory = mkdtempSync(join(tmpdir(), `pump-research-${mode}-`));
      try {
        const rows = cohortRows();
        const manifestPath = join(directory, 'manifest.json');
        const recordsPath = join(directory, 'records.json');
        const configPath = join(directory, 'config.json');
        const hookPath = join(directory, 'mutate-after-fstat.cjs');
        writeFileSync(manifestPath, JSON.stringify(manifestFor(rows)));
        writeFileSync(recordsPath, JSON.stringify(rows));
        writeFileSync(configPath, JSON.stringify(config));
        writeFileSync(hookPath, `
const fs = require('node:fs');
const { syncBuiltinESMExports } = require('node:module');
const original = fs.fstatSync;
let changed = false;
fs.fstatSync = function(fd, ...args) {
  const result = original.call(this, fd, ...args);
  if (!changed) {
    let actual = '';
    try { actual = fs.realpathSync('/proc/self/fd/' + fd); } catch {}
    if (actual === process.env.MUTATE_TARGET) {
      changed = true;
      if (process.env.MUTATE_MODE === 'grow') fs.appendFileSync(actual, ' ');
      else fs.writeFileSync(actual, '{}');
    }
  }
  return result;
};
syncBuiltinESMExports();
`);
        const result = spawnSync(join(process.cwd(), 'node_modules/.bin/tsx'), [
          'src/research/pump-historical-cli.ts', '--manifest', manifestPath,
          '--records', recordsPath, '--config', configPath,
        ], {
          cwd: process.cwd(), encoding: 'utf8',
          env: {
            ...process.env,
            NODE_OPTIONS: `--require=${hookPath}`,
            MUTATE_TARGET: manifestPath,
            MUTATE_MODE: mode,
          },
        });
        expect(result.status).toBe(1);
        expect(result.stdout).toBe('');
      } finally {
        rmSync(directory, { recursive: true, force: true });
      }
    }
  });

  it('blocks oversized public keys without reaching base58 decoding in the public CLI path', () => {
    const directory = mkdtempSync(join(tmpdir(), 'pump-research-base58-bound-'));
    try {
      const rows = cohortRows();
      rows[0] = { ...rows[0], mint: '1'.repeat(10_000) };
      const manifestPath = join(directory, 'manifest.json');
      const recordsPath = join(directory, 'records.json');
      const configPath = join(directory, 'config.json');
      const hookPath = join(directory, 'base58-canary.cjs');
      writeFileSync(manifestPath, JSON.stringify(manifestFor(rows)));
      writeFileSync(recordsPath, JSON.stringify(rows));
      writeFileSync(configPath, JSON.stringify(config));
      writeFileSync(hookPath, `
const Module = require('node:module');
const original = Module._load;
Module._load = function guardedLoad(request, ...args) {
  const loaded = original.call(this, request, ...args);
  if (request === 'bs58') {
    const api = loaded.default || loaded;
    const decode = api.decode;
    api.decode = function(value) {
      if (typeof value === 'string' && value.length > 44) process.exit(97);
      return decode.call(this, value);
    };
  }
  return loaded;
};
`);
      const result = spawnSync(join(process.cwd(), 'node_modules/.bin/tsx'), [
        'src/research/pump-historical-cli.ts', '--manifest', manifestPath,
        '--records', recordsPath, '--config', configPath,
      ], {
        cwd: process.cwd(), encoding: 'utf8',
        env: { ...process.env, NODE_OPTIONS: `--require=${hookPath}` },
      });
      expect(result.status).toBe(2);
      expect(result.stderr).toBe('');
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it('keeps the source research graph on the narrow pure PDA primitive', () => {
    const engine = readFileSync(join(process.cwd(), 'src/research/pump-historical.ts'), 'utf8');
    const address = readFileSync(join(process.cwd(), 'src/pump-address.ts'), 'utf8');
    expect(engine).toContain("from '../pump-address.js'");
    expect(engine).not.toContain("from '../pump-parser.js'");
    expect(`${engine}\n${address}`).not.toMatch(/@solana\/web3\.js|node-fetch|rpc-websockets|from ['"](?:node:)?(?:http|https|net|tls)['"]/);
  });

  it('binds every report to canonical manifest, records, config, and claimed provenance hashes', () => {
    const rows = cohortRows();
    const manifest = manifestFor(rows);
    const base = runPumpResearch(manifest, rows, config) as { bindings: Record<string, unknown> };
    const changedManifest = runPumpResearch({ ...manifest, generatedAt: '2026-08-16T20:00:01.000Z' }, rows, config) as { bindings: Record<string, unknown> };
    const changedRows = runPumpResearch(manifest, [{ ...rows[0], symbol: 'changed' }, ...rows.slice(1)], config) as { bindings: Record<string, unknown> };
    const changedConfig = runPumpResearch(manifest, rows, { ...config, maxPositionSol: 0.5 }) as { bindings: Record<string, unknown> };
    expect(base.bindings).toMatchObject({
      provenance: {
        provenanceId: baseManifest.provenanceId,
        parserGitSha: baseManifest.parserGitSha,
        exportQuerySha256: baseManifest.exportQuerySha256,
        approved: false,
      },
    });
    expect(changedManifest.bindings.manifestSha256).not.toBe(base.bindings.manifestSha256);
    expect(changedRows.bindings.recordsSha256).not.toBe(base.bindings.recordsSha256);
    expect(changedConfig.bindings.configSha256).not.toBe(base.bindings.configSha256);
  });

  it('is deterministic and networkless while real v2 provenance remains HOLD', () => {
    const rows = cohortRows();
    const originalFetch = globalThis.fetch;
    let calls = 0;
    globalThis.fetch = (async () => { calls += 1; throw new Error('network forbidden'); }) as typeof fetch;
    try {
      const first = stableResearchJson(runPumpResearch(manifestFor(rows), rows, config));
      const second = stableResearchJson(runPumpResearch(manifestFor(rows), rows, config));
      expect(second).toBe(first);
      expect(JSON.parse(first).status).toBe('BLOCKED');
      expect(JSON.parse(first).suitability.blockers).toContain('unreviewed_provenance');
      expect(calls).toBe(0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
