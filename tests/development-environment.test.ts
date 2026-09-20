import { afterEach, describe, expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { collectDoctor, inspectDatasetRoot, parseDoctorArgs, supportedFilesystem } from '../scripts/doctor.mjs';
import { developmentEnvironment, NODE_VERSION, RUST_VERSION, toolchainPaths, validQueryVersion } from '../scripts/lib/development-toolchain.mjs';
import { runWithToolchain } from '../scripts/with-toolchain.mjs';

const temporary: string[] = [];
function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'solana-doctor-test-'));
  temporary.push(root);
  const repo = join(root, 'repo'); const data = join(root, 'data');
  mkdirSync(repo); mkdirSync(data);
  return { root, repo, data };
}
afterEach(() => { for (const root of temporary.splice(0)) rmSync(root, { recursive: true, force: true }); });

describe('read-only Linux development doctor', () => {
  it('requires an explicit absolute root and rejects overlap with the checkout', () => {
    const { root, repo } = fixture();
    for (const input of [undefined, '', './data']) expect(inspectDatasetRoot(input, repo).status).toBe('FAIL');
    for (const input of [repo, join(repo, 'data'), root, '/']) {
      expect(inspectDatasetRoot(input, repo).reason).toBe('DATASET_REPOSITORY_OVERLAP');
    }
  });

  it('inspects the nearest existing parent without creating missing data directories', () => {
    const { root, repo } = fixture();
    const missing = join(root, 'missing', 'nested');
    expect(inspectDatasetRoot(missing, repo)).toMatchObject({ status: 'FAIL', reason: 'DATASET_ROOT_MISSING', inspectedParent: root });
    expect(existsSync(join(root, 'missing'))).toBe(false);
  });

  it('rejects symlinks in any dataset path component and file parents', () => {
    const { root, repo, data } = fixture();
    symlinkSync(data, join(root, 'alias'));
    expect(inspectDatasetRoot(join(root, 'alias'), repo).reason).toBe('DATASET_PATH_SYMLINK');
    expect(inspectDatasetRoot(join(root, 'alias', 'new'), repo).reason).toBe('DATASET_PATH_SYMLINK');
    symlinkSync(repo, join(data, 'checkout'));
    expect(inspectDatasetRoot(join(data, 'checkout'), repo).status).toBe('FAIL');
    writeFileSync(join(data, 'file'), 'preserved');
    expect(inspectDatasetRoot(join(data, 'file'), repo).reason).toBe('DATASET_PARENT_NOT_DIRECTORY');
  });

  it('does not change a valid existing dataset', () => {
    const { repo, data } = fixture();
    writeFileSync(join(data, 'receipt.json'), '{"preserved":true}');
    const before = readdirSync(data);
    expect(inspectDatasetRoot(data, repo)).toMatchObject({ status: 'PASS', exists: true, writable: true });
    expect(readdirSync(data)).toEqual(before);
    expect(readFileSync(join(data, 'receipt.json'), 'utf8')).toBe('{"preserved":true}');
  });

  it('accepts native ext filesystems and rejects Windows/shared mount locations', () => {
    for (const fs of ['ext2', 'ext3', 'ext4']) expect(supportedFilesystem(fs, '/home/user/data')).toBe(true);
    for (const fs of ['drvfs', '9p', 'nfs', 'overlay']) expect(supportedFilesystem(fs, '/home/user/data')).toBe(false);
    expect(supportedFilesystem('ext4', '/mnt/c/data')).toBe(false);
  });

  it('continues independent checks and never invokes a missing Rust toolchain', () => {
    const { data } = fixture(); const calls: string[] = [];
    const command = (name: string, args: string[]) => {
      calls.push([name, ...args].join(' '));
      if (name === 'rustup' && args[0] === '--version') return 'rustup 1.29.1';
      if (name === 'rustup') return 'stable-x86_64-unknown-linux-gnu';
      return null;
    };
    const result = collectDoctor({ datasetRoot: data, command });
    expect(result.status).toBe('FAIL');
    expect(calls.some(call => /^(rustc|cargo|rustfmt) /u.test(call))).toBe(false);
    expect(calls).toContain('python3 --version');
    expect(calls).toContain('pkg-config --version');
    expect(result.checks.find((c: { id: string }) => c.id === 'rust-toolchain')).toMatchObject({ status: 'FAIL' });
  });

  it('accepts only the dataset-root argument and does not serialize ambient secrets', () => {
    expect(parseDoctorArgs([], { SOLANA_QUANT_DATA_ROOT: '/home/user/data' })).toEqual({ datasetRoot: '/home/user/data' });
    expect(parseDoctorArgs(['--dataset-root', '/data'])).toEqual({ datasetRoot: '/data' });
    expect(() => parseDoctorArgs(['--install'])).toThrow('usage');
    const { data } = fixture();
    const result = collectDoctor({ datasetRoot: data, env: { PRIVATE_TEST_SENTINEL: 'do-not-serialize' }, command: () => null });
    expect(JSON.stringify(result)).not.toContain('do-not-serialize');
    expect(result.checks.find((c: { id: string }) => c.id === 'network')).toMatchObject({ providerProbe: 'NOT_RUN' });
  });
});

describe('project-specific toolchain selection', () => {
  it('keeps pins aligned with the existing CI contract', () => {
    const ci = readFileSync(resolve('.github/workflows/ci.yml'), 'utf8');
    expect(ci).toContain(`node-version: '${NODE_VERSION}'`);
    expect(ci).toContain(`rustup toolchain install ${RUST_VERSION} `);
  });

  it('selects isolated tools/caches without mutating the parent environment', () => {
    const env = { PATH: '/existing/node/bin:/usr/bin', CARGO_HOME: '/other/project', SOLANA_TOOLCHAIN_ROOT: '/separate/solana' };
    const selected = developmentEnvironment(env);
    expect(env.CARGO_HOME).toBe('/other/project');
    expect(env.PATH).toBe('/existing/node/bin:/usr/bin');
    expect(selected.CARGO_HOME).toBe('/separate/solana/cargo');
    expect(selected.PATH.split(':').slice(0, 2)).toEqual(['/separate/solana/node-v22.23.2-linux-x64/bin', '/separate/solana/cargo/bin']);
    expect(selected.RUSTUP_AUTO_INSTALL).toBe('0');
    expect(selected.CARGO_NET_OFFLINE).toBe('true');
    expect(selected.npm_config_offline).toBe('true');
    expect(selected.npm_config_cache).toBe('/separate/solana/npm-cache');
    expect(selected.CARGO_BUILD_JOBS).toBe('2');
    expect(() => toolchainPaths({ SOLANA_TOOLCHAIN_ROOT: 'relative' })).toThrow('absolute');
  });

  it('fails before executing a command when project tools are missing', () => {
    const { root } = fixture(); const marker = join(root, 'must-not-exist');
    expect(() => runWithToolchain(['node', '-e', `require('fs').writeFileSync(${JSON.stringify(marker)},'bad')`], {
      ...process.env, SOLANA_TOOLCHAIN_ROOT: root,
    })).toThrow('absent');
    expect(existsSync(marker)).toBe(false);
    expect(readdirSync(root).sort()).toEqual(['data', 'repo']);
  });

  it('selects the query venv without inheriting another project Python environment', () => {
    const env = { PATH: '/usr/bin', SOLANA_TOOLCHAIN_ROOT: '/tools',
      PYTHONHOME: '/other/python', PYTHONPATH: '/other/modules',
      SOLANA_QUANT_DATA_ROOT: '/explicit/data' };
    const selected = developmentEnvironment(env);
    expect(selected.COLUMNAR_QUERY_PYTHON).toBe('/tools/columnar-query-313-duckdb155/bin/python');
    expect(selected.PATH.split(':')[2]).toBe('/tools/columnar-query-313-duckdb155/bin');
    expect(selected.SOLANA_QUANT_DATA_ROOT).toBe('/explicit/data');
    expect(selected.PYTHONHOME).toBeUndefined();
    expect(selected.PYTHONPATH).toBeUndefined();
    expect(selected.PYTHONNOUSERSITE).toBe('1');
    expect(selected.PYTHONDONTWRITEBYTECODE).toBe('1');
    expect(env.PYTHONHOME).toBe('/other/python');
    expect(developmentEnvironment({ COLUMNAR_QUERY_PYTHON: '/custom/bin/python' }).COLUMNAR_QUERY_PYTHON).toBe('/custom/bin/python');
    expect(() => toolchainPaths({ COLUMNAR_QUERY_PYTHON: 'relative/python' })).toThrow('absolute');
  });

  it('requires the reviewed DuckDB reader ABI and rejects missing or different metadata', () => {
    const valid = { implementation: 'CPython', abi: '3.13', python: '3.13.15', duckdb: '1.5.5' };
    expect(validQueryVersion(JSON.stringify(valid))).toBe(true);
    for (const other of [null, {}, { ...valid, abi: '3.12' }, { ...valid, duckdb: '1.5.4' }, { ...valid, implementation: 'PyPy' }]) {
      expect(validQueryVersion(JSON.stringify(other))).toBe(false);
    }
    expect(validQueryVersion('not json')).toBe(false);
  });

  it('reports the query reader independently without opening a dataset or installing Python', () => {
    const { data } = fixture();
    const calls: string[] = [];
    const result = collectDoctor({ datasetRoot: data, env: { COLUMNAR_QUERY_PYTHON: '/query/bin/python' },
      command: (name: string, args: string[]) => {
        calls.push([name, ...args].join(' '));
        if (name === '/query/bin/python') {
          expect(args.slice(0, 3)).toEqual(['-I', '-B', '-c']);
          return JSON.stringify({ implementation: 'CPython', abi: '3.13', python: '3.13.15', duckdb: '1.5.5' });
        }
        return null;
      } });
    expect(result.checks.find((c: { id: string }) => c.id === 'columnar-query')).toMatchObject({ status: 'PASS' });
    expect(calls.some(call => /(^pip\b|\binstall\b)/u.test(call))).toBe(false);
    expect(calls).toContain('uv --version');
  });

  it('rejects unsafe data roots before starting any development child', () => {
    const { root, repo } = fixture();
    const marker = join(root, 'must-not-exist');
    expect(() => runWithToolchain(['node', '-e', `require('fs').writeFileSync(${JSON.stringify(marker)},'bad')`], {
      SOLANA_QUANT_DATA_ROOT: '/', SOLANA_TOOLCHAIN_ROOT: repo,
    })).toThrow('external directory');
    expect(existsSync(marker)).toBe(false);
  });
});
