import { afterEach, describe, expect, it } from 'vitest';
import { chmod, mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadCockpitConfig } from '../src/cockpit-config.js';
import { lstatSync, accessSync, readdirSync } from 'node:fs';

const roots: string[] = [];
afterEach(async () => {
  for (const root of roots.splice(0)) {
    try { await chmod(root, 0o700); } catch { /* already absent */ }
    await rm(root, { recursive: true, force: true });
  }
});

async function readonlyDirectory(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-config-'));
  roots.push(root);
  await chmod(root, 0o555);
  return root;
}

describe('cockpit-only configuration', () => {
  it('defaults to loopback and requires an explicit bounded port', () => {
    expect(() => loadCockpitConfig({})).toThrow(/COCKPIT_PORT/);
    expect(loadCockpitConfig({ COCKPIT_PORT: '3000' })).toEqual({
      bindHost: '127.0.0.1',
      port: 3000,
      outputDirectory: undefined,
    });
    for (const value of ['0', '-1', '1.5', '65536', ' 3000', '3000 ']) {
      expect(() => loadCockpitConfig({ COCKPIT_PORT: value })).toThrow(/COCKPIT_PORT/);
    }
  });

  it('accepts only explicit IP loopback literals', () => {
    expect(loadCockpitConfig({ COCKPIT_PORT: '3000', COCKPIT_BIND_HOST: '127.0.0.1' }).bindHost).toBe('127.0.0.1');
    expect(loadCockpitConfig({ COCKPIT_PORT: '3000', COCKPIT_BIND_HOST: '::1' }).bindHost).toBe('::1');
    for (const value of ['', 'localhost', '0.0.0.0', '192.168.1.234', '999.1.1.1', '127.0.0.1 ']) {
      expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', COCKPIT_BIND_HOST: value })).toThrow(/COCKPIT.*LOOPBACK/);
    }
  });

  it('accepts no provider and rejects relative, symlinked, worktree and mutable outputs', async () => {
    expect(loadCockpitConfig({ COCKPIT_PORT: '3000' }).outputDirectory).toBeUndefined();
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: 'relative/output' })).toThrow(/OUTPUT_DIR/);
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: process.cwd() })).toThrow(/WORKTREE/);

    const mutable = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-mutable-'));
    roots.push(mutable);
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: mutable })).toThrow(/MUTABLE/);

    const real = await readonlyDirectory();
    const parent = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-symlink-'));
    roots.push(parent);
    const linked = join(parent, 'linked');
    await symlink(real, linked);
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: linked })).toThrow(/SYMLINK/);
  });

  it('rejects mutable files and symlinks anywhere below an otherwise read-only output root', async () => {
    const mutableRoot = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-mutable-file-'));
    roots.push(mutableRoot);
    await writeFile(join(mutableRoot, 'cockpit-snapshot.json'), '{}\n');
    await chmod(mutableRoot, 0o555);
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: mutableRoot })).toThrow(/MUTABLE/);

    const symlinkRoot = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-child-symlink-'));
    roots.push(symlinkRoot);
    const nested = join(symlinkRoot, 'nested');
    await mkdir(nested);
    await symlink('/dev/null', join(nested, 'unsafe'));
    await chmod(nested, 0o555);
    await chmod(symlinkRoot, 0o555);
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: symlinkRoot })).toThrow(/SYMLINK/);
    await chmod(nested, 0o700);
    await chmod(symlinkRoot, 0o700);
  });

  it('accepts an absolute non-symlinked output directory that is not writable', async () => {
    const outputDirectory = await readonlyDirectory();
    expect(loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: outputDirectory })).toEqual({
      bindHost: '127.0.0.1',
      port: 3000,
      outputDirectory,
    });
  });

  it('rejects a non-sticky world-writable ancestor', async () => {
    const ancestor = await mkdtemp(join(tmpdir(), 'phase8c-cockpit-world-'));
    roots.push(ancestor);
    const outputDirectory = join(ancestor, 'output');
    await mkdir(outputDirectory);
    await chmod(outputDirectory, 0o555);
    await chmod(ancestor, 0o777);
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: outputDirectory })).toThrow(/WORLD_WRITABLE/);
    await chmod(ancestor, 0o700);
  });

  it('fails closed on unexpected .git lookup and write-access inspection errors', async () => {
    const outputDirectory = await readonlyDirectory();
    const base = { lstatSync, accessSync, readdirSync };
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: outputDirectory }, {
      ...base,
      lstatSync(path: Parameters<typeof lstatSync>[0], options?: Parameters<typeof lstatSync>[1]) {
        if (String(path).endsWith('/.git')) throw Object.assign(new Error('I/O fault'), { code: 'EIO' });
        return lstatSync(path, options as never);
      },
    })).toThrow(/OUTPUT_INSPECTION_FAILED/);
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: outputDirectory }, {
      ...base,
      accessSync() { throw Object.assign(new Error('I/O fault'), { code: 'EIO' }); },
    })).toThrow(/OUTPUT_INSPECTION_FAILED/);
  });

  it('inspects and rejects a non-sticky world-writable filesystem root', async () => {
    const outputDirectory = await readonlyDirectory();
    const rootStats = lstatSync('/');
    const adversarialRoot = new Proxy(rootStats, {
      get(target, property) {
        if (property === 'mode') return (target.mode | 0o002) & ~0o1000;
        const value = Reflect.get(target, property, target);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
    expect(() => loadCockpitConfig({ COCKPIT_PORT: '3000', PHASE8A_RESEARCH_OUTPUT_DIR: outputDirectory }, {
      lstatSync(path: Parameters<typeof lstatSync>[0], options?: Parameters<typeof lstatSync>[1]) {
        return String(path) === '/' ? adversarialRoot : lstatSync(path, options as never);
      },
      accessSync,
      readdirSync,
    })).toThrow(/WORLD_WRITABLE/);
  });
});
