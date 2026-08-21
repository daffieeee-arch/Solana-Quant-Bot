import { afterEach, describe, expect, it } from 'vitest';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { buildResearchSeccompLauncher } from '../scripts/build-research-seccomp-launcher.mjs';
import { writeCockpitOutboundDenyFilter } from '../scripts/write-cockpit-seccomp-filter.mjs';

const roots: string[] = [];
afterEach(async () => { for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true }); });

describe('cockpit outbound-only seccomp profile', () => {
  it('allows the explicit loopback listener but denies TCP connect and UDP send', async () => {
    const root = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-seccomp-'));
    roots.push(root);
    const filter = join(root, 'outbound-deny.bpf');
    const launcher = join(root, 'launcher');
    await writeCockpitOutboundDenyFilter(filter);
    await buildResearchSeccompLauncher(launcher);

    const listener = spawnSync(launcher, [filter, process.execPath, '--eval', `
      const http = require('node:http');
      const server = http.createServer((_request, response) => response.end('ok'));
      server.listen(0, '127.0.0.1', () => server.close(() => process.stdout.write('LISTENER_OK')));
    `], { cwd: resolve('.'), encoding: 'utf8' });
    expect(listener.status, listener.stderr).toBe(0);
    expect(listener.stdout).toBe('LISTENER_OK');

    const tcp = spawnSync(launcher, [filter, process.execPath, '--eval', `
      const net = require('node:net');
      const socket = net.connect({host:'127.0.0.1',port:9});
      socket.on('error', (error) => process.exit(error.code === 'EPERM' ? 0 : 2));
      setTimeout(() => process.exit(3), 1000);
    `], { encoding: 'utf8' });
    expect(tcp.status, tcp.stderr).toBe(0);

    const udp = spawnSync(launcher, [filter, process.execPath, '--eval', `
      const dgram = require('node:dgram');
      const socket = dgram.createSocket('udp4');
      socket.send(Buffer.from('x'), 9, '127.0.0.1', (error) => { socket.close(); process.exit(error?.code === 'EPERM' ? 0 : 2); });
      setTimeout(() => process.exit(3), 1000);
    `], { encoding: 'utf8' });
    expect(udp.status, udp.stderr).toBe(0);
  });
});
