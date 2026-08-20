import { createHash } from 'node:crypto';

export type OldFaithfulPilotAReadinessResult = Readonly<{
  schemaVersion: 'OLD_FAITHFUL_PILOT_A_READINESS_RESULT_1';
  status: 'HOLD_UNPROVEN_ACTIVATION' | 'QUARANTINED';
  approved: false;
  pilotEligible: false;
  researchReady: false;
  transportCoverageStatus: 'NOT_RUN';
  eventSemanticCoverageStatus: 'NOT_RUN';
  reasons: readonly string[];
  hashBindingsValid: boolean;
  sourceManifestSha256: string | null;
  packageCoreSha256: string | null;
  canonicalHash: string;
}>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  return canonicalJson(Object.keys(value).sort()) === canonicalJson([...expected].sort());
}

const PACKAGE_KEYS = Object.freeze([
  'abortConditions', 'approved', 'bandwidthCapPreflightPlan', 'claimBoundary', 'executionAuthorization',
  'frontendContract', 'hashBindings', 'observabilitySideChannel', 'outputContract', 'packageKind',
  'pilotBudget', 'pilotEligible', 'pilotRange', 'registry', 'researchReady', 'schemaVersion',
  'sourceAndToolchainFreeze', 'sourceManifestPath', 'status',
]);
const PILOT_RANGE_KEYS = Object.freeze([
  'endExclusive', 'epoch', 'eventSemanticCoverageStatus', 'followUpRangePolicy', 'inventorySlotsPresent',
  'requestedSlots', 'selectionNonClaims', 'selectionPolicy', 'startInclusive', 'transportCoverageStatus',
]);
const EXPECTED_SELECTION_NONCLAIMS = Object.freeze([
  'NO_PROFIT_SELECTION', 'NO_TOKEN_PERFORMANCE_SELECTION', 'NO_KNOWN_WINNER_SELECTION',
]);
const EXPECTED_FOLLOW_UP_RANGE_POLICY = 'FIRST_ASCENDING_SUBSEQUENT_1000_SLOT_HALF_OPEN_WINDOW_WITH_COMPLETE_PINNED_INVENTORY_WITHOUT_EVENT_OR_OUTCOME_INSPECTION';

function validatePackageRoot(value: Record<string, unknown>): string | undefined {
  if (!hasExactKeys(value, PACKAGE_KEYS)
    || value.schemaVersion !== 'OLD_FAITHFUL_PILOT_A_READINESS_PACKAGE_1'
    || value.packageKind !== 'OLD_FAITHFUL_EVENT_TRANSPORT_PILOT'
    || value.status !== 'CANDIDATE_UNAPPROVED'
    || value.sourceManifestPath !== 'docs/research/OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST.json') {
    return 'PACKAGE_CONTRACT_INVALID';
  }
  return undefined;
}

const EXPECTED_SOURCE_MANIFEST = Object.freeze({
  schemaVersion: 'OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST_1',
  status: 'CANDIDATE_UNAPPROVED',
  approved: false,
  pilotEligible: false,
  researchReady: false,
  epoch: 978,
  canonicalEpochRange: Object.freeze({ startInclusive: 422_496_000, endExclusive: 422_928_000 }),
  epochCid: 'bafyreidv5xfqynnope3ul23a7qhcl4csf52ts2wcxdeoi5gqlbkwsn3k4m',
  car: Object.freeze({
    url: 'https://files.old-faithful.net/978/epoch-978.car',
    sizeBytes: '709264399796',
    sha256: '4ceef2830882036491f2f5a0fbc9ba5ccb94e7783a6723c42336969a50cb212c',
    payloadBytesReadDuringReadiness: '0',
  }),
  inventory: Object.freeze({
    url: 'https://files.old-faithful.net/978/978.slots.txt',
    sizeBytes: 4_313_880,
    sha256: '1de4b8689cfe7362b6129e8123f53fe3863616d7bb7c08338d157bb6d4754e97',
    totalEntryCount: 431_388,
    extraBoundarySlot: 422_495_999,
    canonicalRangeEntryCount: 431_387,
    canonicalRangeAbsentSlotCount: 613,
    semantics: 'UNRESOLVED_BOUNDARY_ENTRY_FILTER_BEFORE_RANGE_SELECTION',
  }),
  recap: Object.freeze({
    url: 'https://files.old-faithful.net/978/978.recap.yaml',
    sizeBytes: 1_260,
    sha256: '2666bac343424fa6d18b3a01e1512a6231eac6a1e6bdd40f8d0f5d9157d121db',
  }),
});

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new Error('non_canonical_number');
    return String(value);
  }
  if (Array.isArray(value)) {
    const items: string[] = [];
    for (let index = 0; index < value.length; index += 1) {
      if (!Object.hasOwn(value, index)) throw new Error('sparse_array');
      items.push(canonicalJson(value[index]));
    }
    return `[${items.join(',')}]`;
  }
  if (!isRecord(value)) throw new Error('non_canonical_value');
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

function domainHash(domain: string, value: unknown): string {
  return createHash('sha256').update(`${domain}\n${canonicalJson(value)}`).digest('hex');
}

const PINNED_PACKAGE_CORE_SHA256 = '154ac2c5da0e413c54c552079817fe35974a1f56f6d9824e034cb65f30df9eff';

type HashValidation = {
  valid: boolean;
  sourceManifestSha256: string;
  packageCoreSha256: string;
};

function validateHashBindings(sourceManifest: Record<string, unknown>, readinessPackage: Record<string, unknown>): HashValidation {
  const sourceManifestSha256 = domainHash('OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST_1', sourceManifest);
  const hashBindings = readinessPackage.hashBindings;
  const outputContract = readinessPackage.outputContract;
  if (!isRecord(hashBindings) || !isRecord(hashBindings.outputSchemaSha256) || !isRecord(outputContract)) {
    return { valid: false, sourceManifestSha256, packageCoreSha256: '' };
  }
  const { hashBindings: _excluded, ...packageCore } = readinessPackage;
  const packageCoreSha256 = domainHash('OLD_FAITHFUL_PILOT_A_READINESS_PACKAGE_1', packageCore);
  const expectedOutputHashes: Record<string, string> = {};
  for (const [key, value] of Object.entries(outputContract)) {
    expectedOutputHashes[key] = domainHash(`OLD_FAITHFUL_PILOT_A_OUTPUT_${key}`, value);
  }
  const valid = hashBindings.sourceManifestSha256 === sourceManifestSha256
    && hashBindings.registrySha256 === domainHash('PUMP_SLOT_EFFECTIVE_REGISTRY_CANDIDATE_1', readinessPackage.registry)
    && hashBindings.observabilitySha256 === domainHash('OLD_FAITHFUL_PILOT_A_OBSERVABILITY_CONTRACT_1', readinessPackage.observabilitySideChannel)
    && hashBindings.frontendSha256 === domainHash('OLD_FAITHFUL_PILOT_A_FRONTEND_CONTRACT_1', readinessPackage.frontendContract)
    && canonicalJson(hashBindings.outputSchemaSha256) === canonicalJson(expectedOutputHashes)
    && hashBindings.packageCoreSha256 === packageCoreSha256
    && packageCoreSha256 === PINNED_PACKAGE_CORE_SHA256;
  return { valid, sourceManifestSha256, packageCoreSha256 };
}

function exactSourceManifest(value: unknown): boolean {
  try {
    return canonicalJson(value) === canonicalJson(EXPECTED_SOURCE_MANIFEST);
  } catch {
    return false;
  }
}

const PUMP_PROGRAM_ID = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const PUMP_DOCS_COMMIT = '9c82f61cb711b044a17f770ab8ce9f9bdf78f333';
const PUMP_IDL_SHA256 = 'b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49';
const REGISTRY_ENTRY_KEYS = Object.freeze([
  'accountLayout', 'classification', 'discriminatorHex', 'endExclusive', 'entryId', 'evidenceSource',
  'instructionOrEventLayout', 'kind', 'officialDocsCommit', 'officialSourceSha256',
  'onChainActivationEvidence', 'programId', 'startInclusive', 'uncertainty', 'variant',
]);
const EXPECTED_REGISTRY_ENTRIES = new Map<string, Readonly<Record<string, string>>>([
  ['instruction:create', Object.freeze({ kind: 'INSTRUCTION', variant: 'create', discriminatorHex: '181ec828051c0777', instructionOrEventLayout: 'PINNED_IDL_CREATE_LAYOUT', accountLayout: 'PINNED_IDL_CREATE_ACCOUNT_ROLES' })],
  ['instruction:create_v2', Object.freeze({ kind: 'INSTRUCTION', variant: 'create_v2', discriminatorHex: 'd6904cec5f8b31b4', instructionOrEventLayout: 'PINNED_IDL_CREATE_V2_LAYOUT', accountLayout: 'PINNED_IDL_CREATE_V2_ACCOUNT_ROLES' })],
  ['instruction:buy', Object.freeze({ kind: 'INSTRUCTION', variant: 'buy', discriminatorHex: '66063d1201daebea', instructionOrEventLayout: 'PINNED_IDL_BUY_LAYOUT', accountLayout: 'PINNED_IDL_BUY_ACCOUNT_ROLES' })],
  ['instruction:sell', Object.freeze({ kind: 'INSTRUCTION', variant: 'sell', discriminatorHex: '33e685a4017f83ad', instructionOrEventLayout: 'PINNED_IDL_SELL_LAYOUT', accountLayout: 'PINNED_IDL_SELL_ACCOUNT_ROLES' })],
  ['instruction:buy_exact_sol_in', Object.freeze({ kind: 'INSTRUCTION', variant: 'buy_exact_sol_in', discriminatorHex: '38fc74089edfcd5f', instructionOrEventLayout: 'PINNED_IDL_BUY_EXACT_SOL_IN_LAYOUT', accountLayout: 'PINNED_IDL_BUY_ACCOUNT_ROLES' })],
  ['instruction:buy_v2', Object.freeze({ kind: 'INSTRUCTION', variant: 'buy_v2', discriminatorHex: 'b817ee6167c5d33d', instructionOrEventLayout: 'PINNED_IDL_BUY_V2_LAYOUT', accountLayout: 'PINNED_IDL_BUY_V2_ACCOUNT_ROLES' })],
  ['instruction:sell_v2', Object.freeze({ kind: 'INSTRUCTION', variant: 'sell_v2', discriminatorHex: '5df6823ce7e940b2', instructionOrEventLayout: 'PINNED_IDL_SELL_V2_LAYOUT', accountLayout: 'PINNED_IDL_SELL_V2_ACCOUNT_ROLES' })],
  ['instruction:buy_exact_quote_in_v2', Object.freeze({ kind: 'INSTRUCTION', variant: 'buy_exact_quote_in_v2', discriminatorHex: 'c2ab1c46684d5b2f', instructionOrEventLayout: 'PINNED_IDL_BUY_EXACT_QUOTE_IN_V2_LAYOUT', accountLayout: 'PINNED_IDL_BUY_V2_ACCOUNT_ROLES' })],
  ['event:CreateEvent', Object.freeze({ kind: 'EVENT', variant: 'CreateEvent', discriminatorHex: '1b72a94ddeeb6376', instructionOrEventLayout: 'PINNED_IDL_CREATE_EVENT_BORSH_LAYOUT', accountLayout: 'ANCHOR_SELF_CPI_EVENT_AUTHORITY_BINDING' })],
  ['event:TradeEvent', Object.freeze({ kind: 'EVENT', variant: 'TradeEvent', discriminatorHex: 'bddb7fd34ee661ee', instructionOrEventLayout: 'PINNED_IDL_TRADE_EVENT_BORSH_LAYOUT', accountLayout: 'ANCHOR_SELF_CPI_EVENT_AUTHORITY_BINDING' })],
]);

type RegistryValidation = { reason?: string; hasProvenEntry: boolean };

function validateRegistry(value: unknown): RegistryValidation {
  if (!isRecord(value) || !Array.isArray(value.entries)) return { reason: 'REGISTRY_CONTRACT_INVALID', hasProvenEntry: false };
  if (value.unknownPolicy !== 'BRONZE_RAW_AND_QUARANTINE_ONLY'
    || value.acceptancePolicy !== 'ONLY_PROVEN_AT_SLOT_RANGE_MAY_PRODUCE_ACCEPTED_SILVER') {
    return { reason: 'REGISTRY_POLICY_INVALID', hasProvenEntry: false };
  }
  if (value.schemaVersion !== 'PUMP_SLOT_EFFECTIVE_REGISTRY_CANDIDATE_1'
    || canonicalJson(value.candidateRange) !== canonicalJson({ startInclusive: 422_506_000, endExclusive: 422_507_000 })
    || canonicalJson(value.allowedClassifications) !== canonicalJson(['PROVEN_AT_SLOT_RANGE', 'STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION', 'UNKNOWN'])
    || value.activationVerdict !== 'HOLD_UNPROVEN_ACTIVATION'
    || !isRecord(value.commonOfficialProvenance)
    || value.commonOfficialProvenance.commit !== PUMP_DOCS_COMMIT
    || value.commonOfficialProvenance.sourceSha256 !== PUMP_IDL_SHA256
    || value.commonOfficialProvenance.programId !== PUMP_PROGRAM_ID
    || value.commonOfficialProvenance.onChainDeploymentOrUpgradeEvidence !== null
    || value.commonOfficialProvenance.activationEvidenceStatus !== 'NONE_FOUND_IN_APPROVED_PRIMARY_SOURCES') {
    return { reason: 'REGISTRY_PROVENANCE_MISMATCH', hasProvenEntry: false };
  }
  if (value.entries.length !== EXPECTED_REGISTRY_ENTRIES.size) return { reason: 'REGISTRY_CONTRACT_INVALID', hasProvenEntry: false };
  const observed = new Set<string>();
  for (let index = 0; index < value.entries.length; index += 1) {
    if (!Object.hasOwn(value.entries, index) || !isRecord(value.entries[index])) return { reason: 'REGISTRY_CONTRACT_INVALID', hasProvenEntry: false };
    const entry = value.entries[index];
    const entryId = entry.entryId;
    if (typeof entryId !== 'string' || observed.has(entryId)) return { reason: 'REGISTRY_CONTRACT_INVALID', hasProvenEntry: false };
    observed.add(entryId);
    if (entry.classification === 'PROVEN_AT_SLOT_RANGE'
      || entry.startInclusive !== null
      || entry.endExclusive !== null
      || entry.onChainActivationEvidence !== null) {
      return { reason: 'UNPROVEN_ACTIVATION_CANNOT_BE_PROMOTED', hasProvenEntry: false };
    }
    if (entry.officialDocsCommit !== PUMP_DOCS_COMMIT || entry.officialSourceSha256 !== PUMP_IDL_SHA256) {
      return { reason: 'REGISTRY_PROVENANCE_MISMATCH', hasProvenEntry: false };
    }
    const expected = EXPECTED_REGISTRY_ENTRIES.get(entryId);
    if (!expected
      || canonicalJson(Object.keys(entry).sort()) !== canonicalJson([...REGISTRY_ENTRY_KEYS].sort())
      || entry.programId !== PUMP_PROGRAM_ID
      || entry.classification !== 'STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION'
      || entry.evidenceSource !== 'OFFICIAL_PUMP_IDL_ONLY'
      || entry.uncertainty !== 'IDL_CONTENT_DOES_NOT_PROVE_EPOCH_978_ACTIVATION'
      || entry.kind !== expected.kind
      || entry.variant !== expected.variant
      || entry.discriminatorHex !== expected.discriminatorHex
      || entry.instructionOrEventLayout !== expected.instructionOrEventLayout
      || entry.accountLayout !== expected.accountLayout) {
      return { reason: 'REGISTRY_CONTRACT_INVALID', hasProvenEntry: false };
    }
  }
  return { hasProvenEntry: false };
}

const REQUIRED_NONCLAIMS = Object.freeze([
  'RAW_HISTORICAL_ACCOUNT_STATE', 'INSTRUCTION_EXACT_STATE', 'AUTHORITATIVE_BONDING_CURVE_STATE',
  'AUTHORITATIVE_MINT_STATE', 'HOLDER_STATE', 'EXACT_LIQUIDITY', 'POSITION_SIZE_DEPENDENT_IMPACT',
  'COUNTERFACTUAL_EXECUTABLE_FILLS', 'EXECUTION_GRADE_RETURNS', 'OOS_READINESS', 'PROFITABILITY',
]);

function validateClaimBoundary(value: unknown): string | undefined {
  if (!isRecord(value) || !Array.isArray(value.mayLaterProve) || !Array.isArray(value.mustNeverClaim)) {
    return 'PILOT_A_NONCLAIM_VIOLATION';
  }
  const may = new Set(value.mayLaterProve);
  const never = new Set(value.mustNeverClaim);
  if (REQUIRED_NONCLAIMS.some((claim) => !never.has(claim) || may.has(claim))) return 'PILOT_A_NONCLAIM_VIOLATION';
  return undefined;
}

const EXPECTED_EVENT_COUNTS = Object.freeze([
  'SUCCESSFUL_CREATES', 'FAILED_CREATE_ATTEMPTS', 'BUYS', 'SELLS', 'FAILED_PUMP_TRANSACTIONS',
  'UNKNOWN_DISCRIMINATORS', 'TOP_LEVEL_PUMP_INSTRUCTIONS', 'INNER_CPI_PUMP_INSTRUCTIONS',
  'LEGACY_TRANSACTIONS', 'V0_TRANSACTIONS', 'COMPLETION_EVENTS', 'MIGRATIONS',
]);
const EXPECTED_BOUNDED_REASONS = Object.freeze([
  'SOURCE_METADATA_DRIFT', 'SOURCE_IDENTITY_MISMATCH', 'INVENTORY_MISMATCH', 'OUT_OF_RANGE_SLOT',
  'DUPLICATE_CONFLICT', 'TRANSACTION_IDENTITY_CONFLICT', 'UNKNOWN_DISCRIMINATOR', 'UNPROVEN_ACTIVATION',
  'AMBIGUOUS_EVENT', 'MALFORMED_EVENT', 'FAILED_PUMP_TRANSACTION', 'COVERAGE_GAP', 'WAL_FAILURE',
  'CHECKPOINT_FAILURE', 'BUDGET_EXCEEDED', 'DISK_SAFETY_MARGIN', 'LIMITER_INACTIVE', 'WIRE_RATE_STALE',
  'BANDWIDTH_CAP_EXCEEDED', 'DETERMINISTIC_RERUN_MISMATCH', 'UNEXPECTED_NETWORK_CAPABILITY',
  'APPROVAL_ESCALATION_ATTEMPT', 'ACCOUNT_STATE_CLAIM_FORBIDDEN', 'OBSERVABILITY_INFLUENCE_FORBIDDEN',
]);
const EXPECTED_AGGREGATE_MANIFEST = Object.freeze({
  sourceHashesRequired: true,
  configAndSchemaHashesRequired: true,
  perSlotContentHashesRequired: true,
  aggregateContentHashRequired: true,
  deterministicRerunHashRequired: true,
  wireBytesRangesAndRetriesRequired: true,
});

function validateOutputContract(value: unknown): string | undefined {
  if (!isRecord(value)
    || !isRecord(value.accountStatePolicy)
    || !isRecord(value.quarantine)
    || !isRecord(value.immutablePerSlotSilverEventOnly)
    || !isRecord(value.immutablePerSlotBronze)
    || !isRecord(value.coverageLedger)
    || !isRecord(value.retryDuplicateConflictLedger)
    || !isRecord(value.wal)
    || !isRecord(value.checkpoints)
    || !isRecord(value.aggregateManifest)
    || !Array.isArray(value.boundedReasonCodes)) return 'OUTPUT_CONTRACT_INVALID';
  const state = value.accountStatePolicy;
  if (state.rawHistoricalAccountState !== 'UNAVAILABLE_OF1'
    || state.instructionExactState !== 'UNAVAILABLE_OF1'
    || state.authoritativeCurveOrMintState !== 'UNAVAILABLE_OF1'
    || state.zeroFillForbidden !== true
    || state.eventReserveBackfillForbidden !== true
    || state.transactionWideBalanceBackfillForbidden !== true
    || state.currentStateBackfillForbidden !== true) return 'RAW_STATE_CLAIM_FORBIDDEN';
  if (value.quarantine.rawEvidenceRequired !== true
    || value.quarantine.unknownDiscriminatorRequired !== true
    || value.quarantine.bestEffortDecodeForbidden !== true) return 'UNKNOWN_NOT_QUARANTINED';
  const expectedBronze = { schemaVersion: 'PUMP_V2_BRONZE_TRANSACTION_1', required: true, contentHashRequired: true };
  const expectedSilver = {
    schemaVersion: 'PUMP_SILVER_TRANSACTION_CONTRACT_1', required: true,
    acceptedDecodeRequires: 'PROVEN_AT_SLOT_RANGE', accountStateFields: 'UNAVAILABLE_OF1',
  };
  const expectedCoverage = {
    transportCoveragePass: 'ALL_1000_INVENTORY_SLOTS_RECONCILED_WITHOUT_CONFLICT',
    eventSemanticCoveragePass: 'SEPARATE_CATEGORY_COUNTS_REPORTED_WITHOUT_REQUIRING_EVERY_CATEGORY',
    requiredEventCounts: EXPECTED_EVENT_COUNTS,
  };
  const expectedRetry = {
    exactRetry: 'IDEMPOTENT', duplicateConflict: 'ABORT', signatureOrCoordinateConflict: 'ABORT',
  };
  if (canonicalJson(value.immutablePerSlotBronze) !== canonicalJson(expectedBronze)
    || canonicalJson(value.immutablePerSlotSilverEventOnly) !== canonicalJson(expectedSilver)
    || canonicalJson(value.coverageLedger) !== canonicalJson(expectedCoverage)
    || canonicalJson(value.retryDuplicateConflictLedger) !== canonicalJson(expectedRetry)
    || canonicalJson(value.wal) !== canonicalJson({ required: true, appendOnly: true, failure: 'ABORT' })
    || canonicalJson(value.checkpoints) !== canonicalJson({ required: true, contentHashRequired: true, failure: 'ABORT' })
    || canonicalJson(value.aggregateManifest) !== canonicalJson(EXPECTED_AGGREGATE_MANIFEST)
    || canonicalJson(value.boundedReasonCodes) !== canonicalJson(EXPECTED_BOUNDED_REASONS)) return 'OUTPUT_CONTRACT_INVALID';
  return undefined;
}

const EXPECTED_PILOT_BUDGET = Object.freeze({
  requestedSlots: 1_000,
  maxWireBytes: 8 * 1024 ** 3,
  maxVerifiedRangeCacheBytes: 8 * 1024 ** 3,
  maxBronzeSilverOutputBytes: 10 * 1024 ** 3,
  maxWalCheckpointManifestBytes: 2 * 1024 ** 3,
  minimumFreeBytesBeforeStart: 40 * 1024 ** 3,
  maxRuntimeSeconds: 3_600,
  cgroupMemoryHardLimitBytes: 16 * 1024 ** 3,
  rssAbortThresholdBytes: 12 * 1024 ** 3,
  rssAbortMustBeSustained: true,
  readers: 1,
  writers: 1,
  isolatedImmutableOutputMapRequired: true,
});
const REQUIRED_ABORTS = Object.freeze([
  'BANDWIDTH_LIMIT_EXCEEDED', 'LIMITER_INACTIVE', 'WIRE_RATE_METRICS_STALE',
  'SOURCE_METADATA_DRIFT', 'CID_SHA_SIZE_MISMATCH', 'INVENTORY_CONFLICT', 'OUT_OF_RANGE_SLOT',
  'DUPLICATE_CONFLICT', 'COVERAGE_NOT_CLOSED', 'UNKNOWN_REGISTRY_LAYOUT_OUTSIDE_QUARANTINE',
  'BUDGET_EXCEEDED', 'WAL_FAILURE', 'CHECKPOINT_FAILURE', 'DISK_SAFETY_MARGIN_VIOLATION',
  'DETERMINISTIC_RERUN_MISMATCH', 'UNEXPECTED_NETWORK_CAPABILITY',
  'RESEARCH_READY_OR_PILOT_ELIGIBLE_ESCALATION',
]);

const EXPECTED_EXECUTION_AUTHORIZATION = Object.freeze({
  bandwidthCapPreflightAuthorized: false,
  carOrRangeDownloadAuthorized: false,
  archiveStreamAuthorized: false,
  slotProcessingAuthorized: false,
  pilotAExecutionAuthorized: false,
  pilotBAuthorized: false,
  clickHouseOrBackfillAuthorized: false,
});
const EXPECTED_BANDWIDTH_PLAN = Object.freeze({
  status: 'DESIGNED_NOT_EXECUTED',
  connectionCapacityBitsPerSecond: 1_000_000_000,
  absoluteHardCapBitsPerSecond: 799_000_000,
  applicationTargetBytesPerSecond: 85 * 1024 ** 2,
  applicationAbsoluteMaxBytesPerSecond: 90 * 1024 ** 2,
  measurementWindowsSeconds: Object.freeze([1, 10, 60]),
  wireRateNotPayloadOnly: true,
  limiterActiveHeartbeatRequired: true,
  burstControlRequired: true,
  sequentialStreams: 1,
  parallelRangeWorkers: 0,
  routerWanOption: 'BACK_UP_EXISTING_QOS_THEN_APPLY_WAN_INGRESS_CEILING_BELOW_800_MBIT_S_AND_VERIFY_ROUTER_COUNTERS',
  trueNasIfbOption: 'IDENTIFY_ACTUAL_WAN_FACING_INTERFACE_FROM_ROUTE_AND_COUNTER_DELTA_THEN_APPLY_IFB_INGRESS_SHAPING_WITH_CRASH_SAFE_CLEANUP',
  containerProcessLimits: 'CGROUP_MEMORY_CPU_AND_SINGLE_PROCESS_READER_WRITER_LIMITS_DO_NOT_REPLACE_WIRE_SHAPING',
  interfaceIdentification: 'DEFAULT_ROUTE_PLUS_READ_ONLY_INTERFACE_COUNTER_CORRELATION_DURING_SEPARATELY_APPROVED_PREFLIGHT',
  backup: 'EXPORT_ROUTER_QOS_AND_CAPTURE_TC_QDISC_FILTER_LINK_STATE_BEFORE_CHANGE',
  rollback: 'RESTORE_ROUTER_EXPORT_AND_DELETE_ONLY_TAGGED_PILOT_QDISC_IFB_FILTERS',
  crashCleanup: 'SYSTEMD_TRANSIENT_UNIT_OR_TRAP_REMOVES_ONLY_TAGGED_PILOT_NETWORK_OBJECTS_AND_REVERIFIES_BASELINE',
  manualHouseholdCheck: 'USER_CONFIRMS_VIDEO_CALL_STREAMING_AND_NORMAL_BROWSING_ON_OTHER_DEVICES_BEFORE_AND_DURING_TARGET_RATE_STAGE',
  abortOnLimiterOrMeasurementFailure: true,
  jetstreamerCapacitySettingIsLimiterProof: false,
  executed: false,
});

function validateExecutionAuthorization(value: unknown): string | undefined {
  if (!isRecord(value) || canonicalJson(value) !== canonicalJson(EXPECTED_EXECUTION_AUTHORIZATION)) {
    return 'EXECUTION_AUTHORIZATION_FORBIDDEN';
  }
  return undefined;
}

function validateBandwidthPlan(value: unknown): string | undefined {
  if (!isRecord(value)) return 'BANDWIDTH_PLAN_INVALID';
  if (value.executed !== false) return 'EXECUTION_AUTHORIZATION_FORBIDDEN';
  return canonicalJson(value) === canonicalJson(EXPECTED_BANDWIDTH_PLAN) ? undefined : 'BANDWIDTH_PLAN_INVALID';
}

function validateBudget(value: unknown): string | undefined {
  try {
    return canonicalJson(value) === canonicalJson(EXPECTED_PILOT_BUDGET) ? undefined : 'PILOT_BUDGET_INVALID';
  } catch {
    return 'PILOT_BUDGET_INVALID';
  }
}

function validateAbortConditions(value: unknown): string | undefined {
  if (!Array.isArray(value) || value.length !== REQUIRED_ABORTS.length) return 'ABORT_CONTRACT_INCOMPLETE';
  const observed = new Set(value);
  if (observed.size !== REQUIRED_ABORTS.length || REQUIRED_ABORTS.some((reason) => !observed.has(reason))) {
    return 'ABORT_CONTRACT_INCOMPLETE';
  }
  return undefined;
}

const EXPECTED_FROZEN_SOURCES = Object.freeze({
  'src/research/old-faithful-jetstreamer-adapter.ts': '603b0fecf57cb771a6ff5ff3956cceb0c696ec3acd9b227613a11d869ba82b81',
  'src/research/pump-v2-bronze.ts': 'e32d0c4babd8d410cdf87adecab94d273603802f803f72156b76bcd9916a25d9',
  'src/research/pump-silver-event.ts': '7bdf657447a8659b0b7ee67221e129fdd13a49785f5da71b140c3c80b944c97f',
  'src/research/pump-silver-contract.ts': '7b3bbd6202e7aada67eef89aec9b7eeb363f2dab53cf0c78748f7c2d9f98055f',
  'rust/old-faithful-pump-reducer/src/lib.rs': '4fd0038fdbd0e0d18fae9f855406d518babd833c9cf773f0dffee06940059051',
  'rust/old-faithful-pump-reducer/src/silver_event.rs': '022f96c39fd5ee3ec719f8eb40eb120bbdcba56b73975981de890a498481ffd3',
});
const ALLOWED_METRIC_LABELS = Object.freeze(['stage', 'result', 'quarantine_reason', 'source', 'schema_version', 'run_mode']);
const FORBIDDEN_METRIC_LABELS = Object.freeze(['mint', 'signature', 'wallet', 'account_pubkey', 'slot', 'transaction_id', 'event_key', 'run_id', 'arbitrary_error_text']);
const REQUIRED_EVIDENCE_BADGES = Object.freeze(['OBSERVED', 'DERIVED', 'INFERRED', 'UNKNOWN', 'QUARANTINED', 'SYNTHETIC', 'REAL_UNAPPROVED', 'RESEARCH_READY']);
const FORBIDDEN_PILOT_A_VIEWS = Object.freeze(['LIQUIDITY', 'HOLDER_STATE', 'RAW_ACCOUNT_STATE', 'EXECUTABLE_FILL', 'PROFITABILITY']);
const REQUIRED_FRONTEND_ROW_FIELDS = Object.freeze([
  'datasetId', 'runId', 'asOfSlot', 'asOfTime', 'provenanceId', 'approvalStatus',
  'evidenceBadge', 'completeness', 'uncertainty',
]);
const EXPECTED_DASHBOARD_GROUPS = Object.freeze([
  'DOWNLOAD_NETWORK_SAFETY', 'PIPELINE_HEALTH', 'COVERAGE_DATA_QUALITY',
  'AGGREGATE_MARKET_RESEARCH_LATER_CLICKHOUSE', 'STRATEGY_VALIDATION_ONLY_AFTER_APPROVED_GOLD',
]);
const EXPECTED_METRICS = Object.freeze([
  'wire_bytes_total', 'payload_bytes_total', 'current_wire_rate_bits_per_second', 'network_cap_status',
  'retries_total', 'timeouts_total', 'requested_slots', 'reconciled_slots', 'callbacks_total',
  'transactions_total', 'pump_candidates_total', 'silver_events_total', 'quarantines_total', 'queue_depth',
  'current_slot', 'last_completed_slot', 'wal_results_total', 'checkpoint_results_total',
  'process_resident_memory_bytes', 'process_cpu_seconds_total', 'disk_throughput_bytes_per_second',
  'disk_free_bytes', 'deterministic_rerun_matches_total', 'deterministic_rerun_mismatches_total',
]);
const EXPECTED_LABEL_VALUES = Object.freeze({
  stage: Object.freeze(['download', 'transport', 'bronze', 'silver', 'coverage', 'wal', 'checkpoint']),
  result: Object.freeze(['success', 'failure', 'retry', 'timeout', 'match', 'mismatch', 'reconciled', 'quarantined', 'aborted']),
  quarantine_reason: EXPECTED_BOUNDED_REASONS,
  source: Object.freeze(['old_faithful_of1', 'jetstreamer', 'pilot_a']),
  schema_version: Object.freeze([
    'OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST_1', 'PUMP_V2_BRONZE_TRANSACTION_1',
    'PUMP_SILVER_TRANSACTION_CONTRACT_1', 'OLD_FAITHFUL_COVERAGE_LEDGER_1',
  ]),
  run_mode: Object.freeze(['readiness_only', 'cap_preflight', 'pilot_a_event_transport']),
});
const EXPECTED_FRONTEND_SCREENS = Object.freeze({
  datasetRunSelector: '/api/research/datasets',
  provenanceDataQuality: '/api/research/datasets/{datasetId}/provenance',
  tokenLifecycleExplorer: '/api/research/datasets/{datasetId}/tokens/{mint}/lifecycle?asOfSlot={slot}',
  tradeTape: '/api/research/datasets/{datasetId}/tokens/{mint}/trades?cursor={cursor}',
  eventInstructionInspector: '/api/research/datasets/{datasetId}/events/{eventKey}',
  quantFeatureExplorer: '/api/research/datasets/{datasetId}/observations?asOfSlot={slot}',
  coverageQuarantineExplorer: '/api/research/datasets/{datasetId}/coverage',
});

function validateSourceAndToolchainFreeze(value: unknown): string | undefined {
  if (!isRecord(value) || !isRecord(value.node) || !isRecord(value.rust)
    || !isRecord(value.lockfiles) || !isRecord(value.buildConfig) || !isRecord(value.sourceFiles)) {
    return 'SOURCE_TOOLCHAIN_FREEZE_MISMATCH';
  }
  if (value.repositoryMainGitSha !== '16fa8fac09b3a96f1210503152f10b6ff6af5e4a'
    || value.jetstreamerVersion !== '0.7.0'
    || value.jetstreamerGitSha !== 'cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24'
    || value.jetstreamerFirehoseSourceSha256 !== '572ec56122e898f2318adc34d5998296b4fa0d31cf4bcab40950f5615a526a00'
    || value.localJetstreamerWorkspaceClassification !== 'INSUFFICIENT_PROVENANCE_NOT_ALLOWED'
    || value.node.version !== '22.23.2'
    || value.node.abi !== '127'
    || value.node.archiveSha256 !== 'd60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307'
    || value.rust.version !== '1.97.1'
    || value.rust.commitHash !== '8bab26f4f68e0e26f0bb7960be334d5b520ea452'
    || value.rust.hostTarget !== 'x86_64-unknown-linux-gnu'
    || value.lockfiles.packageLockSha256 !== '88da77e840dc08dcc9c8deaee993d89584b16b1f918e058b5f2b4414ca77c06f'
    || value.lockfiles.cargoLockSha256 !== '850a84e6a4781381f2c84efca4c5975612f5729e6872a3aac4bc1ff2ebfb6062'
    || value.buildConfig.tsconfigSha256 !== '8ea19ab6fa1835a2c57ec8427083b564a0ef527de9f3e5597708738766f83a27'
    || value.buildConfig.cargoTomlSha256 !== '7c8424a9a1677b9dd24125a59945a27ee4c797118b4f4142b705fa38a1ce2c0b'
    || value.buildConfig.lockedDependenciesRequired !== true
    || value.buildConfig.pilotBinaryBuildAuthorized !== false
    || canonicalJson(value.sourceFiles) !== canonicalJson(EXPECTED_FROZEN_SOURCES)) return 'SOURCE_TOOLCHAIN_FREEZE_MISMATCH';
  return undefined;
}

function validateObservability(value: unknown): string | undefined {
  if (!isRecord(value) || !Array.isArray(value.allowedLabels) || !Array.isArray(value.forbiddenLabels)
    || !isRecord(value.labelValues) || !Array.isArray(value.dashboardGroups) || !Array.isArray(value.metrics)) {
    return 'OBSERVABILITY_CONTRACT_INVALID';
  }
  if (canonicalJson(value.allowedLabels) !== canonicalJson(ALLOWED_METRIC_LABELS)
    || canonicalJson(value.forbiddenLabels) !== canonicalJson(FORBIDDEN_METRIC_LABELS)
    || canonicalJson(value.labelValues) !== canonicalJson(EXPECTED_LABEL_VALUES)) {
    return 'OBSERVABILITY_CARDINALITY_VIOLATION';
  }
  if (value.status !== 'CONTRACT_ONLY_NOT_IMPLEMENTED'
    || canonicalJson(value.dashboardGroups) !== canonicalJson(EXPECTED_DASHBOARD_GROUPS)
    || canonicalJson(value.metrics) !== canonicalJson(EXPECTED_METRICS)) return 'OBSERVABILITY_CONTRACT_INVALID';
  if (value.canonicalDatasetInfluence !== false
    || value.orderingInfluence !== false
    || value.hashInfluence !== false
    || value.acceptanceInfluence !== false
    || value.quarantineDecisionInfluence !== false) return 'OBSERVABILITY_INFLUENCE_FORBIDDEN';
  return undefined;
}

function validateFrontend(value: unknown): string | undefined {
  if (!isRecord(value) || !Array.isArray(value.evidenceBadges)
    || !Array.isArray(value.requiredRowFields)
    || !Array.isArray(value.pilotAForbiddenProvenViews) || !isRecord(value.screens)) return 'FRONTEND_CONTRACT_INVALID';
  if (value.status !== 'QUERY_CONTRACT_ONLY_NOT_IMPLEMENTED'
    || canonicalJson(value.evidenceBadges) !== canonicalJson(REQUIRED_EVIDENCE_BADGES)
    || canonicalJson(value.requiredRowFields) !== canonicalJson(REQUIRED_FRONTEND_ROW_FIELDS)
    || canonicalJson(value.pilotAForbiddenProvenViews) !== canonicalJson(FORBIDDEN_PILOT_A_VIEWS)
    || canonicalJson(value.screens) !== canonicalJson(EXPECTED_FRONTEND_SCREENS)) return 'FRONTEND_CONTRACT_INVALID';
  return undefined;
}

function quarantine(reason: string): OldFaithfulPilotAReadinessResult {
  const unsigned = {
    schemaVersion: 'OLD_FAITHFUL_PILOT_A_READINESS_RESULT_1' as const,
    status: 'QUARANTINED' as const,
    approved: false as const,
    pilotEligible: false as const,
    researchReady: false as const,
    transportCoverageStatus: 'NOT_RUN' as const,
    eventSemanticCoverageStatus: 'NOT_RUN' as const,
    reasons: Object.freeze([reason]),
    hashBindingsValid: false,
    sourceManifestSha256: null,
    packageCoreSha256: null,
  };
  return Object.freeze({
    ...unsigned,
    canonicalHash: domainHash('OLD_FAITHFUL_PILOT_A_READINESS_RESULT_1', unsigned),
  });
}

function evaluateOldFaithfulPilotAReadinessUnsafe(
  sourceManifest: unknown,
  readinessPackage: unknown,
): OldFaithfulPilotAReadinessResult {
  if (!isRecord(sourceManifest) || !isRecord(readinessPackage)) return quarantine('INVALID_PACKAGE');
  if (sourceManifest.approved !== false
    || sourceManifest.pilotEligible !== false
    || sourceManifest.researchReady !== false
    || readinessPackage.approved !== false
    || readinessPackage.pilotEligible !== false
    || readinessPackage.researchReady !== false) {
    return quarantine('APPROVAL_ESCALATION_FORBIDDEN');
  }
  const rootReason = validatePackageRoot(readinessPackage);
  if (rootReason) return quarantine(rootReason);
  if (!exactSourceManifest(sourceManifest)) return quarantine('SOURCE_MANIFEST_MISMATCH');
  const pilotRange = readinessPackage.pilotRange;
  const registry = readinessPackage.registry;
  if (!isRecord(pilotRange) || !isRecord(registry) || !Array.isArray(registry.entries)) {
    return quarantine('INVALID_PACKAGE');
  }
  if (pilotRange.selectionPolicy !== 'SOURCE_AND_ENGINEERING_PROPERTIES_ONLY'
    || canonicalJson(pilotRange.selectionNonClaims) !== canonicalJson(EXPECTED_SELECTION_NONCLAIMS)
    || pilotRange.followUpRangePolicy !== EXPECTED_FOLLOW_UP_RANGE_POLICY) {
    return quarantine('OUTCOME_DRIVEN_SELECTION_FORBIDDEN');
  }
  if (!hasExactKeys(pilotRange, PILOT_RANGE_KEYS)
    || pilotRange.epoch !== 978
    || pilotRange.startInclusive !== 422_506_000
    || pilotRange.endExclusive !== 422_507_000
    || pilotRange.requestedSlots !== 1_000
    || pilotRange.inventorySlotsPresent !== 1_000) {
    return quarantine('PILOT_RANGE_INVALID');
  }
  if (pilotRange.transportCoverageStatus !== 'NOT_RUN'
    || pilotRange.eventSemanticCoverageStatus !== 'NOT_RUN') {
    return quarantine('COVERAGE_SEMANTICS_CONFLATED');
  }
  const freezeReason = validateSourceAndToolchainFreeze(readinessPackage.sourceAndToolchainFreeze);
  if (freezeReason) return quarantine(freezeReason);
  const observabilityReason = validateObservability(readinessPackage.observabilitySideChannel);
  if (observabilityReason) return quarantine(observabilityReason);
  const frontendReason = validateFrontend(readinessPackage.frontendContract);
  if (frontendReason) return quarantine(frontendReason);
  const authorizationReason = validateExecutionAuthorization(readinessPackage.executionAuthorization);
  if (authorizationReason) return quarantine(authorizationReason);
  const bandwidthReason = validateBandwidthPlan(readinessPackage.bandwidthCapPreflightPlan);
  if (bandwidthReason) return quarantine(bandwidthReason);
  const budgetReason = validateBudget(readinessPackage.pilotBudget);
  if (budgetReason) return quarantine(budgetReason);
  const abortReason = validateAbortConditions(readinessPackage.abortConditions);
  if (abortReason) return quarantine(abortReason);
  const claimReason = validateClaimBoundary(readinessPackage.claimBoundary);
  if (claimReason) return quarantine(claimReason);
  const outputReason = validateOutputContract(readinessPackage.outputContract);
  if (outputReason) return quarantine(outputReason);
  const registryValidation = validateRegistry(registry);
  if (registryValidation.reason) return quarantine(registryValidation.reason);
  const hashValidation = validateHashBindings(sourceManifest, readinessPackage);
  if (!hashValidation.valid) return quarantine('HASH_BINDING_MISMATCH');
  if (!registryValidation.hasProvenEntry) {
    const unsigned = {
      schemaVersion: 'OLD_FAITHFUL_PILOT_A_READINESS_RESULT_1' as const,
      status: 'HOLD_UNPROVEN_ACTIVATION' as const,
      approved: false as const,
      pilotEligible: false as const,
      researchReady: false as const,
      transportCoverageStatus: 'NOT_RUN' as const,
      eventSemanticCoverageStatus: 'NOT_RUN' as const,
      reasons: Object.freeze(['NO_PROVEN_REGISTRY_ENTRY_FOR_CANDIDATE_RANGE']),
      hashBindingsValid: true,
      sourceManifestSha256: hashValidation.sourceManifestSha256,
      packageCoreSha256: hashValidation.packageCoreSha256,
    };
    return Object.freeze({
      ...unsigned,
      canonicalHash: domainHash('OLD_FAITHFUL_PILOT_A_READINESS_RESULT_1', unsigned),
    });
  }
  return quarantine('PILOT_EXECUTION_NOT_AUTHORIZED');
}

export function evaluateOldFaithfulPilotAReadiness(
  sourceManifest: unknown,
  readinessPackage: unknown,
): OldFaithfulPilotAReadinessResult {
  try {
    return evaluateOldFaithfulPilotAReadinessUnsafe(sourceManifest, readinessPackage);
  } catch {
    return quarantine('INVALID_PACKAGE');
  }
}
