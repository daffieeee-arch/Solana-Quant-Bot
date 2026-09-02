import {
  mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  collectPumpProtocolRustSources,
  resolveEvidenceBinary,
  validatePumpProtocolGraph,
  validatePumpProtocolSources,
} from '../scripts/assert-pump-protocol-v2-offline.mjs';
import { researchNetworkDenyFilter } from '../scripts/write-research-seccomp-filter.mjs';

const SAFE_MANIFEST = readFileSync(
  resolve(import.meta.dirname, '../rust/pump-protocol-v2/Cargo.toml'),
  'utf8',
);
const SAFE_LOCK = readFileSync(
  resolve(import.meta.dirname, '../rust/pump-protocol-v2/Cargo.lock'),
  'utf8',
);

function deniedSyscalls(filter: Buffer): number[] {
  const syscalls: number[] = [];
  for (let instruction = 4; instruction < (filter.length / 8) - 1; instruction += 2) {
    syscalls.push(filter.readUInt32LE((instruction * 8) + 4));
  }
  return syscalls;
}

describe('Pump protocol v2 offline boundary', () => {
  it('accepts only the reviewed direct dependencies, lock graph and inert Rust source', () => {
    expect(validatePumpProtocolSources({
      cargoToml: SAFE_MANIFEST,
      cargoLock: SAFE_LOCK,
      sources: { 'src/lib.rs': 'pub fn decode(bytes: &[u8]) -> usize { bytes.len() }' },
    })).toEqual([]);
  });

  it('rejects unreviewed dependency sources and runtime network/process capabilities', () => {
    const unreviewedLock = `${SAFE_LOCK}\n[[package]]\nname = "unreviewed"\nversion = "1.0.0"\nsource = "git+https://example.invalid/repository"\n`;
    expect(validatePumpProtocolSources({
      cargoToml: SAFE_MANIFEST.replace(
        '\n[lints.rust]',
        '\nreqwest = "0.12"\n\n[lints.rust]',
      ),
      cargoLock: unreviewedLock,
      sources: {
        'src/net.rs': 'use std::net::TcpStream;',
        'tests/process.rs': 'std::process::Command::new("probe");',
      },
    }).join('\n')).toMatch(/unreviewed Pump protocol direct dependency:reqwest/);
    expect(validatePumpProtocolSources({
      cargoToml: SAFE_MANIFEST,
      cargoLock: unreviewedLock,
      sources: { 'tests/net.rs': 'use std::net::TcpStream;' },
    }).join('\n')).toMatch(/lock source|source capability/);
  });

  it('rejects build, dev, target, path and build-script capability before fetch', () => {
    for (const section of [
      '[build-dependencies]\ncc = "1"',
      '[dev-dependencies]\nreqwest = "0.12"',
      "[target.'cfg(unix)'.dependencies]\nlibc = \"0.2\"",
    ]) {
      expect(validatePumpProtocolSources({
        cargoToml: `${SAFE_MANIFEST}\n${section}\n`,
        cargoLock: SAFE_LOCK,
        sources: { 'src/lib.rs': 'pub fn bounded() {}' },
      }).join('\n')).toMatch(/forbidden Pump protocol dependency section/);
    }

    expect(validatePumpProtocolSources({
      cargoToml: SAFE_MANIFEST.replace(
        'borsh = { version = "=1.8.0", features = ["derive"] }',
        'borsh = { path = "../borsh" }',
      ),
      cargoLock: SAFE_LOCK,
      sources: { 'src/lib.rs': 'pub fn bounded() {}' },
    }).join('\n')).toMatch(/forbidden Pump protocol dependency declaration:dependencies.borsh/);
    expect(validatePumpProtocolSources({
      cargoToml: SAFE_MANIFEST.replace('hex = "=0.4.3"', 'hex = "0.4"'),
      cargoLock: SAFE_LOCK,
      sources: { 'src/lib.rs': 'pub fn bounded() {}' },
    }).join('\n')).toMatch(/dependency declaration drift:hex/);
    const sourceErrors = validatePumpProtocolSources({
      cargoToml: SAFE_MANIFEST,
      cargoLock: SAFE_LOCK,
      sources: {
        'build.rs': 'fn main() {}',
        'tests/network.rs': 'use std::net::TcpStream;',
      },
    }).join('\n');
    expect(sourceErrors).toMatch(/build script:build\.rs/);
    expect(sourceErrors).toMatch(/source capability:tests\/network\.rs/);

    for (const externalSource of [
      '#[path = "../../../outside.rs"] mod outside;',
      'include!("../../../outside.rs");',
      '#[cfg_attr(unix, path = "../../../outside.rs")] mod outside;',
      'use std::include as inc; inc!("../../../outside.rs");',
      'include_str!("../../../outside.rs");',
      'include_bytes!("../../../outside.rs");',
    ]) {
      expect(validatePumpProtocolSources({
        cargoToml: SAFE_MANIFEST,
        cargoLock: SAFE_LOCK,
        sources: { 'src/lib.rs': externalSource },
      }).join('\n')).toMatch(/source capability:src\/lib\.rs/);
    }
  });

  it('scans a nested source directory named target while skipping only build output', () => {
    const crate = mkdtempSync(join(tmpdir(), 'pump-protocol-source-scan-'));
    try {
      mkdirSync(join(crate, 'target'), { recursive: true });
      writeFileSync(join(crate, 'target', 'ignored.rs'), 'use std::net::TcpStream;');
      mkdirSync(join(crate, 'src', 'target'), { recursive: true });
      writeFileSync(join(crate, 'src', 'lib.rs'), 'mod target;');
      writeFileSync(join(crate, 'src', 'target', 'mod.rs'), 'use std::net::TcpStream;');

      const sources = collectPumpProtocolRustSources(crate);
      expect(Object.keys(sources).sort()).toEqual(['src/lib.rs', 'src/target/mod.rs']);
      expect(validatePumpProtocolSources({
        cargoToml: SAFE_MANIFEST,
        cargoLock: SAFE_LOCK,
        sources,
      }).join('\n')).toMatch(/source capability:src\/target\/mod\.rs/);
    } finally {
      rmSync(crate, { recursive: true, force: true });
    }
  });

  it('rejects feature, dependency-subtable, patch and external-target manifest drift', () => {
    for (const manifestAddition of [
      '[features]\ndefault = ["serde/rc"]',
      '[dependencies.alias]\npackage = "hex"\nversion = "=0.4.3"',
      '[patch.crates-io]\nserde = { path = "../serde" }',
      '[[bin]]\nname = "outside"\npath = "../outside.rs"',
    ]) {
      expect(validatePumpProtocolSources({
        cargoToml: `${SAFE_MANIFEST}\n${manifestAddition}\n`,
        cargoLock: SAFE_LOCK,
        sources: { 'src/lib.rs': 'pub fn bounded() {}' },
      }).join('\n')).toMatch(/Cargo\.toml differs from the reviewed bounded manifest/);
    }
  });

  it('pins the complete lock bytes before parsing package tables', () => {
    const hiddenTableHeader = SAFE_LOCK.replace('[[package]]', '[[package]] # hidden');
    expect(validatePumpProtocolSources({
      cargoToml: SAFE_MANIFEST,
      cargoLock: hiddenTableHeader,
      sources: { 'src/lib.rs': 'pub fn bounded() {}' },
    })).toContain('Pump protocol Cargo.lock differs from the reviewed bounded lock bytes');
  });

  it('rejects Cargo configuration on the pre-fetch search path', () => {
    expect(validatePumpProtocolSources({
      cargoToml: SAFE_MANIFEST,
      cargoLock: SAFE_LOCK,
      sources: { 'src/lib.rs': 'pub fn bounded() {}' },
      cargoConfigPaths: ['/workspace/.cargo/config.toml'],
    })).toContain(
      'unreviewed Cargo configuration on Pump protocol pre-fetch path:/workspace/.cargo/config.toml',
    );
  });

  it('requires exact reachable parity with the reviewed lock graph', () => {
    const identities = [...SAFE_LOCK.matchAll(
      /\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"/gu,
    )].map((match) => `${match[1]}@${match[2]}`);
    expect(validatePumpProtocolGraph(identities)).toEqual([]);

    expect(validatePumpProtocolGraph(identities.slice(1)).join('\n')).toMatch(
      /missing reviewed Pump protocol dependency graph:/,
    );
    expect(validatePumpProtocolGraph([...identities, 'unexpected@1.0.0']).join('\n')).toMatch(
      /unreviewed Pump protocol dependency graph:unexpected@1\.0\.0/,
    );
  });

  it('requires exactly one compiler artifact for the evidence binary', () => {
    const artifact = JSON.stringify({
      reason: 'compiler-artifact',
      target: { name: 'pump-protocol-evidence' },
      executable: '/tmp/pump-protocol-evidence',
    });
    expect(resolveEvidenceBinary(`${artifact}\n`)).toBe('/tmp/pump-protocol-evidence');
    expect(() => resolveEvidenceBinary('')).toThrow(/count mismatch:0/);
    expect(() => resolveEvidenceBinary(`${artifact}\n${artifact}\n`)).toThrow(/count mismatch:2/);
  });

  it('allows only local fork/exec socketpair I/O while external network creation remains denied', () => {
    const x64 = deniedSyscalls(researchNetworkDenyFilter('x64', { allowLocalProcessSpawn: true }));
    expect(x64).toEqual(expect.arrayContaining([41, 42, 43, 49, 50]));
    expect(x64).not.toEqual(expect.arrayContaining([44, 45, 46, 47, 48, 53]));

    const arm64 = deniedSyscalls(researchNetworkDenyFilter('arm64', { allowLocalProcessSpawn: true }));
    expect(arm64).toEqual(expect.arrayContaining([198, 200, 201, 202, 203]));
    expect(arm64).not.toEqual(expect.arrayContaining([199, 206, 207, 210, 211, 212]));
  });
});
