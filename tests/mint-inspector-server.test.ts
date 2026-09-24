import { afterEach, describe, expect, it } from 'vitest';
import { mkdtemp, mkdir, writeFile, readFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { request } from 'node:http';
import { createMintInspector, loadInspection } from '../src/mint-inspector/server.js';
import { fixtureInspection } from './fixtures/mint-inspector.js';
import { fixturePilotQuality } from './fixtures/pilot-quality.js';
import { fixtureMintFlow } from './fixtures/mint-flow.js';
import { object } from '../src/mint-inspector/contract.js';

const roots: string[] = [], servers: Array<{ close(): Promise<void> }> = [];
const sha = (s: string) => createHash('sha256').update(s).digest('hex');
async function setup(data = fixtureInspection()) {
  const root = await mkdtemp(join(tmpdir(), 'mint-inspector-')); roots.push(root);
  const plan = '{}', planHash = sha(plan);
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
  await writeFile(join(root, 'site/inspector.html'), '<!doctype html><html><head><title>Fixture</title></head><body></body></html>');
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
async function registerPilot(fixture: Awaited<ReturnType<typeof setup>>) {
  const data = fixturePilotQuality(), pilot: Record<string, { path: string; sha256: string }> = {};
  const p = data.manifest.provenance;
  p.collection_sha256 = fixture.registry.inputs.collection.sha256; p.plan_sha256 = fixture.registry.inputs.plan.sha256;
  for (let i = 0; i < 3; i++) {
    const d = data.decoders[i], bytes = JSON.stringify({ schema: 'OF1_BRONZE_EXECUTION_1', slice_class: 'RESEARCH_SAMPLING',
      batch_binding: { batch_id: d.batch_id, source_id: d.source_id, plan_sha256: p.plan_sha256 },
      decoder_source_sha256: d.decoder_source_sha256, executable_sha256: d.executable_sha256, lock_sha256: d.lock_sha256,
      unused_numeric_field: 42 });
    pilot[`decoder${i}`] = { path: `decoder${i}.json`, sha256: sha(bytes) };
    object((p.selected_batches as unknown[])[i]).decoder_execution_sha256 = sha(bytes);
    await writeFile(join(fixture.root, `decoder${i}.json`), bytes);
  }
  const bytes = JSON.stringify(data.manifest); pilot.manifest = { path: 'pilot.json', sha256: sha(bytes) };
  await writeFile(join(fixture.root, 'pilot.json'), bytes);
  await writeFile(join(fixture.root, 'registry.json'), JSON.stringify({ ...fixture.registry, pilot }));
  return { pilot, bytes };
}
async function registerOperations(fixture: Awaited<ReturnType<typeof setup>>) {
  const { pilot } = await registerPilot(fixture), manifest = JSON.parse(await readFile(join(fixture.root, 'pilot.json'), 'utf8'));
  const p = manifest.provenance; p.selected_source.run_id = 'fixture-run';
  const pins: Record<string, { path: string; sha256: string }> = {};
  async function save(name: string, value: unknown) {
    const bytes = JSON.stringify(value); pins[name] = { path: `${name}.json`, sha256: sha(bytes) }; await writeFile(join(fixture.root, `${name}.json`), bytes);
  }
  for (let i = 0; i < 3; i++) {
    const r = p.selected_receipts[i], bytes = 100 + i;
    await save(`acquisition${i}`, { schema: 'OF1_ACQUISITION_RECEIPT_1', run_id: p.selected_source.run_id,
      aggregate_sha256: p.selected_source.bindings.aggregate_sha256, request: { sequence: i + 4,
        kind: { kind: 'CAR_RANGE', slot: 422669516 + i, start: i * 200, end_exclusive: i * 200 + bytes } },
      source_host: 'fixture.invalid', source_path: '/fixture.car', response_entity_bytes: bytes, sha256: r.raw_sha256, acquired_at: { wall_ms: 1000 + i } });
    for (const receipt of [r, p.selected_source.bindings.receipts[i]]) { receipt.raw_bytes = String(bytes); receipt.sha256 = pins[`acquisition${i}`].sha256; }
  }
  const bytes = JSON.stringify(manifest); await writeFile(join(fixture.root, 'pilot.json'), bytes); pilot.manifest.sha256 = sha(bytes);
  await save('reportSource', manifest);
  await save('reportExecution', { schema: 'OF1_RAW_BRONZE_SILVER_WALKING_SKELETON_EXECUTION_1', provider_calls: false,
    skeleton_sha256: pins.reportSource.sha256, started_at_utc: '2026-01-01T00:00:00Z', completed_at_utc: '2026-01-01T00:00:02Z', elapsed_seconds: 2.125 });
  const registry = { ...fixture.registry, pilot, pilotOperations: pins };
  await writeFile(join(fixture.root, 'registry.json'), JSON.stringify(registry)); return registry;
}
afterEach(async () => { await Promise.all(servers.splice(0).map(s => s.close())); await Promise.all(roots.splice(0).map(p => rm(p, { recursive: true, force: true }))); });

describe('registered loopback mint adapter', () => {
  it('publishes operational clocks only with the selected receipt and original report bindings', async () => {
    const fixture = await setup(), registry = await registerOperations(fixture), server = await createMintInspector(fixture.options); servers.push(server);
    const q = JSON.parse((await get(server.port, '/api/pilot-quality')).body);
    expect(q.operations.acquisitions.map((r: { acquired_at_unix_ms: string }) => r.acquired_at_unix_ms)).toEqual(['1000', '1001', '1002']);
    expect(q.operations.report.elapsed_seconds).toBe('2.125');
    expect(sha((await get(server.port, '/evidence/pilot-acquisition0.json')).body)).toBe(registry.pilotOperations.acquisition0.sha256);
    expect((await get(server.port, '/evidence/pilot-reportSource.json', 'POST')).status).toBe(405);
    expect((await get(server.port, '/evidence/pilot-reportSource.json?path=other')).status).toBe(404);
  });
  it.each(['corrupt', 'wrong-receipt', 'missing-input', 'symlink'])('rejects operational registration %s before listening', async variant => {
    const fixture = await setup(), registry = await registerOperations(fixture);
    if (variant === 'corrupt') await writeFile(join(fixture.root, 'reportExecution.json'), '{}');
    if (variant === 'wrong-receipt') registry.pilotOperations.acquisition0 = registry.pilotOperations.acquisition1;
    if (variant === 'missing-input') delete registry.pilotOperations.reportSource;
    if (variant === 'symlink') { await symlink('acquisition0.json', join(fixture.root, 'alias.json')); registry.pilotOperations.acquisition0.path = 'alias.json'; }
    await writeFile(join(fixture.root, 'registry.json'), JSON.stringify(registry));
    await expect(createMintInspector(fixture.options)).rejects.toThrow();
  });
  it('binds the Python flow to the registered snapshot and keeps its exact bytes read-only', async () => {
    const { inspection, flow } = fixtureMintFlow(), fixture = await setup(inspection);
    flow.report.inputs = Object.fromEntries(Object.entries(fixture.registry.inputs).map(([k, v]) => [k, v.sha256])) as typeof flow.report.inputs;
    const bytes = JSON.stringify(flow.report);
    await writeFile(join(fixture.root, 'flow.json'), bytes);
    await writeFile(join(fixture.root, 'registry.json'), JSON.stringify({ ...fixture.registry, flow: { path: 'flow.json', sha256: sha(bytes) } }));
    const server = await createMintInspector(fixture.options); servers.push(server);
    expect(JSON.parse((await get(server.port, '/api/mint-flow')).body).report).toEqual(flow.report);
    expect((await get(server.port, '/evidence/mint-flow.json')).body).toBe(bytes);
    expect((await get(server.port, '/api/mint-flow', 'POST')).status).toBe(405);
    expect((await get(server.port, '/api/mint-flow?path=other.json')).status).toBe(404);
    expect((await get(server.port, '/evidence/mint-flow.json', 'GET', { Origin: 'https://attacker.invalid' })).status).toBe(403);
    expect(JSON.parse((await get(server.port, '/api/inspection')).body).timeline).toEqual(fixture.data.timeline);
  });
  it.each(['wrong-snapshot', 'corrupted', 'symlink', 'fact-order'])('never publishes invalid registered flow: %s', async variant => {
    const { inspection, flow } = fixtureMintFlow(), fixture = await setup(inspection);
    flow.report.inputs = Object.fromEntries(Object.entries(fixture.registry.inputs).map(([k, v]) => [k, v.sha256])) as typeof flow.report.inputs;
    if (variant === 'wrong-snapshot') flow.report.inputs.timeline = '0'.repeat(64);
    if (variant === 'fact-order') flow.report.packages[0].fact_hashes.reverse();
    const bytes = JSON.stringify(flow.report), registered = { path: 'flow.json', sha256: sha(bytes) };
    await writeFile(join(fixture.root, 'flow.json'), variant === 'corrupted' ? '{}' : bytes);
    if (variant === 'symlink') { await symlink('flow.json', join(fixture.root, 'flow-link.json')); registered.path = 'flow-link.json'; }
    await writeFile(join(fixture.root, 'registry.json'), JSON.stringify({ ...fixture.registry, flow: registered }));
    await expect(createMintInspector(fixture.options)).rejects.toThrow();
  });
  it('serves the bound pilot separately, preserving manifest bytes and the mint snapshot', async () => {
    const fixture = await setup(), registered = await registerPilot(fixture), server = await createMintInspector(fixture.options); servers.push(server);
    const result = await get(server.port, '/api/pilot-quality'); expect(result.status).toBe(200);
    expect(JSON.parse(result.body).manifest.counts).toEqual(fixturePilotQuality().manifest.counts);
    expect(result.body).not.toContain('unused_numeric_field');
    expect(JSON.parse(result.body).decoders[0].processed_at_unix_ms).toBeNull();
    expect((await get(server.port, '/')).body).toContain(`name="inspector-snapshot-pilot-quality" content="${sha(result.body)}"`);
    expect((await get(server.port, '/evidence/pilot-manifest.json')).body).toBe(registered.bytes);
    expect(JSON.parse((await get(server.port, '/api/inspection')).body).timeline).toEqual(fixture.data.timeline);
    expect((await get(server.port, '/api/pilot-quality', 'POST')).status).toBe(405);
    expect((await get(server.port, '/api/pilot-quality?path=pilot.json')).status).toBe(404);
    expect((await get(server.port, '/evidence/pilot-unknown.json')).status).toBe(404);
    expect((await get(server.port, '/evidence/pilot-decoder0.json', 'GET', { Origin: 'https://attacker.invalid' })).status).toBe(403);
  });
  it('keeps unregistered pilot evidence unavailable without affecting the mint', async () => {
    const fixture = await setup(), server = await createMintInspector(fixture.options); servers.push(server);
    expect((await get(server.port, '/api/pilot-quality')).status).toBe(404);
    expect((await get(server.port, '/api/inspection')).status).toBe(200);
    expect((await get(server.port, '/api/mint-flow')).status).toBe(404);
  });
  it.each(['modified', 'incomplete', 'wrong-receipt', 'symlink'])('fails before publishing invalid pilot registration: %s', async variant => {
    const fixture = await setup(), { pilot } = await registerPilot(fixture);
    if (variant === 'modified') await writeFile(join(fixture.root, 'pilot.json'), '{}');
    if (variant === 'incomplete') delete pilot.decoder2;
    if (variant === 'wrong-receipt') pilot.decoder1 = pilot.decoder0;
    if (variant === 'symlink') { await symlink('pilot.json', join(fixture.root, 'pilot-link.json')); pilot.manifest.path = 'pilot-link.json'; }
    await writeFile(join(fixture.root, 'registry.json'), JSON.stringify({ ...fixture.registry, pilot }));
    await expect(createMintInspector(fixture.options)).rejects.toThrow();
  });
  it('serves one complete immutable snapshot and byte-identical registered artifacts', async () => {
    const fixture = await setup(), server = await createMintInspector(fixture.options); servers.push(server);
    const result = await get(server.port, '/api/inspection'); expect(result.status).toBe(200);
    expect(JSON.parse(result.body).timeline).toEqual(fixture.data.timeline);
    expect((await get(server.port, '/evidence/timeline.json')).body).toBe(fixture.inputBytes.timeline);
    const html = (await get(server.port, '/')).body;
    expect(html).toContain(`name="inspector-snapshot-inspection" content="${sha(result.body)}"`);
    expect(html).toContain('name="inspector-snapshot-pilot-quality" content="UNAVAILABLE"');
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
