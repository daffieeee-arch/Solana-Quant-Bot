#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  chmodSync, existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
import { writeResearchNetworkDenyFilter } from './write-research-seccomp-filter.mjs';

const ALLOWED_DIRECT_DEPENDENCIES = new Set(['borsh', 'hex', 'serde', 'serde-json', 'sha2', 'thiserror']);
const REVIEWED_CARGO_TOML = `[package]
name = "pump-protocol-v2"
version = "0.1.0"
edition = "2024"
publish = false
license = "UNLICENSED"

[lib]
path = "src/lib.rs"

[[bin]]
name = "pump-protocol-evidence"
path = "src/bin/pump-protocol-evidence.rs"

[dependencies]
borsh = { version = "=1.8.0", features = ["derive"] }
hex = "=0.4.3"
serde = { version = "=1.0.229", features = ["derive"] }
serde_json = "=1.0.151"
sha2 = "=0.10.9"
thiserror = "=2.0.20"

[lints.rust]
unsafe_code = "forbid"

[lints.clippy]
all = "deny"
pedantic = "deny"
`;
const REVIEWED_DIRECT_DECLARATIONS = new Map([
  ['borsh', '{ version = "=1.8.0", features = ["derive"] }'],
  ['hex', '"=0.4.3"'],
  ['serde', '{ version = "=1.0.229", features = ["derive"] }'],
  ['serde-json', '"=1.0.151"'],
  ['sha2', '"=0.10.9"'],
  ['thiserror', '"=2.0.20"'],
]);
const REVIEWED_CARGO_LOCK_SHA256 = 'b1c990f36986a4d7e306625fe69d12bf16f67ae5aac71f600b3b1b063783033a';
const REVIEWED_LOCK_PACKAGES = new Set([
  'block-buffer@0.10.4', 'borsh@1.8.0', 'borsh-derive@1.8.1', 'bytes@1.12.1',
  'cfg-if@1.0.4', 'cfg_aliases@0.2.2', 'cpufeatures@0.2.17', 'crypto-common@0.1.7',
  'digest@0.10.7', 'equivalent@1.0.2', 'generic-array@0.14.7', 'hashbrown@0.17.1',
  'hex@0.4.3', 'indexmap@2.14.1', 'itoa@1.0.18', 'libc@0.2.189', 'memchr@2.8.3',
  'once_cell@1.21.4', 'proc-macro-crate@3.5.0', 'proc-macro2@1.0.107',
  'pump-protocol-v2@0.1.0', 'quote@1.0.47', 'serde@1.0.229', 'serde_core@1.0.229',
  'serde_derive@1.0.229', 'serde_json@1.0.151', 'sha2@0.10.9', 'syn@3.0.4',
  'thiserror@2.0.20', 'thiserror-impl@2.0.20', 'toml_datetime@1.1.1+spec-1.1.0',
  'toml_edit@0.25.13+spec-1.1.0', 'toml_parser@1.1.3+spec-1.1.0', 'typenum@1.20.1',
  'unicode-ident@1.0.24', 'version_check@0.9.5', 'winnow@1.0.4', 'zmij@1.0.23',
]);
const FORBIDDEN_CRATES = new Set([
  'reqwest', 'hyper', 'hyper-util', 'tokio', 'tokio-tungstenite', 'tungstenite', 'tonic',
  'quinn', 'ureq', 'curl', 'curl-sys', 'isahc', 'surf', 'attohttpc', 'websocket',
  'solana-client', 'solana-rpc-client', 'solana-rpc-client-api', 'yellowstone-grpc-client',
  'jupiter-swap-api-client', 'helius', 'helius-rust-sdk',
]);
const FORBIDDEN_SOURCE_PATTERNS = [
  /\buse\s+(?:std::net|reqwest|hyper|tokio::net|tonic|tungstenite|quinn)\b/u,
  /\b(?:std::process::)?Command\s*::\s*new\s*\(/u,
  /\bTcpStream\b|\bUdpSocket\b|\bTcpListener\b/u,
  /\bpath\s*=/u,
  /\binclude\b/u,
  /\binclude_(?:str|bytes)\b/u,
];
const CRATES_IO_SOURCE = 'registry+https://github.com/rust-lang/crates.io-index';

function dependencySection(section) {
  return section === 'dependencies'
    || section === 'dev-dependencies'
    || section === 'build-dependencies'
    || section.endsWith('.dependencies')
    || section.endsWith('.dev-dependencies')
    || section.endsWith('.build-dependencies');
}

function manifestDependencies(cargoToml) {
  const direct = [];
  const declarations = new Map();
  const forbiddenSections = [];
  const forbiddenDeclarations = [];
  let section = '';
  for (const rawLine of cargoToml.split(/\r?\n/u)) {
    const line = rawLine.replace(/#.*$/u, '').trim();
    const header = line.match(/^\[([^\]]+)\]$/u);
    if (header) {
      section = header[1];
      if (dependencySection(section) && section !== 'dependencies') forbiddenSections.push(section);
      continue;
    }
    if (!line || !line.includes('=')) continue;
    const key = line.slice(0, line.indexOf('=')).trim();
    const value = line.slice(line.indexOf('=') + 1).trim();
    if (section === 'package' && key === 'build') forbiddenDeclarations.push('package.build');
    if (section !== 'dependencies') continue;
    const normalized = key.replaceAll('_', '-');
    direct.push(normalized);
    if (declarations.has(normalized)) forbiddenDeclarations.push(`duplicate:${normalized}`);
    declarations.set(normalized, value);
    if (/\b(?:git|path|registry)\s*=/u.test(value)) {
      forbiddenDeclarations.push(`dependencies.${normalized}`);
    }
  }
  return { direct, declarations, forbiddenSections, forbiddenDeclarations };
}

function lockPackages(cargoLock) {
  const packages = [];
  for (const block of cargoLock.split(/(?=^\[\[package\]\]$)/gmu)) {
    if (!block.startsWith('[[package]]')) continue;
    const name = block.match(/^name = "([^"]+)"$/mu)?.[1];
    const version = block.match(/^version = "([^"]+)"$/mu)?.[1];
    const source = block.match(/^source = "([^"]+)"$/mu)?.[1] ?? null;
    if (!name || !version) throw new Error('invalid Pump protocol Cargo.lock package block');
    packages.push({ name, version, source, identity: `${name}@${version}` });
  }
  return packages;
}

export function validatePumpProtocolSources({
  cargoToml, cargoLock, sources, cargoConfigPaths = [],
}) {
  const errors = [];
  for (const path of cargoConfigPaths) {
    errors.push(`unreviewed Cargo configuration on Pump protocol pre-fetch path:${path}`);
  }
  if (cargoToml !== REVIEWED_CARGO_TOML) {
    errors.push('Pump protocol Cargo.toml differs from the reviewed bounded manifest');
  }
  const cargoLockSha256 = createHash('sha256').update(cargoLock).digest('hex');
  if (cargoLockSha256 !== REVIEWED_CARGO_LOCK_SHA256) {
    errors.push('Pump protocol Cargo.lock differs from the reviewed bounded lock bytes');
  }
  const {
    direct, declarations, forbiddenSections, forbiddenDeclarations,
  } = manifestDependencies(cargoToml);
  for (const section of forbiddenSections) {
    errors.push(`forbidden Pump protocol dependency section:${section}`);
  }
  for (const declaration of forbiddenDeclarations) {
    errors.push(`forbidden Pump protocol dependency declaration:${declaration}`);
  }
  for (const dependency of direct) {
    if (!ALLOWED_DIRECT_DEPENDENCIES.has(dependency)) {
      errors.push(`unreviewed Pump protocol direct dependency:${dependency}`);
    }
  }
  for (const required of ALLOWED_DIRECT_DEPENDENCIES) {
    if (!direct.includes(required)) errors.push(`missing reviewed Pump protocol dependency:${required}`);
    const expected = REVIEWED_DIRECT_DECLARATIONS.get(required);
    if (declarations.has(required) && declarations.get(required) !== expected) {
      errors.push(`Pump protocol dependency declaration drift:${required}`);
    }
  }

  const locked = lockPackages(cargoLock);
  const lockedIdentities = new Set();
  for (const pkg of locked) {
    if (lockedIdentities.has(pkg.identity)) {
      errors.push(`duplicate Pump protocol lock package:${pkg.identity}`);
    }
    lockedIdentities.add(pkg.identity);
    if (pkg.name === 'pump-protocol-v2' && pkg.source !== null) {
      errors.push(`unexpected Pump protocol root lock source:${String(pkg.source)}`);
    } else if (pkg.name !== 'pump-protocol-v2' && pkg.source !== CRATES_IO_SOURCE) {
      errors.push(`unreviewed Pump protocol lock source:${pkg.identity}:${String(pkg.source)}`);
    }
    if (!REVIEWED_LOCK_PACKAGES.has(pkg.identity)) {
      errors.push(`unreviewed Pump protocol lock package:${pkg.identity}`);
    }
    if (FORBIDDEN_CRATES.has(pkg.name)) {
      errors.push(`forbidden Pump protocol lock package:${pkg.name}`);
    }
  }
  for (const identity of REVIEWED_LOCK_PACKAGES) {
    if (!lockedIdentities.has(identity)) errors.push(`missing reviewed Pump protocol lock package:${identity}`);
  }

  for (const [path, source] of Object.entries(sources)) {
    if (path === 'build.rs' || path.endsWith('/build.rs')) {
      errors.push(`forbidden Pump protocol build script:${path}`);
    }
    for (const pattern of FORBIDDEN_SOURCE_PATTERNS) {
      if (pattern.test(source)) errors.push(`forbidden Pump protocol source capability:${path}`);
    }
  }
  return [...new Set(errors)].sort();
}

function existingCargoConfigurationPaths(root) {
  const candidates = new Set();
  let directory = resolve(root);
  while (true) {
    candidates.add(join(directory, '.cargo', 'config.toml'));
    candidates.add(join(directory, '.cargo', 'config'));
    const parent = dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  if (process.env.CARGO_HOME) {
    candidates.add(join(resolve(process.env.CARGO_HOME), 'config.toml'));
    candidates.add(join(resolve(process.env.CARGO_HOME), 'config'));
  }
  return [...candidates].filter(existsSync).sort();
}

export function resolveEvidenceBinary(stdout) {
  const matches = [];
  for (const line of stdout.split(/\r?\n/u)) {
    if (!line.trim()) continue;
    let value;
    try {
      value = JSON.parse(line);
    } catch {
      continue;
    }
    if (value.reason === 'compiler-artifact'
      && value.target?.name === 'pump-protocol-evidence'
      && typeof value.executable === 'string') {
      matches.push(value.executable);
    }
  }
  if (matches.length !== 1) throw new Error(`Pump protocol evidence artifact count mismatch:${matches.length}`);
  return matches[0];
}

export function collectPumpProtocolRustSources(directory, root = directory) {
  const output = {};
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (directory === root && entry.name === 'target') continue;
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`forbidden Pump protocol source symlink:${path}`);
    if (entry.isDirectory()) Object.assign(output, collectPumpProtocolRustSources(path, root));
    else if (entry.isFile() && entry.name.endsWith('.rs')) {
      output[path.slice(root.length + 1)] = readFileSync(path, 'utf8');
    }
  }
  return output;
}

function reachablePackageIdentities(metadata) {
  const rootPackage = metadata.packages.find((pkg) => pkg.name === 'pump-protocol-v2'
    && pkg.manifest_path.endsWith('/rust/pump-protocol-v2/Cargo.toml'));
  if (!rootPackage) throw new Error('Pump protocol package missing from cargo metadata');
  const nodes = new Map(metadata.resolve.nodes.map((node) => [node.id, node]));
  const packages = new Map(metadata.packages.map((pkg) => [pkg.id, `${pkg.name}@${pkg.version}`]));
  const pending = [rootPackage.id];
  const visited = new Set();
  const identities = new Set();
  while (pending.length > 0) {
    const id = pending.pop();
    if (visited.has(id)) continue;
    visited.add(id);
    const identity = packages.get(id);
    if (!identity) throw new Error(`Pump protocol dependency node has no package:${id}`);
    identities.add(identity);
    for (const dependency of nodes.get(id)?.deps ?? []) pending.push(dependency.pkg);
  }
  return identities;
}

export function validatePumpProtocolGraph(reachableIdentities) {
  const reachable = new Set(reachableIdentities);
  const errors = [];
  for (const identity of [...reachable].sort()) {
    if (!REVIEWED_LOCK_PACKAGES.has(identity)) {
      errors.push(`unreviewed Pump protocol dependency graph:${identity}`);
    }
    const name = identity.slice(0, identity.lastIndexOf('@'));
    if (FORBIDDEN_CRATES.has(name)) {
      errors.push(`forbidden Pump protocol dependency graph:${identity}`);
    }
  }
  for (const identity of [...REVIEWED_LOCK_PACKAGES].sort()) {
    if (!reachable.has(identity)) {
      errors.push(`missing reviewed Pump protocol dependency graph:${identity}`);
    }
  }
  return errors;
}

function makeWritable(path) {
  if (!statSync(path).isDirectory()) {
    chmodSync(path, 0o600);
    return;
  }
  chmodSync(path, 0o700);
  for (const entry of readdirSync(path)) makeWritable(join(path, entry));
}

function runIsolated(launcher, filter, command, args, root, maxBuffer = 16 * 1024 * 1024) {
  const result = spawnSync(launcher, [filter, command, ...args], {
    cwd: root,
    encoding: 'utf8',
    maxBuffer,
    env: {
      ...process.env,
      CARGO_NET_OFFLINE: 'true',
      NO_PROXY: '*',
      HTTPS_PROXY: 'http://127.0.0.1:1',
      HTTP_PROXY: 'http://127.0.0.1:1',
    },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `${command} failed:${String(result.status)}`);
  }
  return result;
}

function loadAndValidate(root, crate) {
  const cargoToml = readFileSync(join(crate, 'Cargo.toml'), 'utf8');
  const cargoLock = readFileSync(join(crate, 'Cargo.lock'), 'utf8');
  const errors = validatePumpProtocolSources({
    cargoToml,
    cargoLock,
    sources: collectPumpProtocolRustSources(crate),
    cargoConfigPaths: existingCargoConfigurationPaths(root),
  });
  if (errors.length > 0) throw new Error(errors.join('\n'));
  return lockPackages(cargoLock);
}

async function runStatic(root, crate) {
  const packages = loadAndValidate(root, crate);
  process.stdout.write(`Pump protocol v2 pre-fetch dependency/source policy PASS (${packages.length} locked packages)\n`);
}

async function runFull(root, crate) {
  loadAndValidate(root, crate);
  const manifestArgs = ['--manifest-path', join(crate, 'Cargo.toml')];
  const scratch = mkdtempSync(join(tmpdir(), 'pump-protocol-v2-offline-'));
  const filter = join(scratch, 'network-deny.bpf');
  const launcher = join(scratch, 'seccomp-launcher');
  try {
    // Rust's process launcher uses a local socketpair to report exec failures.
    // External socket/connect/bind syscalls remain denied for the full tree.
    await writeResearchNetworkDenyFilter(filter, process.arch, { allowLocalProcessSpawn: true });
    await buildResearchSeccompLauncher(launcher);

    const networkProbe = runIsolated(launcher, filter, process.execPath, [
      '--input-type=module',
      '--eval',
      "import net from 'node:net'; const socket = net.createConnection({ host: '127.0.0.1', port: 9 }); socket.once('connect', () => process.exit(2)); socket.once('error', (error) => { if (error.code === 'EPERM') { process.stdout.write('NETWORK_DENIED\\n'); process.exit(0); } process.exit(3); });",
    ], root);
    if (networkProbe.stdout !== 'NETWORK_DENIED\n' || networkProbe.stderr !== '') {
      throw new Error('Pump protocol network-deny probe drift');
    }

    const metadataResult = runIsolated(launcher, filter, 'cargo', [
      '+1.97.1', 'metadata', '--locked', '--offline', '--format-version', '1', ...manifestArgs,
    ], root);
    const reachable = reachablePackageIdentities(JSON.parse(metadataResult.stdout));
    const graphErrors = validatePumpProtocolGraph(reachable);
    if (graphErrors.length > 0) throw new Error(graphErrors.join('\n'));

    runIsolated(launcher, filter, 'cargo', [
      '+1.97.1', 'clippy', '--locked', '--offline', '--all-targets', ...manifestArgs, '--', '-D', 'warnings',
    ], root);
    runIsolated(launcher, filter, 'cargo', [
      '+1.97.1', 'test', '--locked', '--offline', '--all-targets', ...manifestArgs,
    ], root);
    const build = runIsolated(launcher, filter, 'cargo', [
      '+1.97.1', 'build', '--locked', '--offline', '--message-format=json-render-diagnostics',
      ...manifestArgs, '--bin', 'pump-protocol-evidence',
    ], root);
    const binary = resolveEvidenceBinary(build.stdout);
    if (!statSync(binary).isFile()) throw new Error('Pump protocol evidence artifact is not a regular file');
    const evidence = runIsolated(launcher, filter, binary, ['--check'], root);
    if (evidence.stderr !== '' || evidence.stdout !== 'Pump protocol evidence matrix --check PASS\n') {
      throw new Error(evidence.stderr || 'Pump protocol evidence output drift');
    }
    process.stdout.write(
      `Pump protocol v2 isolated graph/clippy/tests/evidence PASS (${reachable.size} all-edge packages)\n`,
    );
  } finally {
    makeWritable(scratch);
    rmSync(scratch, { recursive: true, force: true });
  }
}

async function runCli() {
  const root = resolve(import.meta.dirname, '..');
  const crate = resolve(root, 'rust/pump-protocol-v2');
  const mode = process.argv[2] ?? '--all';
  if (process.argv.length > 3 || !['--all', '--static'].includes(mode)) {
    throw new Error('usage: assert-pump-protocol-v2-offline.mjs [--static|--all]');
  }
  if (mode === '--static') await runStatic(root, crate);
  else await runFull(root, crate);
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === fileURLToPath(import.meta.url)) {
  runCli().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
}
