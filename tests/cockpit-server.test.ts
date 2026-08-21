import { afterEach, describe, expect, it } from 'vitest';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createCockpitServer, type CockpitServer } from '../src/cockpit-server.js';
import { createInMemoryPhase8AResearchProvider } from '../src/research/phase8a-research-provider.js';
import { phase8aCockpitSnapshot } from './fixtures/phase8a/research-output.js';

let server: CockpitServer | undefined;
const roots: string[] = [];
afterEach(async () => {
  await server?.close();
  server = undefined;
  for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true });
});

function url(path = '/') { return `http://127.0.0.1:${server!.port}${path}`; }

async function staticRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-static-'));
  roots.push(root);
  await writeFile(join(root, 'index.html'), '<!doctype html><title>Research Cockpit</title><main>cockpit-only</main>');
  await mkdir(join(root, 'assets'));
  await writeFile(join(root, 'assets/app.js'), 'document.documentElement.dataset.mode="cockpit";');
  return root;
}

describe('cockpit-only HTTP server', () => {
  it('serves only GET and HEAD health/readiness/static routes', async () => {
    server = await createCockpitServer({ bindHost: '127.0.0.1', port: 0, staticDir: await staticRoot() });
    expect(await (await fetch(url('/healthz'))).text()).toBe('ok');
    const unavailable = await fetch(url('/readyz'));
    expect(unavailable.status).toBe(503);
    await expect(unavailable.json()).resolves.toEqual({ schemaVersion: 'PHASE8C_COCKPIT_READINESS_1', status: 'UNAVAILABLE' });
    const root = await fetch(url('/'));
    expect(root.status).toBe(200);
    expect(root.headers.get('content-type')).toContain('text/html');
    expect(await root.text()).toContain('cockpit-only');
    const head = await fetch(url('/assets/app.js'), { method: 'HEAD' });
    expect(head.status).toBe(200);
    expect(await head.text()).toBe('');
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
      const response = await fetch(url('/'), { method });
      expect(response.status).toBe(405);
      expect(response.headers.get('allow')).toBe('GET, HEAD');
    }
  });

  it('exposes no paper, control, debug, replay or mutation route', async () => {
    server = await createCockpitServer({ bindHost: '127.0.0.1', port: 0, staticDir: await staticRoot() });
    for (const path of ['/api/dashboard-data', '/api/status', '/api/debug', '/api/control', '/api/controls', '/api/replay', '/api/approve', '/api/start', '/api/stop']) {
      expect((await fetch(url(path))).status).toBe(404);
      expect((await fetch(url(path), { method: 'POST' })).status).toBe(405);
    }
  });

  it('returns explicit UNAVAILABLE without a provider and bounded real provider responses when configured', async () => {
    server = await createCockpitServer({ bindHost: '127.0.0.1', port: 0, staticDir: await staticRoot() });
    const missing = await fetch(url('/api/research/pilot-a/summary'));
    expect(missing.status).toBe(404);
    await expect(missing.json()).resolves.toEqual({ schemaVersion: 'PHASE8A_RESEARCH_API_ERROR_1', status: 'UNAVAILABLE' });
    await server.close();

    const provider = createInMemoryPhase8AResearchProvider(phase8aCockpitSnapshot);
    server = await createCockpitServer({ bindHost: '127.0.0.1', port: 0, staticDir: await staticRoot(), researchProvider: provider });
    const ready = await fetch(url('/readyz'));
    expect(ready.status).toBe(200);
    await expect(ready.json()).resolves.toEqual({ schemaVersion: 'PHASE8C_COCKPIT_READINESS_1', status: 'READY' });
    await expect((await fetch(url('/api/research/pilot-a/summary'))).json()).resolves.toMatchObject({
      sourceClass: 'SYNTHETIC_FIXTURE_ONLY',
      activationVerdict: 'HOLD_UNPROVEN_ACTIVATION',
      acceptedSilver: false,
      researchReady: false,
    });
    expect((await fetch(url('/api/research/pilot-a/events?cursor=0&limit=1'))).status).toBe(200);
    expect((await fetch(url('/api/research/pilot-a/events?limit=101'))).status).toBe(400);
  });

  it('closes its only listener cleanly', async () => {
    server = await createCockpitServer({ bindHost: '127.0.0.1', port: 0, staticDir: await staticRoot() });
    const endpoint = url('/healthz');
    expect((await fetch(endpoint)).status).toBe(200);
    await Promise.all([server.close(), server.close()]);
    server = undefined;
    await expect(fetch(endpoint)).rejects.toThrow();
  });
});
