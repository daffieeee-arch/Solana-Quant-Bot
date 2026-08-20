import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { evaluateOldFaithfulPilotAReadiness } from '../src/research/old-faithful-pilot-a-readiness.js';

function sourceManifest() {
  return {
    schemaVersion: 'OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST_1',
    status: 'CANDIDATE_UNAPPROVED',
    approved: false,
    pilotEligible: false,
    researchReady: false,
    epoch: 978,
    canonicalEpochRange: { startInclusive: 422_496_000, endExclusive: 422_928_000 },
    epochCid: 'bafyreidv5xfqynnope3ul23a7qhcl4csf52ts2wcxdeoi5gqlbkwsn3k4m',
    car: {
      url: 'https://files.old-faithful.net/978/epoch-978.car',
      sizeBytes: '709264399796',
      sha256: '4ceef2830882036491f2f5a0fbc9ba5ccb94e7783a6723c42336969a50cb212c',
      payloadBytesReadDuringReadiness: '0',
    },
    inventory: {
      url: 'https://files.old-faithful.net/978/978.slots.txt',
      sizeBytes: 4_313_880,
      sha256: '1de4b8689cfe7362b6129e8123f53fe3863616d7bb7c08338d157bb6d4754e97',
      totalEntryCount: 431_388,
      extraBoundarySlot: 422_495_999,
      canonicalRangeEntryCount: 431_387,
      canonicalRangeAbsentSlotCount: 613,
      semantics: 'UNRESOLVED_BOUNDARY_ENTRY_FILTER_BEFORE_RANGE_SELECTION',
    },
    recap: {
      url: 'https://files.old-faithful.net/978/978.recap.yaml',
      sizeBytes: 1_260,
      sha256: '2666bac343424fa6d18b3a01e1512a6231eac6a1e6bdd40f8d0f5d9157d121db',
    },
  };
}

function readinessPackage() {
  return JSON.parse(readFileSync(resolve('docs/research/OLD_FAITHFUL_PILOT_A_PLAN.json'), 'utf8'));
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number') return String(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
}

function domainHash(domain: string, value: unknown): string {
  return createHash('sha256').update(`${domain}\n${canonicalJson(value)}`).digest('hex');
}

function rebindHashes(candidate: any, manifest: any = sourceManifest()) {
  const { hashBindings: _old, ...core } = candidate;
  candidate.hashBindings = {
    sourceManifestSha256: domainHash('OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST_1', manifest),
    registrySha256: domainHash('PUMP_SLOT_EFFECTIVE_REGISTRY_CANDIDATE_1', candidate.registry),
    observabilitySha256: domainHash('OLD_FAITHFUL_PILOT_A_OBSERVABILITY_CONTRACT_1', candidate.observabilitySideChannel),
    frontendSha256: domainHash('OLD_FAITHFUL_PILOT_A_FRONTEND_CONTRACT_1', candidate.frontendContract),
    outputSchemaSha256: Object.fromEntries(Object.entries(candidate.outputContract)
      .map(([key, value]) => [key, domainHash(`OLD_FAITHFUL_PILOT_A_OUTPUT_${key}`, value)])),
    packageCoreSha256: domainHash('OLD_FAITHFUL_PILOT_A_READINESS_PACKAGE_1', core),
  };
}

describe('Old Faithful Pilot A readiness package', () => {
  it('hard-holds a structurally supported registry without slot activation proof', () => {
    const result = evaluateOldFaithfulPilotAReadiness(sourceManifest(), readinessPackage());

    expect(result).toMatchObject({
      schemaVersion: 'OLD_FAITHFUL_PILOT_A_READINESS_RESULT_1',
      status: 'HOLD_UNPROVEN_ACTIVATION',
      approved: false,
      pilotEligible: false,
      researchReady: false,
      transportCoverageStatus: 'NOT_RUN',
      eventSemanticCoverageStatus: 'NOT_RUN',
      reasons: ['NO_PROVEN_REGISTRY_ENTRY_FOR_CANDIDATE_RANGE'],
    });
  });

  it.each([
    ['wrong CID', (value: any) => { value.epochCid = `b${'a'.repeat(58)}`; }],
    ['wrong CAR SHA', (value: any) => { value.car.sha256 = '0'.repeat(64); }],
    ['wrong CAR size', (value: any) => { value.car.sizeBytes = '709264399795'; }],
    ['wrong inventory SHA', (value: any) => { value.inventory.sha256 = '0'.repeat(64); }],
    ['wrong inventory size', (value: any) => { value.inventory.sizeBytes = 4_313_879; }],
    ['wrong inventory count', (value: any) => { value.inventory.totalEntryCount = 431_387; }],
    ['wrong extra boundary slot', (value: any) => { value.inventory.extraBoundarySlot = 422_496_000; }],
    ['wrong canonical inventory count', (value: any) => { value.inventory.canonicalRangeEntryCount = 431_388; }],
    ['wrong absent-slot count', (value: any) => { value.inventory.canonicalRangeAbsentSlotCount = 612; }],
    ['wrong canonical epoch range', (value: any) => { value.canonicalEpochRange.endExclusive += 1; }],
    ['wrong recap SHA', (value: any) => { value.recap.sha256 = '0'.repeat(64); }],
  ])('quarantines %s as source-manifest drift', (_label, mutate) => {
    const manifest = structuredClone(sourceManifest());
    mutate(manifest);

    expect(evaluateOldFaithfulPilotAReadiness(manifest, readinessPackage())).toMatchObject({
      status: 'QUARANTINED',
      approved: false,
      pilotEligible: false,
      researchReady: false,
      reasons: ['SOURCE_MANIFEST_MISMATCH'],
    });
  });

  it.each([
    ['outside epoch', (value: any) => { value.pilotRange.startInclusive = 422_495_999; }, 'PILOT_RANGE_INVALID'],
    ['wrong end', (value: any) => { value.pilotRange.endExclusive = 422_507_001; }, 'PILOT_RANGE_INVALID'],
    ['missing inventory slot', (value: any) => { value.pilotRange.inventorySlotsPresent = 999; }, 'PILOT_RANGE_INVALID'],
    ['wrong requested count', (value: any) => { value.pilotRange.requestedSlots = 999; }, 'PILOT_RANGE_INVALID'],
    ['outcome-driven selection', (value: any) => { value.pilotRange.selectionPolicy = 'KNOWN_WINNERS'; }, 'OUTCOME_DRIVEN_SELECTION_FORBIDDEN'],
    ['transport/semantic coverage conflation', (value: any) => {
      value.pilotRange.transportCoverageStatus = 'PASS';
      value.pilotRange.eventSemanticCoverageStatus = 'PASS';
    }, 'COVERAGE_SEMANTICS_CONFLATED'],
  ])('rejects a pilot range with %s', (_label, mutate, reason) => {
    const candidate = structuredClone(readinessPackage());
    mutate(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED',
      reasons: [reason],
    });
  });

  it.each([
    ['source approved', (manifest: any, _candidate: any) => { manifest.approved = true; }],
    ['source research-ready', (manifest: any, _candidate: any) => { manifest.researchReady = true; }],
    ['package pilot-eligible', (_manifest: any, candidate: any) => { candidate.pilotEligible = true; }],
    ['package research-ready', (_manifest: any, candidate: any) => { candidate.researchReady = true; }],
  ])('rejects approval escalation through %s', (_label, mutate) => {
    const manifest = structuredClone(sourceManifest());
    const candidate = structuredClone(readinessPackage());
    mutate(manifest, candidate);
    expect(evaluateOldFaithfulPilotAReadiness(manifest, candidate)).toMatchObject({
      status: 'QUARANTINED',
      approved: false,
      pilotEligible: false,
      researchReady: false,
      reasons: ['APPROVAL_ESCALATION_FORBIDDEN'],
    });
  });

  it('binds the source, registry, output schemas and package core to deterministic hashes', () => {
    const candidate = readinessPackage();
    const result = evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate);
    expect(candidate.hashBindings).toBeTypeOf('object');
    expect(result).toMatchObject({
      status: 'HOLD_UNPROVEN_ACTIVATION',
      hashBindingsValid: true,
      sourceManifestSha256: expect.stringMatching(/^[0-9a-f]{64}$/),
      packageCoreSha256: expect.stringMatching(/^[0-9a-f]{64}$/),
      canonicalHash: expect.stringMatching(/^[0-9a-f]{64}$/),
    });
  });

  it.each([
    ['registry hash', (value: any) => { value.hashBindings.registrySha256 = '0'.repeat(64); }],
    ['Bronze schema hash', (value: any) => { value.hashBindings.outputSchemaSha256.immutablePerSlotBronze = '0'.repeat(64); }],
    ['package core hash', (value: any) => { value.hashBindings.packageCoreSha256 = '0'.repeat(64); }],
  ])('rejects drift in %s', (_label, mutate) => {
    const candidate = readinessPackage();
    mutate(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED',
      hashBindingsValid: false,
      reasons: ['HASH_BINDING_MISMATCH'],
    });
  });

  it.each([
    ['Jetstreamer commit drift', (value: any) => { value.sourceAndToolchainFreeze.jetstreamerGitSha = '0'.repeat(40); }, 'SOURCE_TOOLCHAIN_FREEZE_MISMATCH'],
    ['adapter source drift', (value: any) => { value.sourceAndToolchainFreeze.sourceFiles['src/research/old-faithful-jetstreamer-adapter.ts'] = '0'.repeat(64); }, 'SOURCE_TOOLCHAIN_FREEZE_MISMATCH'],
    ['Cargo lock drift', (value: any) => { value.sourceAndToolchainFreeze.lockfiles.cargoLockSha256 = '0'.repeat(64); }, 'SOURCE_TOOLCHAIN_FREEZE_MISMATCH'],
    ['wrong Node version', (value: any) => { value.sourceAndToolchainFreeze.node.version = '26.5.1'; }, 'SOURCE_TOOLCHAIN_FREEZE_MISMATCH'],
    ['wrong Rust version', (value: any) => { value.sourceAndToolchainFreeze.rust.version = '1.96.0'; }, 'SOURCE_TOOLCHAIN_FREEZE_MISMATCH'],
    ['mutable local Jetstreamer accepted', (value: any) => { value.sourceAndToolchainFreeze.localJetstreamerWorkspaceClassification = 'APPROVED'; }, 'SOURCE_TOOLCHAIN_FREEZE_MISMATCH'],
    ['mint metric label', (value: any) => { value.observabilitySideChannel.allowedLabels.push('mint'); }, 'OBSERVABILITY_CARDINALITY_VIOLATION'],
    ['run ID no longer forbidden', (value: any) => {
      value.observabilitySideChannel.forbiddenLabels = value.observabilitySideChannel.forbiddenLabels.filter((label: string) => label !== 'run_id');
    }, 'OBSERVABILITY_CARDINALITY_VIOLATION'],
    ['metrics influence canonical hash', (value: any) => { value.observabilitySideChannel.hashInfluence = true; }, 'OBSERVABILITY_INFLUENCE_FORBIDDEN'],
    ['missing frontend evidence badge', (value: any) => {
      value.frontendContract.evidenceBadges = value.frontendContract.evidenceBadges.filter((badge: string) => badge !== 'QUARANTINED');
    }, 'FRONTEND_CONTRACT_INVALID'],
    ['liquidity shown as proven', (value: any) => {
      value.frontendContract.pilotAForbiddenProvenViews = value.frontendContract.pilotAForbiddenProvenViews.filter((view: string) => view !== 'LIQUIDITY');
    }, 'FRONTEND_CONTRACT_INVALID'],
  ])('rejects freeze/side-channel/frontend violation: %s', (_label, mutate, reason) => {
    const candidate = readinessPackage();
    mutate(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED',
      reasons: [reason],
    });
  });

  it.each([
    ['hard cap at 800 Mbit/s', (value: any) => { value.bandwidthCapPreflightPlan.absoluteHardCapBitsPerSecond = 800_000_000; }, 'BANDWIDTH_PLAN_INVALID'],
    ['application target above 85 MiB/s', (value: any) => { value.bandwidthCapPreflightPlan.applicationTargetBytesPerSecond = 85 * 1024 * 1024 + 1; }, 'BANDWIDTH_PLAN_INVALID'],
    ['application max above 90 MiB/s', (value: any) => { value.bandwidthCapPreflightPlan.applicationAbsoluteMaxBytesPerSecond = 90 * 1024 * 1024 + 1; }, 'BANDWIDTH_PLAN_INVALID'],
    ['missing limiter heartbeat', (value: any) => { value.bandwidthCapPreflightPlan.limiterActiveHeartbeatRequired = false; }, 'BANDWIDTH_PLAN_INVALID'],
    ['parallel range worker', (value: any) => { value.bandwidthCapPreflightPlan.parallelRangeWorkers = 1; }, 'BANDWIDTH_PLAN_INVALID'],
    ['executed cap preflight', (value: any) => { value.bandwidthCapPreflightPlan.executed = true; }, 'EXECUTION_AUTHORIZATION_FORBIDDEN'],
    ['budget wire drift', (value: any) => { value.pilotBudget.maxWireBytes -= 1; }, 'PILOT_BUDGET_INVALID'],
    ['insufficient free storage', (value: any) => { value.pilotBudget.minimumFreeBytesBeforeStart = 39 * 1024 ** 3; }, 'PILOT_BUDGET_INVALID'],
    ['missing abort condition', (value: any) => {
      value.abortConditions = value.abortConditions.filter((entry: string) => entry !== 'UNEXPECTED_NETWORK_CAPABILITY');
    }, 'ABORT_CONTRACT_INCOMPLETE'],
    ['Pilot A execution authorization', (value: any) => { value.executionAuthorization.pilotAExecutionAuthorized = true; }, 'EXECUTION_AUTHORIZATION_FORBIDDEN'],
  ])('rejects unsafe budget/preflight mutation: %s', (_label, mutate, reason) => {
    const candidate = readinessPackage();
    mutate(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED',
      reasons: [reason],
    });
  });

  it.each([
    ['raw state claim from OF1', (value: any) => { value.outputContract.accountStatePolicy.rawHistoricalAccountState = 'AVAILABLE'; }, 'RAW_STATE_CLAIM_FORBIDDEN'],
    ['event reserves as authoritative state', (value: any) => { value.outputContract.accountStatePolicy.eventReserveBackfillForbidden = false; }, 'RAW_STATE_CLAIM_FORBIDDEN'],
    ['transaction balances as raw state', (value: any) => { value.outputContract.accountStatePolicy.transactionWideBalanceBackfillForbidden = false; }, 'RAW_STATE_CLAIM_FORBIDDEN'],
    ['current state backfill into history', (value: any) => { value.outputContract.accountStatePolicy.currentStateBackfillForbidden = false; }, 'RAW_STATE_CLAIM_FORBIDDEN'],
    ['unknown discriminator outside quarantine', (value: any) => { value.outputContract.quarantine.unknownDiscriminatorRequired = false; }, 'UNKNOWN_NOT_QUARANTINED'],
    ['Silver acceptance without proven range', (value: any) => { value.outputContract.immutablePerSlotSilverEventOnly.acceptedDecodeRequires = 'STRUCTURAL_ONLY'; }, 'OUTPUT_CONTRACT_INVALID'],
    ['profitability listed as provable', (value: any) => { value.claimBoundary.mayLaterProve.push('PROFITABILITY'); }, 'PILOT_A_NONCLAIM_VIOLATION'],
    ['execution returns removed from nonclaims', (value: any) => {
      value.claimBoundary.mustNeverClaim = value.claimBoundary.mustNeverClaim.filter((claim: string) => claim !== 'EXECUTION_GRADE_RETURNS');
    }, 'PILOT_A_NONCLAIM_VIOLATION'],
  ])('rejects output/nonclaim violation: %s', (_label, mutate, reason) => {
    const candidate = readinessPackage();
    mutate(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED',
      reasons: [reason],
    });
  });

  it.each([
    ['self-promoted activation', (value: any) => {
      value.registry.entries[0].classification = 'PROVEN_AT_SLOT_RANGE';
      value.registry.entries[0].startInclusive = 422_506_000;
      value.registry.entries[0].endExclusive = 422_507_000;
      value.registry.entries[0].onChainActivationEvidence = 'CALLER_ASSERTED';
    }, 'UNPROVEN_ACTIVATION_CANNOT_BE_PROMOTED'],
    ['official source hash drift', (value: any) => {
      value.registry.entries[0].officialSourceSha256 = '0'.repeat(64);
    }, 'REGISTRY_PROVENANCE_MISMATCH'],
    ['best-effort unknown decode', (value: any) => {
      value.registry.unknownPolicy = 'BEST_EFFORT_SILVER_DECODE';
    }, 'REGISTRY_POLICY_INVALID'],
    ['accepted Silver from unproven activation', (value: any) => {
      value.registry.acceptancePolicy = 'STRUCTURAL_SUPPORT_IS_ENOUGH';
    }, 'REGISTRY_POLICY_INVALID'],
    ['duplicate registry identity', (value: any) => {
      value.registry.entries.push(structuredClone(value.registry.entries[0]));
    }, 'REGISTRY_CONTRACT_INVALID'],
  ])('fails closed for registry violation: %s', (_label, mutate, reason) => {
    const candidate = readinessPackage();
    mutate(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED',
      reasons: [reason],
    });
  });

  it.each([
    ['package kind', (value: any) => { value.packageKind = 'OLD_FAITHFUL_STATE_ENRICHED_PILOT'; }, 'PACKAGE_CONTRACT_INVALID'],
    ['root status', (value: any) => { value.status = 'APPROVED'; }, 'PACKAGE_CONTRACT_INVALID'],
    ['root schema', (value: any) => { value.schemaVersion = 'OLD_FAITHFUL_PILOT_A_READINESS_PACKAGE_2'; }, 'PACKAGE_CONTRACT_INVALID'],
    ['selection nonclaims', (value: any) => { value.pilotRange.selectionNonClaims = []; }, 'OUTCOME_DRIVEN_SELECTION_FORBIDDEN'],
    ['follow-up range policy', (value: any) => { value.pilotRange.followUpRangePolicy = 'RANGE_WITH_MOST_WINNERS'; }, 'OUTCOME_DRIVEN_SELECTION_FORBIDDEN'],
  ])('rejects rebound root/range bypass: %s', (_label, mutate, reason) => {
    const candidate = readinessPackage();
    mutate(candidate);
    rebindHashes(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED', reasons: [reason], hashBindingsValid: false,
    });
  });

  it.each([
    ['retry conflict policy', (value: any) => {
      value.outputContract.retryDuplicateConflictLedger = {
        exactRetry: 'NON_IDEMPOTENT', duplicateConflict: 'IGNORE', signatureOrCoordinateConflict: 'IGNORE',
      };
    }],
    ['coverage semantics', (value: any) => {
      value.outputContract.coverageLedger.transportCoveragePass = 'PASS_IF_ANY';
      value.outputContract.coverageLedger.eventSemanticCoveragePass = 'PASS_IF_NONE';
      value.outputContract.coverageLedger.requiredEventCounts = [];
    }],
    ['aggregate manifest closure', (value: any) => { value.outputContract.aggregateManifest = {}; }],
    ['bounded reason closure', (value: any) => { value.outputContract.boundedReasonCodes = []; }],
    ['required Silver output', (value: any) => { value.outputContract.immutablePerSlotSilverEventOnly.required = false; }],
  ])('rejects rebound output-contract bypass: %s', (_label, mutate) => {
    const candidate = readinessPackage();
    mutate(candidate);
    rebindHashes(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED', reasons: ['OUTPUT_CONTRACT_INVALID'], hashBindingsValid: false,
    });
  });

  it('rejects rebound removal of concrete bandwidth safeguards', () => {
    const candidate = readinessPackage();
    for (const key of ['routerWanOption', 'trueNasIfbOption', 'interfaceIdentification', 'backup', 'rollback', 'crashCleanup', 'manualHouseholdCheck']) {
      candidate.bandwidthCapPreflightPlan[key] = '';
    }
    rebindHashes(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED', reasons: ['BANDWIDTH_PLAN_INVALID'], hashBindingsValid: false,
    });
  });

  it('rejects rebound observability contract replacement and requires closed label domains', () => {
    const candidate = readinessPackage();
    expect(candidate.observabilitySideChannel.labelValues).toBeTypeOf('object');
    candidate.observabilitySideChannel.status = 'IMPLEMENTED_CANONICAL';
    candidate.observabilitySideChannel.dashboardGroups = [];
    candidate.observabilitySideChannel.metrics = ['mint_profitability'];
    rebindHashes(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED', reasons: ['OBSERVABILITY_CONTRACT_INVALID'], hashBindingsValid: false,
    });
  });

  it('rejects rebound frontend route replacement and requires per-row evidence fields', () => {
    const candidate = readinessPackage();
    expect(candidate.frontendContract.requiredRowFields).toEqual([
      'datasetId', 'runId', 'asOfSlot', 'asOfTime', 'provenanceId', 'approvalStatus',
      'evidenceBadge', 'completeness', 'uncertainty',
    ]);
    candidate.frontendContract.status = 'IMPLEMENTED_PROVEN';
    candidate.frontendContract.screens = Object.fromEntries(Array.from({ length: 7 }, (_, index) => [`arbitrary${index}`, '']));
    rebindHashes(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED', reasons: ['FRONTEND_CONTRACT_INVALID'], hashBindingsValid: false,
    });
  });

  it('rejects seven arbitrary false authorization keys after rebinding', () => {
    const candidate = readinessPackage();
    candidate.executionAuthorization = Object.fromEntries(Array.from({ length: 7 }, (_, index) => [`fake${index}`, false]));
    rebindHashes(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED', reasons: ['EXECUTION_AUTHORIZATION_FORBIDDEN'], hashBindingsValid: false,
    });
  });

  it('rejects a correctly rebound mutation of any remaining package-core field', () => {
    const candidate = readinessPackage();
    candidate.sourceAndToolchainFreeze.futureBuildRecipe.buildNow = true;
    rebindHashes(candidate);
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED', reasons: ['HASH_BINDING_MISMATCH'], hashBindingsValid: false,
    });
  });

  it('classifies JITO_TIP_TRANSFER_OBSERVED as point-in-time market-source dependent', () => {
    const quant = readFileSync(resolve('docs/research/OLD_FAITHFUL_QUANT_DATA_SPEC.md'), 'utf8');
    expect(quant).toContain('`JITO_TIP_TRANSFER_OBSERVED` — `REQUIRES_POINT_IN_TIME_MARKET_SOURCE`');
    expect(quant).toContain('tip-account-set source');
  });

  it('pins the bounded official metadata verification without CAR payload bytes', () => {
    const verification = JSON.parse(readFileSync(resolve('tests/fixtures/old-faithful-pilot-a/official-metadata-verification.json'), 'utf8'));
    expect(verification).toMatchObject({
      schemaVersion: 'OLD_FAITHFUL_OFFICIAL_METADATA_VERIFICATION_1',
      allChecksPass: true,
      networkBodyBytes: 4_315_301,
      networkCapBytes: 50 * 1024 * 1024,
      carRequestMethod: 'HEAD',
      carPayloadBytesRead: 0,
      inventory: {
        totalEntries: 431_388,
        outsideCanonicalRange: [422_495_999],
        canonicalRangeEntries: 431_387,
        canonicalRangeAbsentSlots: 613,
        pilotRangeEntries: 1_000,
      },
    });
  });

  it('durably documents the quant matrix and Phase 7 fail-closed boundary', () => {
    const quant = readFileSync(resolve('docs/research/OLD_FAITHFUL_QUANT_DATA_SPEC.md'), 'utf8');
    const phase7 = readFileSync(resolve('docs/PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md'), 'utf8');
    for (const required of [
      'DIRECT_OF1', 'DETERMINISTICALLY_DERIVABLE_FROM_OF1', 'REQUIRES_HISTORICAL_ACCOUNT_STATE',
      'REQUIRES_PROSPECTIVE_LIVE_CAPTURE', 'REQUIRES_POINT_IN_TIME_MARKET_SOURCE',
      'INFERRED_NOT_FACT', 'NOT_RELIABLY_AVAILABLE', 'ONCHAIN_ORACLE_OBSERVED',
      'OFFCHAIN_PROVIDER_OBSERVED', 'CROSS_RATE_DERIVED', 'JITO_TIP_TRANSFER_OBSERVED',
      'JITO_BUNDLE_PROVEN', 'Bronze', 'Silver', 'Gold', 'Pilot A', 'Pilot B',
    ]) expect(quant).toContain(required);
    for (const required of [
      'CANDIDATE_UNAPPROVED', 'HOLD_UNPROVEN_ACTIVATION', 'approved: false',
      'researchReady: false', 'pilotEligible: false', '[422506000, 422507000)',
      'geen CAR-', 'geen bandwidth-cap-preflight',
    ]) expect(phase7).toContain(required);
  });

  it('returns QUARANTINED instead of throwing when selectionNonClaims is deleted and hashes are rebound', () => {
    const candidate = readinessPackage();
    delete candidate.pilotRange.selectionNonClaims;
    rebindHashes(candidate);
    expect(() => evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).not.toThrow();
    expect(evaluateOldFaithfulPilotAReadiness(sourceManifest(), candidate)).toMatchObject({
      status: 'QUARANTINED', reasons: ['INVALID_PACKAGE'], hashBindingsValid: false,
    });
  });

  it.each([
    'README.md',
    'docs/CURRENT_STATE.md',
    'docs/HANDOFF.md',
    'docs/ARCHITECTURE.md',
    'docs/KNOWN_ISSUES.md',
    'docs/PUMP_OFFLINE_RESEARCH.md',
  ])('keeps %s aligned with the Phase 7 candidate HOLD', (path) => {
    const text = readFileSync(resolve(path), 'utf8');
    expect(text).toContain('PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md');
    expect(text).toContain('HOLD_UNPROVEN_ACTIVATION');
    expect(text).toContain('pilotEligible: false');
  });

  it('loads the tracked manifest and plan as a permanently unapproved HOLD package', () => {
    const manifest = JSON.parse(readFileSync(resolve('docs/research/OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST.json'), 'utf8'));
    const candidate = JSON.parse(readFileSync(resolve('docs/research/OLD_FAITHFUL_PILOT_A_PLAN.json'), 'utf8'));
    expect(evaluateOldFaithfulPilotAReadiness(manifest, candidate)).toMatchObject({
      status: 'HOLD_UNPROVEN_ACTIVATION',
      approved: false,
      pilotEligible: false,
      researchReady: false,
      reasons: ['NO_PROVEN_REGISTRY_ENTRY_FOR_CANDIDATE_RANGE'],
    });
  });
});
