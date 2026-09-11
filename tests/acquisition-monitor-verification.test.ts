import { afterEach, describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createSnapshotReader } from '../src/acquisition-monitor/reader.js';
import { createAcquisitionMonitor, type AcquisitionMonitorServer } from '../src/acquisition-monitor/server.js';
import { parseOfflineVerification } from '../frontend/src/monitor/contract.js';

const id = 'a'.repeat(64);
const hash = (value: string) => createHash('sha256').update(value).digest('hex');
const roots: string[] = [];
let server: AcquisitionMonitorServer | undefined;
afterEach(async () => {
  await server?.close(); server = undefined;
  for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true });
});

/** Synthetic transport fixture, not the authentic CAR regression vector. */
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'of1-offline-report-')); roots.push(root);
  const dataset = join(root, 'dataset'); await mkdir(dataset);
  const aggregate = 'b'.repeat(64), prepared = 'c'.repeat(64), metadata = 'd'.repeat(64);
  const manifest = JSON.stringify({ schema: 'OF1_ACQUISITION_STORE_1', run_id: id, aggregate_sha256: aggregate });
  const payload = JSON.stringify({ lease: { prepared_payload_sha256: prepared, metadata_receipt_sha256: metadata },
    prepared: { start_slot: 422496000, end_slot: 422496001,
      requests: [{ sequence: 4, kind: { kind: 'CAR_RANGE', slot: 422496000, start: 59, end_exclusive: 45110 } }] } });
  await writeFile(join(dataset, 'run.json'), manifest);
  await writeFile(join(dataset, 'payload.json'), payload);
  const receipts: any[] = [];
  const artifacts = [
    { id: 'run-manifest', label: 'Run', path: 'run.json', sha256: hash(manifest) },
    { id: 'payload-manifest', label: 'Payload', path: 'payload.json', sha256: hash(payload) },
  ];
  for (let sequence = 0; sequence < 5; sequence++) {
    const raw = sequence === 4 ? 'x'.repeat(45051) : `synthetic-raw-${sequence}`, path = `published/${String(sequence).padStart(10, '0')}/receipt.json`;
    const receipt = JSON.stringify({ run_id: id, aggregate_sha256: aggregate, request: { sequence }, sha256: hash(raw), response_entity_bytes: raw.length });
    await mkdir(join(dataset, 'published', String(sequence).padStart(10, '0')), { recursive: true });
    await writeFile(join(dataset, path), receipt);
    receipts.push({ sequence, path, sha256: hash(receipt), raw_sha256: hash(raw), raw_bytes: raw.length });
    artifacts.push({ id: `receipt-${sequence}`, label: `Receipt ${sequence}`, path, sha256: hash(receipt) });
  }
  const snapshot = {
    schema_version: 'OF1_MONITOR_1', id, session_id: 'synthetic-recorded', sequence: 0, updated_at_ms: 1000,
    kind: 'LOCAL_SIMULATION', mode: 'RECORDED', stage: 'COMPLETE', label: 'Synthetic report binding', dataset_root: dataset,
    source: 'LOCAL_LOOPBACK_FIXTURE', epoch: 978, selected_slots: { start: 422496000, end_exclusive: 422496001 },
    started_at_ms: 0, completed_at_ms: 1000, elapsed_ms: 1000,
    selection: { operations_total: 5, operations_published: 5, planned_bytes: 45111, received_selection_bytes: 45111, published_bytes: 45111, verified_bytes: 45111 },
    traffic: { received_bytes: 45111, received_basis: 'RECEIPTS_ONLY', reserved_bytes: 100000, attempts: 5, retries: 0, speed_bps: null, download_eta_ms: null, eta_scope: null, speed_samples: [] },
    storage: { used_bytes: 1000, available_bytes: 1000000, cap_bytes: 20000 },
    budgets: { attempts_remaining: 11, entity_bytes_remaining: 1000, stage_attempts_remaining: 2, stage_entity_bytes_remaining: 100, runtime_remaining_ms: 0 },
    operations: receipts.map(receipt => ({ sequence: receipt.sequence, method: 'GET', path: '/synthetic', range: null,
      state: 'PUBLISHED', expected_bytes: receipt.raw_bytes, received_bytes: receipt.raw_bytes, published_bytes: receipt.raw_bytes, attempts: 1, status_code: 200, error: null })),
    integrity: { receipts: 'VERIFIED', car: 'UNAVAILABLE_NOT_CHECKED_BY_MONITOR', root_to_slot: 'UNAVAILABLE' },
    domain_counts: 'UNAVAILABLE_NOT_DECODED_IN_B4', artifacts, errors: [], dropped_samples: 0,
  };
  const report: any = {
    schema: 'OF1_OFFLINE_VERIFICATION_1', run_id: id, dataset_root: dataset,
    verifier: { name: 'of1-verify-recorded', version: '1', binary_sha256: 'e'.repeat(64), source_sha256: 'f'.repeat(64) },
    bindings: { manifest_sha256: hash(manifest), payload_manifest_sha256: hash(payload), aggregate_sha256: aggregate,
      prepared_payload_sha256: prepared, metadata_receipt_sha256: metadata, receipts },
    stages: { capture: 'COMPLETE', raw_receipts: 'VERIFIED', car_slot: 'VERIFIED', domain_decoding: 'NOT_PERFORMED' },
    integrity: { error: null, root_to_slot_membership: 'UNAVAILABLE', whole_car_sha256_verified: false,
      slots: [{ slot: 422496000, report: { selected_slot: 422496000, captured_section_bytes: 45051, verified_nodes: 66, verified_links: 65,
        root_to_slot_membership: 'UNAVAILABLE', domain_counts: 'UNAVAILABLE_NOT_DECODED_IN_B4' },
      archival_node_counts: { transaction: 0, entry: 64, block: 1, rewards: 1, dataframe: 0 } }] },
    prior_failure: { error: 'CAR_CBOR_OR_ARCHIVAL_SCHEMA_INVALID', binary_sha256: '1'.repeat(64), artifact_sha256: '2'.repeat(64),
      run_result_sha256: '3'.repeat(64), raw_sha256: receipts[4].raw_sha256, status: 'HISTORICAL_FAILURE_PRESERVED' },
    evidence: 'RAW_ENGINEERING_CHECK_ONLY', research_ready: false,
  };
  await writeFile(join(root, `latest-${id}.json`), JSON.stringify(snapshot));
  const reportPath = join(root, `verification-${id}.json`);
  const publish = () => writeFile(reportPath, JSON.stringify(report));
  await publish();
  return { root, dataset, reportPath, report, snapshot, publish, reader: createSnapshotReader(root) };
}

describe('external offline verification attachment', () => {
  it('binds all current manifests, receipts and raw references without replacing failed history or claiming domain decoding', async () => {
    const f = await fixture();
    const before = await readFile(f.reportPath);
    const actual = await f.reader.verification(id);
    expect(actual).toEqual({ state: 'READY', report_sha256: hash(before.toString()), report: f.report });
    expect(parseOfflineVerification(actual, id)).toEqual(actual);
    expect((actual.report as any).prior_failure.error).toBe('CAR_CBOR_OR_ARCHIVAL_SCHEMA_INVALID');
    expect((actual.report as any).stages.domain_decoding).toBe('NOT_PERFORMED');
    expect(await readFile(f.reportPath)).toEqual(before);
    // No Raw or writer.lock is needed by the transport reader. Rust performed the offline byte check.
  });

  it('returns explicit unavailable with 5/5 capture publications when the external report is absent', async () => {
    const f = await fixture(); await rm(f.reportPath);
    expect(await f.reader.verification(id)).toEqual({ state: 'UNAVAILABLE', reason: 'FILE_UNAVAILABLE' });
    expect((await f.reader.runs())[0].snapshot?.stage).toBe('COMPLETE');
  });

  it.each([
    ['run', (r: any) => { r.run_id = '0'.repeat(64); }],
    ['root', (r: any) => { r.dataset_root = '/another-root'; }],
    ['manifest', (r: any) => { r.bindings.manifest_sha256 = '0'.repeat(64); }],
    ['payload manifest', (r: any) => { r.bindings.payload_manifest_sha256 = null; }],
    ['aggregate', (r: any) => { r.bindings.aggregate_sha256 = '0'.repeat(64); }],
    ['prepared', (r: any) => { r.bindings.prepared_payload_sha256 = '0'.repeat(64); }],
    ['metadata', (r: any) => { r.bindings.metadata_receipt_sha256 = '0'.repeat(64); }],
    ['receipt hash', (r: any) => { r.bindings.receipts[0].sha256 = '0'.repeat(64); }],
    ['raw hash', (r: any) => { r.bindings.receipts[0].raw_sha256 = '0'.repeat(64); }],
    ['raw bytes', (r: any) => { r.bindings.receipts[0].raw_bytes += 1; }],
    ['omitted receipt', (r: any) => { r.bindings.receipts.pop(); }],
    ['duplicate receipt', (r: any) => { r.bindings.receipts.push(r.bindings.receipts[0]); }],
    ['path traversal', (r: any) => { r.bindings.receipts[0].path = '../receipt.json'; }],
    ['wrong identity', (r: any) => { r.verifier.binary_sha256 = 'unbound'; }],
    ['research promotion', (r: any) => { r.research_ready = true; }],
    ['membership promotion', (r: any) => { r.integrity.root_to_slot_membership = 'VERIFIED'; }],
    ['domain promotion', (r: any) => { r.stages.domain_decoding = 'VERIFIED'; }],
    ['inconsistent node counts', (r: any) => { r.integrity.slots[0].archival_node_counts.transaction = 999; }],
    ['wrong block count', (r: any) => { r.integrity.slots[0].archival_node_counts.block = 2; r.integrity.slots[0].report.verified_nodes += 1; }],
    ['verified incomplete capture', (r: any) => { r.stages.capture = 'INCOMPLETE'; }],
    ['quarantine retaining slot facts', (r: any) => { r.stages.car_slot = 'QUARANTINED'; r.integrity.error = 'CAR_MISSING_LINK'; }],
    ['not acquired retaining slot facts', (r: any) => { r.stages.car_slot = 'NOT_ACQUIRED'; }],
    ['not acquired with payload identity', (r: any) => { r.stages.car_slot = 'NOT_ACQUIRED'; r.integrity.slots = []; }],
    ['wrong selected slot', (r: any) => { r.integrity.slots[0].slot += 1; r.integrity.slots[0].report.selected_slot += 1; }],
    ['wrong slot byte total', (r: any) => { r.integrity.slots[0].report.captured_section_bytes += 1; }],
    ['duplicate slot', (r: any) => { r.integrity.slots.push(r.integrity.slots[0]); }],
  ])('fails closed on %s mismatch', async (_name, change) => {
    const f = await fixture(); change(f.report); await f.publish();
    expect(await f.reader.verification(id)).toMatchObject({ state: 'UNAVAILABLE' });
  });

  it.each(['run.json', 'payload.json', 'published/0000000004/receipt.json'])('rejects a changed captured %s rather than using a cached success', async path => {
    const f = await fixture(); expect((await f.reader.verification(id)).state).toBe('READY');
    await writeFile(join(f.dataset, path), '{}');
    expect(await f.reader.verification(id)).toEqual({ state: 'UNAVAILABLE', reason: 'ARTIFACT_HASH_MISMATCH' });
  });

  it('rejects a corrupt or oversized report and does not retain an earlier success', async () => {
    const f = await fixture(); expect((await f.reader.verification(id)).state).toBe('READY');
    await writeFile(f.reportPath, '{partial');
    expect(await f.reader.verification(id)).toEqual({ state: 'UNAVAILABLE', reason: 'INVALID_OFFLINE_VERIFICATION' });
    await writeFile(f.reportPath, 'x'.repeat(1024 * 1024 + 1));
    expect(await f.reader.verification(id)).toEqual({ state: 'UNAVAILABLE', reason: 'FILE_TOO_LARGE' });
  });

  it('keeps CAR quarantine distinct from successful capture, Raw receipts and preserved historical failure', async () => {
    const f = await fixture(); f.report.stages.car_slot = 'QUARANTINED';
    f.report.integrity.error = 'CAR_MISSING_LINK'; f.report.integrity.slots = []; await f.publish();
    const result = await f.reader.verification(id);
    expect(result).toMatchObject({ state: 'READY', report: { stages: { capture: 'COMPLETE', raw_receipts: 'VERIFIED', car_slot: 'QUARANTINED' }, integrity: { error: 'CAR_MISSING_LINK' } } });
    expect(parseOfflineVerification(result, id)).toEqual(result);
  });

  it('permits genuinely metadata-only reports without slot facts or payload identities', async () => {
    const f = await fixture();
    f.report.stages.car_slot = 'NOT_ACQUIRED'; f.report.integrity.slots = []; f.report.prior_failure = null;
    f.report.bindings.payload_manifest_sha256 = null; f.report.bindings.prepared_payload_sha256 = null; f.report.bindings.metadata_receipt_sha256 = null;
    f.report.bindings.receipts.pop();
    f.snapshot.artifacts = f.snapshot.artifacts.filter(ref => ref.id !== 'payload-manifest' && ref.id !== 'receipt-4');
    f.snapshot.operations.pop(); f.snapshot.selection.operations_total = 4; f.snapshot.selection.operations_published = 4;
    await writeFile(join(f.root, `latest-${id}.json`), JSON.stringify(f.snapshot)); await f.publish();
    const result = await f.reader.verification(id);
    expect(result).toMatchObject({ state: 'READY', report: { stages: { car_slot: 'NOT_ACQUIRED' } } });
    expect(parseOfflineVerification(result, id)).toEqual(result);
  });

  it('binds slots and aggregate section lengths to the hash-bound prepared payload, not report claims', async () => {
    const f = await fixture();
    const payload = JSON.parse(await readFile(join(f.dataset, 'payload.json'), 'utf8'));
    payload.prepared.requests[0].kind.end_exclusive += 1;
    const changed = JSON.stringify(payload);
    await writeFile(join(f.dataset, 'payload.json'), changed);
    f.snapshot.artifacts.find(ref => ref.id === 'payload-manifest')!.sha256 = hash(changed);
    f.report.bindings.payload_manifest_sha256 = hash(changed);
    await writeFile(join(f.root, `latest-${id}.json`), JSON.stringify(f.snapshot)); await f.publish();
    expect(await f.reader.verification(id)).toEqual({ state: 'UNAVAILABLE', reason: 'VERIFICATION_SELECTION_MISMATCH' });
  });

  it('serves one fixed read-only report endpoint, no caller file path or acquisition action', async () => {
    const f = await fixture();
    server = await createAcquisitionMonitor({ snapshotsDirectory: f.root, staticDirectory: f.root, port: 0 });
    const endpoint = `http://127.0.0.1:${server.port}/api/acquisition/runs/${id}/verification`;
    const response = await fetch(endpoint);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toMatchObject({ state: 'READY', report: { run_id: id } });
    expect((await fetch(`${endpoint}?path=/tmp/report.json`)).status).toBe(400);
    expect((await fetch(endpoint, { method: 'POST' })).status).toBe(405);
  });
});
