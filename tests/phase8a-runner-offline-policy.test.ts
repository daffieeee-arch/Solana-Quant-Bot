import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  resolveBuiltRunnerArtifact,
  validatePhase8ARunnerSources,
} from '../scripts/assert-phase8a-runner-offline.mjs';

const safeCargo = `[dependencies]\nserde = "1"\nsha2 = "0.10"\n`;
const safeSources = {
  'src/phase8a_runner.rs': 'use std::{fs::File, io::Read};\npub fn run() {}\n',
  'src/bin/phase8a-bronze-runner.rs': 'fn main() { crate_name::run(); }\n',
};

describe('Phase 8A Rust runner offline policy', () => {
  it('binds execution to Cargo compiler-artifact output under a custom target directory', () => {
    const custom = '/tmp/custom-target/debug/phase8a-bronze-runner';
    const stdout = [
      JSON.stringify({ reason: 'compiler-artifact', target: { name: 'dependency' }, executable: '/tmp/custom-target/debug/dependency' }),
      JSON.stringify({ reason: 'compiler-artifact', target: { name: 'phase8a-bronze-runner' }, executable: custom }),
      JSON.stringify({ reason: 'build-finished', success: true }),
    ].join('\n');
    expect(resolveBuiltRunnerArtifact(stdout)).toBe(custom);
    expect(() => resolveBuiltRunnerArtifact(JSON.stringify({ reason: 'build-finished', success: true }))).toThrow(/artifact/i);
  });
  it('pins the explicit fixture command and executes the offline binary gate during build', () => {
    const pkg = JSON.parse(readFileSync('package.json', 'utf8')) as any;
    expect(pkg.scripts['research:pilot-a:fixture']).toBe('cargo +1.97.1 run --quiet --locked --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --bin phase8a-bronze-runner --');
    expect(pkg.scripts['verify:phase8a-runner-offline']).toBe('node scripts/assert-phase8a-runner-offline.mjs');
    expect(pkg.scripts.build).toContain('npm run verify:phase8a-runner-offline');
  });
  it('accepts the minimal offline source and dependency graph', () => {
    expect(validatePhase8ARunnerSources({ cargoToml: safeCargo, sources: safeSources })).toEqual([]);
  });

  it.each([
    ['network dependency', `${safeCargo}reqwest = "0.12"`, safeSources],
    ['async network runtime', `${safeCargo}tokio = "1"`, safeSources],
    ['direct TCP import', safeCargo, { ...safeSources, 'src/phase8a_runner.rs': 'use std::net::TcpStream;\n' }],
    ['HTTP client import', safeCargo, { ...safeSources, 'src/phase8a_runner.rs': 'use reqwest::Client;\n' }],
    ['process spawning', safeCargo, { ...safeSources, 'src/phase8a_runner.rs': 'std::process::Command::new("sh");\n' }],
    ['shell command', safeCargo, { ...safeSources, 'src/phase8a_runner.rs': 'Command::new("curl");\n' }],
  ])('rejects %s', (_name, cargoToml, sources) => {
    expect(validatePhase8ARunnerSources({ cargoToml, sources }).join('\n')).toMatch(/forbidden/i);
  });
});
