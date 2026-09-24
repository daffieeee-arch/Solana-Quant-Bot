/** Operational receipt projection only; no protocol interpretation or historical clock. */
import { isDeepStrictEqual } from 'node:util';
import { check, hash, list, object, uint, type ObjectValue } from './contract.js';

export const OPERATION_INPUT_NAMES = ['acquisition0', 'acquisition1', 'acquisition2', 'reportExecution', 'reportSource'] as const;
export type OperationInputName = typeof OPERATION_INPUT_NAMES[number];
// Original acquisition receipts use JSON safe integers. Never round an unsafe value.
function integer(value: unknown): string {
  check(typeof value === 'number' && Number.isSafeInteger(value) && value >= 0);
  return String(value);
}
export function decoderClocks(execution: Record<string, unknown>): ObjectValue {
  const start = execution.processed_at_unix_ms ?? null, finish = execution.finished_at_unix_ms ?? null;
  if (start !== null) uint(start);
  if (finish !== null) uint(finish);
  if (start !== null || finish !== null) check(execution.operational_timestamps_are_not_features === true);
  return { processed_at_unix_ms: start, finished_at_unix_ms: finish };
}
export function operationalProjection(manifest: Record<string, unknown>, inputs: Record<OperationInputName, string>, values: Record<OperationInputName, unknown>): ObjectValue {
  const provenance = object(manifest.provenance), source = object(provenance.selected_source);
  const selected = list(provenance.selected_receipts).map(object), batches = list(provenance.selected_batches).map(object);
  const acquisitions = selected.map((r, i) => {
    hash(r.raw_sha256);
    const receipt = object(values[`acquisition${i}` as OperationInputName]), request = object(receipt.request), kind = object(request.kind), clock = object(receipt.acquired_at);
    check(inputs[`acquisition${i}` as OperationInputName] === r.sha256);
    check(receipt.schema === 'OF1_ACQUISITION_RECEIPT_1' && receipt.run_id === source.run_id
      && receipt.aggregate_sha256 === object(source.bindings).aggregate_sha256);
    check(integer(request.sequence) === r.sequence && receipt.sha256 === r.raw_sha256
      && integer(receipt.response_entity_bytes) === r.raw_bytes);
    check(kind.kind === 'CAR_RANGE' && integer(kind.slot) === list(batches[i].selected_slots)[0]);
    const start = integer(kind.start), end = integer(kind.end_exclusive);
    check(BigInt(end) - BigInt(start) === BigInt(r.raw_bytes as string));
    check(typeof receipt.source_host === 'string' && typeof receipt.source_path === 'string');
    return { sequence: r.sequence, receipt_sha256: r.sha256, raw_sha256: r.raw_sha256, raw_bytes: r.raw_bytes,
      slot: String(kind.slot), source_host: receipt.source_host, source_path: receipt.source_path,
      range_start: start, range_end_exclusive: end, acquired_at_unix_ms: integer(clock.wall_ms) };
  });
  // A receipt repeated under another sequence or overlapping payload must not be counted twice.
  for (let i = 0; i < acquisitions.length; i++) for (let j = 0; j < i; j++) {
    const a = acquisitions[i], b = acquisitions[j];
    check(a.receipt_sha256 !== b.receipt_sha256);
    if (a.source_host === b.source_host && a.source_path === b.source_path)
      check(BigInt(a.range_end_exclusive) <= BigInt(b.range_start) || BigInt(b.range_end_exclusive) <= BigInt(a.range_start));
  }
  const report = object(values.reportExecution), skeleton = object(values.reportSource);
  check(report.schema === 'OF1_RAW_BRONZE_SILVER_WALKING_SKELETON_EXECUTION_1' && report.provider_calls === false);
  hash(report.skeleton_sha256); check(report.skeleton_sha256 === inputs.reportSource);
  check(skeleton.research_ready === undefined || skeleton.research_ready === false);
  // The producer sets research_ready=false in the manifest projection, not the skeleton root.
  check(isDeepStrictEqual(manifest, Object.fromEntries(Object.keys(manifest).map(k => [k, k === 'research_ready' ? false : skeleton[k]]))));
  for (const k of ['started_at_utc', 'completed_at_utc']) {
    check(typeof report[k] === 'string' && /^\d{4}-\d\d-\d\dT[\d:.]+(?:\+00:00|Z)$/.test(report[k] as string) && Number.isFinite(Date.parse(report[k] as string)));
  }
  check(typeof report.elapsed_seconds === 'number' && Number.isFinite(report.elapsed_seconds) && report.elapsed_seconds >= 0);
  return { inputs, acquisitions, report: { started_at_utc: report.started_at_utc as string, completed_at_utc: report.completed_at_utc as string,
    elapsed_seconds: String(report.elapsed_seconds), skeleton_sha256: report.skeleton_sha256 } };
}
