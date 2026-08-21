#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
import { writeResearchNetworkDenyFilter } from './write-research-seccomp-filter.mjs';

const FORBIDDEN_CRATES = new Set([
  'reqwest', 'hyper', 'hyper-util', 'tokio', 'tokio-tungstenite', 'tungstenite', 'quinn', 'quinn-proto',
  'tonic', 'tonic-prost', 'ureq', 'curl', 'curl-sys', 'isahc', 'surf', 'attohttpc',
  'async-tungstenite', 'websocket', 'ws', 'tower-http',
]);
const FORBIDDEN_SOURCE_PATTERNS = [
  /\buse\s+(?:std::net|reqwest|hyper|tokio::net|tonic|tungstenite|quinn)\b/u,
  /\b(?:std::process::)?Command\s*::\s*new\s*\(/u,
  /\bTcpStream\b|\bUdpSocket\b|\bTcpListener\b/u,
];

function directDependencies(cargoToml) {
  const names = [];
  let inDependencies = false;
  for (const rawLine of cargoToml.split(/\r?\n/u)) {
    const line = rawLine.replace(/#.*$/u, '').trim();
    if (/^\[.*\]$/u.test(line)) {
      inDependencies = line === '[dependencies]';
      continue;
    }
    if (!inDependencies || !line || !line.includes('=')) continue;
    names.push(line.slice(0, line.indexOf('=')).trim().replaceAll('_', '-'));
  }
  return names;
}

export function validatePhase8ARunnerSources({ cargoToml, sources }) {
  const errors = [];
  for (const dependency of directDependencies(cargoToml)) {
    if (FORBIDDEN_CRATES.has(dependency)) errors.push(`forbidden runner dependency:${dependency}`);
  }
  for (const [path, source] of Object.entries(sources)) {
    for (const pattern of FORBIDDEN_SOURCE_PATTERNS) {
      if (pattern.test(source)) errors.push(`forbidden runner source capability:${path}`);
    }
  }
  return [...new Set(errors)].sort();
}

export function resolveBuiltRunnerArtifact(stdout) {
  const matches = [];
  for (const line of stdout.split(/\r?\n/u)) {
    if (!line.trim()) continue;
    let value;
    try { value = JSON.parse(line); } catch { continue; }
    if (value.reason === 'compiler-artifact' && value.target?.name === 'phase8a-bronze-runner' && typeof value.executable === 'string') matches.push(value.executable);
  }
  if (matches.length !== 1) throw new Error(`runner compiler artifact count mismatch:${matches.length}`);
  return matches[0];
}

function rustSources(directory, root = directory) {
  const output = {};
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`forbidden runner source symlink:${path}`);
    if (entry.isDirectory()) Object.assign(output, rustSources(path, root));
    else if (entry.isFile() && entry.name.endsWith('.rs')) output[path.slice(root.length + 1)] = readFileSync(path, 'utf8');
  }
  return output;
}

function reachablePackageNames(metadata, rootName) {
  const rootPackage = metadata.packages.find((pkg) => pkg.name === rootName && pkg.manifest_path.includes('/rust/old-faithful-pump-reducer/'));
  if (!rootPackage) throw new Error('runner package missing from cargo metadata');
  const nodes = new Map(metadata.resolve.nodes.map((node) => [node.id, node]));
  const packages = new Map(metadata.packages.map((pkg) => [pkg.id, pkg.name]));
  const pending = [rootPackage.id];
  const visited = new Set();
  const names = new Set();
  while (pending.length) {
    const id = pending.pop();
    if (visited.has(id)) continue;
    visited.add(id);
    names.add(packages.get(id));
    for (const dep of nodes.get(id)?.deps ?? []) {
      if (dep.dep_kinds.some((kind) => kind.kind === null)) pending.push(dep.pkg);
    }
  }
  return names;
}

function makeWritable(path) {
  if (!statSync(path).isDirectory()) { chmodSync(path, 0o600); return; }
  chmodSync(path, 0o700);
  for (const entry of readdirSync(path)) makeWritable(join(path, entry));
}

async function runCli() {
  const root = resolve(import.meta.dirname, '..');
  const crate = resolve(root, 'rust/old-faithful-pump-reducer');
  const cargoToml = readFileSync(join(crate, 'Cargo.toml'), 'utf8');
  const errors = validatePhase8ARunnerSources({ cargoToml, sources: rustSources(join(crate, 'src')) });
  if (errors.length) throw new Error(errors.join('\n'));

  const metadataResult = spawnSync('cargo', ['+1.97.1', 'metadata', '--locked', '--format-version', '1', '--manifest-path', join(crate, 'Cargo.toml')], { cwd: root, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  if (metadataResult.status !== 0) throw new Error(metadataResult.stderr || 'cargo metadata failed');
  const reachable = reachablePackageNames(JSON.parse(metadataResult.stdout), 'old-faithful-pump-reducer');
  const forbidden = [...reachable].filter((name) => FORBIDDEN_CRATES.has(name)).sort();
  if (forbidden.length) throw new Error(`forbidden runner built dependency graph:${forbidden.join(',')}`);

  const build = spawnSync('cargo', ['+1.97.1', 'build', '--locked', '--message-format=json-render-diagnostics', '--manifest-path', join(crate, 'Cargo.toml'), '--bin', 'phase8a-bronze-runner'], { cwd: root, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  if (build.status !== 0) throw new Error(build.stderr || 'runner build failed');
  const binary = resolveBuiltRunnerArtifact(build.stdout);
  if (!statSync(binary).isFile()) throw new Error('runner compiler artifact is not a regular file');

  const scratch = mkdtempSync(join(tmpdir(), 'phase8a-runner-offline-'));
  const filter = join(scratch, 'network-deny.bpf');
  const launcher = join(scratch, 'seccomp-launcher');
  const output = join(scratch, 'output');
  try {
    await writeResearchNetworkDenyFilter(filter);
    await buildResearchSeccompLauncher(launcher);
    const fixture = join(root, 'tests/fixtures/phase8a/bronze-runner-rich.json');
    const result = spawnSync(launcher, [filter, binary, '--input', fixture, '--output', output], { cwd: root, encoding: 'utf8', env: { ...process.env, NO_PROXY: '*', HTTPS_PROXY: 'http://127.0.0.1:1', HTTP_PROXY: 'http://127.0.0.1:1' } });
    if (result.status !== 0 || result.stderr !== '') throw new Error(result.stderr || `runner seccomp execution failed:${String(result.status)}`);
    const verdict = JSON.parse(result.stdout);
    if (verdict.status !== 'SUCCEEDED' || verdict.sourceClass !== 'SYNTHETIC_FIXTURE_ONLY' || verdict.acceptedSilver !== false || verdict.researchReady !== false) {
      throw new Error('runner seccomp verdict drift');
    }
  } finally {
    if (statSync(scratch).isDirectory()) makeWritable(scratch);
    rmSync(scratch, { recursive: true, force: true });
  }
  process.stdout.write('Phase 8A Rust runner offline source/built graph PASS\n');
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === fileURLToPath(import.meta.url)) {
  runCli().catch((error) => { process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`); process.exit(1); });
}
