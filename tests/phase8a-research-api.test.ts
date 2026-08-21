import { afterEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { chmod, lstat, mkdtemp, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  createDashboardServer,
  type DashboardServer,
  type ResearchDashboardProvider,
} from '../src/dashboard.js';
import {
  createFilePhase8AResearchProvider,
  createInMemoryPhase8AResearchProvider,
  createOptionalPhase8AResearchProvider,
} from '../src/research/phase8a-research-provider.js';
import { resolveBuiltRunnerArtifact } from '../scripts/assert-phase8a-runner-offline.mjs';
import { phase8aCockpitSnapshot } from './fixtures/phase8a/research-output.js';

const RUNNER_COMPILE_TIMEOUT_MS = 240_000;
const RUNNER_EXECUTION_TIMEOUT_MS = 15_000;
const RUNNER_INTEGRATION_TEST_TIMEOUT_MS = 270_000;

let dashboard: DashboardServer | undefined;
afterEach(async () => { await dashboard?.close(); dashboard = undefined; });

const status = () => ({
  mode: 'paper' as const,
  updatedAt: '2026-08-20T20:00:00.000Z',
  availableLamports: 1,
  openPositions: [],
  realizedPnlLamports: 0,
});

async function response(path: string, init?: RequestInit) {
  return fetch(`http://127.0.0.1:${dashboard!.port}${path}`, init);
}

async function makeWritable(path: string): Promise<void> {
  const stats = await lstat(path);
  if (!stats.isDirectory()) { await chmod(path, 0o600); return; }
  await chmod(path, 0o700);
  for (const entry of await readdir(path)) await makeWritable(join(path, entry));
}

describe('Phase 8A optional read-only dashboard provider', () => {
  it('constructs no research provider unless an explicit output directory is configured', () => {
    expect(createOptionalPhase8AResearchProvider({})).toBeUndefined();
    expect(createOptionalPhase8AResearchProvider({ PHASE8A_RESEARCH_OUTPUT_DIR: '   ' })).toBeUndefined();
  });
  it('returns explicit bounded UNAVAILABLE responses when no provider is configured', async () => {
    dashboard = await createDashboardServer({ port: 0, bindHost: '127.0.0.1', getStatus: status });
    for (const path of [
      '/api/research/pilot-a/summary', '/api/research/pilot-a/events',
      '/api/research/pilot-a/quarantines', '/api/research/pilot-a/provenance',
      '/api/research/pilot-a/metrics', '/api/research/pilot-a/metrics/prometheus',
    ]) {
      const result = await response(path);
      expect(result.status).toBe(404);
      await expect(result.json()).resolves.toEqual({
        schemaVersion: 'PHASE8A_RESEARCH_API_ERROR_1', status: 'UNAVAILABLE',
      });
    }
    expect(await (await response('/api/dashboard-data')).json()).toMatchObject({ mode: 'paper' });
  });

  it('serves only bounded GET research routes from the injected provider', async () => {
    const provider = createInMemoryPhase8AResearchProvider(phase8aCockpitSnapshot);
    dashboard = await createDashboardServer({ port: 0, bindHost: '127.0.0.1', getStatus: status, researchProvider: provider });

    await expect((await response('/api/research/pilot-a/summary')).json()).resolves.toMatchObject({
      schemaVersion: 'PHASE8A_RESEARCH_SUMMARY_RESPONSE_1', sourceClass: 'SYNTHETIC_FIXTURE_ONLY',
      activationVerdict: 'HOLD_UNPROVEN_ACTIVATION', acceptedSilver: false,
    });
    await expect((await response('/api/research/pilot-a/events?cursor=0&limit=1')).json()).resolves.toMatchObject({
      schemaVersion: 'PHASE8A_RESEARCH_EVENTS_RESPONSE_1', cursor: 0, limit: 1,
      rows: [expect.objectContaining({ evidenceBadge: 'SHADOW_STRUCTURAL_OBSERVATION' })],
    });
    await expect((await response('/api/research/pilot-a/quarantines?cursor=0&limit=1')).json()).resolves.toMatchObject({
      schemaVersion: 'PHASE8A_RESEARCH_QUARANTINES_RESPONSE_1', rows: [expect.objectContaining({ reason: 'UNKNOWN_DISCRIMINATOR' })],
    });
    await expect((await response('/api/research/pilot-a/provenance')).json()).resolves.toMatchObject({ schemaVersion: 'PHASE8A_RESEARCH_PROVENANCE_RESPONSE_1' });
    await expect((await response('/api/research/pilot-a/metrics')).json()).resolves.toMatchObject({ schemaVersion: 'PHASE8A_RESEARCH_METRICS_RESPONSE_1' });
    const prometheus = await response('/api/research/pilot-a/metrics/prometheus');
    expect(prometheus.headers.get('content-type')).toContain('text/plain');
    expect(await prometheus.text()).toContain('phase8a_requested_slots');
  });

  it('rejects traversal, invalid cursors/limits, oversized provider responses, methods and controls', async () => {
    const provider = createInMemoryPhase8AResearchProvider(phase8aCockpitSnapshot);
    dashboard = await createDashboardServer({ port: 0, bindHost: '127.0.0.1', getStatus: status, researchProvider: provider });
    for (const path of [
      '/api/research/pilot-a/events?cursor=-1',
      '/api/research/pilot-a/events?cursor=1.5',
      '/api/research/pilot-a/events?limit=0',
      '/api/research/pilot-a/events?limit=101',
      '/api/research/pilot-a/events?path=../../etc/passwd',
      '/api/research/pilot-a/events?cursor=%2e%2e%2f',
    ]) expect((await response(path)).status).toBe(400);
    expect((await response('/api/research/pilot-a/summary', { method: 'POST' })).status).toBe(405);
    expect((await response('/api/research/pilot-a/control')).status).toBe(404);

    const oversized: ResearchDashboardProvider = {
      getSummary: () => ({ schemaVersion: 'PHASE8A_RESEARCH_SUMMARY_RESPONSE_1', payload: 'x'.repeat(300_000) }),
      getEvents: () => ({ schemaVersion: 'PHASE8A_RESEARCH_EVENTS_RESPONSE_1', rows: [] }),
      getQuarantines: () => ({ schemaVersion: 'PHASE8A_RESEARCH_QUARANTINES_RESPONSE_1', rows: [] }),
      getProvenance: () => ({ schemaVersion: 'PHASE8A_RESEARCH_PROVENANCE_RESPONSE_1' }),
      getMetrics: () => ({ schemaVersion: 'PHASE8A_RESEARCH_METRICS_RESPONSE_1' }),
      getPrometheus: () => '',
    };
    await dashboard.close();
    dashboard = await createDashboardServer({ port: 0, bindHost: '127.0.0.1', getStatus: status, researchProvider: oversized });
    expect((await response('/api/research/pilot-a/summary')).status).toBe(500);
  });

  it('returns immutable detached snapshots and keeps high-cardinality values out of metrics', async () => {
    const provider = createInMemoryPhase8AResearchProvider(phase8aCockpitSnapshot);
    const first = await provider.getSummary();
    (first as any).sourceClass = 'REAL';
    expect(await provider.getSummary()).toMatchObject({ sourceClass: 'SYNTHETIC_FIXTURE_ONLY' });
    const metrics = JSON.stringify(await provider.getMetrics());
    expect(metrics).not.toContain(phase8aCockpitSnapshot.runId);
    expect(metrics).not.toContain(phase8aCockpitSnapshot.events[0].signature);
  });

  it('rejects malformed nested cockpit rows, counters and provenance at construction', () => {
    for (const mutate of [
      (value: any) => { delete value.events[0].structuralVariant; },
      (value: any) => { value.events[0].schemaVersion = 'OTHER'; },
      (value: any) => { value.events[0].executionStatus = 'executed'; },
      (value: any) => { value.quarantines[0].reason = ''; },
      (value: any) => { value.dataflow.callbacks = -1; },
      (value: any) => { value.resources.quarantineByReason.wallet = 1; },
      (value: any) => { value.provenance.aggregateOutputSha256 = 'not-a-hash'; },
      (value: any) => { value.unexpectedPromotion = true; },
      (value: any) => { value.eligibility.acceptedSilver.unexpected = true; },
      (value: any) => { value.provenance.providerUrl = 'https://example.invalid'; },
      (value: any) => { value.resources.providerUrl = 'https://example.invalid'; },
    ]) {
      const candidate = structuredClone(phase8aCockpitSnapshot) as any;
      mutate(candidate);
      expect(() => createInMemoryPhase8AResearchProvider(candidate)).toThrow(/invalid_phase8a_snapshot|unknown label/i);
    }
  });

  it('loads the real Rust runner cockpit snapshot through the file provider', async () => {
    const root = await mkdtemp(join(tmpdir(), 'phase8a-real-provider-'));
    const output = join(root, 'output');
    try {
      const buildOutput = execFileSync('cargo', [
        '+1.97.1', 'build', '--locked', '--message-format=json-render-diagnostics',
        '--manifest-path', 'rust/old-faithful-pump-reducer/Cargo.toml',
        '--bin', 'phase8a-bronze-runner',
      ], {
        cwd: process.cwd(),
        encoding: 'utf8',
        maxBuffer: 8 * 1024 * 1024,
        stdio: 'pipe',
        timeout: RUNNER_COMPILE_TIMEOUT_MS,
      });
      const binary = resolveBuiltRunnerArtifact(buildOutput);
      execFileSync(binary, [
        '--input', 'tests/fixtures/phase8a/bronze-runner-rich.json',
        '--output', output,
      ], { cwd: process.cwd(), stdio: 'pipe', timeout: RUNNER_EXECUTION_TIMEOUT_MS });
      const provider = createFilePhase8AResearchProvider(output);
      expect(await provider.getSummary()).toMatchObject({
        sourceClass: 'SYNTHETIC_FIXTURE_ONLY',
        activationVerdict: 'HOLD_UNPROVEN_ACTIVATION',
      });
      const page = await provider.getEvents({ cursor: 0, limit: 10 }) as any;
      expect(page.total).toBe(5);
      expect(page.rows[0]).toMatchObject({
        schemaVersion: 'PHASE8A_EVENT_ROW_1',
        rawDetail: expect.objectContaining({ strategyStatus: 'NOT_STRATEGY_INPUT' }),
      });
    } finally {
      try { await makeWritable(root); } catch { /* partial failure before publication */ }
      await rm(root, { recursive: true, force: true });
    }
  }, RUNNER_INTEGRATION_TEST_TIMEOUT_MS);

  it('pages the bounded cockpit snapshot while size-checking audit NDJSON', async () => {
    const root = await mkdtemp(join(tmpdir(), 'phase8a-provider-'));
    try {
      await writeFile(join(root, 'cockpit-snapshot.json'), `${JSON.stringify(phase8aCockpitSnapshot)}\n`);
      await writeFile(join(root, 'event-observations.ndjson'), `${JSON.stringify({ schemaVersion: 'PHASE8A_SHADOW_EVENT_OBSERVATION_1', candidate: { variant: 'nested-audit-shape' } })}\n`);
      await writeFile(join(root, 'quarantines.ndjson'), `${JSON.stringify({ schemaVersion: 'PHASE8A_BRONZE_QUARANTINE_1', reason: 'nested-audit-shape' })}\n`);
      const provider = createFilePhase8AResearchProvider(root);
      await expect(provider.getEvents({ cursor: 0, limit: 1 })).resolves.toMatchObject({
        schemaVersion: 'PHASE8A_RESEARCH_EVENTS_RESPONSE_1', total: 1,
        rows: [expect.objectContaining({ schemaVersion: 'PHASE8A_EVENT_ROW_1', slot: 422506000, structuralVariant: 'buy' })],
      });
      await expect(provider.getQuarantines({ cursor: 0, limit: 1 })).resolves.toMatchObject({
        schemaVersion: 'PHASE8A_RESEARCH_QUARANTINES_RESPONSE_1', total: 1,
      });
    } finally { await rm(root, { recursive: true, force: true }); }
  });

  it('rejects malformed, symlinked and oversized runner provider inputs', async () => {
    const parent = await mkdtemp(join(tmpdir(), 'phase8a-provider-bad-'));
    try {
      const malformed = join(parent, 'malformed');
      await (await import('node:fs/promises')).mkdir(malformed);
      await writeFile(join(malformed, 'cockpit-snapshot.json'), '{');
      expect(() => createFilePhase8AResearchProvider(malformed)).toThrow(/invalid|malformed/i);

      const real = join(parent, 'real');
      await (await import('node:fs/promises')).mkdir(real);
      await writeFile(join(real, 'cockpit-snapshot.json'), `${JSON.stringify(phase8aCockpitSnapshot)}\n`);
      await writeFile(join(real, 'event-observations.ndjson'), `${'x'.repeat(4 * 1024 * 1024 + 1)}\n`);
      await writeFile(join(real, 'quarantines.ndjson'), '');
      expect(() => createFilePhase8AResearchProvider(real)).toThrow(/too_large/i);

      const linked = join(parent, 'linked');
      await symlink(real, linked);
      expect(() => createFilePhase8AResearchProvider(linked)).toThrow(/unsafe_path/i);
    } finally { await rm(parent, { recursive: true, force: true }); }
  });
});
