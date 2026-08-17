import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { afterEach, describe, expect, it } from 'vitest';

const repositoryRoot = resolve(import.meta.dirname, '..');
const policyEntrypoint = resolve(repositoryRoot, 'scripts/assert-research-transport-free.mjs');
const runtimePreload = resolve(repositoryRoot, 'scripts/research-transport-preload.cjs');
const runtimeRegister = resolve(repositoryRoot, 'scripts/research-transport-register.mjs');
const seccompBuilder = resolve(repositoryRoot, 'scripts/write-research-seccomp-filter.mjs');
const seccompLauncherBuilder = resolve(repositoryRoot, 'scripts/build-research-seccomp-launcher.mjs');
const scratch: string[] = [];

afterEach(async () => {
  await Promise.all(scratch.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

async function runStaticPolicy(source: string, siblingSource?: string) {
  const base = await mkdtemp(join(tmpdir(), 'research-transport-policy-'));
  scratch.push(base);
  const graphRoot = join(base, 'graph');
  await mkdir(graphRoot);
  await writeFile(join(graphRoot, 'entry.js'), source, 'utf8');
  if (siblingSource !== undefined) await writeFile(join(base, 'helper.js'), siblingSource, 'utf8');
  return spawnSync(process.execPath, [policyEntrypoint, '--static-only', graphRoot], {
    cwd: repositoryRoot,
    encoding: 'utf8',
    env: { ...process.env, NODE_NO_WARNINGS: '1' },
  });
}

describe('research transport policy entrypoint', () => {
  it('accepts a pure built research module', async () => {
    const result = await runStaticPolicy('export const capture = (value) => value;\n');
    expect(result.status).toBe(0);
    expect(result.stdout).toContain('Research transport-free static graph PASS');
    expect(result.stderr).toBe('');
  });

  it.each([
    ['UDP sockets', "import dgram from 'node:dgram';\nexport const socket = () => dgram.createSocket('udp4');\n"],
    ['side-effect UDP import', "import 'node:dgram';\nexport const value = 1;\n"],
    ['child processes', "import { spawn } from 'node:child_process';\nexport const run = () => spawn('true');\n"],
    ['dormant global fetch', "export const dormant = false && globalThis.fetch('https://example.invalid');\n"],
    ['bracket-access global fetch', "export const dormant = false && globalThis['fetch']('https://example.invalid');\n"],
    ['aliased global fetch', "const transport = globalThis.fetch;\nexport const dormant = false && transport('https://example.invalid');\n"],
    ['process builtin-module escape', "const cp = process.getBuiltinModule('node:child_process');\nexport const run = cp.spawn;\n"],
    ['non-literal dynamic import', "const target = '../helper.js';\nexport const load = () => import(target);\n"],
    ['constructor reflection escape', "export const transport = globalThis.constructor.constructor('return globalThis.fetch')();\n"],
    ['main-module require escape', "export const cp = process.mainModule.require('node:child_process');\n"],
    ['linked-binding TCP escape', "export const tcp = process._linkedBinding('tcp_wrap');\n"],
    ['computed process binding', "export const tcp = process['bind' + 'ing']('tcp_wrap');\n"],
    ['destructured linked binding', "const { _linkedBinding: linked } = process;\nexport const tcp = linked('tcp_wrap');\n"],
    ['computed global WebSocket', "export const socket = global['Web' + 'Socket'];\n"],
    ['computed constructor reflection', "export const transport = ({})['con' + 'structor']['con' + 'structor']('return global.fetch')();\n"],
    ['const-computed process binding', "const first = 'bind'; const second = 'ing';\nexport const tcp = process[first + second]('tcp_wrap');\n"],
    ['const-computed constructor reflection', "const key = 'constructor';\nexport const transport = ({})[key][key]('return 1')();\n"],
    ['lexically shadowed process binding', "const capability = 'binding'; { const capability = 'argv'; void capability; }\nexport const tcp = process[capability]('tcp_wrap');\n"],
  ])('rejects %s in the actual policy entrypoint', async (_label, source) => {
    const result = await runStaticPolicy(source);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain('forbidden research capability');
    expect(result.stdout).not.toContain('PASS');
  });

  it('rejects a transport capability in a transitive local helper outside the scan root', async () => {
    const result = await runStaticPolicy(
      "import '../helper.js';\nexport const value = 1;\n",
      'export const transport = globalThis.fetch;\n',
    );
    expect(result.status).toBe(1);
    expect(result.stderr).toContain('forbidden research capability');
    expect(result.stdout).not.toContain('PASS');
  });

  it('resolves harmless computed process properties in their lexical scopes', async () => {
    const result = await runStaticPolicy(
      "const capability = 'argv'; { const capability = 'env'; export const inner = process[capability]; }\nexport const outer = process[capability];\n",
    );
    expect(result.status, result.stderr).toBe(0);
  });

  it.each([
    ['aliased global fetch', "const transport = globalThis.fetch; transport('https://example.invalid');"],
    ['process builtin-module escape', "process.getBuiltinModule('node:child_process');"],
    ['computed process binding', "process['bind' + 'ing']('tcp_wrap');"],
    ['destructured linked binding', "const { _linkedBinding: linked } = process; linked('tcp_wrap');"],
    ['computed global WebSocket', "const Socket = global['Web' + 'Socket']; Socket('ws://example.invalid');"],
    ['computed constructor reflection', "const Socket = ({})['con' + 'structor']['con' + 'structor']('return global.WebSocket')(); Socket('ws://example.invalid');"],
  ])('blocks %s at runtime as a second line of defense', async (_label, source) => {
    const base = await mkdtemp(join(tmpdir(), 'research-transport-runtime-'));
    scratch.push(base);
    const browserSeed = join(base, 'browser-seed.cjs');
    const probe = join(base, 'probe.cjs');
    await writeFile(browserSeed, "Object.defineProperty(globalThis, 'WebSocket', { configurable: true, writable: true, value: function WebSocket() {} });\n", 'utf8');
    await writeFile(probe, source, 'utf8');
    const result = spawnSync(process.execPath, [
      '--require', browserSeed,
      '--require', runtimePreload,
      probe,
    ], {
      cwd: repositoryRoot,
      encoding: 'utf8',
      env: { ...process.env, NODE_NO_WARNINGS: '1' },
    });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('forbidden research transport');
  });

  it('blocks non-constant reflection from obtaining node:http2 through getBuiltinModule', async () => {
    const source = `
      const make = (...pieces) => pieces.join('');
      const constructorKey = make('con', 'structor');
      const builtinKey = make('getBuiltin', 'Module');
      const processObject = ({})[constructorKey][constructorKey]('return process')();
      processObject[builtinKey]('node:http2');
    `;
    const base = await mkdtemp(join(tmpdir(), 'research-http2-reflection-'));
    scratch.push(base);
    const probe = join(base, 'probe.cjs');
    await writeFile(probe, source, 'utf8');
    const result = spawnSync(process.execPath, ['--require', runtimePreload, probe], {
      cwd: repositoryRoot,
      encoding: 'utf8',
      env: { ...process.env, NODE_NO_WARNINGS: '1' },
    });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('forbidden research transport');
  });

  it('loads the exact fs-ext native addon through the preload allowlist', async () => {
    const base = await mkdtemp(join(tmpdir(), 'research-fs-ext-allowlist-'));
    scratch.push(base);
    const probe = join(base, 'probe.cjs');
    await writeFile(probe, `require(${JSON.stringify(resolve(repositoryRoot, 'node_modules/fs-ext'))}); process.stdout.write('FS_EXT_OK');\n`, 'utf8');
    const result = spawnSync(process.execPath, ['--require', runtimePreload, probe], {
      cwd: repositoryRoot,
      encoding: 'utf8',
      env: { ...process.env, NODE_NO_WARNINGS: '1' },
    });
    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout).toBe('FS_EXT_OK');
  });

  it('blocks node:http2 through the ESM loader', () => {
    const result = spawnSync(process.execPath, [
      '--require', runtimePreload,
      '--import', runtimeRegister,
      '--input-type=module',
      '--eval', "await import('node:http2')",
    ], {
      cwd: repositoryRoot,
      encoding: 'utf8',
      env: { ...process.env, NODE_NO_WARNINGS: '1' },
    });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('forbidden research transport import: node:http2');
  });

  it('generates an OS filter that permits normal Node execution and rejects network syscalls', async () => {
    const base = await mkdtemp(join(tmpdir(), 'research-seccomp-policy-'));
    scratch.push(base);
    const filter = join(base, 'network-deny.bpf');
    const launcher = join(base, 'research-seccomp-launcher');
    const generated = spawnSync(process.execPath, [seccompBuilder, filter], {
      cwd: repositoryRoot,
      encoding: 'utf8',
    });
    expect(generated.status, generated.stderr).toBe(0);
    const compiled = spawnSync(process.execPath, [seccompLauncherBuilder, launcher], {
      cwd: repositoryRoot,
      encoding: 'utf8',
    });
    expect(compiled.status, compiled.stderr).toBe(0);
    const normal = spawnSync(launcher, [
      filter,
      process.execPath, '--eval', "process.stdout.write('NORMAL_OK')",
    ], { encoding: 'utf8' });
    expect(normal.status, normal.stderr).toBe(0);
    expect(normal.stdout).toBe('NORMAL_OK');
    const network = spawnSync(launcher, [
      filter,
      process.execPath, '--eval', `
        const net = require('node:net');
        const socket = net.connect({ host: '127.0.0.1', port: 9 });
        socket.on('error', (error) => process.exit(error.code === 'EPERM' ? 0 : 2));
        setTimeout(() => process.exit(3), 1_000);
      `,
    ], { encoding: 'utf8' });
    expect(network.status, network.stderr).toBe(0);
  });
});
