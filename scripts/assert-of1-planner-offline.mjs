#!/usr/bin/env node
// Default-feature planner/store: syscall network deny. Source-pinned loopback TLS
// tests exercise framing/durability; the official connector is compiled, never dispatched.
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
import { writeResearchNetworkDenyFilter } from './write-research-seccomp-filter.mjs';
import { createCiPhaseTimer } from './lib/ci-phase-timing.mjs';

const root = resolve(import.meta.dirname, '..');
const crate = join(root, 'rust/of1-range-recorder');
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const MANIFEST_HASH = 'dfd72a9c45e3c1d20f1c50830933711fb953e1d402019ca873c16bc7049d8bbf';
const LOCK_HASH = '0f99d01a8121f77f689e7c48d5df497aa538dbd81ba1b6efeadb9e430744d78d';
// Only the reviewed same-test-binary child harness may spawn; runtime code still cannot.
const PROCESS_TEST_HASH = 'c7896fb4c4b0f7b5519f193ad0fd44e08967bd04cf220406542ac208b21c0c9b';
const RATE_PROCESS_TEST_HASH = 'a1982e6bf196bc790fbd4689fa6c802b4f947a9b2dfb82d4c4788986e792ccf7';
// No blanket network/process exception for a directory or Cargo feature. Exact reviewed
// fixture sources only; their constructors accept a port, never a host/URL/provider config.
const LOOPBACK_SOURCE_HASHES = {
  'src/transport.rs': '61bab41046ac7541993b7da7d1fe6d5abd5f36a193945f23487836acef2fe68b',
  'tests/transport.rs': '69de382c46b79118fca8f3e4c7d75b5a63c08cfe6cb6a725847b4f67a52e3610',
  'src/bin/of1-transport-evidence.rs': 'ade072ca0f9941d494cb95cac6ded78d47a4647a815c60a7abf59e12ec52b059',
};
// These files alone contain the reviewed production capability / fixture orchestration.
// An exact source pin is not a network lease; no official request runs in this gate.
const ACQUISITION_SOURCE_HASHES = {
  'src/https.rs': '01e08fdabef6be6b3b1b966457ffa57748fc033448f7f551fba4ad5deb36a86e',
  'src/https/fixture.rs': 'cbc520a5cc3e8fc3a22eb6e473a104cdc155207a2aaac25f4e8f21e5f5c47360',
  'tests/acquisition_https.rs': '8612ae55ded39f1c1252de7cda00903adadddb528ff1dfefce0cf9480548890d',
  'tests/acquisition_e2e.rs': 'f535c35a39a7a3069c847017ead8bfe4a347493854deec56a7628a405ca5358e',
  'src/bin/of1-acquisition-fixture-evidence.rs': 'b97f14882816bc65e7c0ce08ebc9c117152e0b9850865cdc8bcff2b35ca9f05f',
};
// Local-only operational telemetry is a separate, default-disabled capability.
// Pin its Unix IPC and sealed simulator, never grant a directory-wide exception.
const MONITOR_SOURCE_HASHES = {
  'src/monitor/relay.rs': '236744232c2916f09b96f27473300f67dadd6e71e6b91b56ea4caf8b193a8135',
  'tests/monitor_ipc.rs': 'b0d3b8719bfc4faf29ad2cdbc8652283ac866256cd492a823ca7698272b8d8c5',
  'src/bin/of1-monitor-simulation.rs': '72c773d485506ca41cb74d19901488fec10efee073371f14452cae334f7d259d',
};

export function validateOf1PlannerInputs(manifest, lock, sources) {
  const errors = [];
  if (hash(manifest) !== MANIFEST_HASH) errors.push('unreviewed OF1 manifest');
  if (hash(lock) !== LOCK_HASH) errors.push('unreviewed OF1 dependency lock');
  for (const [path, source] of Object.entries(sources)) {
    const crashHarness = path === 'tests/durability_process.rs';
    const rateHarness = path === 'tests/rate_process.rs';
    const fixtureSource = Object.hasOwn(LOOPBACK_SOURCE_HASHES, path);
    const acquisitionSource = Object.hasOwn(ACQUISITION_SOURCE_HASHES, path);
    const monitorSource = Object.hasOwn(MONITOR_SOURCE_HASHES, path);
    // The retained Node error has this literal prefix; its word "Command" is
    // evidence text, not Rust process capability. Ignore only that exact quoted
    // prefix in the two forensic-reader files. Every other token stays scanned,
    // including process APIs added next to the literal; no file-wide exemption.
    const processSource = ['src/recorded_verification.rs', 'tests/recorded_verification.rs'].includes(path)
      ? String(source).replaceAll('"Error: Command failed: ', '"Error: diagnostic failed: ')
      : source;
    if (crashHarness && hash(source) !== PROCESS_TEST_HASH) errors.push('unreviewed OF1 process-crash harness');
    if (rateHarness && hash(source) !== RATE_PROCESS_TEST_HASH) errors.push('unreviewed OF1 rate-process harness');
    if (fixtureSource && hash(source) !== LOOPBACK_SOURCE_HASHES[path]) errors.push(`unreviewed OF1 loopback source: ${path}`);
    if (acquisitionSource && hash(source) !== ACQUISITION_SOURCE_HASHES[path]) errors.push(`unreviewed OF1 acquisition source: ${path}`);
    if (monitorSource && hash(source) !== MONITOR_SOURCE_HASHES[path]) errors.push(`unreviewed OF1 monitor source: ${path}`);
    if (path === 'build.rs' || /\bunsafe\s*\{|#\s*\[\s*path\s*=/u.test(source)
      || (!fixtureSource && !acquisitionSource && /\b(?:TcpStream|TcpListener|UdpSocket)\b|std::net/u.test(source))
      || (!monitorSource && /\bUnixDatagram\b/u.test(source))
      || (!crashHarness && !rateHarness && !fixtureSource && !acquisitionSource && !monitorSource && /\bCommand\b|std::process::Command/u.test(processSource))) {
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
  const timing = createCiPhaseTimer();
  let checksPassed = false;
  const isolated = (label, command, args) => timing.measure(label, () => {
    const result = spawnSync(launcher, [filter, command, ...args], {
      cwd: root, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, CARGO_NET_OFFLINE: 'true' },
    });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(result.stderr || result.stdout || 'isolated process failed');
    return result;
  });
  // A loopback listener cannot run under the all-sockets-denied lane. These exact
  // source-pinned tests/binary use only fixed numeric loopback, no ambient endpoints,
  // DNS or HTTP proxy. This is not claimed to be an OS-wide external-network sandbox.
  const loopback = (label, command, args) => timing.measure(label, () => {
    const result = spawnSync(command, args, { cwd: root, encoding: 'utf8',
      maxBuffer: 16 * 1024 * 1024, timeout: 180_000,
      env: { ...process.env, CARGO_NET_OFFLINE: 'true' } });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(result.stderr || result.stdout || 'loopback fixture failed');
    return result;
  });
  try {
    await writeResearchNetworkDenyFilter(filter, process.arch, { allowLocalProcessSpawn: true });
    await buildResearchSeccompLauncher(launcher);
    const probe = isolated('probe.network-deny', process.execPath, ['--input-type=module', '-e',
      "import net from 'node:net';const s=net.createConnection({host:'127.0.0.1',port:9});s.on('connect',()=>process.exit(2));s.on('error',e=>{if(e.code==='EPERM'){console.log('NETWORK_DENIED');}else process.exit(3);});"]);
    if (probe.stdout !== 'NETWORK_DENIED\n') throw new Error('OF1 isolation probe failed');
    const manifest = ['--manifest-path', join(crate, 'Cargo.toml')];
    const metadata = JSON.parse(isolated('dependency.metadata', 'cargo', ['+1.97.1', 'metadata', '--locked', '--offline', '--all-features', '--format-version', '1', ...manifest]).stdout);
    const review = JSON.parse(readFileSync(join(crate, 'dependency-review.json'), 'utf8'));
    const actual = metadata.packages.map(p => ({ name: p.name, version: p.version, license: p.license,
      buildScript: p.targets.some(t => t.kind.includes('custom-build')) })).sort((a,b) => a.name.localeCompare(b.name));
    if (JSON.stringify(actual) !== JSON.stringify(review.lockedGraphPackages)) throw new Error('OF1 dependency/license/build-script graph drift');
    for (const script of review.buildScriptReview.filter(s => s.sha256)) {
      const pkg = metadata.packages.find(p => p.name === script.name && p.version === script.version);
      const target = pkg?.targets.find(t => t.kind.includes('custom-build'));
      if (!target || hash(readFileSync(target.src_path)) !== script.sha256) {
        throw new Error(`OF1 reviewed build-script bytes drift: ${script.name}`);
      }
    }
    for (const profile of review.featureProfiles) {
      const featureArgs = profile.allFeatures ? ['--all-features']
        : profile.features.length ? ['--features', profile.features.join(',')] : [];
      const tree = isolated(`dependency.tree.${profile.name}`, 'cargo', ['+1.97.1', 'tree', ...manifest, '--locked', '--offline',
        '--target', profile.target, '--prefix', 'none', '--edges', 'normal,build,dev',
        '--format', '{p}', ...featureArgs]).stdout;
      const identities = new Set(tree.trim().split('\n').map(line => {
        const match = /^(\S+) v(\S+)/u.exec(line);
        if (!match) throw new Error(`unrecognized Cargo tree record: ${profile.name}`);
        return `${match[1]}@${match[2]}`;
      }));
      const selected = actual.filter(p => identities.has(`${p.name}@${p.version}`));
      if (selected.length !== identities.size || JSON.stringify(selected) !== JSON.stringify(profile.packages)) {
        throw new Error(`OF1 enabled feature/license/build-script drift: ${profile.name}`);
      }
    }
    isolated('format.all', 'cargo', ['+1.97.1', 'fmt', ...manifest, '--all', '--', '--check']);
    isolated('clippy.default', 'cargo', ['+1.97.1', 'clippy', ...manifest, '--locked', '--offline', '--all-targets', '--', '-D', 'warnings']);
    isolated('clippy.loopback', 'cargo', ['+1.97.1', 'clippy', ...manifest, '--locked', '--offline', '--all-targets', '--features', 'loopback-fixture', '--', '-D', 'warnings']);
    isolated('clippy.all-features', 'cargo', ['+1.97.1', 'clippy', ...manifest, '--locked', '--offline', '--all-targets', '--all-features', '--', '-D', 'warnings']);
    process.stdout.write(isolated('test.lib.all-features', 'cargo', ['+1.97.1', 'test', ...manifest, '--locked', '--offline', '--all-features', '--lib']).stdout);
    const tests = isolated('test.default', 'cargo', ['+1.97.1', 'test', ...manifest, '--locked', '--offline', '--all-targets']);
    process.stdout.write(tests.stdout);
    const build = isolated('compile.default-bins', 'cargo', ['+1.97.1', 'build', ...manifest, '--locked', '--offline', '--bins', '--message-format=json-render-diagnostics']);
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
      const first = isolated(`fixture.plan.${format.slice(2)}.first`, binary, [format, index]).stdout;
      const second = isolated(`fixture.plan.${format.slice(2)}.repeat`, binary, [format, index]).stdout;
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
        return isolated(`fixture.durability.${format.slice(2)}.${iteration}`, durableBinary, [format, directory]).stdout;
      });
      if (outputs[0] !== outputs[1]) throw new Error('OF1 durability report is nondeterministic');
      if (mode === '--print') process.stdout.write(`${path}\n${outputs[0]}`);
      else if (outputs[0] !== readFileSync(join(root, path), 'utf8')) throw new Error(`OF1 durability report drift: ${path}`);
    }
    // Compile dependencies/build scripts with sockets denied, even for the fixture lane.
    const fixtureBuild = isolated('compile.transport-test', 'cargo', ['+1.97.1', 'test', ...manifest, '--locked', '--offline',
      '--features', 'loopback-fixture', '--test', 'transport', '--no-run', '--message-format=json-render-diagnostics']);
    const testBinary = fixtureBuild.stdout.split('\n').filter(Boolean).map(s => JSON.parse(s))
      .find(v => v.reason === 'compiler-artifact' && v.target.name === 'transport' && v.executable)?.executable;
    if (!testBinary) throw new Error('loopback test binary missing');
    process.stdout.write(loopback('test.transport', testBinary, ['--test-threads=1']).stdout);
    const fixtureBins = isolated('compile.transport-evidence', 'cargo', ['+1.97.1', 'build', ...manifest, '--locked', '--offline',
      '--features', 'loopback-fixture', '--bin', 'of1-transport-evidence', '--message-format=json-render-diagnostics']);
    const fixtureBinary = fixtureBins.stdout.split('\n').filter(Boolean).map(s => JSON.parse(s))
      .find(v => v.reason === 'compiler-artifact' && v.target.name === 'of1-transport-evidence' && v.executable)?.executable;
    if (!fixtureBinary) throw new Error('loopback evidence binary missing');
    for (const [format, path] of [
      ['--json', 'schemas/acquisition/of1/transport-evidence.json'],
      ['--markdown', 'docs/research/OF1_TRANSPORT_EVIDENCE.md'],
    ]) {
      const outputs = [0, 1].map(iteration => {
        const directory = join(scratch, `transport-${format}-${iteration}`);
        mkdirSync(directory);
        return loopback(`fixture.transport.${format.slice(2)}.${iteration}`, fixtureBinary, [format, directory]).stdout;
      });
      if (outputs[0] !== outputs[1]) throw new Error('OF1 transport report is nondeterministic');
      if (mode === '--print') process.stdout.write(`${path}\n${outputs[0]}`);
      else if (outputs[0] !== readFileSync(join(root, path), 'utf8')) throw new Error(`OF1 report drift: ${path}`);
    }
    // Source-reviewed numeric-loopback TLS tests only. Use the proposed runner's
    // release profile for these new large-index fixture lanes: repeated debug hashing
    // exhausted the unchanged 25-minute job budget despite passing every test.
    // Existing debug/default tests and all assertions/cases remain. Compilation/build
    // scripts still execute under socket denial; no official dispatch is run.
    for (const target of ['acquisition_https', 'acquisition_e2e', 'monitor_ipc', 'rate_process']) {
      const built = isolated(`compile.${target}`, 'cargo', ['+1.97.1', 'test', ...manifest, '--locked', '--offline', '--release',
        '--all-features', '--test', target, '--no-run', '--message-format=json-render-diagnostics']);
      const executable = built.stdout.split('\n').filter(Boolean).map(s => JSON.parse(s))
        .find(v => v.reason === 'compiler-artifact' && v.target.name === target && v.executable)?.executable;
      if (!executable) throw new Error(`TLS acquisition fixture missing: ${target}`);
      process.stdout.write(loopback(`test.${target}`, executable, ['--test-threads=1']).stdout);
    }
    const acquisitionBuild = isolated('compile.acquisition-evidence', 'cargo', ['+1.97.1', 'build', ...manifest, '--locked', '--offline', '--release',
      '--features', 'tls-fixture', '--bin', 'of1-acquisition-fixture-evidence', '--message-format=json-render-diagnostics']);
    const acquisitionBinary = acquisitionBuild.stdout.split('\n').filter(Boolean).map(s => JSON.parse(s))
      .find(v => v.reason === 'compiler-artifact' && v.target.name === 'of1-acquisition-fixture-evidence' && v.executable)?.executable;
    if (!acquisitionBinary) throw new Error('TLS acquisition evidence binary missing');
    for (const [format, path] of [
      ['--json', 'schemas/acquisition/of1/acquisition-evidence.json'],
      ['--markdown', 'docs/research/OF1_ACQUISITION_EVIDENCE.md'],
    ]) {
      const outputs = [0, 1].map(iteration => {
        const directory = join(scratch, `acquisition-${format}-${iteration}`);
        mkdirSync(directory);
        return loopback(`fixture.acquisition.${format.slice(2)}.${iteration}`, acquisitionBinary, [format, directory]).stdout;
      });
      if (outputs[0] !== outputs[1]) throw new Error('OF1 acquisition evidence is nondeterministic');
      if (mode === '--print') process.stdout.write(`${path}\n${outputs[0]}`);
      else if (outputs[0] !== readFileSync(join(root, path), 'utf8')) throw new Error(`OF1 report drift: ${path}`);
    }
    const monitorBuild = isolated('compile.monitor', 'cargo', ['+1.97.1', 'build', ...manifest, '--locked', '--offline', '--release',
      '--features', 'monitor,tls-fixture', '--bin', 'of1-monitor-simulation', '--message-format=json-render-diagnostics']);
    const monitorBinary = monitorBuild.stdout.split('\n').filter(Boolean).map(s => JSON.parse(s))
      .find(v => v.reason === 'compiler-artifact' && v.target.name === 'of1-monitor-simulation' && v.executable)?.executable;
    if (!monitorBinary) throw new Error('monitor simulation binary missing');
    const monitored = loopback('fixture.monitor.metadata', monitorBinary, [join(scratch, 'monitored-run'), join(scratch, 'absent-relay.sock')]).stdout;
    const terminal = JSON.parse(monitored.slice(monitored.indexOf('{')));
    if (terminal.kind !== 'LOCAL_SIMULATION' || terminal.stage !== 'COMPLETE'
      || terminal.selection.operations_published !== 4 || terminal.traffic.attempts !== 5
      || terminal.traffic.retries !== 1 || terminal.traffic.received_basis !== 'DURABLE_LOWER_BOUND'
      || terminal.dropped_samples < 1 || terminal.domain_counts !== 'UNAVAILABLE_NOT_DECODED_IN_B4') {
      throw new Error('monitor restart/absent-collector fixture drift');
    }
    const payloadOutput = loopback('fixture.monitor.payload', monitorBinary, ['--with-payload', join(scratch, 'monitored-payload-run'),
      join(scratch, 'absent-payload-relay.sock')]).stdout;
    const resultLines = payloadOutput.split('\n').filter(line => line.startsWith('LOCAL_SIMULATION_PAYLOAD_RESULT: '));
    if (resultLines.length !== 1) throw new Error('missing unique monitor payload result');
    const { snapshot: payload, integrity } = JSON.parse(resultLines[0].slice('LOCAL_SIMULATION_PAYLOAD_RESULT: '.length));
    const fixture = JSON.parse(readFileSync(join(root, 'schemas/acquisition/of1/car-structural-fixture.json'), 'utf8'));
    const payloadLength = fixture.sections_hex.length / 2;
    if (payload.kind !== 'LOCAL_SIMULATION' || payload.stage !== 'COMPLETE'
      || payload.selected_slots?.start !== fixture.selected_slot || payload.selected_slots?.end_exclusive !== fixture.selected_slot + 1
      || payload.selection.operations_total !== 5 || payload.selection.operations_published !== 5
      || payload.selection.planned_bytes !== 5_184_140 + payloadLength
      || payload.selection.published_bytes !== payload.selection.planned_bytes
      || payload.traffic.attempts !== 6 || payload.traffic.retries !== 1
      || payload.traffic.reserved_bytes !== 5_196_288 + payloadLength
      || payload.traffic.received_basis !== 'DURABLE_LOWER_BOUND' || payload.dropped_samples < 1
      || payload.operations[4].status_code !== 206 || payload.operations[4].published_bytes !== payloadLength
      || payload.operations[4].range !== `bytes=4096-${4096 + payloadLength - 1}`
      || payload.integrity.car !== 'CID_SLOT_VERIFIED_FIXTURE_ONLY'
      || integrity.slots.length !== 1 || integrity.slots[0].verified_nodes !== 5
      || integrity.slots[0].verified_links !== 4 || integrity.slots[0].captured_section_bytes !== payloadLength
      || integrity.root_to_slot_membership !== 'UNAVAILABLE' || integrity.whole_car_sha256_verified !== false
      || integrity.domain_counts !== 'UNAVAILABLE_NOT_DECODED_IN_B4') {
      throw new Error('monitor staged payload/receipt/CID fixture drift');
    }
    process.stdout.write('OF1 monitor metadata restart -> separate Fixture payload admission -> paced 206 -> published Raw -> CID/slot check PASS\n');
    // The standalone reader needs no monitor IPC, writer, active lease or network.
    // Reuse sealed runs above; repeat under syscall socket denial and prove that
    // every retained input file is unchanged, including the writer lock file.
    const reader = artifacts.find(v => v.reason === 'compiler-artifact'
      && v.target.name === 'of1-verify-recorded' && v.executable)?.executable;
    if (!reader) throw new Error('recorded verification binary missing');
    const inventory = directory => Object.fromEntries(readdirSync(directory, { withFileTypes: true })
      .sort((a, b) => a.name.localeCompare(b.name)).flatMap(entry => {
        const path = join(directory, entry.name);
        if (entry.isSymbolicLink()) throw new Error('symlink in sealed verification run');
        return entry.isDirectory()
          ? Object.entries(inventory(path)).map(([key, value]) => [`${entry.name}/${key}`, value])
          : [[entry.name, hash(readFileSync(path))]];
      }));
    for (const [name, state, nodes] of [['monitored-run', 'NOT_ACQUIRED', 0], ['monitored-payload-run', 'VERIFIED', 5]]) {
      const directory = join(scratch, name);
      const before = JSON.stringify(inventory(directory));
      const first = isolated(`verify.${name}.first`, reader, [directory]).stdout;
      const second = isolated(`verify.${name}.repeat`, reader, [directory]).stdout;
      const report = JSON.parse(first);
      if (first !== second || before !== JSON.stringify(inventory(directory))
        || report.schema !== 'OF1_OFFLINE_VERIFICATION_1'
        || report.stages.raw_receipts !== 'VERIFIED' || report.stages.car_slot !== state
        || report.stages.domain_decoding !== 'NOT_PERFORMED' || report.research_ready !== false
        || report.verifier.binary_sha256 !== hash(readFileSync(reader))
        || report.integrity.root_to_slot_membership !== 'UNAVAILABLE'
        || report.integrity.whole_car_sha256_verified !== false
        || report.integrity.slots.reduce((n, slot) => n + slot.report.verified_nodes, 0) !== nodes) {
        throw new Error('read-only recorded verifier identity/preservation/determinism drift');
      }
    }
    process.stdout.write('OF1 separate read-only verifier: metadata-only + sealed CAR, deterministic, source-preserving, socket-denied PASS\n');
    process.stdout.write('OF1 socket-denied default/build, local IPC and fixed-loopback TLS acquisition graph/fmt/clippy/tests/evidence PASS; no official call\n');
    checksPassed = true;
  } finally {
    try {
      rmSync(scratch, { recursive: true, force: true });
    } catch (error) {
      checksPassed = false;
      throw error;
    } finally {
      timing.finish({
        passed: checksPassed,
        summaryPath: process.env.GITHUB_ACTIONS === 'true' ? process.env.GITHUB_STEP_SUMMARY : undefined,
      });
    }
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const mode = process.argv[2] ?? '--all';
  if (process.argv.length > 3 || !['--static', '--all', '--print'].includes(mode)) throw new Error('expected --static, --all or --print');
  run(mode).catch(error => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
}
