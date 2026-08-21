#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import YAML from 'yaml';
import { validateRuntimeIdentityDrift, validateRuntimeIdentityEvidence } from './validate-runtime-identities.mjs';

const text = path => readFileSync(path, 'utf8');
const json = path => JSON.parse(text(path));
const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj ?? {}, key);
const keys = obj => Object.keys(obj ?? {}).sort();
const exactKeys = (obj, expected) => JSON.stringify(keys(obj)) === JSON.stringify([...expected].sort());
const containsAll = (value, markers) => markers.every(marker => value.includes(marker));
const VERIFY_SEMANTIC_SHA256='be0294557eccf05ecd29aeec7310eb190e67b9ee803b73f4cab9414b3ca93567';
const PUBLISH_SEMANTIC_SHA256='4dba62f9cf15748fc4e2066a6086da2e4b6902159e94fef892fe03c4198a220d';

export function loadPhase8D1Inputs(root = process.cwd()) {
  const at = path => resolve(root, path);
  const verifyText = text(at('.github/workflows/phase8d-images-verify.yml'));
  const publishText = text(at('.github/workflows/phase8d-images-publish.yml'));
  const ciText = text(at('.github/workflows/ci.yml'));
  const contract = json(at('deployment/phase8d1/remote-build-contract.json'));
  const supplyChainFileText=Object.fromEntries(Object.keys(contract.supplyChainFileSha256 ?? {}).map(path=>[path,text(at(path))]));
  const verifyScript = [
    text(at('scripts/phase8d1/resolve-base-images.mjs')),
    text(at('scripts/phase8d1/prebuild-hashes.sh')),
    text(at('scripts/phase8d1/verify-images.sh')),
    text(at('scripts/phase8d1/inventory-rootfs.py')),
    text(at('scripts/phase8d1/validate-image-metadata.mjs')),
  ].join('\n');
  const publishScript = [text(at('scripts/phase8d1/publish-gates.sh')), text(at('scripts/phase8d1/verify-published-images.sh'))].join('\n');
  return {
    verifyWorkflow: YAML.parse(verifyText), publishWorkflow: YAML.parse(publishText), ciWorkflow: YAML.parse(ciText),
    verifyText, publishText, ciText, verifyScript, publishScript,
    identities: json(at('deployment/phase8d1/runtime-identities.json')),
    identityDriftText: text(at('deployment/phase8d1/runtime-identity-drift-55-to-59.json')),
    contract,
    supplyChainFileText,
    baseLockText: text(at('deployment/phase8d1/base-image-lock.json')),
    baseLock: json(at('deployment/phase8d1/base-image-lock.json')),
    releaseSchema: json(at('deployment/phase8d1/release-manifest.schema.json')),
    cockpitDockerfile: text(at('containers/Dockerfile.cockpit')),
    runnerDockerfile: text(at('containers/Dockerfile.phase8a-runner')),
  };
}

function validateActionPins(allText, errors) {
  for (const line of allText.split('\n')) {
    const match = line.match(/^\s*uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?\s*$/);
    if (!match || match[1].startsWith('./')) continue;
    if (!/^[^/@]+\/[^/@]+@[0-9a-f]{40}$/.test(match[1])) errors.push(`invalid mutable action ref:${match[1]}`);
    if (!match[2] || !/^v\d/.test(match[2])) errors.push(`missing action release comment:${match[1]}`);
  }
}
function rejectGlobalProhibitions(combined, errors) {
  for (const marker of ['pull_request_target', 'actions/attest', '/var/run/docker.sock', '/run/docker.sock', 'TRUENAS_API_', 'truenas app.', 'curl | sh']) {
    if (combined.toLowerCase().includes(marker.toLowerCase())) errors.push(`forbidden workflow capability:${marker}`);
  }
  if (/\b(?:PAT|HERMES_PAT|GH_PAT)\b/.test(combined)) errors.push('forbidden PAT use');

}

export function validatePhase8D1Inputs(input) {
  const errors=[];
  const { verifyWorkflow: verify, publishWorkflow: publish, ciWorkflow: ci, contract, identities, baseLock, releaseSchema } = input;
  const combined = [input.verifyText,input.publishText,input.ciText,input.verifyScript,input.publishScript,input.cockpitDockerfile,input.runnerDockerfile,JSON.stringify(contract)].join('\n');
  if (createHash('sha256').update(input.verifyText).digest('hex')!==contract.workflowSha256?.verify) errors.push('reviewed verify workflow digest mismatch');
  if (createHash('sha256').update(input.publishText).digest('hex')!==contract.workflowSha256?.publish) errors.push('reviewed publish workflow digest mismatch');
  if (createHash('sha256').update(JSON.stringify(verify)).digest('hex')!==VERIFY_SEMANTIC_SHA256) errors.push('effective verify workflow semantic mismatch');
  if (createHash('sha256').update(JSON.stringify(publish)).digest('hex')!==PUBLISH_SEMANTIC_SHA256) errors.push('effective publish workflow semantic mismatch');
  for (const [path,expected] of Object.entries(contract.supplyChainFileSha256 ?? {})) {
    if (createHash('sha256').update(input.supplyChainFileText?.[path] ?? '').digest('hex')!==expected) errors.push(`reviewed supply-chain file digest mismatch:${path}`);
  }
  rejectGlobalProhibitions(combined, errors);
  for (const [path,source] of Object.entries(input.supplyChainFileText ?? {})) {
    if (path.startsWith('containers/') && (/https?:\/\/[^\s/@:]+:[^\s/@]+@/iu.test(source)
      || /^\s*(?:ENV|ARG)\s+[^\n]*(?:TOKEN|PASSWORD|SECRET|API_KEY|COOKIE|CREDENTIAL)[^\n]*$/gimu.test(source)
      || /^\s*RUN\s+[^\n]*(?:TOKEN|PASSWORD|SECRET|API_KEY|COOKIE|CREDENTIAL)=[^\s$]{8,}/gimu.test(source))) errors.push(`credential-bearing Dockerfile forbidden:${path}`);
  }
  validateActionPins(`${input.verifyText}\n${input.publishText}\n${input.ciText}`, errors);

  if (!own(verify.on,'pull_request') || own(verify.on,'pull_request_target') || !own(verify.on,'workflow_call')) errors.push('invalid verify triggers');
  if (!exactKeys(verify.permissions,['contents']) || verify.permissions.contents !== 'read') errors.push('invalid PR permissions');
  if (/docker\/login-action/.test(input.verifyText) || /secrets\./.test(input.verifyText)) errors.push('forbidden PR registry credentials');
  if (/push:\s*true/.test(input.verifyText)) errors.push('forbidden PR image publish');
  if (!containsAll(input.verifyText,['persist-credentials: false','github.event.pull_request.head.sha || github.sha','platforms: linux/amd64','push: false','provenance: false','sbom: false','remote-images-no-push'])) errors.push('missing no-push PR isolation');
  if (!containsAll(input.verifyText,['networkless synthetic verification','phase8d1-evidence/release-manifest.json','retention-days: 7'])) errors.push('missing bounded verify artifact');

  if (!exactKeys(publish.on,['workflow_dispatch'])) errors.push('invalid publish trigger');
  const inputs=publish.on?.workflow_dispatch?.inputs;
  if (!inputs?.source_sha?.required || !inputs?.confirmation?.required) errors.push('missing publish inputs');
  const publishJob=publish.jobs?.publish;
  if (!publishJob || !exactKeys(publishJob.permissions,['contents','packages']) || publishJob.permissions.contents!=='read' || publishJob.permissions.packages!=='write') errors.push('invalid publish permissions');
  if (publishJob?.if !== "github.ref == 'refs/heads/main' && inputs.confirmation == 'PUBLISH_SYNTHETIC_PHASE8D_IMAGES' && inputs.source_sha == github.sha") errors.push('missing pre-run main confirmation and source gate');
  if (own(publishJob?.permissions,'id-token') || own(publishJob?.permissions,'attestations')) errors.push('forbidden publish identity permissions');
  if (!exactKeys(publish.permissions,['contents']) || publish.permissions.contents!=='read') errors.push('invalid workflow default permissions');
  if (!publish.jobs?.['normal-ci-gate']?.uses?.endsWith('/ci.yml') || !publish.jobs?.['image-verify-gate']?.uses?.endsWith('/phase8d-images-verify.yml')) errors.push('missing successful CI and image verify gates');
  if (!containsAll(input.publishText,['PUBLISH_SYNTHETIC_PHASE8D_IMAGES','refs/heads/main','LIVE_MAIN_SHA','secrets.GITHUB_TOKEN','packages: write','provenance: mode=max','sbom: true','Pull and retest immutable registry digests'])) errors.push('missing manual main-only publish controls');
  if (/secrets\.(?!GITHUB_TOKEN)/.test(input.publishText)) errors.push('forbidden non-repository publish credential');
  if (!containsAll(input.publishScript,['reject_merge_ref','refs/heads/main','LIVE_MAIN_SHA','TAG_COLLISION_REJECTED','manifest unknown','packageVisibility=="private"','finalDigestRetestVerdict'])) errors.push('missing tag collision, live-main, private or digest-retest gate');
  if (!input.publishScript.includes('gh api') || input.publishScript.includes('Authorization: Bearer')) errors.push('invalid GitHub API authentication boundary');

  if (contract.schemaVersion!=='PHASE8D1_REMOTE_BUILD_CONTRACT_1' || contract.status!=='REMOTE_ISOLATED_GITHUB_BUILDER') errors.push('invalid remote build contract');
  if (contract.platform!=='linux/amd64' || contract.maxCompressedBaseImageBytes>3221225472) errors.push('invalid base image platform or pull budget');
  for (const spec of Object.values(contract.baseImages ?? {})) {
    if (!spec.digestRequired || spec.versionTag==='latest' || !spec.registry || !spec.repository || !spec.requiredSoftwareVersion) errors.push('base digest and exact version required');
  }
  if (createHash('sha256').update(input.baseLockText).digest('hex')!==contract.baseImageLockSha256) errors.push('reviewed base lock digest mismatch');
  if (baseLock.schemaVersion!=='PHASE8D1_BASE_IMAGE_LOCK_1' || baseLock.platform!=='linux/amd64' || baseLock.images?.length!==4 || baseLock.conservativeTwoNamespaceMaximumBytes>contract.maxCompressedBaseImageBytes) errors.push('invalid reviewed base lock');
  for (const [name,spec] of Object.entries(contract.baseImages ?? {})) {
    const locked=baseLock.images?.find(image=>image.name===name);
    if (!locked || locked.registry!==spec.registry || locked.repository!==spec.repository || locked.versionTag!==spec.versionTag || !/^sha256:[0-9a-f]{64}$/.test(locked.manifestListDigest??'') || !/^sha256:[0-9a-f]{64}$/.test(locked.linuxAmd64Digest??'')) errors.push(`base lock mismatch:${name}`);
  }
  const ids=contract.runtimeIdentities;
  if (ids?.runnerUid!==61000 || ids?.cockpitUid!==61001 || ids?.fixtureReadGid!==61000 || ids?.status!=='SELECTED_READ_ONLY_NOT_APPLIED') errors.push('invalid runtime identities');
  errors.push(...validateRuntimeIdentityEvidence(identities));
  if (createHash('sha256').update(input.identityDriftText).digest('hex')!==identities.drift?.differenceFileSha256) errors.push('runtime identity drift evidence digest mismatch');
  try { errors.push(...validateRuntimeIdentityDrift(JSON.parse(input.identityDriftText))); }
  catch { errors.push('runtime identity drift evidence invalid JSON'); }
  if (contract.verify?.push!==false || contract.verify?.registryLogin!==false) errors.push('invalid verify publish contract');
  if (contract.runnerTests?.independentRuns!==2 || contract.runnerTests?.retainedFiles!==20 || contract.runnerTests?.network!=='none') errors.push('invalid networkless runner equality contract');
  if (contract.cockpitTests?.unavailable!==true || contract.cockpitTests?.syntheticProvider!==true) errors.push('missing cockpit test modes');
  if (contract.publish?.trigger!=='workflow_dispatch' || contract.publish?.confirmation!=='PUBLISH_SYNTHETIC_PHASE8D_IMAGES' || !contract.publish.mainOnly || !contract.publish.sourceShaEqualsGithubSha || !contract.publish.sourceShaEqualsLiveMain) errors.push('invalid publish authorization contract');
  if (contract.publish?.packageVisibility!=='private' || contract.publish?.overwriteExistingTag!==false || contract.publish?.deploymentIdentity!=='REGISTRY_DIGEST_ONLY') errors.push('invalid private immutable GHCR contract');
  if (contract.publish?.provenance!=='mode=max' || contract.publish?.sbom!=='spdx' || contract.publish?.finalDigestRetestRequired!==true) errors.push('missing supply-chain attestations or digest retest');
  if (contract.trueNas?.mutationsAuthorized!==false || contract.trueNas?.deploymentAuthorized!==false || contract.trueNas?.privatePullCredentialCreated!==false) errors.push('forbidden TrueNAS operation');
  const allowed=(contract.allowedBuildArgs ?? []).map(x=>x.toUpperCase());
  if ((contract.forbiddenBuildArgs ?? []).some(x=>allowed.some(y=>y.includes(x)))) errors.push('forbidden secret-bearing build arg');

  if (!containsAll(input.verifyScript,['@sha256:','BASE_DIGEST_DRIFT_WITHIN_RUN','BASE_IMAGE_PULL_BUDGET_EXCEEDED','--network none','independentRuns:2','retainedFiles:20','start_cockpit unavailable','start_cockpit provider'])) errors.push('missing digest, runner or cockpit verification implementation');
  if (!containsAll(input.verifyScript,['seccomp=','error.code==="EPERM"','Config.Env','credentialContentFindings'])) errors.push('missing seccomp or credential-content boundary');
  if (!/docker run --name "\$name" --platform linux\/amd64 --network none[^\n]+--read-only[^\n]+--cap-drop ALL[^\n]+--pids-limit 64[^\n]+"\$RUNNER_IMAGE"/.test(input.verifyScript)) errors.push('missing required networkless runner invocation');
  if (!containsAll(input.cockpitDockerfile,['@sha256:[0-9a-f]{64}$','COCKPIT_ENTRYPOINT_SHA256','COCKPIT_RUNTIME_TREE_SHA256','FRONTEND_BUILD_SHA256'])) errors.push('invalid cockpit Dockerfile digest/hash enforcement');
  if (!containsAll(input.runnerDockerfile,['@sha256:[0-9a-f]{64}$','CARGO_LOCK_SHA256','RUNNER_BINARY_SHA256'])) errors.push('invalid runner Dockerfile digest/hash enforcement');

  const props=releaseSchema.properties ?? {};
  for (const key of ['sourceGitSha','workflowRunId','workflowCommitSha','builderRunnerImage','baseImages','runtimeIdentities','prebuildHashes','images','runnerVerification','cockpitVerification','packageVisibility','nonActions']) if (!own(props,key)) errors.push(`missing release manifest field:${key}`);
  const imageProps=props.images?.items?.properties ?? {};
  for (const key of ['registryDigest','ociProvenancePresent','spdxSbomPresent','finalDigestRetestVerdict']) if (!own(imageProps,key)) errors.push(`missing image release field:${key}`);
  if (!containsAll(JSON.stringify(releaseSchema),['PUBLISH_SUCCEEDED','VERIFY_ONLY_SUCCEEDED','REGISTRY_DIGEST_ONLY'].filter(x=>x!=='REGISTRY_DIGEST_ONLY').slice(0,2))) errors.push('invalid release status schema');

  if (!own(ci.on,'workflow_call') || !containsAll(input.ciText,['actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1','actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0','node-version: \'22.23.2\''])) errors.push('normal CI is not reusable or immutable');
  return [...new Set(errors)].sort();
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const errors=validatePhase8D1Inputs(loadPhase8D1Inputs(process.cwd()));
    if (errors.length) throw new Error(errors.join('\n'));
    process.stdout.write('Phase 8D1 remote image supply-chain policy PASS\n');
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  }
}
