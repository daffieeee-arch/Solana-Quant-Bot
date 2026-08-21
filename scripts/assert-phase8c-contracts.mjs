#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

function record(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function exactArray(value, expected) { return Array.isArray(value) && JSON.stringify(value) === JSON.stringify(expected); }
function exactResources(value, expected) {
  return record(value) && value.cpus === expected.cpus
    && value.memoryBytes === expected.memoryBytes && value.pids === expected.pids
    && Object.keys(value).length === 3;
}
function unresolvedIdentity(value) {
  return record(value) && value.uid === null && value.gid === null
    && value.status === 'UNRESOLVED_REQUIRES_OPERATIONS_APPROVAL';
}
function secureRuntime(value) {
  return record(value)
    && value.readOnlyRootFilesystem === true
    && exactArray(value.capDrop, ['ALL'])
    && value.noNewPrivileges === true
    && value.privileged === false
    && value.dockerSocket === false
    && value.hostPid === false
    && value.hostIpc === false;
}
function occurrences(text, pattern) { return [...text.matchAll(pattern)].length; }

export function validatePhase8CContracts(input) {
  const errors = [];
  const { images, dataset, deployment, cockpitDockerfile, runnerDockerfile } = input;

  if (!record(dataset) || dataset.applied !== false) errors.push('invalid unapplied dataset contract');
  const properties = dataset?.properties ?? {};
  for (const [key, expected] of Object.entries({ type: 'FILESYSTEM', acltype: 'POSIX', aclmode: 'DISCARD', xattr: 'SA', atime: 'OFF', exec: 'OFF', setuid: 'OFF', devices: 'OFF', quota: '10 GiB', casesensitivity: 'SENSITIVE' })) {
    if (properties[key] !== expected) errors.push(`invalid dataset property:${key}`);
  }
  if (!['zstd-3', 'lz4'].includes(properties.compression)) errors.push('invalid dataset compression');
  if (!unresolvedIdentity(dataset?.owner)) errors.push('dataset UID/GID must remain unresolved');
  if (dataset?.shares?.smb !== false || dataset?.shares?.nfs !== false || dataset?.shares?.hostExport !== false) errors.push('forbidden dataset share');
  if (dataset?.mounts?.runner?.access !== 'READ_WRITE' || dataset?.mounts?.runner?.network !== 'NONE'
    || dataset?.mounts?.cockpit?.access !== 'READ_ONLY' || dataset?.mounts?.fixtureInput?.access !== 'READ_ONLY') errors.push('invalid dataset mount rights');
  if (dataset?.dataset === dataset?.realPilotDataset) errors.push('fixture and real Pilot dataset must differ');
  if (!record(dataset?.postReplay) || dataset.postReplay.fileMode !== '0444' || dataset.postReplay.directoryMode !== '0555'
    || dataset.postReplay.verifyManifestAndHashes !== true || dataset.postReplay.verifyAppendDenied !== true || dataset.postReplay.verifyCreateDenied !== true) errors.push('invalid dataset post-replay immutability');
  if (!Array.isArray(dataset?.rollback) || dataset.rollback.length < 4) errors.push('invalid dataset rollback');
  if (!record(dataset?.quotaAlarm) || !Number.isFinite(dataset.quotaAlarm.warningPercent)
    || !Number.isFinite(dataset.quotaAlarm.hardStopPercent) || dataset.quotaAlarm.warningPercent <= 0
    || dataset.quotaAlarm.hardStopPercent <= dataset.quotaAlarm.warningPercent || dataset.quotaAlarm.hardStopPercent >= 100
    || dataset.quotaAlarm.action !== 'NO_NEW_RUN_AND_OPERATOR_REVIEW') errors.push('invalid dataset quota alarm');
  if (typeof dataset?.snapshotNameContract !== 'string' || !dataset.snapshotNameContract.startsWith('phase8a-fixture-')) errors.push('invalid dataset snapshot contract');

  if (!record(images) || images.applied === true || images.buildAttempted !== false
    || !Array.isArray(images.images) || images.images.length !== 2) errors.push('invalid image contract');
  const imageMap = new Map((images?.images ?? []).map((image) => [image.name, image]));
  const cockpitImage = imageMap.get('phase8a-research-cockpit');
  const runnerImage = imageMap.get('phase8a-bronze-runner');
  const expectedImageResources = new Map([
    ['phase8a-research-cockpit', { cpus: 1, memoryBytes: 536870912, pids: 64 }],
    ['phase8a-bronze-runner', { cpus: 2, memoryBytes: 4294967296, pids: 64 }],
  ]);
  for (const image of images?.images ?? []) {
    if (image.tagPolicy !== 'IMMUTABLE_UNIQUE_TAG_REQUIRED' || image.mutableTagAllowed !== false
      || /(?:^|:)latest$/i.test(image.tagContract ?? '')) errors.push(`immutable tag required:${image.name}`);
    if (image.uid !== null || image.gid !== null || image.identityStatus !== 'UNRESOLVED_REQUIRES_OPERATIONS_APPROVAL') errors.push(`image UID/GID must remain unresolved:${image.name}`);
    if (image.baseImageStatus !== 'UNRESOLVED_EXACT_DIGEST_REQUIRED_NO_PULL_PERFORMED') errors.push(`image base digest must remain unresolved:${image.name}`);
    if (!record(image.security) || image.security.nonRoot !== true || image.security.readOnlyRootFilesystem !== true
      || !exactArray(image.security.capDrop, ['ALL']) || image.security.noNewPrivileges !== true
      || image.security.privileged !== false || image.security.dockerSocket !== false
      || image.security.hostPid !== false || image.security.hostIpc !== false) errors.push(`invalid image security:${image.name}`);
    if (!exactResources(image.resources, expectedImageResources.get(image.name) ?? {})) errors.push(`invalid image resources:${image.name}`);
  }
  if (!exactArray(cockpitImage?.hashBindings, ['package-lock', 'cockpit-entrypoint', 'frontend-build'])) errors.push('invalid cockpit image hash bindings');
  if (!exactArray(runnerImage?.hashBindings, ['runner-binary'])) errors.push('invalid runner image hash bindings');
  if (cockpitImage?.outboundNetwork !== 'DENY' || cockpitImage?.healthcheck !== '/healthz') errors.push('invalid cockpit image runtime boundary');
  if (runnerImage?.network !== 'NONE' || runnerImage?.inputMount !== 'READ_ONLY' || runnerImage?.outputMount !== 'READ_WRITE') errors.push('invalid runner image runtime boundary');

  if (!record(deployment) || deployment.applied !== false || deployment.operationsAuthorized !== false) errors.push('invalid unapplied deployment contract');
  if (deployment?.existingApp?.name !== 'solana-bot' || deployment?.existingApp?.configuredImage !== 'solana-bot:contra-audit16-offline-pump-3e95a3c'
    || deployment?.existingApp?.mutationAuthorized !== false || deployment?.existingApp?.requiredState !== 'STOPPED') errors.push('forbidden existing app mutation');
  if (!Array.isArray(deployment?.apps) || deployment.apps.length !== 2) errors.push('invalid deployment app count');
  const appMap = new Map((deployment?.apps ?? []).map((app) => [app.name, app]));
  const runner = appMap.get('phase8a-fixture-runner');
  const cockpit = appMap.get('phase8a-research-cockpit');
  if (!record(runner) || runner.lifecycle !== 'ONE_SHOT' || runner.network !== 'NONE' || !exactArray(runner.hostPorts, [])
    || !unresolvedIdentity(runner.user) || runner.mounts?.input?.access !== 'READ_ONLY' || runner.mounts?.output?.access !== 'READ_WRITE'
    || !secureRuntime(runner.security) || !exactResources(runner.resources, { cpus: 2, memoryBytes: 4294967296, pids: 64 }) || runner.successState !== 'COMPLETED_STOPPED'
    || !Array.isArray(runner.postSuccess) || runner.postSuccess.length < 4 || !Array.isArray(runner.rollback) || runner.rollback.length < 3) errors.push('invalid runner deployment contract');
  if (!record(cockpit) || cockpit.lifecycle !== 'LONG_RUNNING_INERT' || cockpit.network !== 'EXPLICIT_INGRESS_ONLY_OUTBOUND_DENY'
    || !exactArray(cockpit.hostPorts, []) || !unresolvedIdentity(cockpit.user) || cockpit.mounts?.output?.access !== 'READ_ONLY'
    || !secureRuntime(cockpit.security) || !exactResources(cockpit.resources, { cpus: 1, memoryBytes: 536870912, pids: 64 }) || cockpit.healthcheck !== '/healthz' || cockpit.readiness !== '/readyz'
    || cockpit.hostBinding?.status !== 'UNRESOLVED_REQUIRES_EXPLICIT_OPERATIONS_APPROVAL' || cockpit.hostBinding?.wildcardAllowed !== false
    || !exactArray(cockpit.hostBinding?.allowedClasses, ['EXACT_LAN_IP', 'EXACT_TAILSCALE_IP'])
    || !Array.isArray(cockpit.forbiddenRuntime) || !cockpit.forbiddenRuntime.includes('child_process')
    || !Array.isArray(cockpit.rollback) || cockpit.rollback.length < 3) errors.push('invalid cockpit deployment contract');

  if (typeof cockpitDockerfile !== 'string' || /dist\/main\.js|(?:^|\/)scanner|(?:^|\/)ledger/u.test(cockpitDockerfile)
    || !cockpitDockerfile.includes('USER ${COCKPIT_UID}:${COCKPIT_GID}')) errors.push('invalid cockpit Dockerfile boundary');
  if (!/^ARG NODE_BUILDER_IMAGE$/mu.test(cockpitDockerfile) || !/^ARG NODE_RUNTIME_IMAGE$/mu.test(cockpitDockerfile)
    || /^ARG NODE_(?:BUILDER|RUNTIME)_IMAGE=/mu.test(cockpitDockerfile)
    || occurrences(cockpitDockerfile, /^FROM \$\{NODE_BUILDER_IMAGE\} AS build$/gmu) !== 1
    || occurrences(cockpitDockerfile, /^FROM \$\{NODE_RUNTIME_IMAGE\} AS runtime$/gmu) !== 1
    || !/"\$NODE_BUILDER_IMAGE" \| grep -Eq '@sha256:\[0-9a-f\]\{64\}\$'/u.test(cockpitDockerfile)
    || !/"\$NODE_RUNTIME_IMAGE" \| grep -Eq '@sha256:\[0-9a-f\]\{64\}\$'/u.test(cockpitDockerfile)) errors.push('unverified cockpit base image forbidden');
  if (typeof runnerDockerfile !== 'string' || /node_modules|package\.json|dist\/main\.js/u.test(runnerDockerfile)
    || !runnerDockerfile.includes('ENTRYPOINT ["/phase8a-bronze-runner"]')
    || occurrences(runnerDockerfile, /^FROM \$\{RUST_BUILDER_IMAGE\} AS build$/gmu) !== 1
    || occurrences(runnerDockerfile, /^FROM \$\{RUNNER_RUNTIME_IMAGE\} AS runtime$/gmu) !== 1
    || !runnerDockerfile.includes("@sha256:[0-9a-f]{64}$")) errors.push('invalid runner Dockerfile boundary');
  const combined = `${cockpitDockerfile}\n${runnerDockerfile}`;
  if (/\b(?:password|secret|token|cookie|api[_-]?key)\s*[:=]\s*['"][^'"]+/iu.test(combined)) errors.push('forbidden image credential');
  return [...new Set(errors)].sort();
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === fileURLToPath(import.meta.url)) {
  try {
    const root = process.cwd();
    const input = {
      images: JSON.parse(readFileSync(resolve(root, 'deployment/phase8c/image-contracts.json'), 'utf8')),
      dataset: JSON.parse(readFileSync(resolve(root, 'deployment/phase8c/posix-fixture-dataset-changeplan.json'), 'utf8')),
      deployment: JSON.parse(readFileSync(resolve(root, 'deployment/phase8c/truenas-deployment-plan.json'), 'utf8')),
      cockpitDockerfile: readFileSync(resolve(root, 'containers/Dockerfile.cockpit'), 'utf8'),
      runnerDockerfile: readFileSync(resolve(root, 'containers/Dockerfile.phase8a-runner'), 'utf8'),
    };
    const errors = validatePhase8CContracts(input);
    if (errors.length) throw new Error(errors.join('\n'));
    process.stdout.write('Phase 8C image/dataset/deployment contract policy PASS\n');
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  }
}
