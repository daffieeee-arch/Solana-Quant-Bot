#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
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
const stableValue=value=>Array.isArray(value)?value.map(stableValue):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,stableValue(value[key])])):value;
const semanticSha256=value=>createHash('sha256').update(JSON.stringify(stableValue(value))).digest('hex');
const VERIFY_SEMANTIC_SHA256='82288f7c7f9c1723fb10eac5614500837fb64759dac92045af6cf57ac64b3033';
const PUBLISH_SEMANTIC_SHA256='1263ebb46ab2e2aa41d91cf1840b904f65ebebb2ba353b916a0ded160919a21f';
const RECOVERY_SEMANTIC_SHA256='2f7ea959df97b42b5d4c7ee3ac5da75de4f56f2c045ea57ac83b739aa9dd1464';

export function loadPhase8D1Inputs(root = process.cwd()) {
  const at = path => resolve(root, path);
  const verifyText = text(at('.github/workflows/phase8d-images-verify.yml'));
  const publishText = text(at('.github/workflows/phase8d-images-publish.yml'));
  const recoveryText = text(at('.github/workflows/phase8d-images-recover.yml'));
  const ciText = text(at('.github/workflows/ci.yml'));
  const contract = json(at('deployment/phase8d1/remote-build-contract.json'));
  const supplyChainFileText=Object.fromEntries(Object.keys(contract.supplyChainFileSha256 ?? {}).map(path=>[path,text(at(path))]));
  const directShellScripts=['prebuild-hashes.sh','publish-gates.sh','recover-existing-digests.sh','verify-images.sh','verify-published-images.sh'].map(name=>`scripts/phase8d1/${name}`);
  const shellScriptModes=Object.fromEntries(directShellScripts.map(path=>[path,execFileSync('git',['ls-files','--stage','--',path],{cwd:root,encoding:'utf8'}).trim().split(/\s+/)[0]]));
  const verifyScript = [
    text(at('scripts/phase8d1/resolve-base-images.mjs')),
    text(at('scripts/phase8d1/prebuild-hashes.sh')),
    text(at('scripts/phase8d1/verify-images.sh')),
    text(at('scripts/phase8d1/inventory-rootfs.py')),
    text(at('scripts/phase8d1/validate-image-metadata.mjs')),
  ].join('\n');
  const publishScript = [text(at('scripts/phase8d1/publish-gates.sh')), text(at('scripts/phase8d1/partial-publish-state.mjs')), text(at('scripts/phase8d1/verify-published-images.sh'))].join('\n');
  const recoveryScript = [text(at('scripts/phase8d1/recover-existing-digests.sh')),text(at('scripts/phase8d1/recover-existing-digests.mjs')),text(at('scripts/phase8d1/inspect-artifact-zip.py')),text(at('scripts/phase8d1/recovery-artifacts.mjs')),text(at('scripts/phase8d1/recovery-http.mjs')),text(at('scripts/phase8d1/recovery-oci.mjs')),text(at('scripts/phase8d1/recovery-state.mjs'))].join('\n');
  return {
    verifyWorkflow: YAML.parse(verifyText), publishWorkflow: YAML.parse(publishText), recoveryWorkflow:YAML.parse(recoveryText), ciWorkflow: YAML.parse(ciText),
    verifyText, publishText, recoveryText, ciText, verifyScript, publishScript, recoveryScript,
    identities: json(at('deployment/phase8d1/runtime-identities.json')),
    identityDriftText: text(at('deployment/phase8d1/runtime-identity-drift-55-to-59.json')),
    contract,
    shellScriptModes,
    supplyChainFileText,
    baseLockText: text(at('deployment/phase8d1/base-image-lock.json')),
    baseLock: json(at('deployment/phase8d1/base-image-lock.json')),
    buildKitLockText: text(at('deployment/phase8d1/buildkit-image-lock.json')),
    buildKitLock: json(at('deployment/phase8d1/buildkit-image-lock.json')),
    publicKeyAllowlist: json(at('deployment/phase8d1/rootfs-public-key-test-vectors.json')),
    partialPublishContractText: text(at('deployment/phase8d1/partial-publish-contract.json')),
    partialPublishContract: json(at('deployment/phase8d1/partial-publish-contract.json')),
    releaseSchema: json(at('deployment/phase8d1/release-manifest.schema.json')),
    recoveryContractText:text(at('deployment/phase8d1/existing-digest-recovery-contract.json')),
    recoveryContract:json(at('deployment/phase8d1/existing-digest-recovery-contract.json')),
    recoverySchema:json(at('deployment/phase8d1/recovery-manifest.schema.json')),
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
  const { verifyWorkflow: verify, publishWorkflow: publish, recoveryWorkflow:recovery, ciWorkflow: ci, contract, identities, baseLock, buildKitLock, partialPublishContract, releaseSchema, recoveryContract, recoverySchema } = input;
  const combined = [input.verifyText,input.publishText,input.recoveryText,input.ciText,input.verifyScript,input.publishScript,input.recoveryScript,input.cockpitDockerfile,input.runnerDockerfile,JSON.stringify(contract)].join('\n');
  if (createHash('sha256').update(input.verifyText).digest('hex')!==contract.workflowSha256?.verify) errors.push('reviewed verify workflow digest mismatch');
  if (createHash('sha256').update(input.publishText).digest('hex')!==contract.workflowSha256?.publish) errors.push('reviewed publish workflow digest mismatch');
  if (createHash('sha256').update(input.recoveryText).digest('hex')!==contract.workflowSha256?.recover) errors.push('reviewed recovery workflow digest mismatch');
  if (createHash('sha256').update(JSON.stringify(verify)).digest('hex')!==VERIFY_SEMANTIC_SHA256) errors.push('effective verify workflow semantic mismatch');
  if (createHash('sha256').update(JSON.stringify(publish)).digest('hex')!==PUBLISH_SEMANTIC_SHA256) errors.push('effective publish workflow semantic mismatch');
  if (createHash('sha256').update(JSON.stringify(recovery)).digest('hex')!==RECOVERY_SEMANTIC_SHA256) errors.push('effective recovery workflow semantic mismatch');
  for (const [path,expected] of Object.entries(contract.supplyChainFileSha256 ?? {})) {
    if (createHash('sha256').update(input.supplyChainFileText?.[path] ?? '').digest('hex')!==expected) errors.push(`reviewed supply-chain file digest mismatch:${path}`);
  }
  rejectGlobalProhibitions(combined, errors);
  for (const [path,source] of Object.entries(input.supplyChainFileText ?? {})) {
    if (path.startsWith('containers/') && (/https?:\/\/[^\s/@:]+:[^\s/@]+@/iu.test(source)
      || /^\s*(?:ENV|ARG)\s+[^\n]*(?:TOKEN|PASSWORD|SECRET|API_KEY|COOKIE|CREDENTIAL)[^\n]*$/gimu.test(source)
      || /^\s*RUN\s+[^\n]*(?:TOKEN|PASSWORD|SECRET|API_KEY|COOKIE|CREDENTIAL)=[^\s$]{8,}/gimu.test(source))) errors.push(`credential-bearing Dockerfile forbidden:${path}`);
  }
  validateActionPins(`${input.verifyText}\n${input.publishText}\n${input.recoveryText}\n${input.ciText}`, errors);

  const buildKitImage='moby/buildkit@sha256:040d34121c27906c4ff9ac152a30d52bf2c5d328d3bb748916bb3d2743c02528';
  const buildKitRuntimeProof=`test "$(docker buildx version)" = "github.com/docker/buildx v0.12.1 30feaa1a915b869ebc2eea6328624b49facd4bfb"
jq -e --arg image 'image=${buildKitImage}' '
  length == 1 and
  .[0].buildkit == "v0.32.2" and
  (.[0].platforms | split(",") | index("linux/amd64") != null) and
  (.[0]["driver-opts"] | index($image) != null) and
  .[0]["buildkitd-flags"] == "--debug --oci-worker-net bridge" and
  (.[0]["buildkitd-flags"] | contains("security.insecure") | not) and
  (.[0]["buildkitd-flags"] | contains("network.host") | not) and
  .[0].labels["org.mobyproject.buildkit.worker.network"] == "cni"
' <<< "$BUILDX_NODES" >/dev/null
`;
  if(createHash('sha256').update(input.buildKitLockText).digest('hex')!==contract.buildKitImageLockSha256)errors.push('reviewed BuildKit lock digest mismatch');
  if(!exactKeys(buildKitLock,['schemaVersion','observedAt','registry','repository','versionTag','buildxVersion','buildxReleaseCommit','buildKitVersion','releaseCommit','manifestListDigest','linuxAmd64Digest','platform','selectionRationale'])||buildKitLock?.schemaVersion!=='PHASE8D1_BUILDKIT_LOCK_1'||buildKitLock?.registry!=='docker.io'||buildKitLock?.repository!=='moby/buildkit'||buildKitLock?.versionTag!=='v0.32.2'||buildKitLock?.buildxVersion!=='v0.12.1'||buildKitLock?.buildxReleaseCommit!=='30feaa1a915b869ebc2eea6328624b49facd4bfb'||buildKitLock?.buildKitVersion!=='v0.32.2'||buildKitLock?.releaseCommit!=='991535e0973488b6a429096d21fa13f81f2d89d8'||buildKitLock?.manifestListDigest!=='sha256:28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8'||buildKitLock?.linuxAmd64Digest!=='sha256:040d34121c27906c4ff9ac152a30d52bf2c5d328d3bb748916bb3d2743c02528'||buildKitLock?.platform!=='linux/amd64'||semanticSha256(buildKitLock)!=='e18850666be9133b830ce011feecd8ba83cc3a2a48b9822e9a4486bddab2c2fa')errors.push('invalid exact BuildKit lock');
  const validateBuildKitJob=(job,label)=>{
    const steps=job?.steps??[];
    const setupSteps=steps.filter(step=>String(step.uses??'').startsWith('docker/setup-buildx-action@'));
    const setup=setupSteps[0];
    const driverOpts=String(setup?.with?.['driver-opts']??'').trim();
    if(setupSteps.length!==1||setup?.id!=='buildx'||setup?.with?.version!=='v0.12.1'||setup?.with?.driver!=='docker-container'||setup?.with?.install!==true||driverOpts!==`image=${buildKitImage}`||setup?.with?.platforms!=='linux/amd64'||setup?.with?.['buildkitd-flags']!=='--debug --oci-worker-net bridge')errors.push(`invalid pinned BuildKit setup:${label}`);
    const serialized=JSON.stringify(setup??{});
    if(/security\.insecure|network\.host|buildx-stable-1/.test(serialized))errors.push(`insecure or mutable BuildKit setup:${label}`);
    const verifyStep=steps.find(step=>step.name==='Verify exact sandboxed BuildKit server');
    if(!verifyStep||verifyStep.env?.BUILDX_NODES!=='${{ steps.buildx.outputs.nodes }}'||verifyStep.run!==buildKitRuntimeProof)errors.push(`missing runtime BuildKit proof:${label}`);
    if(steps.some(step=>own(step.with,'allow')&&String(step.with.allow).trim()))errors.push(`insecure BuildKit build entitlement requested:${label}`);
  };
  validateBuildKitJob(verify.jobs?.['verify-images-no-push'],'verify');
  validateBuildKitJob(publish.jobs?.publish,'publish');
  validateBuildKitJob(recovery.jobs?.recover,'recovery');

  if (!own(verify.on,'pull_request') || own(verify.on,'pull_request_target') || !own(verify.on,'workflow_call')) errors.push('invalid verify triggers');
  if (!exactKeys(verify.permissions,['contents']) || verify.permissions.contents !== 'read') errors.push('invalid PR permissions');
  if (/docker\/login-action/.test(input.verifyText) || /secrets\./.test(input.verifyText)) errors.push('forbidden PR registry credentials');
  if (/push:\s*true/.test(input.verifyText)) errors.push('forbidden PR image publish');
  if (!containsAll(input.verifyText,['persist-credentials: false','github.event.pull_request.head.sha || github.sha','platforms: linux/amd64','push: false','provenance: false','sbom: false','remote-images-no-push'])) errors.push('missing no-push PR isolation');
  if (!containsAll(input.verifyText,['networkless synthetic verification','phase8d1-evidence/release-manifest.json','retention-days: 7'])) errors.push('missing bounded verify artifact');
  const verifyJob=verify.jobs?.['verify-images-no-push'];
  const verifySteps=verifyJob?.steps ?? [];
  const uploadSteps=verifySteps.filter(step=>String(step.uses??'').startsWith('actions/upload-artifact@'));
  if(verifyJob?.env?.DOCKER_BUILD_RECORD_UPLOAD!=='false')errors.push('automatic Buildx record artifact upload forbidden');
  if(uploadSteps.length!==1||uploadSteps[0]?.with?.['retention-days']!==7||uploadSteps[0]?.with?.path!=='phase8d1-evidence/*.json\nphase8d1-evidence/*.sha256\n'||/\.dockerbuild|\.tar|layer/i.test(uploadSteps[0]?.with?.path??''))errors.push('invalid bounded seven-day verify artifact');
  const nodeSetupIndex=verifySteps.findIndex(step=>String(step.uses??'').startsWith('actions/setup-node@')&&step.with?.['node-version']==='22.23.2');
  const npmCiIndex=verifySteps.findIndex(step=>step.run==='npm ci');
  const policyIndex=verifySteps.findIndex(step=>step.run==='node scripts/phase8d1/assert-phase8d1-supply-chain.mjs');
  if(nodeSetupIndex<0||npmCiIndex<=nodeSetupIndex||policyIndex<=npmCiIndex)errors.push('missing locked Node policy dependency bootstrap');
  if(Object.values(input.shellScriptModes??{}).length!==5||Object.values(input.shellScriptModes??{}).some(mode=>mode!=='100755'))errors.push('direct Phase 8D1 shell script not executable in Git index');

  if (!exactKeys(publish.on,['workflow_dispatch'])) errors.push('invalid publish trigger');
  const inputs=publish.on?.workflow_dispatch?.inputs;
  if (!inputs?.source_sha?.required || !inputs?.confirmation?.required) errors.push('missing publish inputs');
  const publishJob=publish.jobs?.publish;
  if (!publishJob || !exactKeys(publishJob.permissions,['contents','packages']) || publishJob.permissions.contents!=='read' || publishJob.permissions.packages!=='write') errors.push('invalid publish permissions');
  if(publishJob?.env?.DOCKER_BUILD_RECORD_UPLOAD!=='false')errors.push('automatic publish Buildx record artifact upload forbidden');
  if (publishJob?.if !== "github.ref == 'refs/heads/main' && inputs.confirmation == 'PUBLISH_SYNTHETIC_PHASE8D_IMAGES' && inputs.source_sha == github.sha") errors.push('missing pre-run main confirmation and source gate');
  if (own(publishJob?.permissions,'id-token') || own(publishJob?.permissions,'attestations')) errors.push('forbidden publish identity permissions');
  if (!exactKeys(publish.permissions,['contents']) || publish.permissions.contents!=='read') errors.push('invalid workflow default permissions');
  if (!publish.jobs?.['normal-ci-gate']?.uses?.endsWith('/ci.yml') || !publish.jobs?.['image-verify-gate']?.uses?.endsWith('/phase8d-images-verify.yml')) errors.push('missing successful CI and image verify gates');
  const publishSteps=publishJob?.steps??[];
  const publishNodeSetup=publishSteps.findIndex(step=>String(step.uses??'').startsWith('actions/setup-node@')&&step.with?.['node-version']==='22.23.2');
  const publishNpmCi=publishSteps.findIndex(step=>step.run==='npm ci');
  const publishPolicy=publishSteps.findIndex(step=>String(step.run??'').includes('node scripts/phase8d1/assert-phase8d1-supply-chain.mjs'));
  const publishFirstProductNode=publishSteps.findIndex(step=>String(step.run??'').includes('node scripts/phase8d1/resolve-base-images.mjs'));
  const publishPolicyRun=String(publishSteps[publishPolicy]?.run??'');
  if(publishNodeSetup<0||publishNpmCi<=publishNodeSetup||publishPolicy<=publishNpmCi||publishFirstProductNode<=publishPolicy||!containsAll(publishPolicyRun,['test "$(node --version)" = v22.23.2','node scripts/phase8d1/assert-phase8d1-supply-chain.mjs','test "$SOURCE_SHA" = "$(git rev-parse HEAD)"','git status --porcelain --untracked-files=no']))errors.push('missing exact publish-job Node dependency and policy bootstrap');
  if(createHash('sha256').update(input.partialPublishContractText).digest('hex')!==contract.partialPublishContractSha256||semanticSha256(partialPublishContract)!=='a549421d2f08ba9da45eae415d4c7b41d7b1590060b72d5ea858152c6a064779')errors.push('invalid partial publish contract binding');
  if(!exactKeys(partialPublishContract,['schemaVersion','releaseId','prePushBothTagsAbsentRequired','sharedImmutableReleaseIdRequired','stateFile','durableArtifactStages','partialVerdict','partialCollisionVerdict','successVerdict','deploymentEligibleBeforeCompleteRetest','automaticRetryAllowed','tagOverwriteAllowed','packageVersionDeletionAllowed','explicitRecoveryAuthorizationRequiredAfterPartial','perImageSuccessRequires','successfulReleaseRequiresBothImages','packages'])||partialPublishContract?.schemaVersion!=='PHASE8D1_PARTIAL_PUBLISH_CONTRACT_1'||partialPublishContract?.releaseId!=='SOURCE_GIT_SHA'||partialPublishContract?.partialVerdict!=='PARTIAL_PUBLISH_HOLD'||partialPublishContract?.successVerdict!=='PUBLISH_SUCCEEDED'||partialPublishContract?.deploymentEligibleBeforeCompleteRetest!==false||partialPublishContract?.automaticRetryAllowed!==false||partialPublishContract?.tagOverwriteAllowed!==false||partialPublishContract?.packageVersionDeletionAllowed!==false||partialPublishContract?.explicitRecoveryAuthorizationRequiredAfterPartial!==true||partialPublishContract?.successfulReleaseRequiresBothImages!==true)errors.push('invalid partial publish safety semantics');
  for(const name of ['Upload preflight publish state','Upload cockpit push state','Upload runner push state','Upload final publish state'])if(!publishSteps.some(step=>step.name===name))errors.push(`missing durable partial publish artifact:${name}`);
  for(const id of ['push-cockpit','push-runner'])if(publishSteps.find(step=>step.id===id)?.['continue-on-error']!==true)errors.push(`push outcome cannot be durably recorded:${id}`);
  if(!String(publishSteps.find(step=>step.id==='push-runner')?.if??'').includes('always()')||!containsAll(input.publishText,['partial-publish-state.mjs record','partial-publish-state.mjs mark-verified','partial-publish-state.mjs require-success','PARTIAL_PUBLISH_STATE']))errors.push('missing partial publish workflow state machine');
  if(/gh api\s+--method DELETE|docker manifest rm|retry-action|tagOverwriteAllowed:\s*true/i.test(`${input.publishText}\n${input.publishScript}`))errors.push('automatic retry delete or overwrite forbidden after partial publish');
  if (!containsAll(input.publishText,['PUBLISH_SYNTHETIC_PHASE8D_IMAGES','refs/heads/main','LIVE_MAIN_SHA','secrets.GITHUB_TOKEN','packages: write','provenance: mode=max','sbom: true','Pull and retest immutable registry digests'])) errors.push('missing manual main-only publish controls');
  if (/secrets\.(?!GITHUB_TOKEN)/.test(input.publishText)) errors.push('forbidden non-repository publish credential');
  if (!containsAll(input.publishScript,['reject_merge_ref','refs/heads/main','LIVE_MAIN_SHA','TAG_COLLISION_REJECTED','manifest unknown','packageVisibility=="private"','finalDigestRetestVerdict'])) errors.push('missing tag collision, live-main, private or digest-retest gate');
  if (!input.publishScript.includes('gh api') || input.publishScript.includes(['Authorization:','Bearer'].join(' '))) errors.push('invalid GitHub API authentication boundary');

  if(!exactKeys(recovery?.on,['workflow_dispatch']))errors.push('invalid recovery trigger');
  const recoveryInputs=recovery?.on?.workflow_dispatch?.inputs;
  const recoveryInputNames=['confirmation','image_source_sha','cockpit_tag','cockpit_digest','runner_tag','runner_digest','original_publish_run_id'];
  if(!exactKeys(recoveryInputs,recoveryInputNames)||recoveryInputNames.some(name=>recoveryInputs?.[name]?.required!==true))errors.push('missing recovery inputs');
  if(!exactKeys(recovery?.permissions,['contents','actions','packages'])||recovery.permissions.contents!=='read'||recovery.permissions.actions!=='read'||recovery.permissions.packages!=='read')errors.push('invalid recovery permissions');
  const recoveryJob=recovery?.jobs?.recover,recoverySteps=recoveryJob?.steps??[];
  if(!recoveryJob||keys(recovery.jobs).length!==1||own(recoveryJob,'permissions')||recoveryJob['runs-on']!=='ubuntu-24.04'||recoveryJob.if!=="github.ref == 'refs/heads/main' && inputs.confirmation == 'RECOVER_EXISTING_PHASE8D_IMAGES'")errors.push('invalid recovery workflow job');
  const recoveryNames=recoverySteps.map(step=>step.name);
  for(const name of ['Check out exact recovery workflow source','Check out exact image source','Set up exact Node runtime','Install locked recovery dependencies','Enforce recovery policy and source ancestry','Set up isolated Buildx','Verify exact sandboxed BuildKit server','Run read-only existing-digest recovery','Upload bounded recovery evidence','Require recovery success'])if(!recoveryNames.includes(name))errors.push(`missing recovery workflow step:${name}`);
  const recoveryCheckouts=recoverySteps.filter(step=>String(step.uses??'').startsWith('actions/checkout@'));
  if(recoveryCheckouts.length!==2||recoveryCheckouts.some(step=>step.with?.['persist-credentials']!==false)||recoveryCheckouts[0]?.with?.ref!=='${{ github.sha }}'||recoveryCheckouts[1]?.with?.ref!=='${{ inputs.image_source_sha }}'||recoveryCheckouts[1]?.with?.path!=='phase8d1-image-source')errors.push('invalid recovery source separation');
  const recoveryNode=recoverySteps.findIndex(step=>String(step.uses??'').startsWith('actions/setup-node@')&&step.with?.['node-version']==='22.23.2'),recoveryNpm=recoverySteps.findIndex(step=>step.run==='npm ci'),recoveryPolicy=recoverySteps.findIndex(step=>step.name==='Enforce recovery policy and source ancestry'),recoveryRun=recoverySteps.findIndex(step=>step.name==='Run read-only existing-digest recovery');
  if(recoveryNode<0||recoveryNpm<=recoveryNode||recoveryPolicy<=recoveryNpm||recoveryRun<=recoveryPolicy||!containsAll(String(recoverySteps[recoveryPolicy]?.run??''),['git merge-base --is-ancestor "$IMAGE_SOURCE_SHA" "$GITHUB_SHA"','node scripts/phase8d1/assert-phase8d1-supply-chain.mjs','git status --porcelain --untracked-files=no']))errors.push('missing recovery policy and ancestor gate');
  const recoveryUpload=recoverySteps.find(step=>step.name==='Upload bounded recovery evidence');
  if(!recoveryUpload||recoveryUpload.with?.path!=='phase8d1-recovery-evidence/*.json\nphase8d1-recovery-evidence/*.txt\nphase8d1-recovery-evidence/*.sha256\n'||recoveryUpload.with?.['retention-days']!==30)errors.push('invalid bounded recovery artifact');
  const recoverySerialized=`${input.recoveryText}\n${input.recoveryScript}`;
  if(input.recoveryText.includes('inputs.image_source_sha == github.sha'))errors.push('invalid recovery source separation');
  if(/docker\s+build(?:\s|$)|docker\s+buildx\s+build|docker\s+(?:push|tag)|docker\/build-push-action|gh\s+api\s+--method\s+(?:DELETE|PATCH|POST|PUT)|packages:\s*write|contents:\s*write|id-token:\s*write|attestations:\s*write|packageVersionDelete\s*:\s*true|packageSettingsChange\s*:\s*true|tagOverwrite\s*:\s*true/i.test(recoverySerialized))errors.push('forbidden recovery mutation capability');
  if(/secrets\.(?!GITHUB_TOKEN)/.test(input.recoveryText)||!containsAll(input.recoveryScript,['evaluatePackageMetadataAttempts','maxAttempts','nextDelaySeconds','PACKAGE_METADATA_TOTAL_TIMEOUT_HOLD','fetchAllPackageVersions','package-version-inventory.json','final-readonly-recheck.json','inspect-artifact-zip.py','evaluateAttestationEvidence','validateOriginalArtifactMetadata','validateOriginalStateChain','unauthenticatedPullDenied','authenticatedPullSucceeded','provenancePresent','spdxSbomPresent','digestRetestPassed','zeroMutationEvidence']))errors.push('missing recovery read-only evidence gates');
  if(!exactKeys(recoveryContract,['schemaVersion','recoveryMode','rootCauseCategory','imageSourceSha','originalPublishRunId','originalFinalVerdict','confirmation','repository','images','originalArtifacts','packageMetadataRetry','buildStack','mutations'])||recoveryContract?.schemaVersion!=='PHASE8D1_EXISTING_DIGEST_RECOVERY_CONTRACT_1'||recoveryContract?.imageSourceSha!=='9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180'||recoveryContract?.originalPublishRunId!==32641496527||recoveryContract?.confirmation!=='RECOVER_EXISTING_PHASE8D_IMAGES'||recoveryContract?.images?.length!==2||recoveryContract?.originalArtifacts?.length!==5||recoveryContract?.packageMetadataRetry?.maxAttempts!==6||recoveryContract?.packageMetadataRetry?.maxDelaySeconds!==15||recoveryContract?.packageMetadataRetry?.maxTotalWaitSeconds!==90||Object.values(recoveryContract?.mutations??{}).some(value=>value!==false))errors.push('invalid recovery contract');
  const expectedRecoveryImages=[
    {name:'cockpit',package:'phase8a-research-cockpit',tag:'ghcr.io/daffieeee-arch/phase8a-research-cockpit:9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180-dec80aec28fd-b6799c7bb168',digest:'sha256:6963e72814a3c26c6454f3de670cb92ec78e6dc3258fe7a0e2869075788e32fd',uid:61001,gid:61000},
    {name:'runner',package:'phase8a-bronze-runner',tag:'ghcr.io/daffieeee-arch/phase8a-bronze-runner:9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180-0e93202ac05c',digest:'sha256:76af7eac2bd1b04045ba570f8bec26033c503ded6d78b6e30d1eabfa590b429a',uid:61000,gid:61000},
  ];
  const expectedRecoveryArtifacts=[
    {role:'preflight',id:9493911850,name:'phase8d1-publish-preflight-32641496527-1',bytes:638,sha256:'659e65f6e79aa4087304e61b52fc1f10aa68290c455fe0481c3424d4db409388',expectedVerdict:'PRE_PUSH_READY'},
    {role:'cockpit-push',id:9493915717,name:'phase8d1-publish-cockpit-32641496527-1',bytes:697,sha256:'1580b7850bd737bf690eb90374bd32927902a1569ee66e972ae08665328d219d',expectedVerdict:'PARTIAL_PUBLISH_HOLD'},
    {role:'runner-push',id:9493917239,name:'phase8d1-publish-runner-32641496527-1',bytes:744,sha256:'adc47365ff0ec33bd524e2e29e7ca09ab1c5cee57b118fe1edcfc28c8e8d9766',expectedVerdict:'BOTH_PUSHED_RETEST_REQUIRED_HOLD'},
    {role:'final-hold',id:9493919367,name:'phase8d1-publish-final-32641496527-1',bytes:1186,sha256:'2312c9ea3d52d9913c9567b1c4c3247fb13f787330699609afabecc51279ae4f',expectedVerdict:'BOTH_PUSHED_RETEST_REQUIRED_HOLD'},
    {role:'verify-support',id:9493819797,name:'phase8d1-verify-32641496527-1',bytes:13698,sha256:'b8c10fa9e6aeabe6a74d5020ff1c16375143f7304c396458b114ba4593466ce4',expectedVerdict:'VERIFY_ONLY_SUCCEEDED'},
  ];
  const expectedRecoveryRetry={maxAttempts:6,maxDelaySeconds:15,maxTotalWaitSeconds:90},expectedRecoveryBuildStack={buildxVersion:'v0.12.1',buildxCommit:'30feaa1a915b869ebc2eea6328624b49facd4bfb',buildKitVersion:'v0.32.2',buildKitLinuxAmd64Digest:'sha256:040d34121c27906c4ff9ac152a30d52bf2c5d328d3bb748916bb3d2743c02528',daemonFlags:'--debug --oci-worker-net bridge',workerNetworkLabel:'cni'},expectedRecoveryMutations={imageBuild:false,imagePush:false,tagCreate:false,tagOverwrite:false,packageVersionDelete:false,packageSettingsChange:false,originalArtifactMutation:false,trueNasMutation:false,grafanaMutation:false,clickhouseMutation:false};
  if(recoveryContract?.rootCauseCategory!=='PACKAGE_METADATA_API_EVIDENCE_NOT_PRODUCED_BEFORE_DIGEST_RETEST'||recoveryContract?.originalFinalVerdict!=='BOTH_PUSHED_RETEST_REQUIRED_HOLD'||recoveryContract?.repository!=='daffieeee-arch/solana-paper-scanner'||JSON.stringify(recoveryContract?.images)!==JSON.stringify(expectedRecoveryImages)||JSON.stringify(recoveryContract?.originalArtifacts)!==JSON.stringify(expectedRecoveryArtifacts)||JSON.stringify(recoveryContract?.packageMetadataRetry)!==JSON.stringify(expectedRecoveryRetry)||JSON.stringify(recoveryContract?.buildStack)!==JSON.stringify(expectedRecoveryBuildStack)||JSON.stringify(recoveryContract?.mutations)!==JSON.stringify(expectedRecoveryMutations))errors.push('invalid recovery contract');
  if(createHash('sha256').update(input.recoveryContractText).digest('hex')!==contract.existingDigestRecoveryContractSha256||semanticSha256(recoveryContract)!=='4cf818bde80cb6df320dcaaeaac2fc467fdf62143e4715ed32aa17456f91b40e')errors.push('invalid recovery contract binding');
  const recoveryProps=recoverySchema?.properties??{};
  for(const key of ['recoveryMode','recoveryWorkflowSha','imageSourceSha','originalPublishRunId','originalArtifacts','images','packageMetadata','runnerVerdict','cockpitVerdict','baseImageDriftVerdict','cleanTreeVerdict','zeroMutationEvidence','zeroMutationDetails','verdict','deploymentEligible','recoveryCompleted'])if(!own(recoveryProps,key))errors.push(`missing recovery manifest field:${key}`);
  const recoveryContractState=contract.recovery;
  if(!recoveryContractState||recoveryContractState.trigger!=='workflow_dispatch'||recoveryContractState.confirmation!=='RECOVER_EXISTING_PHASE8D_IMAGES'||recoveryContractState.mainOnly!==true||recoveryContractState.imageSourceAncestorRequired!==true||recoveryContractState.imageBuild!==false||recoveryContractState.imagePush!==false||recoveryContractState.tagMutation!==false||recoveryContractState.packageSettingsMutation!==false||recoveryContractState.packageVersionDeletion!==false||recoveryContractState.originalPublishRunId!==32641496527||recoveryContractState.artifactMutation!==false||recoveryContractState.deploymentIdentity!=='REGISTRY_DIGEST_ONLY'||recoveryContractState.recoveryWorkflowDispatched!==false||JSON.stringify(recoveryContractState.permissions)!==JSON.stringify(['contents:read','actions:read','packages:read']))errors.push('invalid recovery authorization contract');
  if(contract.nonClaims?.currentCandidateStatus!=='BOTH_PUSHED_RETEST_REQUIRED_HOLD'||contract.nonClaims?.imagesPushed!==true||contract.nonClaims?.deploymentEligible!==false||contract.nonClaims?.recoveryCompleted!==false)errors.push('invalid current partial publish HOLD status');

  if (contract.schemaVersion!=='PHASE8D1_REMOTE_BUILD_CONTRACT_1' || contract.status!=='REMOTE_ISOLATED_GITHUB_BUILDER' || contract.sourceBaseSha!=='8b5ecb6168ac3d1ea9fa6630ab8a43ada1b686e8') errors.push('invalid remote build contract');
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
  const allowlist=input.publicKeyAllowlist;
  const allowEntries=allowlist?.entries;
  const publicBaseDigest='sha256:a17d50af28002a160548bd4225b3cfcb12c5efcb171f79e68758f2885fb1b066';
  if(!exactKeys(allowlist,['schemaVersion','entries'])||allowlist?.schemaVersion!=='PHASE8D1_PUBLIC_KEY_TEST_VECTOR_ALLOWLIST_1'||!Array.isArray(allowEntries)||allowEntries.length!==10||semanticSha256(allowlist)!=='5980184ad7bd5aa1872f82ca34a1e8d2c7e87e39c04614709fc696086b050ec8')errors.push('invalid public GnuTLS KAT allowlist');
  if(baseLock.images?.find(image=>image.name==='nodeRuntime')?.linuxAmd64Digest!==publicBaseDigest||allowEntries?.some(entry=>entry.baseLinuxAmd64Digest!==publicBaseDigest))errors.push('public GnuTLS KAT base digest mismatch');
  const ids=contract.runtimeIdentities;
  if (ids?.runnerUid!==61000 || ids?.cockpitUid!==61001 || ids?.fixtureReadGid!==61000 || ids?.status!=='SELECTED_READ_ONLY_NOT_APPLIED') errors.push('invalid runtime identities');
  errors.push(...validateRuntimeIdentityEvidence(identities));
  if (createHash('sha256').update(input.identityDriftText).digest('hex')!==identities.drift?.differenceFileSha256) errors.push('runtime identity drift evidence digest mismatch');
  try { errors.push(...validateRuntimeIdentityDrift(JSON.parse(input.identityDriftText))); }
  catch { errors.push('runtime identity drift evidence invalid JSON'); }
  if (contract.verify?.push!==false || contract.verify?.registryLogin!==false) errors.push('invalid verify publish contract');
  if (contract.runnerTests?.independentRuns!==2 || contract.runnerTests?.retainedFiles!==20 || contract.runnerTests?.network!=='none') errors.push('invalid networkless runner equality contract');
  if (contract.cockpitTests?.unavailable!==true || contract.cockpitTests?.syntheticProvider!==true || contract.cockpitTests?.networkIsolation!=='NETWORK_NONE_LOOPBACK_NSENTER_PROBE_PLUS_SECCOMP_CONNECT_DENY') errors.push('missing cockpit test modes or network-none isolation');
  if (contract.publish?.trigger!=='workflow_dispatch' || contract.publish?.confirmation!=='PUBLISH_SYNTHETIC_PHASE8D_IMAGES' || !contract.publish.mainOnly || !contract.publish.sourceShaEqualsGithubSha || !contract.publish.sourceShaEqualsLiveMain) errors.push('invalid publish authorization contract');
  if (contract.publish?.packageVisibility!=='private' || contract.publish?.overwriteExistingTag!==false || contract.publish?.deploymentIdentity!=='REGISTRY_DIGEST_ONLY') errors.push('invalid private immutable GHCR contract');
  if (contract.publish?.provenance!=='mode=max' || contract.publish?.sbom!=='spdx' || contract.publish?.finalDigestRetestRequired!==true) errors.push('missing supply-chain attestations or digest retest');
  if(contract.publish?.durablePartialPublishStateRequired!==true||contract.publish?.explicitRecoveryAuthorizationRequired!==true||contract.publish?.automaticRetryAllowed!==false||contract.publish?.packageVersionDeletionAllowed!==false)errors.push('missing durable partial publish recovery contract');
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
