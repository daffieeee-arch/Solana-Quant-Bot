import {
  buildPhase8AObservability,
  renderPhase8APrometheus,
  type Phase8AObservabilityInput,
} from './phase8a-observability.js';
import type { ResearchDashboardProvider, ResearchPageQuery } from './phase8a-research-contract.js';
import { closeSync, constants, fstatSync, lstatSync, openSync, readSync } from 'node:fs';
import { dirname, isAbsolute, join, parse, resolve } from 'node:path';

export type Phase8AEventRow = Readonly<{
  schemaVersion: 'PHASE8A_EVENT_ROW_1';
  slot: number;
  transactionIndex: number;
  signature: string;
  instructionLocation: 'top_level' | 'inner';
  parentInstructionIndex?: number;
  instructionIndex: number;
  observedDiscriminator: string;
  structuralVariant: string;
  executionStatus: 'succeeded' | 'failed';
  evidenceBadge: 'SHADOW_STRUCTURAL_OBSERVATION' | 'QUARANTINED';
  quarantineReason: string | null;
  rawDetail: Readonly<Record<string, unknown>>;
}>;

export type Phase8AQuarantineRow = Readonly<{
  schemaVersion: 'PHASE8A_QUARANTINE_ROW_1';
  slot: number;
  transactionIndex: number;
  reason: string;
  evidenceBadge: 'QUARANTINED';
}>;

export type Phase8ACockpitSnapshot = Phase8AObservabilityInput & Readonly<{
  runId: string;
  observedAt: string;
  activationVerdict: 'HOLD_UNPROVEN_ACTIVATION';
  eligibility: Phase8AObservabilityInput['eligibility'] & Readonly<{
    pilotEligible: false;
    transportPilot: Readonly<{
      contractReady: true;
      inputMode: 'SYNTHETIC_FIXTURE_ONLY';
      preflightStatus: 'NOT_RUN';
      eligible: false;
      executionAuthorized: false;
    }>;
    acceptedSilver: Readonly<{
      eligible: false;
      activationVerdict: 'HOLD_UNPROVEN_ACTIVATION';
      provenRegistryEntries: 0;
      totalRegistryEntries: 10;
    }>;
    research: Readonly<{
      approved: false;
      researchReady: false;
      strategyInputEligible: false;
      profitabilityEvidence: false;
    }>;
  }>;
  progress: Phase8AObservabilityInput['progress'] & Readonly<{
    currentSlot: number;
    lastCompletedSlot: number;
  }>;
  events: readonly Phase8AEventRow[];
  quarantines: readonly Phase8AQuarantineRow[];
  provenance: Readonly<{
    schemaVersion: 'PHASE8A_PROVENANCE_1';
    sourceManifestSha256: string;
    configSha256: string;
    schemaSha256: string;
    reducerGitSha: string;
    inputSha256: string;
    aggregateOutputSha256: string;
    rerunSha256: string;
    approvalStatus: 'CANDIDATE_UNAPPROVED';
    completeness: string;
    uncertainty: 'UNPROVEN_ACTIVATION';
  }>;
}>;

const SHA256 = /^[0-9a-f]{64}$/;
const GIT_SHA = /^[0-9a-f]{40}$/;

function safeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function exactKeys(value: unknown, expected: readonly string[]): boolean {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...expected].sort());
}

function validEventRow(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const row = value as Record<string, any>;
  const expected = ['schemaVersion', 'slot', 'transactionIndex', 'signature', 'instructionLocation', 'instructionIndex', 'observedDiscriminator', 'structuralVariant', 'executionStatus', 'evidenceBadge', 'quarantineReason', 'rawDetail'];
  if (row.instructionLocation === 'inner') expected.push('parentInstructionIndex');
  return exactKeys(row, expected)
    && row.schemaVersion === 'PHASE8A_EVENT_ROW_1'
    && safeInteger(row.slot) && safeInteger(row.transactionIndex) && safeInteger(row.instructionIndex)
    && (row.instructionLocation === 'top_level' || (row.instructionLocation === 'inner' && safeInteger(row.parentInstructionIndex)))
    && typeof row.signature === 'string' && row.signature.length > 0 && row.signature.length <= 128
    && typeof row.observedDiscriminator === 'string' && /^[0-9a-f]{16}$/.test(row.observedDiscriminator)
    && typeof row.structuralVariant === 'string' && row.structuralVariant.length > 0 && row.structuralVariant.length <= 64
    && (row.executionStatus === 'succeeded' || row.executionStatus === 'failed')
    && (row.evidenceBadge === 'SHADOW_STRUCTURAL_OBSERVATION' || row.evidenceBadge === 'QUARANTINED')
    && (row.quarantineReason === null || (typeof row.quarantineReason === 'string' && row.quarantineReason.length > 0 && row.quarantineReason.length <= 64))
    && exactKeys(row.rawDetail, ['source', 'realData', 'acceptedSilver', 'researchReady', 'strategyStatus', 'activation'])
    && row.rawDetail.source === 'SYNTHETIC' && row.rawDetail.realData === false
    && row.rawDetail.acceptedSilver === false && row.rawDetail.researchReady === false
    && row.rawDetail.strategyStatus === 'NOT_STRATEGY_INPUT'
    && row.rawDetail.activation === 'UNPROVEN_ACTIVATION';
}

function validQuarantineRow(value: unknown): boolean {
  if (!exactKeys(value, ['schemaVersion', 'slot', 'transactionIndex', 'reason', 'evidenceBadge'])) return false;
  const row = value as Record<string, any>;
  return row.schemaVersion === 'PHASE8A_QUARANTINE_ROW_1'
    && safeInteger(row.slot) && safeInteger(row.transactionIndex)
    && typeof row.reason === 'string' && row.reason.length > 0 && row.reason.length <= 64
    && row.evidenceBadge === 'QUARANTINED';
}

function validCounterRecord(value: unknown, keys: readonly string[]): boolean {
  return exactKeys(value, keys) && keys.every((key) => safeInteger((value as Record<string, unknown>)[key]));
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

export function validatePhase8ACockpitSnapshot(value: unknown): string[] {
  const errors: string[] = [];
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return ['snapshot_not_object'];
  const input = value as Record<string, any>;
  if (!exactKeys(input, ['schemaVersion', 'sourceClass', 'runId', 'observedAt', 'evidenceClass', 'realData', 'acceptedSilver', 'researchReady', 'activationVerdict', 'eligibility', 'progress', 'dataflow', 'events', 'quarantines', 'provenance', 'resources'])) errors.push('top_level_schema_mismatch');
  if (input.schemaVersion !== 'PHASE8A_COCKPIT_SNAPSHOT_1') errors.push('schema_mismatch');
  if (input.sourceClass !== 'SYNTHETIC_FIXTURE_ONLY' || input.evidenceClass !== 'SYNTHETIC') errors.push('source_not_synthetic');
  if (input.realData !== false || input.acceptedSilver !== false || input.researchReady !== false) errors.push('approval_escalation');
  if (input.activationVerdict !== 'HOLD_UNPROVEN_ACTIVATION') errors.push('activation_verdict_mismatch');
  const eligibility = input.eligibility;
  if (!eligibility || !exactKeys(eligibility, ['schemaVersion', 'pilotEligible', 'transportPilot', 'acceptedSilver', 'research'])
    || eligibility.schemaVersion !== 'PHASE8A_ELIGIBILITY_STATUS_1'
    || !exactKeys(eligibility.transportPilot, ['contractReady', 'inputMode', 'preflightStatus', 'eligible', 'executionAuthorized', 'reasonCodes'])
    || !exactKeys(eligibility.acceptedSilver, ['eligible', 'activationVerdict', 'provenRegistryEntries', 'totalRegistryEntries', 'reasonCodes'])
    || !exactKeys(eligibility.research, ['approved', 'researchReady', 'strategyInputEligible', 'profitabilityEvidence', 'reasonCodes'])
    || eligibility.pilotEligible !== false
    || eligibility.transportPilot?.contractReady !== true
    || eligibility.transportPilot?.inputMode !== 'SYNTHETIC_FIXTURE_ONLY'
    || eligibility.transportPilot?.preflightStatus !== 'NOT_RUN'
    || eligibility.transportPilot?.eligible !== false
    || eligibility.transportPilot?.executionAuthorized !== false
    || eligibility.acceptedSilver?.eligible !== false
    || eligibility.acceptedSilver?.activationVerdict !== 'HOLD_UNPROVEN_ACTIVATION'
    || eligibility.acceptedSilver?.provenRegistryEntries !== 0
    || eligibility.acceptedSilver?.totalRegistryEntries !== 10
    || eligibility.research?.approved !== false
    || eligibility.research?.researchReady !== false
    || eligibility.research?.strategyInputEligible !== false
    || eligibility.research?.profitabilityEvidence !== false
    || JSON.stringify(eligibility.transportPilot?.reasonCodes) !== JSON.stringify(['SYNTHETIC_FIXTURE_ONLY', 'PREFLIGHT_NOT_RUN', 'EXECUTION_NOT_AUTHORIZED'])
    || JSON.stringify(eligibility.acceptedSilver?.reasonCodes) !== JSON.stringify(['NO_PROVEN_REGISTRY_ENTRIES', 'UNPROVEN_ACTIVATION'])
    || JSON.stringify(eligibility.research?.reasonCodes) !== JSON.stringify(['FIXTURE_ONLY', 'NOT_APPROVED', 'NOT_STRATEGY_INPUT'])) errors.push('eligibility_mismatch');
  if (!Array.isArray(input.events) || input.events.length > 1_000 || !input.events.every(validEventRow)) errors.push('events_invalid');
  if (!Array.isArray(input.quarantines) || input.quarantines.length > 1_000 || !input.quarantines.every(validQuarantineRow)) errors.push('quarantines_invalid');
  if (!exactKeys(input.progress, ['requestedSlots', 'reconciledSlots', 'skippedSlots', 'provisionalSlots', 'resolvedSlots', 'currentSlot', 'lastCompletedSlot', 'coveragePercent', 'deterministicRerun'])
    || !['requestedSlots', 'reconciledSlots', 'skippedSlots', 'provisionalSlots', 'resolvedSlots', 'currentSlot', 'lastCompletedSlot'].every((key) => safeInteger(input.progress[key]))
    || typeof input.progress.coveragePercent !== 'number' || !Number.isFinite(input.progress.coveragePercent)
    || input.progress.coveragePercent < 0 || input.progress.coveragePercent > 100
    || (input.progress.deterministicRerun !== 'MATCH' && input.progress.deterministicRerun !== 'MISMATCH')) errors.push('progress_invalid');
  if (!validCounterRecord(input.dataflow, ['callbacks', 'blocks', 'transactions', 'topLevelInstructions', 'innerInstructions', 'pumpCandidates', 'failedPumpTransactions', 'unknownDiscriminators', 'quarantines', 'exactRetries', 'duplicateConflicts'])) errors.push('dataflow_invalid');
  const resources = input.resources;
  if (!resources || !exactKeys(resources, ['bytesRead', 'bytesWritten', 'outputBytes', 'queueDepth', 'peakRssBytes', 'stageDurationsMs', 'walStatus', 'checkpointStatus', 'quarantineByReason', 'evidenceBasis'])
    || !['bytesRead', 'bytesWritten', 'outputBytes', 'queueDepth', 'peakRssBytes'].every((key) => safeInteger(resources[key]))
    || !validCounterRecord(resources.stageDurationsMs, ['validate', 'reduce', 'publish'])
    || resources.walStatus !== 'CLEAN' || resources.checkpointStatus !== 'PUBLISHED'
    || !validCounterRecord(resources.quarantineByReason, ['UNKNOWN_DISCRIMINATOR', 'FAILED_TRANSACTION'])
    || resources.evidenceBasis !== 'FIXTURE_PINNED_NOT_MEASURED') errors.push('resources_invalid');
  const provenance = input.provenance;
  if (!provenance || !exactKeys(provenance, ['schemaVersion', 'sourceManifestSha256', 'configSha256', 'schemaSha256', 'reducerGitSha', 'inputSha256', 'aggregateOutputSha256', 'rerunSha256', 'approvalStatus', 'completeness', 'uncertainty'])
    || provenance.schemaVersion !== 'PHASE8A_PROVENANCE_1' || !SHA256.test(provenance.sourceManifestSha256 ?? '')
    || !SHA256.test(provenance.configSha256 ?? '') || !SHA256.test(provenance.schemaSha256 ?? '')
    || !SHA256.test(provenance.inputSha256 ?? '') || !SHA256.test(provenance.aggregateOutputSha256 ?? '')
    || !SHA256.test(provenance.rerunSha256 ?? '') || !GIT_SHA.test(provenance.reducerGitSha ?? '')
    || provenance.approvalStatus !== 'CANDIDATE_UNAPPROVED'
    || typeof provenance.completeness !== 'string' || provenance.completeness.length === 0 || provenance.completeness.length > 64
    || provenance.uncertainty !== 'UNPROVEN_ACTIVATION') errors.push('provenance_mismatch');
  return [...new Set(errors)].sort();
}

function page<T>(rows: readonly T[], query: ResearchPageQuery, schemaVersion: string) {
  const selected = rows.slice(query.cursor, query.cursor + query.limit);
  const nextCursor = query.cursor + selected.length < rows.length ? query.cursor + selected.length : null;
  return { schemaVersion, cursor: query.cursor, limit: query.limit, nextCursor, total: rows.length, rows: clone(selected) };
}

export function createInMemoryPhase8AResearchProvider(value: unknown): ResearchDashboardProvider {
  const errors = validatePhase8ACockpitSnapshot(value);
  if (errors.length) throw new Error(`invalid_phase8a_snapshot:${errors.join(',')}`);
  const snapshot = clone(value as Phase8ACockpitSnapshot);
  const metrics = buildPhase8AObservability(snapshot);
  return Object.freeze({
    getSummary: () => clone({
      schemaVersion: 'PHASE8A_RESEARCH_SUMMARY_RESPONSE_1',
      sourceClass: snapshot.sourceClass,
      runId: snapshot.runId,
      observedAt: snapshot.observedAt,
      evidenceClass: snapshot.evidenceClass,
      realData: false,
      acceptedSilver: false,
      researchReady: false,
      activationVerdict: snapshot.activationVerdict,
      eligibility: snapshot.eligibility,
      progress: snapshot.progress,
      dataflow: snapshot.dataflow,
    }),
    getEvents: (query: ResearchPageQuery) => page(snapshot.events, query, 'PHASE8A_RESEARCH_EVENTS_RESPONSE_1'),
    getQuarantines: (query: ResearchPageQuery) => page(snapshot.quarantines, query, 'PHASE8A_RESEARCH_QUARANTINES_RESPONSE_1'),
    getProvenance: () => clone({ schemaVersion: 'PHASE8A_RESEARCH_PROVENANCE_RESPONSE_1', provenance: snapshot.provenance }),
    getMetrics: () => clone({ schemaVersion: 'PHASE8A_RESEARCH_METRICS_RESPONSE_1', metrics }),
    getPrometheus: () => renderPhase8APrometheus(metrics),
  });
}

const MAX_PROVIDER_JSON_BYTES = 512 * 1024;
const MAX_PROVIDER_NDJSON_BYTES = 4 * 1024 * 1024;

function assertSafeExistingPath(path: string, expected: 'directory' | 'file'): void {
  const absolute = resolve(path);
  if (!isAbsolute(absolute)) throw new Error('unsafe_path');
  const root = parse(absolute).root;
  const parts = absolute.slice(root.length).split('/').filter(Boolean);
  let current = root;
  for (const part of parts) {
    current = join(current, part);
    const stats = lstatSync(current);
    if (stats.isSymbolicLink()) throw new Error('unsafe_path');
  }
  const final = lstatSync(absolute);
  if ((expected === 'directory' && !final.isDirectory()) || (expected === 'file' && !final.isFile())) {
    throw new Error('unsafe_path');
  }
}

function readBoundedFile(path: string, maxBytes: number): Buffer {
  assertSafeExistingPath(dirname(path), 'directory');
  assertSafeExistingPath(path, 'file');
  const descriptor = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const before = fstatSync(descriptor);
    if (!before.isFile()) throw new Error('unsafe_path');
    if (before.size > maxBytes) throw new Error('provider_input_too_large');
    const bytes = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < bytes.length) {
      const count = readSync(descriptor, bytes, offset, bytes.length - offset, null);
      if (count === 0) break;
      offset += count;
    }
    const after = fstatSync(descriptor);
    if (offset !== before.size || after.size !== before.size || after.ino !== before.ino || after.dev !== before.dev) {
      throw new Error('provider_input_size_drift');
    }
    return bytes;
  } finally { closeSync(descriptor); }
}

function readBoundedJson(path: string): unknown {
  try { return JSON.parse(readBoundedFile(path, MAX_PROVIDER_JSON_BYTES).toString('utf8')); }
  catch (error) {
    if (error instanceof Error && /unsafe_path|too_large|size_drift/.test(error.message)) throw error;
    throw new Error('malformed_provider_json');
  }
}

export function createFilePhase8AResearchProvider(outputDirectory: string): ResearchDashboardProvider {
  assertSafeExistingPath(outputDirectory, 'directory');
  const snapshot = readBoundedJson(join(outputDirectory, 'cockpit-snapshot.json'));
  const base = createInMemoryPhase8AResearchProvider(snapshot);
  const eventsPath = join(outputDirectory, 'event-observations.ndjson');
  const quarantinesPath = join(outputDirectory, 'quarantines.ndjson');
  for (const path of [eventsPath, quarantinesPath]) {
    assertSafeExistingPath(path, 'file');
    if (lstatSync(path).size > MAX_PROVIDER_NDJSON_BYTES) throw new Error('provider_input_too_large');
  }
  return Object.freeze({
    ...base,
    getEvents: async (query: ResearchPageQuery) => base.getEvents(query),
    getQuarantines: async (query: ResearchPageQuery) => base.getQuarantines(query),
  });
}

export function createOptionalPhase8AResearchProvider(env: Readonly<Record<string, string | undefined>>): ResearchDashboardProvider | undefined {
  const directory = env.PHASE8A_RESEARCH_OUTPUT_DIR?.trim();
  return directory ? createFilePhase8AResearchProvider(directory) : undefined;
}
