import { afterEach, describe, expect, it } from 'vitest';
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startCockpit, type RunningCockpit } from '../src/cockpit-main.js';
import { phase8aCockpitSnapshot } from './fixtures/phase8a/research-output.js';

let running: RunningCockpit | undefined;
const roots: string[] = [];
afterEach(async () => {
  await running?.close();
  running = undefined;
  for (const root of roots.splice(0)) {
    await chmod(root, 0o700).catch(() => {});
    for (const child of ['output', 'static']) await chmod(join(root, child), 0o700).catch(() => {});
    await rm(root, { recursive: true, force: true });
  }
});

async function fixtureRoot(): Promise<{ root: string; staticDir: string; output: string }> {
  const root = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-main-'));
  roots.push(root);
  const staticDir = join(root, 'static');
  const output = join(root, 'output');
  await mkdir(staticDir);
  await mkdir(output);
  await writeFile(join(staticDir, 'cockpit.html'), '<!doctype html><title>Cockpit only</title>');
  await writeFile(join(output, 'cockpit-snapshot.json'), `${JSON.stringify(phase8aCockpitSnapshot)}\n`);
  await writeFile(join(output, 'event-observations.ndjson'), '{}\n');
  await writeFile(join(output, 'quarantines.ndjson'), '{}\n');
  for (const file of ['cockpit-snapshot.json', 'event-observations.ndjson', 'quarantines.ndjson']) await chmod(join(output, file), 0o444);
  await chmod(output, 0o555);
  return { root, staticDir, output };
}

describe('cockpit-only production entrypoint', () => {
  it('starts without a provider and reports research UNAVAILABLE', async () => {
    const { staticDir } = await fixtureRoot();
    running = await startCockpit({ env: { COCKPIT_PORT: '3000' }, staticDir, portOverride: 0 });
    expect(running.bindHost).toBe('127.0.0.1');
    expect(running.providerConfigured).toBe(false);
    const root = await fetch(`http://127.0.0.1:${running.port}/`);
    expect(root.status).toBe(200);
    expect(await root.text()).toContain('Cockpit only');
    const response = await fetch(`http://127.0.0.1:${running.port}/api/research/pilot-a/summary`);
    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({ schemaVersion: 'PHASE8A_RESEARCH_API_ERROR_1', status: 'UNAVAILABLE' });
  });

  it('loads only immutable runner output through the real file provider without writing it', async () => {
    const { staticDir, output } = await fixtureRoot();
    const before = await Promise.all(['cockpit-snapshot.json', 'event-observations.ndjson', 'quarantines.ndjson'].map(async (name) => ({ name, bytes: await readFile(join(output, name)), stats: await stat(join(output, name)) })));
    running = await startCockpit({
      env: { COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: output },
      staticDir,
      portOverride: 0,
    });
    expect(running.providerConfigured).toBe(true);
    const response = await fetch(`http://127.0.0.1:${running.port}/api/research/pilot-a/summary`);
    await expect(response.json()).resolves.toMatchObject({
      sourceClass: 'SYNTHETIC_FIXTURE_ONLY',
      activationVerdict: 'HOLD_UNPROVEN_ACTIVATION',
      acceptedSilver: false,
      researchReady: false,
    });
    for (const item of before) {
      expect(await readFile(join(output, item.name))).toEqual(item.bytes);
      const after = await stat(join(output, item.name));
      expect(after.size).toBe(item.stats.size);
      expect(after.mtimeMs).toBe(item.stats.mtimeMs);
    }
  });

  it('closes the explicit listener gracefully', async () => {
    const { staticDir } = await fixtureRoot();
    running = await startCockpit({ env: { COCKPIT_PORT: '3000' }, staticDir, portOverride: 0 });
    const endpoint = `http://127.0.0.1:${running.port}/healthz`;
    expect((await fetch(endpoint)).status).toBe(200);
    await running.close();
    running = undefined;
    await expect(fetch(endpoint)).rejects.toThrow();
  });
});
