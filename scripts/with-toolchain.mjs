#!/usr/bin/env node
// Select already-installed project tools for one child. Never install or edit a profile.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { developmentEnvironment, toolchainPaths, NODE_VERSION, RUST_VERSION, QUERY_VERSION_PROBE, validQueryVersion } from './lib/development-toolchain.mjs';
import { inspectDatasetRoot } from './doctor.mjs';

export function runWithToolchain(args, env = process.env) {
  if (args[0] === '--') args = args.slice(1);
  if (!args.length) throw new Error('usage: node scripts/with-toolchain.mjs -- <command> [args...]');
  if (process.platform !== 'linux' || process.arch !== 'x64') {
    throw new Error('This local toolchain profile requires Linux x86_64');
  }
  const paths = toolchainPaths(env);
  const childEnv = developmentEnvironment(env);
  if (env.SOLANA_QUANT_DATA_ROOT && inspectDatasetRoot(env.SOLANA_QUANT_DATA_ROOT).status !== 'PASS') {
    throw new Error('SOLANA_QUANT_DATA_ROOT must be an existing external directory without symlink components');
  }
  for (const name of ['rustup', 'cargo', 'rustc', 'rustfmt', 'cargo-clippy']) {
    if (!existsSync(join(paths.cargoHome, 'bin', name))) throw new Error('Project Rust proxies are absent; no installation attempted.');
  }
  for (const [binary, expected] of [
    [paths.node, `v${NODE_VERSION}`],
    [join(paths.rustBin, 'rustc'), `rustc ${RUST_VERSION} `],
  ]) {
    if (!existsSync(binary)) throw new Error('Pinned project toolchain is absent; run the doctor. No installation attempted.');
    const check = spawnSync(binary, ['--version'], { env: childEnv, encoding: 'utf8', timeout: 10_000 });
    const version = check.stdout?.trim() || '';
    if (check.status !== 0 || !(expected.endsWith(' ') ? version.startsWith(expected) : version === expected)) {
      throw new Error('Pinned project toolchain version mismatch');
    }
  }
  const query = spawnSync(paths.queryPython, ['-I', '-B', '-c', QUERY_VERSION_PROBE], {
    env: childEnv, encoding: 'utf8', timeout: 10_000, maxBuffer: 64 * 1024,
  });
  if (query.status !== 0 || !validQueryVersion(query.stdout)) {
    throw new Error('Project query reader requires isolated CPython 3.13 and DuckDB 1.5.5; no installation attempted.');
  }
  const child = spawn(args[0], args.slice(1), { env: childEnv, stdio: 'inherit' });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('error', () => { console.error('Unable to start the requested development command'); process.exitCode = 1; });
  child.on('exit', (code, signal) => { process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 143); });
  return child;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { runWithToolchain(process.argv.slice(2)); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
