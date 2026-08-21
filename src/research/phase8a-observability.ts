export const PHASE8A_ALLOWED_METRIC_LABELS = Object.freeze([
  'stage',
  'result',
  'quarantine_reason',
  'source',
  'schema_version',
  'run_mode',
  'evidence_class',
] as const);

const LABEL_DOMAINS = Object.freeze({
  stage: new Set(['validate', 'reducer', 'bronze', 'coverage', 'publish', 'wal', 'checkpoint']),
  result: new Set(['success', 'failure', 'retry', 'match', 'mismatch', 'quarantined', 'reconciled']),
  quarantine_reason: new Set(['UNKNOWN_DISCRIMINATOR', 'FAILED_TRANSACTION', 'DUPLICATE_CONFLICT', 'UNRESOLVED_IDENTITY']),
  source: new Set(['synthetic_fixture']),
  schema_version: new Set(['phase8a_1']),
  run_mode: new Set(['fixture_replay']),
  evidence_class: new Set(['synthetic', 'shadow_structural', 'quarantined']),
});

export type Phase8AMetricLabels = Partial<Record<(typeof PHASE8A_ALLOWED_METRIC_LABELS)[number], string>>;

export function validatePhase8AMetricLabels(labels: Record<string, string>): string[] {
  const errors: string[] = [];
  for (const [name, value] of Object.entries(labels)) {
    if (!PHASE8A_ALLOWED_METRIC_LABELS.includes(name as never)) {
      errors.push(`forbidden_label:${name}`);
      continue;
    }
    if (!LABEL_DOMAINS[name as keyof typeof LABEL_DOMAINS].has(value)) {
      errors.push(`unknown_label_value:${name}`);
    }
  }
  return errors.sort();
}

export type Phase8AObservabilityInput = Readonly<{
  schemaVersion: 'PHASE8A_COCKPIT_SNAPSHOT_1';
  sourceClass: 'SYNTHETIC_FIXTURE_ONLY';
  evidenceClass: 'SYNTHETIC';
  realData: false;
  acceptedSilver: false;
  researchReady: false;
  eligibility: Readonly<{
    transportPilot: Readonly<{ eligible: false }>;
    acceptedSilver: Readonly<{ eligible: false }>;
  }>;
  progress: Readonly<{
    requestedSlots: number;
    reconciledSlots: number;
    skippedSlots: number;
    provisionalSlots: number;
    resolvedSlots: number;
    coveragePercent: number;
    deterministicRerun: 'MATCH' | 'MISMATCH';
  }>;
  dataflow: Readonly<{
    callbacks: number;
    blocks: number;
    transactions: number;
    topLevelInstructions: number;
    innerInstructions: number;
    pumpCandidates: number;
    failedPumpTransactions: number;
    unknownDiscriminators: number;
    quarantines: number;
    exactRetries: number;
    duplicateConflicts: number;
  }>;
  resources: Readonly<{
    bytesRead: number;
    bytesWritten: number;
    queueDepth: number;
    peakRssBytes: number;
    outputBytes: number;
    stageDurationsMs: Readonly<Record<string, number>>;
    walStatus: 'CLEAN' | 'RECOVERED';
    checkpointStatus: 'PUBLISHED' | 'RECOVERED';
    quarantineByReason: Readonly<Record<string, number>>;
  }>;
}>;

export type Phase8AMetricsSnapshot = Readonly<{
  schemaVersion: 'PHASE8A_METRICS_SNAPSHOT_1';
  source: 'synthetic_fixture';
  runMode: 'fixture_replay';
  schemaLabel: 'phase8a_1';
  requestedSlots: number;
  reconciledSlots: number;
  skippedSlots: number;
  provisionalSlots: number;
  resolvedSlots: number;
  coveragePercent: number;
  callbacks: number;
  blocks: number;
  transactions: number;
  topLevelInstructions: number;
  innerInstructions: number;
  pumpCandidates: number;
  failedPumpTransactions: number;
  unknownDiscriminators: number;
  quarantines: number;
  exactRetries: number;
  duplicateConflicts: number;
  bytesRead: number;
  bytesWritten: number;
  outputBytes: number;
  queueDepth: number;
  peakRssBytes: number;
  transportEligible: 0;
  acceptedSilverEligible: 0;
  deterministicRerunMatch: 0 | 1;
  walClean: 0 | 1;
  checkpointPublished: 0 | 1;
  stageDurationsMs: Readonly<Record<string, number>>;
  quarantineByReason: Readonly<Record<string, number>>;
}>;

function nonNegativeSafe(value: unknown, name: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error(`invalid_metric:${name}`);
  return value;
}

function closedRecord(value: unknown, allowed: ReadonlySet<string>, name: string): Record<string, number> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error(`invalid_metric:${name}`);
  const output: Record<string, number> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (!allowed.has(key)) throw new Error(`invalid_metric:${name}:${key}`);
    output[key] = nonNegativeSafe(raw, `${name}:${key}`);
  }
  return Object.freeze(output);
}

export function buildPhase8AObservability(input: Phase8AObservabilityInput): Phase8AMetricsSnapshot {
  if (input.schemaVersion !== 'PHASE8A_COCKPIT_SNAPSHOT_1'
    || input.sourceClass !== 'SYNTHETIC_FIXTURE_ONLY'
    || input.evidenceClass !== 'SYNTHETIC'
    || input.realData !== false
    || input.acceptedSilver !== false
    || input.researchReady !== false
    || input.eligibility.transportPilot.eligible !== false
    || input.eligibility.acceptedSilver.eligible !== false) throw new Error('invalid_metric:source_contract');
  const progress = input.progress;
  if (typeof progress.coveragePercent !== 'number' || !Number.isFinite(progress.coveragePercent)
    || progress.coveragePercent < 0 || progress.coveragePercent > 100) throw new Error('invalid_metric:coveragePercent');
  const dataflow = input.dataflow;
  const resources = input.resources;
  const stages = closedRecord(resources.stageDurationsMs, new Set(['validate', 'reduce', 'publish']), 'stage');
  const quarantines = closedRecord(resources.quarantineByReason, LABEL_DOMAINS.quarantine_reason, 'quarantine');
  return Object.freeze({
    schemaVersion: 'PHASE8A_METRICS_SNAPSHOT_1',
    source: 'synthetic_fixture',
    runMode: 'fixture_replay',
    schemaLabel: 'phase8a_1',
    requestedSlots: nonNegativeSafe(progress.requestedSlots, 'requestedSlots'),
    reconciledSlots: nonNegativeSafe(progress.reconciledSlots, 'reconciledSlots'),
    skippedSlots: nonNegativeSafe(progress.skippedSlots, 'skippedSlots'),
    provisionalSlots: nonNegativeSafe(progress.provisionalSlots, 'provisionalSlots'),
    resolvedSlots: nonNegativeSafe(progress.resolvedSlots, 'resolvedSlots'),
    coveragePercent: progress.coveragePercent,
    callbacks: nonNegativeSafe(dataflow.callbacks, 'callbacks'),
    blocks: nonNegativeSafe(dataflow.blocks, 'blocks'),
    transactions: nonNegativeSafe(dataflow.transactions, 'transactions'),
    topLevelInstructions: nonNegativeSafe(dataflow.topLevelInstructions, 'topLevelInstructions'),
    innerInstructions: nonNegativeSafe(dataflow.innerInstructions, 'innerInstructions'),
    pumpCandidates: nonNegativeSafe(dataflow.pumpCandidates, 'pumpCandidates'),
    failedPumpTransactions: nonNegativeSafe(dataflow.failedPumpTransactions, 'failedPumpTransactions'),
    unknownDiscriminators: nonNegativeSafe(dataflow.unknownDiscriminators, 'unknownDiscriminators'),
    quarantines: nonNegativeSafe(dataflow.quarantines, 'quarantines'),
    exactRetries: nonNegativeSafe(dataflow.exactRetries, 'exactRetries'),
    duplicateConflicts: nonNegativeSafe(dataflow.duplicateConflicts, 'duplicateConflicts'),
    bytesRead: nonNegativeSafe(resources.bytesRead, 'bytesRead'),
    bytesWritten: nonNegativeSafe(resources.bytesWritten, 'bytesWritten'),
    outputBytes: nonNegativeSafe(resources.outputBytes, 'outputBytes'),
    queueDepth: nonNegativeSafe(resources.queueDepth, 'queueDepth'),
    peakRssBytes: nonNegativeSafe(resources.peakRssBytes, 'peakRssBytes'),
    transportEligible: 0,
    acceptedSilverEligible: 0,
    deterministicRerunMatch: progress.deterministicRerun === 'MATCH' ? 1 : 0,
    walClean: resources.walStatus === 'CLEAN' ? 1 : 0,
    checkpointPublished: resources.checkpointStatus === 'PUBLISHED' ? 1 : 0,
    stageDurationsMs: stages,
    quarantineByReason: quarantines,
  });
}

export function safeBuildPhase8AObservability(input: Phase8AObservabilityInput): Readonly<{
  schemaVersion: 'PHASE8A_OBSERVABILITY_RESULT_1';
  status: 'AVAILABLE' | 'UNAVAILABLE';
  snapshot: Phase8AMetricsSnapshot | null;
}> {
  try {
    return Object.freeze({ schemaVersion: 'PHASE8A_OBSERVABILITY_RESULT_1', status: 'AVAILABLE', snapshot: buildPhase8AObservability(input) });
  } catch {
    return Object.freeze({ schemaVersion: 'PHASE8A_OBSERVABILITY_RESULT_1', status: 'UNAVAILABLE', snapshot: null });
  }
}

function labels(values: Phase8AMetricLabels): string {
  const errors = validatePhase8AMetricLabels(values as Record<string, string>);
  if (errors.length) throw new Error(errors.join(','));
  return `{${Object.entries(values).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `${key}="${value}"`).join(',')}}`;
}

export function renderPhase8APrometheus(snapshot: Phase8AMetricsSnapshot): string {
  const common = { source: snapshot.source, schema_version: snapshot.schemaLabel, run_mode: snapshot.runMode } as const;
  const rows: Array<[string, number, Phase8AMetricLabels]> = [
    ['phase8a_requested_slots', snapshot.requestedSlots, common],
    ['phase8a_reconciled_slots', snapshot.reconciledSlots, common],
    ['phase8a_coverage_percent', snapshot.coveragePercent, common],
    ['phase8a_callbacks_total', snapshot.callbacks, { ...common, stage: 'reducer', result: 'success' }],
    ['phase8a_transactions_total', snapshot.transactions, { ...common, stage: 'bronze', result: 'success' }],
    ['phase8a_pump_candidates_total', snapshot.pumpCandidates, { ...common, evidence_class: 'shadow_structural' }],
    ['phase8a_quarantines_total', snapshot.quarantines, { ...common, result: 'quarantined' }],
    ['phase8a_exact_retries_total', snapshot.exactRetries, { ...common, result: 'retry' }],
    ['phase8a_duplicate_conflicts_total', snapshot.duplicateConflicts, { ...common, result: 'failure' }],
    ['phase8a_bytes_read', snapshot.bytesRead, common],
    ['phase8a_bytes_written', snapshot.bytesWritten, common],
    ['phase8a_queue_depth', snapshot.queueDepth, common],
    ['phase8a_peak_rss_bytes', snapshot.peakRssBytes, common],
    ['phase8a_deterministic_rerun_match', snapshot.deterministicRerunMatch, { ...common, result: snapshot.deterministicRerunMatch ? 'match' : 'mismatch' }],
    ['phase8a_transport_eligible', snapshot.transportEligible, common],
    ['phase8a_accepted_silver_eligible', snapshot.acceptedSilverEligible, common],
    ['phase8a_wal_clean', snapshot.walClean, { ...common, stage: 'wal' }],
    ['phase8a_checkpoint_published', snapshot.checkpointPublished, { ...common, stage: 'checkpoint' }],
  ];
  for (const [stage, value] of Object.entries(snapshot.stageDurationsMs)) rows.push(['phase8a_stage_duration_ms', value, { ...common, stage: stage === 'reduce' ? 'reducer' : stage }]);
  for (const [reason, value] of Object.entries(snapshot.quarantineByReason)) rows.push(['phase8a_quarantines_by_reason_total', value, { ...common, quarantine_reason: reason, result: 'quarantined' }]);
  return `${rows.map(([name, value, metricLabels]) => `${name}${labels(metricLabels)} ${String(value)}`).join('\n')}\n`;
}
