import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const PLAN_PATH = 'docs/research/B4_ENGINEERING_VALIDATION_LEASE_PLAN.json';
const DOC_PATH = 'docs/research/B4_ENGINEERING_VALIDATION_LEASE_PLAN.md';
const MIB = 1024 * 1024;

describe('B4 engineering-validation lease plan draft', () => {
  const plan = JSON.parse(readFileSync(PLAN_PATH, 'utf8'));
  const doc = readFileSync(DOC_PATH, 'utf8');
  const planner = readFileSync('rust/of1-range-recorder/src/lib.rs', 'utf8');
  const store = readFileSync('rust/of1-range-recorder/src/durable.rs', 'utf8');

  it('stays non-executable, unapproved and offline', () => {
    expect(plan.schemaVersion).toBe('B4_ENGINEERING_VALIDATION_LEASE_PLAN_2');
    expect(plan.approved).toBe(false);
    expect(plan.networkEnabled).toBe(false);
    expect(plan.readyToRun).toBe(false);
    expect(plan.executablePlan).toBe(false);
    expect(plan.creditSpendAuthorized).toBe(false);
    expect(plan.cost_confirmation).toBe('NOT_CONFIRMED');
    expect(plan.operator).toBeNull();
    expect(plan.approved_at).toBeNull();
    expect(plan.approved_by).toBeNull();
    expect(plan.purpose).toBe('ENGINEERING_VALIDATION_ONLY');
    expect(plan.evidenceLimits).toMatchObject({
      currentImplementation: 'Fixture',
      b4ProjectEvidence: 'Unproven',
      authenticSourceObservation: 'UNAVAILABLE',
      carCidVerification: 'UNAVAILABLE',
      rootToSlotMembership: 'UNAVAILABLE',
    });
  });

  it('permits only the proposed official fixed-host inventory, never overrides or fallback', () => {
    expect(plan.host).toBe('files.old-faithful.net');
    expect(plan.port).toBe(443);
    expect(plan.hostAllowlist).toEqual(['files.old-faithful.net']);
    for (const field of [
      'redirectsAllowed', 'mirrorsAllowed', 'fallbackHostAllowed', 'alternateProtocolAllowed',
      'proxyAllowed', 's3Allowed', 'hostedGrpcAllowed', 'callerSuppliedUrlAllowed',
      'endpointEnvironmentAllowed', 'legacyIndexFallbackAllowed',
    ]) expect(plan[field], field).toBe(false);
    expect(plan.stages.metadata.inventory.map((item: { method: string; path: string }) => [item.method, item.path])).toEqual([
      ['GET', '/978/epoch-978-slot-ranges.raw'],
      ['GET', '/978/epoch-978.sha256'],
      ['GET', '/978/epoch-978.cid'],
      ['HEAD', '/978/epoch-978.car'],
    ]);
    expect(plan.stages.metadata.failurePolicy).toContain('No legacy index');
    const inventoryRows = doc.split('\n').filter((line) => /^\| (GET|HEAD) \|/.test(line));
    expect(inventoryRows).toHaveLength(plan.stages.metadata.inventory.length);
    plan.stages.metadata.inventory.forEach((item: { method: string; path: string }, index: number) => {
      expect(inventoryRows[index]).toContain(`| ${item.method} | \`${item.path}\` |`);
    });
  });

  it('records a 128-slot epoch-978 window and refuses the old provisional example', () => {
    expect(plan.epoch).toBe(978);
    expect(plan.slot_range).toMatchObject({
      startInclusive: 422_496_000,
      endExclusive: 422_496_128,
      requestedSlots: 128,
      objectKind: 'SLOT_WINDOW',
      selectionPolicy: 'SOURCE_AND_ENGINEERING_PROPERTIES_ONLY',
    });
    expect(plan.slot_range.startInclusive).toBe(978 * 432_000);
    expect(plan.slot_range.rejectedProvisionalExample).toEqual({
      startInclusive: 422_506_000,
      endExclusive: 422_506_128,
      reason: 'Must not be fetched because it appeared in earlier docs without origin/class.',
    });
    expect(plan.objectIdentities.epochCarSha256).toBe('UNAVAILABLE');
  });

  it('preserves every old proposed cap without approving or allocating it', () => {
    expect(plan.budgetStatus).toBe('UNVALIDATED_NOT_ALLOCATED');
    expect(plan.budgetMetric).toBe('response_entity_bytes');
    expect(plan.budget).toEqual({
      maxRequests: 16,
      warningRequests: 13,
      requestRetries: 2,
      concurrency: 1,
      maxResponseEntityBytes: 16 * MIB,
      warningResponseEntityBytes: 12 * MIB,
      maxResponseBytesTotal: 128 * MIB,
      warningResponseBytesTotal: 100 * MIB,
      maxDiskBytes: 256 * MIB,
      warningDiskBytes: 200 * MIB,
      requiredFreeDiskBytes: 512 * MIB,
      maxMemoryBytes: 512 * MIB,
      warningMemoryBytes: 400 * MIB,
      maxRuntimeMs: 30 * 60 * 1000,
      warningRuntimeMs: 24 * 60 * 1000,
      responseTimeoutMs: 30_000,
    });
    expect(plan.hard_stop.join(' ')).toMatch(/Do not auto-expand/i);
  });

  it('requires separate explicit metadata and payload approval with no fabricated stage allocations', () => {
    expect(plan.approvalModel).toBe('SEPARATE_METADATA_GO_AND_PAYLOAD_GO_AFTER_IMPLEMENTATION_REVIEW');
    for (const stage of [plan.stages.metadata, plan.stages.payload]) {
      expect(stage.approved).toBe(false);
      expect(stage.networkEnabled).toBe(false);
      for (const field of ['approvalId', 'approvedPlanSha256', 'operator', 'approvedAt', 'allocatedCaps', 'deadline']) {
        expect(stage[field], field).toBeNull();
      }
    }
    expect(plan.stages.metadata.receiptSha256).toBeNull();
    expect(plan.stages.payload.boundMetadataReceiptSha256).toBeNull();
    expect(plan.stages.payload.rangePlanSha256).toBeNull();
    expect(plan.stages.payload.requiredBeforeGo).toContain('ACCEPTED_METADATA_RECEIPT_AND_EXACT_SOURCE_INDEX_OBJECT_IDENTITIES');
    expect(plan.stages.payload.requiredBeforeGo).toContain('PROVEN_AGGREGATE_REQUEST_BYTE_DISK_RUNTIME_FEASIBILITY');
  });

  it('derives the exact structural index size from the pinned source contract, not authentic observations', () => {
    const source = JSON.parse(readFileSync(plan.budgetBasis.sourceReceipt, 'utf8'));
    expect(source.commit).toBe(plan.jetstreamerCandidateSha);
    expect(source.evidence).toBe('STRUCTURAL_SOURCE_ONLY');
    expect(source.authentic_chain_bytes).toBe(false);
    expect(planner).toMatch(/SLOTS_PER_EPOCH: u64 = 432_000;/);
    expect(planner).toMatch(/RECORD_BYTES: u64 = 12;/);
    expect(plan.budgetBasis.indexBytes).toBe(plan.budgetBasis.indexRecords * plan.budgetBasis.indexRecordBytes);
    expect(plan.budgetBasis.indexBytes).toBe(5_184_000);
    expect(plan.stages.metadata.inventory[0]).toMatchObject({
      maxResponseEntityBytes: 5_184_000,
      expectedResponseEntityBytes: 5_184_000,
      sizeEvidence: 'STRUCTURAL_SOURCE_ONLY_432000_RECORDS_TIMES_12_BYTES',
    });
  });

  it('counts metadata HEAD as an attempt but not CAR entity bytes and leaves sidecar sizes unmeasured', () => {
    const [index, sha, cid, head] = plan.stages.metadata.inventory;
    for (const item of [index, sha, cid]) expect(item.expectedStatus).toBe(200);
    for (const sidecar of [sha, cid]) {
      expect(sidecar.maxResponseEntityBytes).toBe(4096);
      expect(sidecar.expectedResponseEntityBytes).toBeNull();
      expect(sidecar.sizeEvidence).toBe('UNMEASURED_PROPOSED_CAP');
    }
    expect(head).toMatchObject({ method: 'HEAD', maxResponseEntityBytes: 0, expectedResponseEntityBytes: 0 });
    expect(plan.stages.metadata.headRule).toContain('one durable attempt');
    expect(plan.stages.metadata.headRule).toContain('not transferred entity bytes');
    expect(plan.objectIdentities.epochCarSizeBytes).toBeNull();
    expect(plan.objectIdentities.sidecarMeaning).toContain('cannot recompute the entire epoch CAR SHA-256');
  });

  it('computes metadata retry allowances and subtracts them from the aggregate candidate exactly once', () => {
    const operations = plan.stages.metadata.inventory;
    const initialBytes = operations.reduce((sum: number, operation: { maxResponseEntityBytes: number }) => sum + operation.maxResponseEntityBytes, 0);
    const attempts = plan.budget.requestRetries + 1;
    const basis = plan.budgetBasis;
    expect(basis.metadataInitialRequests).toBe(operations.length);
    expect(basis.metadataInitialEntityAllowance).toBe(initialBytes);
    expect(basis.metadataMaximumAttemptsPerOperation).toBe(attempts);
    expect(basis.metadataRetryEnvelopeRequests).toBe(operations.length * attempts);
    expect(basis.metadataRetryEnvelopeEntityAllowance).toBe(initialBytes * attempts);
    expect(basis.metadataRetryEnvelopeRequests).toBe(12);
    expect(basis.metadataRetryEnvelopeEntityAllowance).toBe(15_576_576);
    expect(basis.remainingAggregateRequestsAfterMetadataEnvelope).toBe(plan.budget.maxRequests - operations.length * attempts);
    expect(basis.remainingAggregateEntityAllowanceAfterMetadataEnvelope).toBe(plan.budget.maxResponseBytesTotal - initialBytes * attempts);
    expect(basis.remainingAggregateRequestsAfterMetadataEnvelope).toBe(4);
    expect(basis.remainingAggregateEntityAllowanceAfterMetadataEnvelope).toBe(118_641_152);
    expect(plan.budgetScope).toContain('METADATA_PLUS_PAYLOAD_PLUS_RETRIES');
    expect(basis.aggregateRule).toContain('no stage reset or refund');
  });

  it('exposes request-cap infeasibility for 128 nonempty slots without inventing actual coverage', () => {
    const example = plan.budgetBasis.all128SlotsNonemptyIllustration;
    expect(example.status).toBe('CONDITIONAL_ARITHMETIC_NOT_OBSERVED_COVERAGE');
    expect(example.minimumPayloadInitialRequests).toBe(plan.slot_range.requestedSlots);
    expect(example.minimumFullRetryEnvelopeRequests).toBe(plan.slot_range.requestedSlots * (plan.budget.requestRetries + 1));
    expect(example.minimumFullRetryEnvelopeRequests).toBe(384);
    expect(example.minimumPayloadInitialRequests).toBeGreaterThan(plan.budget.maxRequests);
    expect(example.fitsRetained16RequestCandidate).toBe(false);
    expect(plan.budgetBasis.full16MiBAttemptsWithin128MiBAggregateIgnoringMetadata).toBe(plan.budget.maxResponseBytesTotal / plan.budget.maxResponseEntityBytes);
    expect(plan.budgetBasis.payloadRequestFormula).toContain('Q = sum_i ceil(L_i / C)');
    expect(plan.budgetBasis.payloadEntityFormula).toContain('3*Q');
    expect(plan.budgetBasis.payloadEntityFormula).toContain('3*S');
    for (const field of ['actualPlannedRequests', 'actualPlannedResponseEntityBytes', 'actualAbsentIndexRecords', 'additionalProofRequests']) {
      expect(plan.stages.payload[field], field).toBeNull();
    }
  });

  it('records the retained-chunk disk lower bound without claiming a safe upper bound', () => {
    const example = plan.budgetBasis.diskIllustration;
    expect(store).toMatch(/MAX_STREAM_CHUNK_BYTES: usize = 8192;/);
    expect(store).toMatch(/const BLOCK: u64 = 4096;/);
    const chunkCount = example.payloadBytes / example.fullChunkBytes;
    const minimumPending = chunkCount * (example.fullChunkBytes + example.minimumReceiptChargeBytes + example.minimumChunkDirectoryChargeBytes);
    expect(example.minimumPendingChunkChargeBytes).toBe(minimumPending);
    expect(example.finalRawCopyBytes).toBe(example.payloadBytes);
    expect(example.minimumCombinedBytesBeforeOtherMetadata).toBe(minimumPending + example.finalRawCopyBytes);
    expect(example.minimumCombinedBytesBeforeOtherMetadata).toBe(384 * MIB);
    expect(example.minimumCombinedBytesBeforeOtherMetadata).toBeGreaterThan(plan.budget.maxDiskBytes);
    expect(example.fitsRetained256MiBDiskCandidate).toBe(false);
    expect(example.status).toBe('LOWER_BOUND_ILLUSTRATION_NOT_A_SAFE_UPPER_BOUND');
    expect(example.limits).toContain('Short reads');
    expect(plan.budgetBasis.enforcementLimits.join(' ')).toContain('metadata artifacts outside the payload run directory');
  });

  it('keeps unmeasured memory, free-space, warning, throughput and runtime guarantees explicit', () => {
    expect(plan.budgetBasis.unmeasured).toEqual(expect.arrayContaining([
      'ACTUAL_SIDECAR_LENGTHS_AND_FORMATS',
      'PAYLOAD_RANGE_COUNT_AND_ENTITY_BYTES',
      'DISK_HIGH_WATER_WITH_STREAM_FRAGMENTATION_AND_RETRIES',
      'RSS_PEAK',
      'SOURCE_THROUGHPUT_AND_LOCAL_FSYNC_RUNTIME',
      'CURRENT_COST_AND_AVAILABILITY',
    ]));
    expect(plan.budgetBasis.enforcementLimits.join(' ')).toContain('Memory cap, free-space prerequisite and warning thresholds');
    expect(plan.budgetBasis.enforcementLimits.join(' ')).toContain('excludes headers, framing probes and TCP/TLS overhead');
    expect(plan.budgetBasis.runtimeRule).toContain('not allocated twice');
    expect(plan.budgetBasis.runtimeRule).toContain('Runtime allocation remains null');
    expect(plan.resumePolicy).toContain('No restart refund, deadline reset');
  });

  it('separates engineering outcomes from missing domain data and market-edge evaluation', () => {
    expect(plan.outcomes.engineeringTerminalStates).toEqual([
      'ENGINEERING_PASS', 'ENGINEERING_FAILURE', 'ABORTED_BUDGET', 'QUARANTINED',
    ]);
    expect(plan.outcomes.engineeringTerminalStates).not.toContain('FALSIFIED');
    expect(plan.outcomes.engineeringTerminalStates).not.toContain('INSUFFICIENT_SAMPLE');
    expect(plan.outcomes.dataSufficiency).toBe('UNAVAILABLE_NOT_DECODED_IN_B4');
    expect(plan.evidenceLimits.domainCounts).toBe('UNAVAILABLE_NOT_DECODED_IN_B4');
    expect(plan.outcomes.laterValidDecodeWithoutRequiredPumpEvidence).toBe('INSUFFICIENT_DATA_FOR_PUMP_MECHANICS');
    expect(plan.outcomes.edgeEvaluation).toBe('NOT_EVALUATED_ENGINEERING_SLICE');
    expect(plan.outcomes.edgeFalsificationRequires).toContain('valid, sufficient, outcome-independent, point-in-time and appropriately costed');
    expect(plan.coveragePolicy).toContain('Index-reported absence is not proven skipped-slot or chain coverage');
  });

  it('does not mistake draft flags or the loopback client for a completed live implementation', () => {
    expect(planner).toContain('plan.mode != "OFFLINE_FIXTURE"');
    expect(planner).toContain('|| plan.approved');
    expect(planner).toContain('|| plan.network_enabled');
    expect(plan.budgetBasis.enforcementLimits.join(' ')).toContain('changing this JSON cannot authorize execution');
    expect(plan.stages.payload.requiredBeforeGo).toContain('REVIEWED_OFFICIAL_HTTPS_METADATA_AND_RANGE_IMPLEMENTATION');
    expect(plan.stages.payload.requiredBeforeGo).toContain('REVIEWED_CAR_CID_VERIFICATION_AND_EXPLICIT_MEMBERSHIP_LIMITS');
  });

  it('keeps the prose draft from authorizing a call', () => {
    expect(doc).toContain('`approved: false`');
    expect(doc).toContain('`networkEnabled: false`');
    expect(doc).toContain('[422496000, 422496128)');
    expect(doc).not.toMatch(/(?:Pilot A|bandwidth(?:-cap)? preflight)\s*:\s*(?:authorized|approved|GO)\b/i);
  });
});
