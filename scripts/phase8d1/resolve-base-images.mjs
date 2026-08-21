#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

function run(command, args, options = {}) {
  return execFileSync(command, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], ...options }).trim();
}
function exactRef(spec) { return `${spec.repository.split('/').at(-1)}:${spec.versionTag}`; }
function repositoryRef(spec) { return `${spec.registry}/${spec.repository}`; }
function inspectManifest(ref) {
  const manifest = JSON.parse(run('docker', ['buildx', 'imagetools', 'inspect', '--format', '{{json .Manifest}}', ref]));
  if (!/^sha256:[0-9a-f]{64}$/.test(manifest?.digest ?? '')) throw new Error('INVALID_EXPLICIT_MANIFEST_DIGEST');
  return manifest;
}
function inspectRawManifest(ref) {
  const manifest = JSON.parse(run('docker', ['buildx', 'imagetools', 'inspect', '--raw', ref]));
  if (!manifest?.config || !Array.isArray(manifest.layers)) throw new Error('INVALID_PLATFORM_MANIFEST');
  return manifest;
}

export function resolveDescriptor(index, platform = 'linux/amd64') {
  const [os, architecture] = platform.split('/');
  if (!Array.isArray(index.manifests)) throw new Error('PLATFORM_AMBIGUITY_NO_MANIFEST_LIST');
  const rows = index.manifests.filter(x => x.platform?.os === os && x.platform?.architecture === architecture && !x.platform?.variant);
  if (rows.length !== 1 || !/^sha256:[0-9a-f]{64}$/.test(rows[0].digest ?? '')) throw new Error('PLATFORM_AMBIGUITY_LINUX_AMD64');
  return rows[0];
}
export function compressedSize(manifest) {
  if (!manifest?.config || !Array.isArray(manifest.layers)) throw new Error('INVALID_PLATFORM_MANIFEST');
  const values = [manifest.config, ...manifest.layers].map(x => x.size);
  if (values.some(x => !Number.isSafeInteger(x) || x < 1)) throw new Error('INVALID_COMPRESSED_SIZE');
  return values.reduce((a, b) => a + b, 0);
}
export function validatePlatformManifest(descriptor, formattedManifest, rawManifest) {
  if (formattedManifest?.digest !== descriptor?.digest) throw new Error('PLATFORM_DIGEST_DRIFT');
  compressedSize(rawManifest);
  return rawManifest;
}

function resolveOne(name, spec, platform) {
  const tagRef = exactRef(spec);
  const index = inspectManifest(tagRef);
  const manifestListDigest = index.digest;
  const descriptor = resolveDescriptor(index, platform);
  const repo = repositoryRef(spec);
  const digestRef = `${repo}@${descriptor.digest}`;
  if (!/@sha256:[0-9a-f]{64}$/.test(digestRef)) throw new Error(`DIGEST_QUALIFIED_REFERENCE_REQUIRED:${name}`);
  const manifest = validatePlatformManifest(descriptor, inspectManifest(digestRef), inspectRawManifest(digestRef));
  return {
    name, registry: spec.registry, repository: spec.repository, versionTag: spec.versionTag,
    manifestListDigest, linuxAmd64Digest: descriptor.digest, digestRef,
    os: 'linux', architecture: 'amd64', compressedSizeBytes: compressedSize(manifest),
    observedAt: new Date().toISOString(), softwareVersion: null,
  };
}
function probeVersions(rows, contract) {
  for (const row of rows) {
    run('docker', ['pull', '--platform', contract.platform, row.digestRef]);
    let version;
    if (row.name.startsWith('node')) version = run('docker', ['run', '--rm', '--platform', contract.platform, row.digestRef, 'node', '--version']);
    else if (row.name === 'rust') version = run('docker', ['run', '--rm', '--platform', contract.platform, row.digestRef, 'rustc', '--version']);
    else version = run('docker', ['run', '--rm', '--platform', contract.platform, row.digestRef, 'sh', '-c', '. /etc/os-release; printf "%s %s" "$NAME" "$VERSION_ID"']);
    const required = contract.baseImages[row.name].requiredSoftwareVersion;
    const matches = row.name === 'rust' ? version.startsWith(`${required} `) : version === required;
    if (!matches) throw new Error(`BASE_SOFTWARE_VERSION_MISMATCH:${row.name}:${version}`);
    row.softwareVersion = version;
  }
}
function writeOutputs(rows, output) {
  if (!process.env.GITHUB_OUTPUT) return;
  const keys = { nodeBuilder: 'node_builder_ref', nodeRuntime: 'node_runtime_ref', rust: 'rust_ref', runnerRuntime: 'runtime_ref' };
  const lines=[];
  for (const row of rows) lines.push(`${keys[row.name]}=${row.digestRef}`);
  lines.push(`manifest=${output}`);
  writeFileSync(process.env.GITHUB_OUTPUT, `${lines.join('\n')}\n`, { flag: 'a' });
}
function sameResolution(a, b) {
  return a.length === b.length && a.every((x, i) => x.name === b[i].name && x.manifestListDigest === b[i].manifestListDigest && x.linuxAmd64Digest === b[i].linuxAmd64Digest && x.compressedSizeBytes === b[i].compressedSizeBytes);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const mode = process.argv[2];
  const output = resolve(process.argv[3] ?? 'phase8d1-evidence/base-images.json');
  const contract = JSON.parse(readFileSync('deployment/phase8d1/remote-build-contract.json', 'utf8'));
  const lockBytes = readFileSync('deployment/phase8d1/base-image-lock.json');
  if (createHash('sha256').update(lockBytes).digest('hex') !== contract.baseImageLockSha256) throw new Error('REVIEWED_BASE_LOCK_DIGEST_MISMATCH');
  const lock = JSON.parse(lockBytes.toString('utf8'));
  if (mode === 'resolve') {
    mkdirSync(resolve(output, '..'), { recursive: true });
    const first = Object.entries(contract.baseImages).map(([name, spec]) => resolveOne(name, spec, contract.platform));
    for (const row of first) {
      const expected = lock.images.find(image => image.name === row.name);
      if (!expected || expected.versionTag !== row.versionTag || expected.manifestListDigest !== row.manifestListDigest || expected.linuxAmd64Digest !== row.linuxAmd64Digest) {
        throw new Error(`BASE_DIGEST_DRIFT_FROM_REVIEWED_LOCK:${row.name}`);
      }
    }
    const total = first.reduce((sum, row) => sum + row.compressedSizeBytes, 0);
    const conservativeMaximumDownloadedBytes = total * 2;
    if (conservativeMaximumDownloadedBytes > contract.maxCompressedBaseImageBytes) throw new Error(`BASE_IMAGE_PULL_BUDGET_EXCEEDED:${conservativeMaximumDownloadedBytes}`);
    probeVersions(first, contract);
    const second = Object.entries(contract.baseImages).map(([name, spec]) => resolveOne(name, spec, contract.platform));
    if (!sameResolution(first, second)) throw new Error('BASE_DIGEST_DRIFT_WITHIN_RUN');
    const manifest = { schemaVersion: 'PHASE8D1_BASE_IMAGES_1', platform: contract.platform, oneBaseSetCompressedSizeBytes: total, conservativeMaximumDownloadedBytes, maxDownloadedBytes: contract.maxCompressedBaseImageBytes, cacheNamespacesAssumed: 2, images: first };
    writeFileSync(output, `${JSON.stringify(manifest, null, 2)}\n`);
    writeOutputs(first, output);
    process.stdout.write(`Phase 8D1 base-image resolution PASS (${total} compressed bytes)\n`);
  } else if (mode === 'compare') {
    const original = JSON.parse(readFileSync(output, 'utf8'));
    const current = Object.entries(contract.baseImages).map(([name, spec]) => resolveOne(name, spec, contract.platform));
    if (!sameResolution(original.images, current)) throw new Error('BASE_DIGEST_DRIFT_WITHIN_RUN');
    process.stdout.write('Phase 8D1 base-image drift check PASS\n');
  } else throw new Error('usage: resolve-base-images.mjs resolve|compare [manifest]');
}
