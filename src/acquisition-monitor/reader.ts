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

function boundedTree(value: unknown, depth = 0): boolean {
  if (depth > 8) return false;
  if (value === null || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value) && value <= Number.MAX_SAFE_INTEGER && value >= 0;
  if (typeof value === 'string') return value.length <= 4096;
  if (Array.isArray(value)) return value.length <= 64 && value.every((item) => boundedTree(item, depth + 1));
  return object(value) && Object.keys(value).length <= 64
    && Object.entries(value).every(([key, item]) => key.length <= 80 && boundedTree(item, depth + 1));
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
    || !integers(value.budgets, ['attempts_remaining', 'entity_bytes_remaining', 'stage_attempts_remaining', 'stage_entity_bytes_remaining', 'runtime_remaining_ms'])
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
    async artifact(id: string, key: string): Promise<Buffer> {
      if (!/^[a-f0-9]{64}$/.test(id) || !/^[a-z0-9-]{1,80}$/.test(key)) throw new MonitorReadError('ARTIFACT_NOT_FOUND');
      const run = await snapshot(id);
      if (!run.snapshot) throw new MonitorReadError(run.reason ?? 'SNAPSHOT_UNAVAILABLE');
      const reference = (run.snapshot.artifacts as Record<string, unknown>[]).find((item) => item.id === key);
      if (!reference) throw new MonitorReadError('ARTIFACT_NOT_FOUND');
      const root = run.snapshot.dataset_root as string;
      const path = resolve(root, reference.path as string);
      try {
        if (await realpath(root) !== root || await realpath(path) !== path) throw new MonitorReadError('ARTIFACT_PATH_CHANGED');
      } catch { throw new MonitorReadError('ARTIFACT_UNAVAILABLE'); }
      const bytes = await readBoundedFile(path, MAX_ARTIFACT_BYTES);
      if (createHash('sha256').update(bytes).digest('hex') !== reference.sha256) throw new MonitorReadError('ARTIFACT_HASH_MISMATCH');
      try { JSON.parse(bytes.toString('utf8')); }
      catch { throw new MonitorReadError('ARTIFACT_NOT_JSON'); }
      return bytes;
    },
  });
}
