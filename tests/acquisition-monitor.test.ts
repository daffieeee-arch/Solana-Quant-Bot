import { afterEach, describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, readdir, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createSnapshotReader, MAX_SNAPSHOT_BYTES, readBoundedFile, validateSnapshot } from '../src/acquisition-monitor/reader.js';
import { createAcquisitionMonitor, type AcquisitionMonitorServer } from '../src/acquisition-monitor/server.js';
import { parseMonitorArguments } from '../src/acquisition-monitor/main.js';
import { parseMonitorResponse } from '../frontend/src/monitor/contract.js';

const id = 'a'.repeat(64);
const roots: string[] = [];
let server: AcquisitionMonitorServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true });
});

async function directory() {
  const root = await mkdtemp(join(tmpdir(), 'of1-monitor-reader-'));
  roots.push(root);
  return root;
}

function fixture(root: string): any {
  return {
    schema_version: 'OF1_MONITOR_1', id, session_id: 'fixture-session', sequence: 4,
    updated_at_ms: 1788780000000, kind: 'LOCAL_SIMULATION', mode: 'LIVE', stage: 'DOWNLOADING',
    label: 'Explicit local simulation', dataset_root: root, source: 'LOCAL_LOOPBACK_FIXTURE', epoch: 978,
    selected_slots: { start: 422496000, end_exclusive: 422496001 }, started_at_ms: 1788779999000,
    completed_at_ms: null, elapsed_ms: 1000,
    selection: { operations_total: 1, operations_published: 0, planned_bytes: 4096, received_selection_bytes: 1024, published_bytes: 0, verified_bytes: 0 },
    traffic: { received_bytes: 2048, received_basis: 'PROCESS_OBSERVED', reserved_bytes: 8192, attempts: 2, retries: 1, speed_bps: 1024.5, download_eta_ms: null, eta_scope: null, speed_samples: [{ elapsed_ms: 1000, bps: 1024.5 }] },
    storage: { used_bytes: 1024, available_bytes: 1048576, cap_bytes: 65536 },
    budgets: { attempts_remaining: 2, entity_bytes_remaining: 8192, stage_attempts_remaining: 2, stage_entity_bytes_remaining: 8192, runtime_remaining_ms: 59000 },
    operations: [{ sequence: 0, method: 'GET', path: '/978/epoch-978.car', range: 'bytes=59-4154', state: 'DOWNLOADING', expected_bytes: 4096, received_bytes: 1024, published_bytes: 0, attempts: 2, status_code: 206, error: null }],
    integrity: { receipts: 'PENDING', car: 'UNAVAILABLE_NO_PAYLOAD', root_to_slot: 'UNAVAILABLE' },
    domain_counts: 'UNAVAILABLE_NOT_DECODED_IN_B4', artifacts: [], errors: [], dropped_samples: 0,
  };
}

async function publish(root: string, value = fixture(root)) {
  await writeFile(join(root, `latest-${id}.json`), JSON.stringify(value));
  return value;
}

describe('passive Rust snapshot reader', () => {
  it('returns the exact Rust values and unknowns without re-deriving accounting', async () => {
    const root = await directory();
    const snapshot = await publish(root);
    const reader = createSnapshotReader(root);
    const runs = await reader.runs();
    expect(runs).toEqual([{ id, state: 'READY', snapshot }]);
    // The browser contract independently validates exactly the same wire object.
    expect(parseMonitorResponse({ schema_version: 'OF1_MONITOR_HTTP_1', read_at_unix_ms: 1, runs }).runs).toEqual(runs);
    expect((runs[0].snapshot?.traffic as any).received_bytes).toBe(2048);
    expect((runs[0].snapshot?.selection as any).received_selection_bytes).toBe(1024);
    expect((runs[0].snapshot?.traffic as any).download_eta_ms).toBeNull();
    expect(runs[0].snapshot?.domain_counts).toBe('UNAVAILABLE_NOT_DECODED_IN_B4');
  });

  it('observes atomic snapshot replacement without claiming a writer lock or changing files', async () => {
    const root = await directory();
    const snapshot = await publish(root);
    await writeFile(join(root, 'writer.lock'), 'owned-by-downloader');
    const reader = createSnapshotReader(root);
    const before = await readdir(root);
    await reader.runs();
    expect(await readdir(root)).toEqual(before);
    expect(await readFile(join(root, 'writer.lock'), 'utf8')).toBe('owned-by-downloader');
    await writeFile(join(root, 'replacement.json'), JSON.stringify({ ...snapshot, sequence: 5 }));
    await rename(join(root, 'replacement.json'), join(root, `latest-${id}.json`));
    expect((await reader.runs())[0].snapshot?.sequence).toBe(5);
  });

  it('preserves unknown recorded attempt bytes instead of claiming a zero prefix', async () => {
    const root = await directory();
    const snapshot = fixture(root);
    snapshot.mode = 'RECORDED';
    snapshot.traffic.received_basis = 'RECEIPTS_ONLY';
    snapshot.operations[0].received_bytes = null;
    await publish(root, snapshot);
    const runs = await createSnapshotReader(root).runs();
    expect(runs[0].state).toBe('READY');
    expect((runs[0].snapshot?.operations as any[])[0].received_bytes).toBeNull();
    expect(parseMonitorResponse({ schema_version: 'OF1_MONITOR_HTTP_1', read_at_unix_ms: 1, runs }).runs).toEqual(runs);
  });

  it('reports malformed, missing-field and oversized projections as unavailable, not zero', async () => {
    const root = await directory();
    const reader = createSnapshotReader(root);
    const path = join(root, `latest-${id}.json`);
    await writeFile(path, '{partial');
    expect(await reader.runs()).toEqual([{ id, state: 'UNAVAILABLE', reason: 'INVALID_SNAPSHOT_JSON' }]);
    await writeFile(path, JSON.stringify({ ...fixture(root), budgets: undefined }));
    expect((await reader.runs())[0]).toMatchObject({ state: 'UNAVAILABLE', reason: 'INVALID_RUST_SNAPSHOT' });
    await writeFile(path, ' '.repeat(MAX_SNAPSHOT_BYTES + 1));
    expect((await reader.runs())[0]).toMatchObject({ state: 'UNAVAILABLE', reason: 'FILE_TOO_LARGE' });
    await expect(createSnapshotReader(join(root, 'missing')).runs()).rejects.toThrow('SNAPSHOT_DIRECTORY_UNAVAILABLE');
  });

  it('rejects swapped identities, schema drift, non-finite/unsafe numbers and unsupported types', async () => {
    const root = await directory();
    for (const mutation of [
      { id: 'b'.repeat(64) }, { schema_version: 'OTHER' }, { kind: 'RESEARCH_READY' },
      { sequence: Number.MAX_SAFE_INTEGER + 1 }, { epoch: 1.5 }, { sequence: -1 },
      { traffic: { ...fixture(root).traffic, speed_bps: Infinity } },
      { domain_counts: 0 }, { dataset_root: 'relative' },
    ]) expect(() => validateSnapshot({ ...fixture(root), ...mutation }, id)).toThrow('INVALID_RUST_SNAPSHOT');
  });

  it('bounds directory enumeration and run count', async () => {
    const root = await directory();
    for (let number = 0; number < 17; number++) await writeFile(join(root, `latest-${number.toString(16).padStart(64, '0')}.json`), '{}');
    await expect(createSnapshotReader(root).runs()).rejects.toThrow('TOO_MANY_SNAPSHOTS');
    const other = await directory();
    for (let number = 0; number < 129; number++) await writeFile(join(other, `ignored-${number}`), '');
    await expect(createSnapshotReader(other).runs()).rejects.toThrow('SNAPSHOT_DIRECTORY_TOO_LARGE');
  });

  it('does not follow symlinks or block on a FIFO masquerading as telemetry', async () => {
    const root = await directory();
    const target = join(root, 'target.json');
    await writeFile(target, JSON.stringify(fixture(root)));
    await symlink(target, join(root, `latest-${id}.json`));
    expect((await createSnapshotReader(root).runs())[0]).toMatchObject({ state: 'UNAVAILABLE', reason: 'FILE_UNAVAILABLE' });
    const fifo = join(root, 'fifo');
    execFileSync('mkfifo', [fifo]);
    await expect(readBoundedFile(fifo, 100)).rejects.toThrow('NOT_A_REGULAR_FILE');
    await expect(readBoundedFile(root, 100)).rejects.toThrow('NOT_A_REGULAR_FILE');
  });

  it('serves only explicitly named hash-matching receipt/manifest JSON, never Raw or source paths', async () => {
    const root = await directory();
    const snapshot = fixture(root);
    const bytes = Buffer.from('{"schema_version":"fixture-receipt","response_entity_bytes":4096}\n');
    await mkdir(join(root, 'published', '0000000000'), { recursive: true });
    const path = join(root, 'published', '0000000000', 'receipt.json');
    await writeFile(path, bytes);
    snapshot.artifacts = [{ id: 'receipt-0', label: 'Receipt 0', path: 'published/0000000000/receipt.json', sha256: createHash('sha256').update(bytes).digest('hex') }];
    await publish(root, snapshot);
    const reader = createSnapshotReader(root);
    expect(await reader.artifact(id, 'receipt-0')).toEqual(bytes);
    await expect(reader.artifact(id, '../run.json')).rejects.toThrow('ARTIFACT_NOT_FOUND');
    await expect(reader.artifact(id, 'raw')).rejects.toThrow('ARTIFACT_NOT_FOUND');
    await writeFile(path, '{}');
    await expect(reader.artifact(id, 'receipt-0')).rejects.toThrow('ARTIFACT_HASH_MISMATCH');
    for (const invalid of ['published/0000000000/raw.bin', '../receipt.json', '/tank/solana/car/978/epoch-978.car', 'https://files.old-faithful.net/978/epoch-978.car']) {
      expect(() => validateSnapshot({ ...snapshot, artifacts: [{ ...snapshot.artifacts[0], path: invalid }] }, id)).toThrow('INVALID_ARTIFACT_REFERENCE');
    }
    expect(() => validateSnapshot({ ...snapshot, artifacts: [snapshot.artifacts[0], snapshot.artifacts[0]] }, id)).toThrow('INVALID_ARTIFACT_REFERENCE');
  });
});

describe('standalone V2 acquisition monitor HTTP', () => {
  async function start() {
    const root = await directory();
    const staticRoot = await directory();
    await writeFile(join(staticRoot, 'monitor.html'), '<!doctype html><title>V2 Acquisition</title>');
    await mkdir(join(staticRoot, 'assets'));
    await writeFile(join(staticRoot, 'assets', 'monitor.js'), '/* V2 monitor only */');
    const snapshot = await publish(root);
    server = await createAcquisitionMonitor({ snapshotsDirectory: root, staticDirectory: staticRoot, port: 0 });
    return { root, snapshot, endpoint: `http://127.0.0.1:${server.port}` };
  }

  it('serves static UI and passive snapshots with no mutation, acquisition or arbitrary path route', async () => {
    const { endpoint, snapshot } = await start();
    expect(await (await fetch(endpoint)).text()).toContain('V2 Acquisition');
    expect((await fetch(`${endpoint}/assets/monitor.js`)).status).toBe(200);
    const response = await fetch(`${endpoint}/api/acquisition/runs`);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('content-security-policy')).toContain("connect-src 'self'");
    const body = await response.json();
    expect(body.runs).toEqual([{ id, state: 'READY', snapshot }]);
    expect(parseMonitorResponse(body).schema_version).toBe('OF1_MONITOR_HTTP_1');
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD']) {
      expect((await fetch(`${endpoint}/api/acquisition/runs`, { method })).status).toBe(405);
    }
    for (const path of ['/api/start', '/api/control', '/api/controls', '/api/retry', '/api/provider', '/api/research/pilot-a/summary', '/run.json']) {
      expect((await fetch(`${endpoint}${path}`)).status).toBe(404);
    }
    expect((await fetch(`${endpoint}/api/acquisition/runs?path=/etc/passwd`)).status).toBe(400);
  });

  it('reports unavailable inputs with bounded reasons without filesystem or environment dumps', async () => {
    const { endpoint, root } = await start();
    await rm(root, { recursive: true });
    const response = await fetch(`${endpoint}/api/acquisition/runs`);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ status: 'UNAVAILABLE', reason: 'SNAPSHOT_DIRECTORY_UNAVAILABLE' });
  });

  it('validates loopback/port arguments and gracefully releases its only listener', async () => {
    const root = await directory();
    await expect(createAcquisitionMonitor({ snapshotsDirectory: root, staticDirectory: root, port: 0, bindHost: '0.0.0.0' as any })).rejects.toThrow('MONITOR_LOOPBACK_ONLY');
    const { endpoint } = await start();
    expect((await fetch(`${endpoint}/healthz`)).status).toBe(200);
    await Promise.all([server!.close(), server!.close()]);
    server = undefined;
    await expect(fetch(`${endpoint}/healthz`)).rejects.toThrow();
    expect(parseMonitorArguments(['--snapshots', root])).toEqual({ snapshotsDirectory: root, port: 4173 });
    expect(parseMonitorArguments(['--port', '4444', '--snapshots', root]).port).toBe(4444);
    for (const args of [[], ['--snapshots', 'relative'], ['--snapshots', root, '--port', '0'], ['--snapshots', root, '--port', '65536'], ['--snapshots', root, '--host', '0.0.0.0'], ['--snapshots', root, '--start', 'true'], ['--snapshots', root, '--snapshots', root]]) {
      expect(() => parseMonitorArguments(args)).toThrow();
    }
  });
});
