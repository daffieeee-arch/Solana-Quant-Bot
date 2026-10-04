import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';
import { assertOf1ProcessResult, validateOf1PlannerInputs } from '../scripts/assert-of1-planner-offline.mjs';

const manifest = readFileSync('rust/of1-range-recorder/Cargo.toml');
const lock = readFileSync('rust/of1-range-recorder/Cargo.lock');

describe('OF1 default-off transport dependency boundary', () => {
  it('fails with both real subprocess streams and retains them alongside a process error', () => {
    const result = spawnSync(process.execPath, ['-e',
      "require('node:fs').writeSync(1, 'fixture-assertion-on-stdout'); require('node:fs').writeSync(2, 'fixture-diagnostic-on-stderr'); process.exit(7);"],
    { encoding: 'utf8', timeout: 2_000, maxBuffer: 4_096 });
    expect(result.error).toBeUndefined();
    expect(result.status).toBe(7);
    for (const failure of [result, { ...result, status: null, signal: 'SIGTERM',
      error: Object.assign(new Error('fixture process error'), { code: 'ETIMEDOUT' }) }]) {
      let caught: unknown;
      try { assertOf1ProcessResult('test.default', failure); } catch (error) { caught = error; }
      expect(caught).toBeInstanceOf(Error);
      const message = (caught as Error).message;
      expect(message).toContain('phase=test.default');
      expect(message).toContain(`exitcode=${failure.status}`);
      expect(message).toContain(`signal=${failure.signal ?? 'none'}`);
      expect(message).toContain('--- stdout ---\nfixture-assertion-on-stdout');
      expect(message).toContain('--- stderr ---\nfixture-diagnostic-on-stderr');
      expect(message).toContain(failure.error ? 'process_error=ETIMEDOUT: fixture process error' : 'process_error=none');
      expect((caught as Error).cause).toBe(failure.error);
    }
  });

  it('pins the test-only metadata-init CLI subprocess without granting runtime or network capability', () => {
    const path = 'src/bin/of1-acquire/metadata_init_tests.rs';
    const source = readFileSync(`rust/of1-range-recorder/${path}`, 'utf8');
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\n' })).toContain('unreviewed metadata-init CLI harness');
    expect(validateOf1PlannerInputs(manifest, lock, { 'src/bin/of1-acquire.rs': source })).toContain('unexpected runtime capability in src/bin/of1-acquire.rs');
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\nstd::net::TcpStream' })).toContain(`unexpected runtime capability in ${path}`);
  });

  it.each([
    ['src/bin/of1-acquire/metadata_continuation_tests.rs', 'unreviewed metadata continuation CLI harness'],
    ['src/durable/acquisition/metadata_continuation_approved_tests.rs', 'unreviewed metadata continuation APPROVED harness'],
  ])('pins subprocess fixture %s and denies runtime relocation', (path, error) => {
    const source = readFileSync(`rust/of1-range-recorder/${path}`, 'utf8');
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\n' })).toContain(error);
    expect(validateOf1PlannerInputs(manifest, lock, { 'src/metadata_override.rs': source })).toContain('unexpected runtime capability in src/metadata_override.rs');
  });

  it('pins the synthetic continuation CLI harness without a runtime transport override', () => {
    const path = 'src/bin/of1-acquire/continuation_tests.rs';
    const source = readFileSync(`rust/of1-range-recorder/${path}`, 'utf8');
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\n' })).toContain('unreviewed continuation CLI harness');
    expect(validateOf1PlannerInputs(manifest, lock, { 'src/bin/of1-acquire.rs': source })).toContain('unexpected runtime capability in src/bin/of1-acquire.rs');
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\nstd::net::TcpStream' })).toContain(`unexpected runtime capability in ${path}`);
  });

  it('accepts the reviewed manifest and lock', () => {
    expect(validateOf1PlannerInputs(manifest, lock, {})).toEqual([]);
  });
  it('pins the B7 same-test-binary crash harness without admitting production process execution', () => {
    const path = 'src/campaign/process_tests.rs';
    const source = readFileSync('rust/of1-range-recorder/' + path, 'utf8');
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\n' })).toContain('unreviewed B7 process-crash harness');
    expect(validateOf1PlannerInputs(manifest, lock, { 'src/campaign.rs': source })).toContain('unexpected runtime capability in src/campaign.rs');
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

  it('allows only the exact offline rate-lock subprocess harness, never another process or socket capability', () => {
    const path = 'tests/rate_process.rs';
    const source = readFileSync(`rust/of1-range-recorder/${path}`, 'utf8');
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source })).toEqual([]);
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + '\n' })).toContain('unreviewed OF1 rate-process harness');
    expect(validateOf1PlannerInputs(manifest, lock, { [path]: source + ' std::net::TcpStream' })).toContain(`unexpected runtime capability in ${path}`);
    expect(validateOf1PlannerInputs(manifest, lock, { 'src/rate.rs': source })).toContain('unexpected runtime capability in src/rate.rs');
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
