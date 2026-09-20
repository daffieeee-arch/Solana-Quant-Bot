#!/usr/bin/env node
// Read-only diagnostics: no network clients, installers, env files or write probes.
import { spawnSync } from 'node:child_process';
import { accessSync, constants, existsSync, lstatSync, readFileSync, realpathSync, statfsSync } from 'node:fs';
import { arch, platform, release } from 'node:os';
import { dirname, isAbsolute, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { NODE_VERSION, RUST_VERSION } from './lib/development-toolchain.mjs';

const REPOSITORY = resolve(import.meta.dirname, '..');
const inside = (child, parent) => child === parent || child.startsWith(parent + sep);

export function inspectDatasetRoot(input, repository = REPOSITORY) {
  if (!input || !isAbsolute(input)) return { status: 'FAIL', reason: 'ABSOLUTE_DATASET_ROOT_REQUIRED' };
  const path = resolve(input);
  const repo = realpathSync(repository);
  if (inside(path, repo) || inside(repo, path)) return { status: 'FAIL', reason: 'DATASET_REPOSITORY_OVERLAP' };
  let cursor = path;
  let nearest = null;
  while (true) {
    try {
      const entry = lstatSync(cursor);
      if (entry.isSymbolicLink()) return { status: 'FAIL', reason: 'DATASET_PATH_SYMLINK' };
      if (nearest === null) {
        if (!entry.isDirectory()) return { status: 'FAIL', reason: 'DATASET_PARENT_NOT_DIRECTORY' };
        nearest = cursor;
      }
    } catch (error) {
      if (error.code !== 'ENOENT') return { status: 'FAIL', reason: 'DATASET_PATH_UNREADABLE' };
    }
    const parent = dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  if (nearest === null) return { status: 'FAIL', reason: 'DATASET_PARENT_UNAVAILABLE' };
  const canonical = realpathSync(nearest);
  if (inside(canonical, repo) || (nearest === path && inside(repo, canonical))) {
    return { status: 'FAIL', reason: 'DATASET_REPOSITORY_OVERLAP' };
  }
  let writable = true;
  try { accessSync(nearest, constants.W_OK | constants.X_OK); } catch { writable = false; }
  return {
    status: nearest === path && writable ? 'PASS' : 'FAIL',
    reason: nearest !== path ? 'DATASET_ROOT_MISSING' : writable ? 'DATASET_ROOT_READY' : 'DATASET_ROOT_NOT_WRITABLE',
    path, inspectedParent: nearest, exists: nearest === path, writable,
  };
}

export function supportedFilesystem(type, path) {
  return /^ext[234]$/u.test(type) && path !== '/mnt' && !path.startsWith('/mnt/');
}

function execute(command, args, env) {
  const result = spawnSync(command, args, {
    cwd: REPOSITORY, encoding: 'utf8', timeout: 10_000, maxBuffer: 256 * 1024,
    env: { ...env, RUSTUP_AUTO_INSTALL: '0', CARGO_NET_OFFLINE: 'true', npm_config_offline: 'true',
      npm_config_update_notifier: 'false', npm_config_audit: 'false', UV_OFFLINE: '1' },
  });
  // Do not print errors/ambient configuration: only known version and Git metadata outputs.
  return result.status === 0 ? result.stdout.trim() : null;
}

export function collectDoctor({ datasetRoot, env = process.env, command = execute } = {}) {
  const checks = [];
  const add = (id, status, detail) => checks.push({ id, status, ...detail });
  const run = (name, args) => command(name, args, env);
  const version = (id, binary, args, required, expected) => {
    const output = run(binary, args);
    const firstLine = output?.split('\n')[0] || null;
    const pass = output !== null && (!expected || expected.test(firstLine));
    add(id, pass ? 'PASS' : required ? 'FAIL' : 'INFO', { version: firstLine, ...(id === 'rustc' ? { fullVersion: output } : {}) });
    return output;
  };
  let distro = {};
  try {
    const values = Object.fromEntries(readFileSync('/etc/os-release', 'utf8').split('\n')
      .filter(line => /^(ID|VERSION_ID)=/u.test(line)).map(line => {
        const at = line.indexOf('='); return [line.slice(0, at), line.slice(at + 1).replace(/^"|"$/gu, '')];
      }));
    distro = { id: values.ID, version: values.VERSION_ID };
  } catch { /* OS check remains independently visible. */ }
  add('os', platform() === 'linux' ? 'PASS' : 'FAIL', { platform: platform(), architecture: arch(), kernel: release(),
    wsl: /microsoft/iu.test(release()), distro, ciReference: 'ubuntu-24.04', wslReference: 'ubuntu-26.04' });
  const repo = realpathSync(REPOSITORY);
  const head = run('git', ['rev-parse', 'HEAD']);
  const dirty = run('git', ['status', '--porcelain=v1']);
  add('repository', head && dirty !== null ? 'PASS' : 'FAIL', { path: repo, head,
    branch: run('git', ['branch', '--show-current']), dirty: dirty === null ? null : dirty.length > 0,
    remotes: run('git', ['remote'])?.split('\n').filter(Boolean) || [] });
  const dataset = inspectDatasetRoot(datasetRoot, repo);
  add('dataset-root', dataset.status, dataset);
  for (const [id, path] of [['repository-filesystem', repo], ['dataset-filesystem', dataset.inspectedParent]]) {
    if (!path) { add(id, 'FAIL', { reason: 'NO_SAFE_PATH_TO_INSPECT' }); continue; }
    const type = run('findmnt', ['--noheadings', '--output', 'FSTYPE', '--target', path]);
    let availableBytes = null;
    try { const fs = statfsSync(path, { bigint: true }); availableBytes = (fs.bavail * fs.bsize).toString(); } catch { /* reported below */ }
    add(id, type && supportedFilesystem(type, path) && availableBytes !== null ? 'PASS' : 'FAIL', {
      path, type, availableBytes, budgetComparison: 'NOT_RUN_NO_ACQUISITION_PLAN',
    });
  }
  add('node', process.versions.node === NODE_VERSION ? 'PASS' : 'FAIL', {
    version: process.versions.node, required: NODE_VERSION, executable: process.execPath, abi: process.versions.modules,
  });
  version('npm', 'npm', ['--version'], true, /^\d+\.\d+\.\d+$/u);
  let lockfileVersion = null;
  try { lockfileVersion = JSON.parse(readFileSync(join(repo, 'package-lock.json'), 'utf8')).lockfileVersion; } catch { /* independent failure */ }
  add('npm-lock', lockfileVersion === 3 ? 'PASS' : 'FAIL', { lockfileVersion });
  const rustup = version('rustup', 'rustup', ['--version'], true, /^rustup /u);
  const installed = rustup ? run('rustup', ['toolchain', 'list']) : null;
  const pinnedInstalled = installed?.split('\n').some(line => new RegExp(`^${RUST_VERSION.replaceAll('.', '\\.')}(-|\\s|$)`, 'u').test(line)) || false;
  add('rust-toolchain', pinnedInstalled ? 'PASS' : 'FAIL', { required: RUST_VERSION, installed: pinnedInstalled });
  for (const [id, binary, args, expected] of [
    ['rustc', 'rustc', [`+${RUST_VERSION}`, '-Vv'], /^rustc 1\.97\.1 /u],
    ['cargo', 'cargo', [`+${RUST_VERSION}`, '-V'], /^cargo 1\.97\.1 /u],
    ['rustfmt', 'rustfmt', [`+${RUST_VERSION}`, '--version'], /^rustfmt /u],
    ['clippy', 'cargo', [`+${RUST_VERSION}`, 'clippy', '--version'], /^clippy /u],
  ]) {
    if (pinnedInstalled) version(id, binary, args, true, expected);
    else add(id, 'FAIL', { reason: 'PINNED_TOOLCHAIN_ABSENT_NOT_INVOKED' });
  }
  for (const [id, binary, args] of [
    ['git', 'git', ['--version']], ['curl', 'curl', ['--version']], ['cc', 'cc', ['--version']],
    ['c++', 'c++', ['--version']], ['make', 'make', ['--version']], ['pkg-config', 'pkg-config', ['--version']],
  ]) version(id, binary, args, true);
  add('ca-certificates', existsSync('/etc/ssl/certs/ca-certificates.crt') ? 'PASS' : 'FAIL', { bundlePresent: existsSync('/etc/ssl/certs/ca-certificates.crt') });
  version('python', 'python3', ['--version'], false);
  version('uv', 'uv', ['--version'], false);
  add('python-contract', 'INFO', { pinned: false, candidatePython: '3.13.15', candidateUv: '0.12.5' });
  add('node-dependencies', existsSync(join(repo, 'node_modules/fs-ext/build/Release/fs_ext.node')) ? 'PASS' : 'FAIL', {
    reason: 'PRESENCE_ONLY_NATIVE_ABI_AND_BEHAVIOR_REQUIRE_TESTS',
  });
  const readFlag = path => { try { return readFileSync(path, 'utf8').trim(); } catch { return null; } };
  add('isolation', 'INFO', { seccompActions: readFlag('/proc/sys/kernel/seccomp/actions_avail'),
    apparmorUsernsRestriction: readFlag('/proc/sys/kernel/apparmor_restrict_unprivileged_userns'),
    executionProbe: 'NOT_RUN_READ_ONLY_DOCTOR', bubblewrapRequired: false });
  add('network', 'INFO', { requestedMode: 'OFFLINE_DEVELOPMENT', providerProbe: 'NOT_RUN',
    note: 'Doctor invokes local metadata/version commands only; replay isolation is verified by the separate seccomp gates.' });
  return { schema: 'SOLANA_DEVELOPMENT_DOCTOR_1', status: checks.some(check => check.status === 'FAIL') ? 'FAIL' : 'PASS', checks };
}

export function parseDoctorArgs(args, env = process.env) {
  if (args.length === 0) return { datasetRoot: env.SOLANA_QUANT_DATA_ROOT };
  if (args.length === 2 && args[0] === '--dataset-root') return { datasetRoot: args[1] };
  throw new Error('usage: node scripts/doctor.mjs [--dataset-root <absolute-path>]');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const result = collectDoctor(parseDoctorArgs(process.argv.slice(2)));
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    const failures = result.checks.filter(check => check.status === 'FAIL').map(check => check.id);
    process.stderr.write(`Solana doctor ${result.status}${failures.length ? ': ' + failures.join(', ') : ''}\n`);
    process.exitCode = result.status === 'PASS' ? 0 : 1;
  } catch {
    process.stderr.write('Doctor could not complete; check arguments and filesystem access. No installation attempted.\n');
    process.exitCode = 1;
  }
}
