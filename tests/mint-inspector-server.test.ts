import { afterEach, describe, expect, it } from 'vitest';
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { request } from 'node:http';
import { createMintInspector, loadInspection } from '../src/mint-inspector/server.js';
import { fixtureInspection } from './fixtures/mint-inspector.js';

const roots: string[] = [], servers: Array<{ close(): Promise<void> }> = [];
const sha = (s: string) => createHash('sha256').update(s).digest('hex');
async function setup() {
  const root = await mkdtemp(join(tmpdir(), 'mint-inspector-')); roots.push(root);
  const data = fixtureInspection(), plan = '{}', planHash = sha(plan);
  const collection = JSON.stringify({ schema: 'OF1_BATCH_COLLECTION_1', state: 'COMPLETE', plan_sha256: planHash, research_ready: false });
  const collectionHash = sha(collection);
  data.timeline.bindings.plan_sha256 = planHash; data.timeline.bindings.collection_sha256 = collectionHash;
  data.timeline.collection.plan_sha256 = planHash;
  data.lifecycle.collection.plan_sha256 = planHash; data.lifecycle.collection.sha256 = collectionHash;
  const inputBytes = { timeline: JSON.stringify(data.timeline), lifecycle: JSON.stringify(data.lifecycle), collection, plan };
  const registry = { schema: 'OF1_MINT_INSPECTOR_REGISTRY_1', inputs: Object.fromEntries(Object.entries(inputBytes).map(([name, bytes]) => [name, { path: `${name}.json`, sha256: sha(bytes) }])) };
  for (const [name, bytes] of Object.entries(inputBytes)) await writeFile(join(root, `${name}.json`), bytes);
  await writeFile(join(root, 'registry.json'), JSON.stringify(registry));
  await mkdir(join(root, 'site/assets'), { recursive: true });
  await writeFile(join(root, 'site/inspector.html'), '<!doctype html><title>Fixture</title>');
  await writeFile(join(root, 'site/assets/fixture.js'), '/* static fixture */');
  return { root, data, registry, inputBytes, options: { dataRoot: root, registryPath: 'registry.json', staticDirectory: join(root, 'site'), port: 0 } };
}
function get(port: number, path: string, method = 'GET', headers: Record<string, string> = {}) {
  return new Promise<{ status: number; body: string; headers: import('node:http').IncomingHttpHeaders }>((done, reject) => {
    const req = request({ hostname: '127.0.0.1', port, path, method, headers, timeout: 2000 }, res => {
      let body = ''; res.setEncoding('utf8'); res.on('data', chunk => { body += chunk; });
      res.on('end', () => done({ status: res.statusCode!, body, headers: res.headers }));
    }); req.on('timeout', () => req.destroy(new Error('local request timeout'))); req.on('error', reject); req.end();
  });
}
afterEach(async () => { await Promise.all(servers.splice(0).map(s => s.close())); await Promise.all(roots.splice(0).map(p => rm(p, { recursive: true, force: true }))); });

describe('registered loopback mint adapter', () => {
  it('serves one complete immutable snapshot and byte-identical registered artifacts', async () => {
    const fixture = await setup(), server = await createMintInspector(fixture.options); servers.push(server);
    const result = await get(server.port, '/api/inspection'); expect(result.status).toBe(200);
    expect(JSON.parse(result.body).timeline).toEqual(fixture.data.timeline);
    expect((await get(server.port, '/evidence/timeline.json')).body).toBe(fixture.inputBytes.timeline);
    expect(result.headers['content-security-policy']).toContain("frame-ancestors 'none'");
    expect(result.headers['access-control-allow-origin']).toBeUndefined();
    await writeFile(join(fixture.root, 'timeline.json'), '{"changed":true}');
    expect((await get(server.port, '/api/inspection')).body).toBe(result.body);
  });
  it('rejects mutation, unregistered paths, rebinding hosts and cross-origin reads', async () => {
    const fixture = await setup(), server = await createMintInspector(fixture.options); servers.push(server);
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']) expect((await get(server.port, '/api/inspection', method)).status).toBe(405);
    for (const path of ['/api/inspection?path=/etc/passwd', '/api/../api/inspection', '/%61pi/inspection', '/evidence/../../registry.json', '/assets/../inspector.html', '//api/inspection', '/.env']) expect((await get(server.port, path)).status).toBe(404);
    expect((await get(server.port, '/api/inspection', 'GET', { Host: 'attacker.invalid' })).status).toBe(403);
    expect((await get(server.port, '/api/inspection', 'GET', { Origin: 'https://attacker.invalid' })).status).toBe(403);
    expect((await get(server.port, '/api/inspection', 'GET', { 'Sec-Fetch-Site': 'cross-site' })).status).toBe(403);
    expect((await get(server.port, '/')).status).toBe(200);
  });
  it.each(['hash', 'file-link', 'ancestor-link', 'escape', 'contract', 'oversized'])('fails before listening on %s', async variant => {
    const fixture = await setup();
    if (variant === 'hash') await writeFile(join(fixture.root, 'timeline.json'), '{}');
    if (variant === 'file-link') { await symlink('timeline.json', join(fixture.root, 'alias.json')); fixture.registry.inputs.timeline.path = 'alias.json'; }
    if (variant === 'ancestor-link') { await symlink(fixture.root, join(fixture.root, 'alias')); fixture.registry.inputs.timeline.path = 'alias/timeline.json'; }
    if (variant === 'escape') fixture.registry.inputs.timeline.path = '../timeline.json';
    if (variant === 'contract') {
      fixture.data.timeline.transactions.reverse(); const bytes = JSON.stringify(fixture.data.timeline);
      await writeFile(join(fixture.root, 'timeline.json'), bytes); fixture.registry.inputs.timeline.sha256 = sha(bytes);
    }
    if (variant === 'oversized') await writeFile(join(fixture.root, 'timeline.json'), ' '.repeat(8 * 1024 * 1024 + 1));
    await writeFile(join(fixture.root, 'registry.json'), JSON.stringify(fixture.registry));
    await expect(loadInspection(fixture.root, 'registry.json')).rejects.toThrow();
    await expect(createMintInspector(fixture.options)).rejects.toThrow();
  });
});
