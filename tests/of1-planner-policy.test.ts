import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { validateOf1PlannerInputs } from '../scripts/assert-of1-planner-offline.mjs';

const manifest = readFileSync('rust/of1-range-recorder/Cargo.toml');
const lock = readFileSync('rust/of1-range-recorder/Cargo.lock');

describe('OF1 default-off transport dependency boundary', () => {
  it('accepts the reviewed manifest and lock', () => {
    expect(validateOf1PlannerInputs(manifest, lock, {})).toEqual([]);
  });
  it('rejects manifest, feature, dependency and lock drift before fetch', () => {
    for (const suffix of ['\n[features]\nnetwork-of1 = []\n', '\n[build-dependencies]\nreqwest="1"\n', '\n[patch.crates-io]\n']) {
      expect(validateOf1PlannerInputs(manifest + suffix, lock, {})).toContain('unreviewed OF1 manifest');
    }
    expect(validateOf1PlannerInputs(manifest, lock + '\n', {})).toContain('unreviewed OF1 dependency lock');
  });
  it('rejects crate-owned network, command and build capabilities', () => {
    for (const input of ['std::net::TcpStream', 'Command::new("curl")', 'unsafe { something(); }', '#[path="external.rs"]']) {
      expect(validateOf1PlannerInputs(manifest, lock, { 'src/lib.rs': input })).not.toEqual([]);
    }
    expect(validateOf1PlannerInputs(manifest, lock, { 'build.rs': '' })).not.toEqual([]);
  });
  it('distinguishes the exact preserved failure literal from actual process capability', () => {
    for (const path of ['src/recorded_verification.rs', 'tests/recorded_verification.rs']) {
      const source = readFileSync(`rust/of1-range-recorder/${path}`, 'utf8');
      expect(source).toContain('"Error: Command failed: ');
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
      for (const capability of ['Command::new("curl")', 'use std::process::Command;', 'std::net::TcpStream', 'unsafe { action(); }']) {
        expect(validateOf1PlannerInputs(manifest, lock, { [path]: `${source}\n${capability}` }))
          .toContain(`unexpected runtime capability in ${path}`);
      }
      // Even the same word elsewhere in the file is not blanket-exempted.
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\nCommand' })).not.toEqual([]);
      expect(validateOf1PlannerInputs(manifest, lock, { 'src/arbitrary.rs': source })).not.toEqual([]);
    }
  });
  it('allows only the reviewed same-binary crash harness, never a generic command exception', () => {
    const source = readFileSync('rust/of1-range-recorder/tests/durability_process.rs', 'utf8');
    expect(validateOf1PlannerInputs(manifest, lock, { 'tests/durability_process.rs': source })).toEqual([]);
    expect(validateOf1PlannerInputs(manifest, lock, { 'tests/durability_process.rs': source + '\n' })).toContain('unreviewed OF1 process-crash harness');
    expect(validateOf1PlannerInputs(manifest, lock, { 'src/worker.rs': source })).not.toEqual([]);
    expect(validateOf1PlannerInputs(manifest, lock, { 'tests/durability_process.rs': source + ' std::net::TcpStream' })).not.toEqual([]);
  });
  it('allows only exact reviewed loopback sources, never a filename or feature blanket exception', () => {
    for (const path of ['src/transport.rs', 'tests/transport.rs', 'src/bin/of1-transport-evidence.rs']) {
      const source = readFileSync(`rust/of1-range-recorder/${path}`, 'utf8');
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\n' }))
        .toContain(`unreviewed OF1 loopback source: ${path}`);
      expect(validateOf1PlannerInputs(manifest, lock, { 'src/provider.rs': source })).not.toEqual([]);
    }
  });
  it('pins official TLS and local fixture capabilities without permitting an arbitrary provider', () => {
    for (const path of ['src/https.rs', 'src/https/fixture.rs', 'tests/acquisition_https.rs',
      'tests/acquisition_e2e.rs', 'src/bin/of1-acquisition-fixture-evidence.rs']) {
      const source = readFileSync(`rust/of1-range-recorder/${path}`, 'utf8');
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\n' }))
        .toContain(`unreviewed OF1 acquisition source: ${path}`);
      expect(validateOf1PlannerInputs(manifest, lock, { 'src/arbitrary-provider.rs': source + '\nstd::net::TcpStream' })).not.toEqual([]);
    }
  });
  it('pins local monitor IPC and same-binary simulation without granting network capabilities', () => {
    for (const path of ['src/monitor/relay.rs', 'tests/monitor_ipc.rs', 'src/bin/of1-monitor-simulation.rs']) {
      const source = readFileSync(`rust/of1-range-recorder/${path}`, 'utf8');
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\n' }))
        .toContain(`unreviewed OF1 monitor source: ${path}`);
      expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\nstd::net::TcpStream' }))
        .toContain(`unexpected runtime capability in ${path}`);
    }
    expect(validateOf1PlannerInputs(manifest, lock, { 'src/other.rs': 'std::os::unix::net::UnixDatagram' }))
      .toContain('unexpected runtime capability in src/other.rs');
  });
});
