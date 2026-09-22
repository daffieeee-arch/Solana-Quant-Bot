import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { buildResearchSeccompLauncher } from '../scripts/build-research-seccomp-launcher.mjs';
import { writeCockpitOutboundDenyFilter } from '../scripts/write-cockpit-seccomp-filter.mjs';

describe('cockpit outbound-only seccomp profile', () => {
  let root: string | undefined;
  let filter: string;
  let launcher: string;
  beforeAll(async () => {
    // Compilation is bounded setup, separate from the unchanged 5s test budget.
    root = await mkdtemp(join(tmpdir(), 'cockpit-seccomp-'));
    filter = join(root, 'outbound-deny.bpf');
    launcher = join(root, 'launcher');
    let started = performance.now();
    try { await writeCockpitOutboundDenyFilter(filter); }
    finally { console.info('SECCOMP_PHASE', { phase: 'filter-generation', ms: performance.now() - started }); }
    started = performance.now();
    try { await buildResearchSeccompLauncher(launcher); }
    finally { console.info('SECCOMP_PHASE', { phase: 'compilation', ms: performance.now() - started }); }
  }, 15_000);
  afterAll(async () => { if (root) await rm(root, { recursive: true, force: true }); });

  it('allows the explicit loopback listener but denies TCP connect and UDP send', () => {
    const startedNs = process.hrtime.bigint();
    // One real filtered child avoids three independent Node startup costs.
    // The child watchdog and parent kill bound are failures, never deny evidence.
    const child = spawnSync(launcher, [filter, process.execPath, '--eval', String.raw`
      const startedNs = process.hrtime.bigint().toString();
      const watchdog = setTimeout(() => { console.error('seccomp probes exceeded 1000ms'); process.exit(3); }, 1000);
      const results = {};
      async function probe(name, operation) {
        const start = performance.now();
        results[name] = { ...(await operation()), ms: performance.now() - start };
      }
      async function run() {
        await probe('listener', () => new Promise((resolve, reject) => {
          const server = require('node:http').createServer((_request, response) => response.end('ok'));
          server.on('error', reject);
          server.listen(0, '127.0.0.1', () => {
            const address = server.address();
            server.close(error => error ? reject(error) : resolve({ result: 'LISTENER_OK', address: address.address, port: address.port }));
          });
        }));
        await probe('tcp', () => new Promise((resolve, reject) => {
          const socket = require('node:net').connect({ host: '127.0.0.1', port: 9 });
          socket.on('connect', () => { socket.destroy(); reject(new Error('TCP unexpectedly connected')); });
          socket.on('error', error => { socket.destroy(); error.code === 'EPERM' ? resolve({ code: error.code }) : reject(error); });
        }));
        await probe('udp', () => new Promise((resolve, reject) => {
          const socket = require('node:dgram').createSocket('udp4');
          socket.on('error', reject);
          socket.send(Buffer.from('x'), 9, '127.0.0.1', error => {
            socket.close();
            error?.code === 'EPERM' ? resolve({ code: error.code }) : reject(error ?? new Error('UDP unexpectedly sent'));
          });
        }));
        console.log(JSON.stringify({ startedNs, ...results }));
        clearTimeout(watchdog);
      }
      run().catch(error => { console.error(error); process.exit(2); });
    `], { cwd: resolve('.'), encoding: 'utf8', timeout: 3_000, killSignal: 'SIGKILL', maxBuffer: 64 * 1024 });
    const totalMs = Number(process.hrtime.bigint() - startedNs) / 1e6;
    const diagnostics = `seccomp probes (limit 3000ms, elapsed ${totalMs}ms): status=${String(child.status)} signal=${String(child.signal)} error=${child.error?.message ?? 'none'}\n${child.stderr}\n${child.stdout}`;
    console.info('SECCOMP_PHASE', { phase: 'child-total', ms: totalMs, status: child.status, signal: child.signal });
    expect(child.error, diagnostics).toBeUndefined();
    expect(child.status, diagnostics).toBe(0);
    expect(child.signal, diagnostics).toBeNull();
    const observed = JSON.parse(child.stdout);
    expect(observed.listener.result).toBe('LISTENER_OK');
    expect(observed.listener.address).toBe('127.0.0.1');
    expect(observed.listener.port).toBeGreaterThan(0);
    expect(observed.tcp.code).toBe('EPERM');
    expect(observed.udp.code).toBe('EPERM');
    console.info('SECCOMP_PHASE', { phase: 'process-startup', ms: Number(BigInt(observed.startedNs) - startedNs) / 1e6 });
    for (const phase of ['listener', 'tcp', 'udp']) console.info('SECCOMP_PHASE', { phase, ms: observed[phase].ms });
  });
});
