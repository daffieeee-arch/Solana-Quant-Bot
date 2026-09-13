/** Read-only Rust projection. Formatting is local; acquisition/domain semantics are not. */
export interface MonitorSnapshot {
  schema_version: 'OF1_MONITOR_1';
  id: string;
  session_id: string;
  sequence: number;
  updated_at_ms: number;
  kind: 'AUTHENTIC_METADATA' | 'AUTHENTIC_PAYLOAD' | 'LOCAL_SIMULATION';
  mode: 'LIVE' | 'RECORDED';
  stage: 'PREPARING' | 'METADATA' | 'DOWNLOADING' | 'VERIFYING' | 'PUBLISHING' | 'COMPLETE' | 'STOPPED';
  label: string;
  dataset_root: string;
  source: string;
  epoch: number;
  selected_slots: null | { start: number; end_exclusive: number };
  started_at_ms: number;
  completed_at_ms: number | null;
  elapsed_ms: number;
  selection: {
    operations_total: number; operations_published: number; planned_bytes: number | null;
    received_selection_bytes: number; published_bytes: number; verified_bytes: number;
  };
  traffic: {
    received_bytes: number;
    received_basis: 'PROCESS_OBSERVED' | 'DURABLE_LOWER_BOUND' | 'RECEIPTS_ONLY';
    reserved_bytes: number; attempts: number; retries: number;
    speed_bps: number | null; download_eta_ms: number | null;
    eta_scope: 'SELECTION' | 'CURRENT_OPERATION' | null;
    speed_samples: Array<{ elapsed_ms: number; bps: number }>;
  };
  rate_limit?: {
    policy: { unit: 'RESPONSE_ENTITY_BYTES'; bytes_per_second: 87_500_000;
      burst_bytes: 65_536; concurrency: 1; scope: 'SAME_USER_OFFICIAL_OF1_ALL_RUNS' };
    waiting: boolean | null;
    process_wait_ns: number | null;
  };
  clock_context?: {
    policy: { schema: 'OF1_BOOT_CLOCK_POLICY_1'; version: 1; initialization_window_ms: 600_000; approval_validity_ms: 1_200_000 };
    boot_id: string; started_at_boot_ms: number; deadline_boot_ms: number;
    observed_boot_ms: number | null;
    runtime_status: 'SAME_BOOT' | 'UNAVAILABLE_CLOCK' | 'UNAVAILABLE_BOOT_MISMATCH' | 'UNAVAILABLE_BOOT_ROLLBACK';
  };
  storage: { used_bytes: number; available_bytes: number; cap_bytes: number };
  budgets: {
    attempts_remaining: number; entity_bytes_remaining: number; stage_attempts_remaining: number;
    stage_entity_bytes_remaining: number; runtime_remaining_ms: number | null;
  };
  operations: Array<{
    sequence: number; method: string; path: string; range: string | null;
    state: 'PENDING' | 'RESERVED' | 'DOWNLOADING' | 'VERIFYING' | 'PUBLISHING' | 'PUBLISHED' | 'FAILED';
    expected_bytes: number | null; received_bytes: number | null; published_bytes: number;
    attempts: number; status_code: number | null; error: string | null;
  }>;
  integrity: { receipts: string; car: string; root_to_slot: 'UNAVAILABLE' };
  domain_counts: 'UNAVAILABLE_NOT_DECODED_IN_B4';
  artifacts: Array<{ id: string; label: string; path: string; sha256: string }>;
  errors: string[];
  dropped_samples: number;
}

export type MonitorRun = { id: string; state: 'READY'; snapshot: MonitorSnapshot }
  | { id: string; state: 'UNAVAILABLE'; reason: string };
export interface MonitorResponse {
  schema_version: 'OF1_MONITOR_HTTP_1'; read_at_unix_ms: number; runs: MonitorRun[];
}

/** External offline Rust outcome; never inferred from the publication counter. */
export interface OfflineVerificationReport {
  schema: 'OF1_OFFLINE_VERIFICATION_1';
  run_id: string;
  dataset_root: string;
  verifier: { name: 'of1-verify-recorded'; version: '1'; binary_sha256: string; source_sha256: string };
  bindings: {
    manifest_sha256: string; payload_manifest_sha256: string | null; aggregate_sha256: string;
    prepared_payload_sha256: string | null; metadata_receipt_sha256: string | null;
    receipts: Array<{ sequence: number; path: string; sha256: string; raw_sha256: string; raw_bytes: number }>;
  };
  stages: {
    capture: 'COMPLETE' | 'INCOMPLETE'; raw_receipts: 'VERIFIED';
    car_slot: 'VERIFIED' | 'QUARANTINED' | 'NOT_ACQUIRED' | 'INCOMPLETE'; domain_decoding: 'NOT_PERFORMED';
  };
  integrity: {
    error: string | null; root_to_slot_membership: 'UNAVAILABLE'; whole_car_sha256_verified: false;
    slots: Array<{
      slot: number;
      report: { selected_slot: number; captured_section_bytes: number; verified_nodes: number; verified_links: number;
        root_to_slot_membership: 'UNAVAILABLE'; domain_counts: 'UNAVAILABLE_NOT_DECODED_IN_B4' };
      archival_node_counts: { transaction: number; entry: number; block: number; rewards: number; dataframe: number };
    }>;
  };
  prior_failure: null | { error: string; binary_sha256: string; artifact_sha256: string;
    run_result_sha256: string; raw_sha256: string; status: 'HISTORICAL_FAILURE_PRESERVED' };
  evidence: 'RAW_ENGINEERING_CHECK_ONLY'; research_ready: false;
}
export type OfflineVerificationResponse = { state: 'READY'; report_sha256: string; report: OfflineVerificationReport }
  | { state: 'UNAVAILABLE'; reason: string };

const fail = (): never => { throw new Error('Ongeldig Rust-monitorcontract; waarden worden niet aangevuld.'); };
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function text(value: unknown): asserts value is string {
  if (typeof value !== 'string' || value.length > 4096) fail();
}
function integer(value: unknown): asserts value is number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) fail();
}
function rate(value: unknown): void {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) fail();
}
function choice(value: unknown, allowed: readonly string[]): void {
  if (typeof value !== 'string' || !allowed.includes(value)) fail();
}
function nullable(value: unknown, check: (input: unknown) => void): void {
  if (value !== null) check(value);
}
function array(value: unknown, max: number, check: (input: unknown) => void): void {
  if (!Array.isArray(value) || value.length > max) return fail();
  value.forEach(check);
}
function numbers(value: unknown, keys: string[]): Record<string, unknown> {
  const row = object(value); keys.forEach(key => integer(row[key])); return row;
}
function safeId(value: unknown): void {
  text(value); if (!/^[a-f0-9]{64}$/.test(value)) fail();
}

export function validateMonitorSnapshot(value: unknown): asserts value is MonitorSnapshot {
  const s = object(value);
  choice(s.schema_version, ['OF1_MONITOR_1']); safeId(s.id); text(s.session_id);
  ['sequence', 'updated_at_ms', 'epoch', 'started_at_ms', 'elapsed_ms', 'dropped_samples'].forEach(key => integer(s[key]));
  ['label', 'dataset_root', 'source'].forEach(key => text(s[key]));
  choice(s.kind, ['AUTHENTIC_METADATA', 'AUTHENTIC_PAYLOAD', 'LOCAL_SIMULATION']);
  choice(s.mode, ['LIVE', 'RECORDED']);
  choice(s.stage, ['PREPARING', 'METADATA', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING', 'COMPLETE', 'STOPPED']);
  nullable(s.completed_at_ms, integer);
  nullable(s.selected_slots, value => {
    const slots = numbers(value, ['start', 'end_exclusive']);
    if ((slots.end_exclusive as number) < (slots.start as number)) fail();
  });
  const selection = numbers(s.selection, ['operations_total', 'operations_published', 'received_selection_bytes', 'published_bytes', 'verified_bytes']);
  nullable(selection.planned_bytes, integer);
  const traffic = numbers(s.traffic, ['received_bytes', 'reserved_bytes', 'attempts', 'retries']);
  choice(traffic.received_basis, ['PROCESS_OBSERVED', 'DURABLE_LOWER_BOUND', 'RECEIPTS_ONLY']);
  nullable(traffic.speed_bps, rate); nullable(traffic.download_eta_ms, integer);
  nullable(traffic.eta_scope, value => choice(value, ['SELECTION', 'CURRENT_OPERATION']));
  array(traffic.speed_samples, 64, value => { const row = object(value); integer(row.elapsed_ms); rate(row.bps); });
  if ('rate_limit' in s) {
    const limit = object(s.rate_limit);
    const policy = numbers(limit.policy, ['bytes_per_second', 'burst_bytes', 'concurrency']);
    if (Object.keys(limit).sort().join(',') !== 'policy,process_wait_ns,waiting'
      || Object.keys(policy).sort().join(',') !== 'burst_bytes,bytes_per_second,concurrency,scope,unit') fail();
    choice(policy.unit, ['RESPONSE_ENTITY_BYTES']); choice(policy.scope, ['SAME_USER_OFFICIAL_OF1_ALL_RUNS']);
    if (policy.bytes_per_second !== 87_500_000 || policy.burst_bytes !== 65_536 || policy.concurrency !== 1) fail();
    nullable(limit.waiting, value => { if (typeof value !== 'boolean') fail(); });
    nullable(limit.process_wait_ns, integer);
    if ((limit.waiting === null) !== (limit.process_wait_ns === null)
      || (s.mode === 'RECORDED' && limit.waiting !== null)
      || (limit.waiting === true && s.stage !== 'DOWNLOADING')) fail();
  }
  numbers(s.storage, ['used_bytes', 'available_bytes', 'cap_bytes']);
  const budgets = numbers(s.budgets, ['attempts_remaining', 'entity_bytes_remaining', 'stage_attempts_remaining', 'stage_entity_bytes_remaining']);
  nullable(budgets.runtime_remaining_ms, integer);
  if ('clock_context' in s) {
    const clock = numbers(s.clock_context, ['started_at_boot_ms', 'deadline_boot_ms']);
    text(clock.boot_id); if (clock.boot_id === '' || String(clock.boot_id).length > 128) fail();
    nullable(clock.observed_boot_ms, integer);
    choice(clock.runtime_status, ['SAME_BOOT', 'UNAVAILABLE_CLOCK', 'UNAVAILABLE_BOOT_MISMATCH', 'UNAVAILABLE_BOOT_ROLLBACK']);
    const policy = numbers(clock.policy, ['version', 'initialization_window_ms', 'approval_validity_ms']);
    choice(policy.schema, ['OF1_BOOT_CLOCK_POLICY_1']);
    if (policy.version !== 1 || policy.initialization_window_ms !== 600_000 || policy.approval_validity_ms !== 1_200_000
      || Object.keys(clock).sort().join(',') !== 'boot_id,deadline_boot_ms,observed_boot_ms,policy,runtime_status,started_at_boot_ms'
      || Object.keys(policy).sort().join(',') !== 'approval_validity_ms,initialization_window_ms,schema,version'
      || Number(clock.deadline_boot_ms) < Number(clock.started_at_boot_ms)
      || (clock.observed_boot_ms !== null && Number(clock.observed_boot_ms) < Number(clock.started_at_boot_ms))
      || (clock.runtime_status === 'SAME_BOOT') !== (budgets.runtime_remaining_ms !== null)
      || (clock.runtime_status === 'SAME_BOOT' && (clock.observed_boot_ms === null
        || budgets.runtime_remaining_ms !== Math.max(0, Number(clock.deadline_boot_ms) - Number(clock.observed_boot_ms))))) fail();
  } else if (budgets.runtime_remaining_ms === null) fail();
  array(s.operations, 32, value => {
    const row = numbers(value, ['sequence', 'published_bytes', 'attempts']);
    nullable(row.received_bytes, integer);
    text(row.method); text(row.path); nullable(row.range, text); nullable(row.expected_bytes, integer);
    nullable(row.status_code, integer); nullable(row.error, text);
    choice(row.state, ['PENDING', 'RESERVED', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING', 'PUBLISHED', 'FAILED']);
  });
  const integrity = object(s.integrity); text(integrity.receipts); text(integrity.car);
  choice(integrity.root_to_slot, ['UNAVAILABLE']); choice(s.domain_counts, ['UNAVAILABLE_NOT_DECODED_IN_B4']);
  array(s.artifacts, 40, value => {
    const row = object(value); text(row.id); text(row.label); text(row.path); text(row.sha256);
    if (!/^[a-z0-9][a-z0-9-]{0,127}$/.test(row.id)) fail();
    if (!/^[0-9a-f]{64}$/.test(row.sha256)) fail();
  });
  array(s.errors, 16, text);
}

/** Order new snapshots by same-boot samples and producer sequence, never UTC. */
export function monitorSnapshotRegressed(old: MonitorSnapshot, fresh: MonitorSnapshot): boolean {
  if (!old.clock_context && !fresh.clock_context) {
    return fresh.updated_at_ms < old.updated_at_ms
      || (fresh.session_id === old.session_id && fresh.sequence < old.sequence);
  }
  if (!old.clock_context || !fresh.clock_context || old.clock_context.boot_id !== fresh.clock_context.boot_id) return true;
  if (fresh.session_id === old.session_id) {
    if (fresh.sequence < old.sequence) return true;
    return old.clock_context.observed_boot_ms !== null && fresh.clock_context.observed_boot_ms !== null
      && fresh.clock_context.observed_boot_ms < old.clock_context.observed_boot_ms;
  }
  return old.clock_context.observed_boot_ms === null || fresh.clock_context.observed_boot_ms === null
    || fresh.clock_context.observed_boot_ms <= old.clock_context.observed_boot_ms;
}

export function parseMonitorResponse(value: unknown): MonitorResponse {
  const response = object(value); choice(response.schema_version, ['OF1_MONITOR_HTTP_1']); integer(response.read_at_unix_ms);
  const ids = new Set<string>();
  array(response.runs, 16, value => {
    const row = object(value); safeId(row.id); const id = row.id as string;
    if (ids.has(id)) fail(); ids.add(id);
    choice(row.state, ['READY', 'UNAVAILABLE']);
    if (row.state === 'READY') { validateMonitorSnapshot(row.snapshot); if (row.snapshot.id !== id) fail(); }
    else text(row.reason);
  });
  return value as MonitorResponse;
}

export function parseOfflineVerification(value: unknown, runId: string): OfflineVerificationResponse {
  const response = object(value); choice(response.state, ['READY', 'UNAVAILABLE']);
  if (response.state === 'UNAVAILABLE') { text(response.reason); return value as OfflineVerificationResponse; }
  safeId(response.report_sha256);
  const report = object(response.report);
  choice(report.schema, ['OF1_OFFLINE_VERIFICATION_1']); safeId(report.run_id);
  if (report.run_id !== runId) fail();
  text(report.dataset_root);
  const verifier = object(report.verifier);
  choice(verifier.name, ['of1-verify-recorded']); choice(verifier.version, ['1']);
  safeId(verifier.binary_sha256); safeId(verifier.source_sha256);
  const binding = object(report.bindings);
  safeId(binding.manifest_sha256); safeId(binding.aggregate_sha256);
  ['payload_manifest_sha256', 'prepared_payload_sha256', 'metadata_receipt_sha256'].forEach(key => nullable(binding[key], safeId));
  let previous = -1;
  array(binding.receipts, 32, value => {
    const receipt = numbers(value, ['sequence', 'raw_bytes']);
    text(receipt.path); safeId(receipt.sha256); safeId(receipt.raw_sha256);
    if (Number(receipt.sequence) <= previous || receipt.path !== `published/${String(receipt.sequence).padStart(10, '0')}/receipt.json`) fail();
    previous = Number(receipt.sequence);
  });
  const stages = object(report.stages);
  choice(stages.capture, ['COMPLETE', 'INCOMPLETE']); choice(stages.raw_receipts, ['VERIFIED']);
  choice(stages.car_slot, ['VERIFIED', 'QUARANTINED', 'NOT_ACQUIRED', 'INCOMPLETE']); choice(stages.domain_decoding, ['NOT_PERFORMED']);
  const integrity = object(report.integrity); nullable(integrity.error, text);
  choice(integrity.root_to_slot_membership, ['UNAVAILABLE']);
  if (integrity.whole_car_sha256_verified !== false || report.research_ready !== false) fail();
  let previousSlot = -1;
  array(integrity.slots, 32, value => {
    const slot = numbers(value, ['slot']);
    const inner = numbers(slot.report, ['selected_slot', 'captured_section_bytes', 'verified_nodes', 'verified_links']);
    if (slot.slot !== inner.selected_slot || Number(slot.slot) <= previousSlot) fail();
    previousSlot = Number(slot.slot);
    choice(inner.root_to_slot_membership, ['UNAVAILABLE']); choice(inner.domain_counts, ['UNAVAILABLE_NOT_DECODED_IN_B4']);
    const counts = numbers(slot.archival_node_counts, ['transaction', 'entry', 'block', 'rewards', 'dataframe']);
    const total = ['transaction', 'entry', 'block', 'rewards', 'dataframe'].reduce((sum, key) => sum + Number(counts[key]), 0);
    integer(total);
    if (total !== inner.verified_nodes || counts.block !== 1) fail();
  });
  if (stages.car_slot === 'VERIFIED' && (stages.capture !== 'COMPLETE' || integrity.error !== null
      || (integrity.slots as unknown[]).length === 0 || binding.payload_manifest_sha256 === null)
    || stages.car_slot === 'QUARANTINED' && (typeof integrity.error !== 'string' || integrity.error.length === 0
      || (integrity.slots as unknown[]).length !== 0 || binding.payload_manifest_sha256 === null)
    || stages.car_slot === 'NOT_ACQUIRED' && ((integrity.slots as unknown[]).length !== 0 || integrity.error !== null
      || binding.payload_manifest_sha256 !== null || binding.prepared_payload_sha256 !== null || binding.metadata_receipt_sha256 !== null)) fail();
  nullable(report.prior_failure, value => {
    const prior = object(value); text(prior.error); choice(prior.status, ['HISTORICAL_FAILURE_PRESERVED']);
    ['binary_sha256', 'artifact_sha256', 'run_result_sha256', 'raw_sha256'].forEach(key => safeId(prior[key]));
  });
  choice(report.evidence, ['RAW_ENGINEERING_CHECK_ONLY']);
  return value as OfflineVerificationResponse;
}
