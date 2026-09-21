import { constants } from 'node:fs';
import { open, opendir, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { isAbsolute, join, normalize, resolve } from 'node:path';

export const MAX_SNAPSHOT_BYTES = 64 * 1024;
export const MAX_RUNS = 16;
const MAX_ARTIFACT_BYTES = 1024 * 1024;
const SNAPSHOT_FILE = /^latest-([a-f0-9]{64})\.json$/;
const ARTIFACT_PATH = /^(?:run\.json|payload\.json|published\/[0-9]{10}\/receipt\.json)$/;

export class MonitorReadError extends Error {
  constructor(public readonly reason: string) { super(reason); }
}

export type MonitorRun = Readonly<{
  id: string;
  state: 'READY' | 'UNAVAILABLE';
  snapshot?: Record<string, unknown>;
  reason?: string;
}>;

/** A transport reader only. Rust owns all measurements, identities and accounting. */
export async function readBoundedFile(path: string, limit: number): Promise<Buffer> {
  let file;
  try {
    file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const stat = await file.stat();
    if (!stat.isFile()) throw new MonitorReadError('NOT_A_REGULAR_FILE');
    if (stat.size > limit) throw new MonitorReadError('FILE_TOO_LARGE');
    const bytes = Buffer.alloc(limit + 1);
    let length = 0;
    while (length <= limit) {
      const { bytesRead } = await file.read(bytes, length, bytes.length - length, length);
      if (!bytesRead) return bytes.subarray(0, length);
      length += bytesRead;
    }
    throw new MonitorReadError('FILE_TOO_LARGE');
  } catch (error) {
    if (error instanceof MonitorReadError) throw error;
    throw new MonitorReadError('FILE_UNAVAILABLE');
  } finally { await file?.close(); }
}

const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown, limit = 4096): value is string => typeof value === 'string' && value.length <= limit;
const integer = (value: unknown): boolean => Number.isSafeInteger(value) && Number(value) >= 0;
const integers = (value: unknown, keys: string[]): value is Record<string, unknown> => object(value) && keys.every((key) => integer(value[key]));
const nullableInteger = (value: unknown): boolean => value === null || integer(value);
const nullableText = (value: unknown): boolean => value === null || text(value);
const sha256 = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const nullableHash = (value: unknown): boolean => value === null || sha256(value);

function boundedTree(value: unknown, depth = 0): boolean {
  if (depth > 8) return false;
  if (value === null || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value) && value <= Number.MAX_SAFE_INTEGER && value >= 0;
  if (typeof value === 'string') return value.length <= 4096;
  if (Array.isArray(value)) return value.length <= 64 && value.every((item) => boundedTree(item, depth + 1));
  return object(value) && Object.keys(value).length <= 64
    && Object.entries(value).every(([key, item]) => key.length <= 80 && boundedTree(item, depth + 1));
}

function validClockContext(value: unknown, remaining: unknown): boolean {
  if (!integers(value, ['started_at_boot_ms', 'deadline_boot_ms'])
    || !text(value.boot_id, 128) || value.boot_id.length === 0 || !nullableInteger(value.observed_boot_ms)
    || !['SAME_BOOT', 'UNAVAILABLE_CLOCK', 'UNAVAILABLE_BOOT_MISMATCH', 'UNAVAILABLE_BOOT_ROLLBACK'].includes(String(value.runtime_status))
    || !integers(value.policy, ['version', 'initialization_window_ms', 'approval_validity_ms'])
    || value.policy.schema !== 'OF1_BOOT_CLOCK_POLICY_1' || value.policy.version !== 1
    || value.policy.initialization_window_ms !== 600_000 || value.policy.approval_validity_ms !== 1_200_000
    || Object.keys(value).sort().join(',') !== 'boot_id,deadline_boot_ms,observed_boot_ms,policy,runtime_status,started_at_boot_ms'
    || Object.keys(value.policy).sort().join(',') !== 'approval_validity_ms,initialization_window_ms,schema,version'
    || Number(value.deadline_boot_ms) < Number(value.started_at_boot_ms)
    || (value.observed_boot_ms !== null && Number(value.observed_boot_ms) < Number(value.started_at_boot_ms))) return false;
  return (value.runtime_status === 'SAME_BOOT') === (remaining !== null)
    && (value.runtime_status !== 'SAME_BOOT' || (value.observed_boot_ms !== null
      && remaining === Math.max(0, Number(value.deadline_boot_ms) - Number(value.observed_boot_ms))));
}

/** Validate the wire envelope, not reinterpret Rust's acquisition semantics. */
export function validateSnapshot(value: unknown, id: string): Record<string, unknown> {
  if (!object(value) || !boundedTree(value) || value.schema_version !== 'OF1_MONITOR_1'
    || value.id !== id || !text(value.session_id, 128)
    || !['AUTHENTIC_METADATA', 'AUTHENTIC_PAYLOAD', 'LOCAL_SIMULATION'].includes(String(value.kind))
    || !Number.isSafeInteger(value.sequence) || !Number.isSafeInteger(value.updated_at_ms)
    || !text(value.dataset_root) || !isAbsolute(value.dataset_root) || normalize(value.dataset_root) !== value.dataset_root
    || !Array.isArray(value.operations) || value.operations.length > 32
    || !Array.isArray(value.artifacts) || value.artifacts.length > 40) {
    throw new MonitorReadError('INVALID_RUST_SNAPSHOT');
  }
  if (!integers(value, ['epoch', 'started_at_ms', 'elapsed_ms', 'dropped_samples'])
    || !['LIVE', 'RECORDED'].includes(String(value.mode))
    || !['PREPARING', 'METADATA', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING', 'COMPLETE', 'STOPPED'].includes(String(value.stage))
    || !text(value.label) || !text(value.source) || !nullableInteger(value.completed_at_ms)
    || !(value.selected_slots === null || integers(value.selected_slots, ['start', 'end_exclusive']))
    || !integers(value.selection, ['operations_total', 'operations_published', 'received_selection_bytes', 'published_bytes', 'verified_bytes'])
    || !nullableInteger(value.selection.planned_bytes)
    || !integers(value.traffic, ['received_bytes', 'reserved_bytes', 'attempts', 'retries'])
    || !['PROCESS_OBSERVED', 'DURABLE_LOWER_BOUND', 'RECEIPTS_ONLY'].includes(String(value.traffic.received_basis))
    || !(value.traffic.speed_bps === null || typeof value.traffic.speed_bps === 'number')
    || !nullableInteger(value.traffic.download_eta_ms)
    || !(value.traffic.eta_scope === null || ['SELECTION', 'CURRENT_OPERATION'].includes(String(value.traffic.eta_scope)))
    || !Array.isArray(value.traffic.speed_samples) || value.traffic.speed_samples.length > 64
    || !value.traffic.speed_samples.every((sample) => object(sample) && integer(sample.elapsed_ms) && typeof sample.bps === 'number')
    || !integers(value.storage, ['used_bytes', 'available_bytes', 'cap_bytes'])
    || !integers(value.budgets, ['attempts_remaining', 'entity_bytes_remaining', 'stage_attempts_remaining', 'stage_entity_bytes_remaining'])
    || !nullableInteger(value.budgets.runtime_remaining_ms)
    || ('clock_context' in value ? !validClockContext(value.clock_context, value.budgets.runtime_remaining_ms) : value.budgets.runtime_remaining_ms === null)
    || !object(value.integrity) || !text(value.integrity.receipts) || !text(value.integrity.car)
    || value.integrity.root_to_slot !== 'UNAVAILABLE' || value.domain_counts !== 'UNAVAILABLE_NOT_DECODED_IN_B4'
    || !Array.isArray(value.errors) || value.errors.length > 16 || !value.errors.every((error) => text(error))) {
    throw new MonitorReadError('INVALID_RUST_SNAPSHOT');
  }
  for (const operation of value.operations) {
    if (!integers(operation, ['sequence', 'published_bytes', 'attempts']) || !nullableInteger(operation.received_bytes)
      || !text(operation.method) || !text(operation.path) || !nullableText(operation.range)
      || !nullableInteger(operation.expected_bytes) || !nullableInteger(operation.status_code) || !nullableText(operation.error)
      || !['PENDING', 'RESERVED', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING', 'PUBLISHED', 'FAILED'].includes(String(operation.state))) {
      throw new MonitorReadError('INVALID_RUST_SNAPSHOT');
    }
  }
  const artifactIds = new Set<string>();
  for (const artifact of value.artifacts) {
    if (!object(artifact) || !text(artifact.id, 80) || !/^[a-z0-9-]+$/.test(artifact.id)
      || !text(artifact.label) || artifactIds.has(artifact.id) || !text(artifact.path, 128)
      || !ARTIFACT_PATH.test(artifact.path) || !text(artifact.sha256, 64)
      || !/^[a-f0-9]{64}$/.test(artifact.sha256)) throw new MonitorReadError('INVALID_ARTIFACT_REFERENCE');
    artifactIds.add(artifact.id);
  }
  return value;
}

/** Validate an external Rust report's envelope. This does not decode CAR or run a verifier. */
function validateVerification(value: unknown, id: string): Record<string, unknown> {
  if (!object(value) || !boundedTree(value) || value.schema !== 'OF1_OFFLINE_VERIFICATION_1'
    || value.run_id !== id || !text(value.dataset_root)
    || !object(value.verifier) || value.verifier.name !== 'of1-verify-recorded' || value.verifier.version !== '1'
    || !sha256(value.verifier.binary_sha256) || !sha256(value.verifier.source_sha256)
    || !object(value.bindings) || !sha256(value.bindings.manifest_sha256) || !sha256(value.bindings.aggregate_sha256)
    || !nullableHash(value.bindings.payload_manifest_sha256) || !nullableHash(value.bindings.prepared_payload_sha256)
    || !nullableHash(value.bindings.metadata_receipt_sha256) || !Array.isArray(value.bindings.receipts) || value.bindings.receipts.length > 32
    || !object(value.stages) || !['COMPLETE', 'INCOMPLETE'].includes(String(value.stages.capture))
    || value.stages.raw_receipts !== 'VERIFIED'
    || !['VERIFIED', 'QUARANTINED', 'NOT_ACQUIRED', 'INCOMPLETE'].includes(String(value.stages.car_slot))
    || value.stages.domain_decoding !== 'NOT_PERFORMED'
    || !object(value.integrity) || !nullableText(value.integrity.error)
    || value.integrity.root_to_slot_membership !== 'UNAVAILABLE' || value.integrity.whole_car_sha256_verified !== false
    || !Array.isArray(value.integrity.slots) || value.integrity.slots.length > 32
    || value.evidence !== 'RAW_ENGINEERING_CHECK_ONLY' || value.research_ready !== false) {
    throw new MonitorReadError('INVALID_OFFLINE_VERIFICATION');
  }
  let previousSequence = -1;
  for (const receipt of value.bindings.receipts) {
    if (!integers(receipt, ['sequence', 'raw_bytes']) || Number(receipt.sequence) <= previousSequence
      || receipt.path !== `published/${String(receipt.sequence).padStart(10, '0')}/receipt.json`
      || !sha256(receipt.sha256) || !sha256(receipt.raw_sha256)) throw new MonitorReadError('INVALID_OFFLINE_VERIFICATION');
    previousSequence = Number(receipt.sequence);
  }
  let previousSlot = -1;
  for (const slot of value.integrity.slots) {
    if (!integers(slot, ['slot']) || Number(slot.slot) <= previousSlot
      || !integers(slot.archival_node_counts, ['transaction', 'entry', 'block', 'rewards', 'dataframe'])
      || !object(slot.report) || slot.report.selected_slot !== slot.slot
      || !integers(slot.report, ['captured_section_bytes', 'verified_nodes', 'verified_links'])
      || slot.report.root_to_slot_membership !== 'UNAVAILABLE'
      || slot.report.domain_counts !== 'UNAVAILABLE_NOT_DECODED_IN_B4') throw new MonitorReadError('INVALID_OFFLINE_VERIFICATION');
    const counts = slot.archival_node_counts as Record<string, number>;
    const countTotal = ['transaction', 'entry', 'block', 'rewards', 'dataframe'].reduce((sum, key) => sum + counts[key], 0);
    if (!integer(countTotal) || countTotal !== slot.report.verified_nodes || counts.block !== 1) throw new MonitorReadError('INVALID_OFFLINE_VERIFICATION');
    previousSlot = Number(slot.slot);
  }
  const unacquired = value.stages.car_slot === 'NOT_ACQUIRED';
  if (value.stages.car_slot === 'VERIFIED' && (value.stages.capture !== 'COMPLETE' || value.integrity.error !== null
      || value.integrity.slots.length === 0 || value.bindings.payload_manifest_sha256 === null)
    || value.stages.car_slot === 'QUARANTINED' && (!text(value.integrity.error) || value.integrity.error.length === 0
      || value.integrity.slots.length !== 0 || value.bindings.payload_manifest_sha256 === null)
    || unacquired && (value.integrity.slots.length !== 0 || value.integrity.error !== null
      || value.bindings.payload_manifest_sha256 !== null || value.bindings.prepared_payload_sha256 !== null
      || value.bindings.metadata_receipt_sha256 !== null)) throw new MonitorReadError('INVALID_OFFLINE_VERIFICATION');
  if (value.prior_failure !== null) {
    const prior = value.prior_failure;
    if (!object(prior) || !text(prior.error) || prior.status !== 'HISTORICAL_FAILURE_PRESERVED'
      || !['binary_sha256', 'artifact_sha256', 'run_result_sha256', 'raw_sha256'].every(key => sha256(prior[key]))
      || !value.bindings.receipts.some(receipt => object(receipt) && receipt.raw_sha256 === prior.raw_sha256)) {
      throw new MonitorReadError('INVALID_OFFLINE_VERIFICATION');
    }
  }
  return value;
}

export function createSnapshotReader(directory: string) {
  if (!isAbsolute(directory) || normalize(directory) !== directory) throw new MonitorReadError('ABSOLUTE_SNAPSHOT_DIRECTORY_REQUIRED');

  async function snapshot(id: string): Promise<MonitorRun> {
    try {
      const bytes = await readBoundedFile(join(directory, `latest-${id}.json`), MAX_SNAPSHOT_BYTES);
      let value: unknown;
      try { value = JSON.parse(bytes.toString('utf8')); }
      catch { throw new MonitorReadError('INVALID_SNAPSHOT_JSON'); }
      return { id, state: 'READY', snapshot: validateSnapshot(value, id) };
    } catch (error) {
      return { id, state: 'UNAVAILABLE', reason: error instanceof MonitorReadError ? error.reason : 'SNAPSHOT_UNAVAILABLE' };
    }
  }

  async function boundArtifact(root: string, path: string, expectedHash: unknown): Promise<Buffer> {
    const absolute = resolve(root, path);
    try {
      if (await realpath(root) !== root || await realpath(absolute) !== absolute) throw new MonitorReadError('ARTIFACT_PATH_CHANGED');
    } catch { throw new MonitorReadError('ARTIFACT_UNAVAILABLE'); }
    const bytes = await readBoundedFile(absolute, MAX_ARTIFACT_BYTES);
    if (createHash('sha256').update(bytes).digest('hex') !== expectedHash) throw new MonitorReadError('ARTIFACT_HASH_MISMATCH');
    return bytes;
  }

  return Object.freeze({
    async runs(): Promise<MonitorRun[]> {
      const ids: string[] = [];
      let scanned = 0;
      try {
        const entries = await opendir(directory);
        for await (const entry of entries) {
          if (++scanned > 128) throw new MonitorReadError('SNAPSHOT_DIRECTORY_TOO_LARGE');
          const match = SNAPSHOT_FILE.exec(entry.name);
          if (!match) continue;
          ids.push(match[1]);
          if (ids.length > MAX_RUNS) throw new MonitorReadError('TOO_MANY_SNAPSHOTS');
        }
      } catch (error) {
        if (error instanceof MonitorReadError) throw error;
        throw new MonitorReadError('SNAPSHOT_DIRECTORY_UNAVAILABLE');
      }
      return Promise.all(ids.sort().map(snapshot));
    },
    async verification(id: string): Promise<Record<string, unknown>> {
      try {
        if (!/^[a-f0-9]{64}$/.test(id)) throw new MonitorReadError('VERIFICATION_NOT_FOUND');
        const run = await snapshot(id);
        if (!run.snapshot) throw new MonitorReadError(run.reason ?? 'SNAPSHOT_UNAVAILABLE');
        const bytes = await readBoundedFile(join(directory, `verification-${id}.json`), MAX_ARTIFACT_BYTES);
        let report: Record<string, unknown>;
        try { report = validateVerification(JSON.parse(bytes.toString('utf8')), id); }
        catch (error) { if (error instanceof MonitorReadError) throw error; throw new MonitorReadError('INVALID_OFFLINE_VERIFICATION'); }
        const root = run.snapshot.dataset_root as string;
        const binding = report.bindings as Record<string, unknown>;
        const refs = run.snapshot.artifacts as Record<string, unknown>[];
        const paths = refs.map(ref => ref.path);
        if (report.dataset_root !== root || new Set(paths).size !== paths.length
          || refs.find(ref => ref.path === 'run.json')?.sha256 !== binding.manifest_sha256
          || (refs.find(ref => ref.path === 'payload.json')?.sha256 ?? null) !== binding.payload_manifest_sha256) {
          throw new MonitorReadError('VERIFICATION_BINDING_MISMATCH');
        }
        const manifest = JSON.parse((await boundArtifact(root, 'run.json', binding.manifest_sha256)).toString('utf8'));
        if (manifest.run_id !== id || manifest.aggregate_sha256 !== binding.aggregate_sha256) throw new MonitorReadError('VERIFICATION_BINDING_MISMATCH');
        const payloadRequestBytes = new Map<number, number>();
        if (binding.payload_manifest_sha256 !== null) {
          const payload = JSON.parse((await boundArtifact(root, 'payload.json', binding.payload_manifest_sha256)).toString('utf8'));
          if (payload.lease?.prepared_payload_sha256 !== binding.prepared_payload_sha256
            || payload.lease?.metadata_receipt_sha256 !== binding.metadata_receipt_sha256) throw new MonitorReadError('VERIFICATION_BINDING_MISMATCH');
          const prepared = payload.prepared;
          const selected = run.snapshot.selected_slots as Record<string, unknown> | null;
          if (!integers(prepared, ['start_slot', 'end_slot']) || Number(prepared.end_slot) <= Number(prepared.start_slot)
            || !selected || selected.start !== prepared.start_slot || selected.end_exclusive !== prepared.end_slot
            || !Array.isArray(prepared.requests) || prepared.requests.length > 32) throw new MonitorReadError('VERIFICATION_SELECTION_MISMATCH');
          const plannedBytes = new Map<number, number>();
          const requestSequences = new Set<number>();
          for (const request of prepared.requests) {
            if (!integers(request, ['sequence']) || Number(request.sequence) < 4 || requestSequences.has(Number(request.sequence))
              || !integers(request.kind, ['slot', 'start', 'end_exclusive']) || request.kind.kind !== 'CAR_RANGE'
              || Number(request.kind.slot) < Number(prepared.start_slot) || Number(request.kind.slot) >= Number(prepared.end_slot)
              || Number(request.kind.end_exclusive) <= Number(request.kind.start)) throw new MonitorReadError('VERIFICATION_SELECTION_MISMATCH');
            requestSequences.add(Number(request.sequence));
            const slot = Number(request.kind.slot);
            const length = Number(request.kind.end_exclusive) - Number(request.kind.start);
            payloadRequestBytes.set(Number(request.sequence), length);
            const total = (plannedBytes.get(slot) ?? 0) + length;
            if (!integer(total)) throw new MonitorReadError('VERIFICATION_SELECTION_MISMATCH');
            plannedBytes.set(slot, total);
          }
          const slots = (report.integrity as Record<string, unknown>).slots as Record<string, unknown>[];
          for (const slot of slots) {
            if (plannedBytes.get(Number(slot.slot)) !== (slot.report as Record<string, unknown>).captured_section_bytes) {
              throw new MonitorReadError('VERIFICATION_SELECTION_MISMATCH');
            }
          }
          if ((report.stages as Record<string, unknown>).car_slot === 'VERIFIED' && slots.length !== plannedBytes.size) {
            throw new MonitorReadError('VERIFICATION_SELECTION_MISMATCH');
          }
        } else if (binding.prepared_payload_sha256 !== null || binding.metadata_receipt_sha256 !== null) {
          throw new MonitorReadError('VERIFICATION_BINDING_MISMATCH');
        }
        const receipts = binding.receipts as Record<string, unknown>[];
        const receiptRefs = refs.filter(ref => String(ref.path).startsWith('published/'));
        const published = (run.snapshot.operations as Record<string, unknown>[]).filter(op => op.state === 'PUBLISHED');
        if (receipts.length !== receiptRefs.length || receipts.length !== published.length) throw new MonitorReadError('VERIFICATION_RECEIPT_SET_MISMATCH');
        for (const ref of receipts) {
          if (receiptRefs.find(item => item.path === ref.path)?.sha256 !== ref.sha256
            || !published.some(op => op.sequence === ref.sequence && op.published_bytes === ref.raw_bytes)
            || Number(ref.sequence) >= 4 && payloadRequestBytes.get(Number(ref.sequence)) !== ref.raw_bytes) throw new MonitorReadError('VERIFICATION_RECEIPT_MISMATCH');
          const receipt = JSON.parse((await boundArtifact(root, ref.path as string, ref.sha256)).toString('utf8'));
          if (receipt.run_id !== id || receipt.aggregate_sha256 !== binding.aggregate_sha256
            || receipt.request?.sequence !== ref.sequence || receipt.sha256 !== ref.raw_sha256
            || receipt.response_entity_bytes !== ref.raw_bytes) throw new MonitorReadError('VERIFICATION_RECEIPT_MISMATCH');
        }
        // Raw is verified by the named Rust executable, not repeatedly decoded/read by this UI server.
        return { state: 'READY', report_sha256: createHash('sha256').update(bytes).digest('hex'), report };
      } catch (error) {
        return { state: 'UNAVAILABLE', reason: error instanceof MonitorReadError ? error.reason : 'VERIFICATION_UNAVAILABLE' };
      }
    },
    async artifact(id: string, key: string): Promise<Buffer> {
      if (!/^[a-f0-9]{64}$/.test(id) || !/^[a-z0-9-]{1,80}$/.test(key)) throw new MonitorReadError('ARTIFACT_NOT_FOUND');
      const run = await snapshot(id);
      if (!run.snapshot) throw new MonitorReadError(run.reason ?? 'SNAPSHOT_UNAVAILABLE');
      const reference = (run.snapshot.artifacts as Record<string, unknown>[]).find((item) => item.id === key);
      if (!reference) throw new MonitorReadError('ARTIFACT_NOT_FOUND');
      const root = run.snapshot.dataset_root as string;
      const bytes = await boundArtifact(root, reference.path as string, reference.sha256);
      try { JSON.parse(bytes.toString('utf8')); }
      catch { throw new MonitorReadError('ARTIFACT_NOT_JSON'); }
      return bytes;
    },
  });
}
