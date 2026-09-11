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
  storage: { used_bytes: number; available_bytes: number; cap_bytes: number };
  budgets: {
    attempts_remaining: number; entity_bytes_remaining: number; stage_attempts_remaining: number;
    stage_entity_bytes_remaining: number; runtime_remaining_ms: number;
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
  numbers(s.storage, ['used_bytes', 'available_bytes', 'cap_bytes']);
  numbers(s.budgets, ['attempts_remaining', 'entity_bytes_remaining', 'stage_attempts_remaining', 'stage_entity_bytes_remaining', 'runtime_remaining_ms']);
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
