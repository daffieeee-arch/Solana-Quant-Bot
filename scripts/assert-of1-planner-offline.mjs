#!/usr/bin/env node
// B4 offline planner/durability gate. Reuses the syscall-deny launcher, not B3 domain logic.
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
import { writeResearchNetworkDenyFilter } from './write-research-seccomp-filter.mjs';

const root = resolve(import.meta.dirname, '..');
const crate = join(root, 'rust/of1-range-recorder');
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const MANIFEST_HASH = '116e977dc000c0ca494a154a3abf9df8b1c34d80394053f7e259bedcc582bcd7';
const LOCK_HASH = 'e2db3294a95adeb6c1078cb8ff6834a1fea41ed4d3e00e9fd324a3279e1daf3b';
// Only the reviewed same-test-binary child harness may spawn; runtime code still cannot.
const PROCESS_TEST_HASH = 'c7896fb4c4b0f7b5519f193ad0fd44e08967bd04cf220406542ac208b21c0c9b';

export function validateOf1PlannerInputs(manifest, lock, sources) {
  const errors = [];
  if (hash(manifest) !== MANIFEST_HASH) errors.push('unreviewed OF1 manifest');
  if (hash(lock) !== LOCK_HASH) errors.push('unreviewed OF1 dependency lock');
  for (const [path, source] of Object.entries(sources)) {
    const crashHarness = path === 'tests/durability_process.rs';
    if (crashHarness && hash(source) !== PROCESS_TEST_HASH) errors.push('unreviewed OF1 process-crash harness');
    if (path === 'build.rs' || /\b(?:TcpStream|TcpListener|UdpSocket)\b|std::net|\bunsafe\s*\{|#\s*\[\s*path\s*=/u.test(source)
      || (!crashHarness && /\bCommand\b|std::process::Command/u.test(source))) {
      errors.push(`unexpected runtime capability in ${path}`);
    }
  }
  return errors;
}

function sourcesIn(directory, prefix = '') {
  const sources = {};
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === 'target') continue;
    const path = join(directory, entry.name);
    const relative = prefix + entry.name;
    if (entry.isSymbolicLink()) throw new Error(`symlink in OF1 crate: ${relative}`);
    if (entry.isDirectory()) Object.assign(sources, sourcesIn(path, `${relative}/`));
    else if (entry.name.endsWith('.rs')) sources[relative] = readFileSync(path, 'utf8');
  }
  return sources;
}

function checkInputs() {
  for (let dir = crate; ; dir = dirname(dir)) {
    for (const name of ['config', 'config.toml']) {
      if (existsSync(join(dir, '.cargo', name))) throw new Error('review Cargo configuration before OF1 build');
    }
    if (dir === dirname(dir)) break;
  }
  const errors = validateOf1PlannerInputs(readFileSync(join(crate, 'Cargo.toml')), readFileSync(join(crate, 'Cargo.lock')), sourcesIn(crate));
  if (errors.length) throw new Error(errors.join('\n'));
}

async function run(mode) {
  checkInputs();
  if (mode === '--static') {
    process.stdout.write('OF1 planner manifest/lock/source pre-fetch check PASS\n');
    return;
  }
  const scratch = mkdtempSync(join(tmpdir(), 'of1-planner-offline-'));
  const launcher = join(scratch, 'launcher');
  const filter = join(scratch, 'network-deny.bpf');
  const isolated = (command, args) => {
    const result = spawnSync(launcher, [filter, command, ...args], {
      cwd: root, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, CARGO_NET_OFFLINE: 'true' },
    });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(result.stderr || result.stdout || 'isolated process failed');
    return result;
  };
  try {
    await writeResearchNetworkDenyFilter(filter, process.arch, { allowLocalProcessSpawn: true });
    await buildResearchSeccompLauncher(launcher);
    const probe = isolated(process.execPath, ['--input-type=module', '-e',
      "import net from 'node:net';const s=net.createConnection({host:'127.0.0.1',port:9});s.on('connect',()=>process.exit(2));s.on('error',e=>{if(e.code==='EPERM'){console.log('NETWORK_DENIED');}else process.exit(3);});"]);
    if (probe.stdout !== 'NETWORK_DENIED\n') throw new Error('OF1 isolation probe failed');
    const manifest = ['--manifest-path', join(crate, 'Cargo.toml')];
    const metadata = JSON.parse(isolated('cargo', ['+1.97.1', 'metadata', '--locked', '--offline', '--format-version', '1', ...manifest]).stdout);
    const review = JSON.parse(readFileSync(join(crate, 'dependency-review.json'), 'utf8'));
    const actual = metadata.packages.map(p => ({ name: p.name, version: p.version, license: p.license,
      buildScript: p.targets.some(t => t.kind.includes('custom-build')) })).sort((a,b) => a.name.localeCompare(b.name));
    if (JSON.stringify(actual) !== JSON.stringify(review.packages)) throw new Error('OF1 dependency/license/build-script graph drift');
    isolated('cargo', ['+1.97.1', 'fmt', ...manifest, '--all', '--', '--check']);
    isolated('cargo', ['+1.97.1', 'clippy', ...manifest, '--locked', '--offline', '--all-targets', '--', '-D', 'warnings']);
    const tests = isolated('cargo', ['+1.97.1', 'test', ...manifest, '--locked', '--offline', '--all-targets']);
    process.stdout.write(tests.stdout);
    const build = isolated('cargo', ['+1.97.1', 'build', ...manifest, '--locked', '--offline', '--bins', '--message-format=json-render-diagnostics']);
    const artifacts = build.stdout.split('\n').filter(Boolean).map(s => JSON.parse(s));
    const binary = artifacts
      .find(v => v.reason === 'compiler-artifact' && v.target.name === 'of1-plan-evidence' && v.executable)?.executable;
    if (!binary) throw new Error('OF1 evidence binary missing');
    // Independent fixture writer: LE bytes in JS, interpretation/report generation in Rust.
    const bytes = Buffer.alloc(432000 * 12);
    for (const [record, offset, length] of [[10,128,64],[11,192,80],[13,400,16]]) {
      bytes.writeBigUInt64LE(BigInt(offset), record * 12);
      bytes.writeUInt32LE(length, record * 12 + 8);
    }
    if (hash(bytes) !== '3919f15479264300e451c53ce7b276cf90391488b9cbbd7f4c9c1ea70f41c60c') throw new Error('sealed index fixture drift');
    const index = join(scratch, 'index.raw');
    writeFileSync(index, bytes, { flag: 'wx' });
    for (const [format, path] of [
      ['--json', 'schemas/acquisition/of1/offline-plan-evidence.json'],
      ['--markdown', 'docs/research/OF1_OFFLINE_PLAN_EVIDENCE.md'],
    ]) {
      const first = isolated(binary, [format, index]).stdout;
      const second = isolated(binary, [format, index]).stdout;
      if (first !== second) throw new Error('OF1 report is nondeterministic');
      if (mode === '--print') process.stdout.write(`${path}\n${first}`);
      else if (first !== readFileSync(join(root, path), 'utf8')) throw new Error(`OF1 report drift: ${path}`);
    }
    const durableBinary = artifacts.find(v => v.reason === 'compiler-artifact' && v.target.name === 'of1-durability-evidence' && v.executable)?.executable;
    if (!durableBinary) throw new Error('OF1 durability evidence binary missing');
    for (const [format, path] of [
      ['--json', 'schemas/acquisition/of1/durability-evidence.json'],
      ['--markdown', 'docs/research/OF1_DURABILITY_EVIDENCE.md'],
    ]) {
      const outputs = [0, 1].map(iteration => {
        const directory = join(scratch, `durability-${format}-${iteration}`);
        mkdirSync(directory);
        return isolated(durableBinary, [format, directory]).stdout;
      });
      if (outputs[0] !== outputs[1]) throw new Error('OF1 durability report is nondeterministic');
      if (mode === '--print') process.stdout.write(`${path}\n${outputs[0]}`);
      else if (outputs[0] !== readFileSync(join(root, path), 'utf8')) throw new Error(`OF1 durability report drift: ${path}`);
    }
    process.stdout.write('OF1 planner/durability offline graph/fmt/clippy/tests/evidence PASS\n');
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const mode = process.argv[2] ?? '--all';
  if (process.argv.length > 3 || !['--static', '--all', '--print'].includes(mode)) throw new Error('expected --static, --all or --print');
  run(mode).catch(error => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
}
