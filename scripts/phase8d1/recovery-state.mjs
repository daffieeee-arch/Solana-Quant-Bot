import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateJsonSchema } from './json-schema-subset.mjs';

const SHA=/^[0-9a-f]{40}$/;
const EXPECTED_IMAGE_SOURCE_SHA='9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180';
const EXPECTED_PUBLISH_RUN=32641496527;
const EXPECTED_REPOSITORY='daffieeee-arch/solana-paper-scanner';
const EXPECTED_IMAGES={
  cockpit:{package:'phase8a-research-cockpit',tag:`ghcr.io/daffieeee-arch/phase8a-research-cockpit:${EXPECTED_IMAGE_SOURCE_SHA}-dec80aec28fd-b6799c7bb168`,digest:'sha256:6963e72814a3c26c6454f3de670cb92ec78e6dc3258fe7a0e2869075788e32fd'},
  runner:{package:'phase8a-bronze-runner',tag:`ghcr.io/daffieeee-arch/phase8a-bronze-runner:${EXPECTED_IMAGE_SOURCE_SHA}-0e93202ac05c`,digest:'sha256:76af7eac2bd1b04045ba570f8bec26033c503ded6d78b6e30d1eabfa590b429a'},
};

export function evaluatePackageMetadataAttempts(attempts) {
  if(!Array.isArray(attempts)||attempts.length<1||attempts.length>6||attempts.some(a=>!a||!Number.isSafeInteger(a.status)))throw new Error('INVALID_PACKAGE_METADATA_ATTEMPTS');
  for(let index=0;index<attempts.length;index++){
    const attempt=attempts[index],attemptsUsed=index+1;
    if(attempt.status===401||attempt.status===403)return Object.freeze({verdict:'PACKAGE_API_PERMISSION_HOLD',terminal:true,attemptsUsed,nextDelaySeconds:0});
    if(attempt.status===200&&attempt.metadata&&typeof attempt.metadata==='object')return Object.freeze({verdict:'PACKAGE_METADATA_READY',terminal:true,attemptsUsed,nextDelaySeconds:0});
    if(attempt.status!==404)return Object.freeze({verdict:'PACKAGE_METADATA_HTTP_HOLD',terminal:true,attemptsUsed,nextDelaySeconds:0});
  }
  if(attempts.length>=6)return Object.freeze({verdict:'PACKAGE_METADATA_NOT_VISIBLE_HOLD',terminal:true,attemptsUsed:attempts.length,nextDelaySeconds:0});
  return Object.freeze({verdict:'PACKAGE_METADATA_RETRY',terminal:false,attemptsUsed:attempts.length,nextDelaySeconds:15});
}

export function evaluateRecoveryObservation(value) {
  const reasons=[];
  if(!value||typeof value!=='object'||Array.isArray(value)||value.schemaVersion!=='PHASE8D1_RECOVERY_OBSERVATION_1')reasons.push('INVALID_RECOVERY_OBSERVATION');
  if(value?.imageSourceSha!==EXPECTED_IMAGE_SOURCE_SHA)reasons.push('IMAGE_SOURCE_SHA_MISMATCH');
  if(!SHA.test(value?.recoveryWorkflowSha??''))reasons.push('RECOVERY_WORKFLOW_SHA_INVALID');
  if(value?.imageSourceAncestor!==true)reasons.push('IMAGE_SOURCE_NOT_ANCESTOR');
  if(value?.originalPublishRunId!==EXPECTED_PUBLISH_RUN)reasons.push('ORIGINAL_PUBLISH_RUN_MISMATCH');
  if(value?.originalArtifactsVerified!==true)reasons.push('ORIGINAL_ARTIFACT_CHAIN_HOLD');
  if(value?.originalHoldPreserved!==true)reasons.push('ORIGINAL_HOLD_PRESERVATION_HOLD');
  if(value?.baseImageDriftVerdict!=='PASS')reasons.push('BASE_IMAGE_DRIFT_HOLD');
  if(value?.cleanTreeVerdict!=='PASS')reasons.push('CLEAN_TREE_HOLD');
  if(value?.zeroMutationEvidence!==true)reasons.push('ZERO_MUTATION_HOLD');
  const images=Array.isArray(value?.images)?value.images:[];
  const names=new Set(images.map(image=>image?.name));
  if(images.length!==2||!names.has('cockpit')||!names.has('runner')||names.size!==2)reasons.push('RECOVERY_IMAGE_SET_INVALID');
  for(const image of images){
    const name=image?.name,expected=EXPECTED_IMAGES[name];
    if(!expected){reasons.push('RECOVERY_IMAGE_SET_INVALID');continue;}
    if(image.package!==expected.package||image.tag!==expected.tag||image.expectedDigest!==expected.digest)reasons.push(`TAG_IDENTITY_MISMATCH:${name}`);
    if(image.resolvedDigest!==expected.digest)reasons.push(`TAG_DIGEST_DRIFT:${name}`);
    if(image.metadataStatus===401||image.metadataStatus===403)reasons.push(`PACKAGE_API_PERMISSION_HOLD:${name}`);
    else if(image.metadataStatus===404||image.packageExists!==true)reasons.push(`PACKAGE_METADATA_NOT_VISIBLE_HOLD:${name}`);
    else if(image.metadataStatus!==200)reasons.push(`PACKAGE_METADATA_HTTP_HOLD:${name}`);
    if(image.packageType!=='container')reasons.push(`PACKAGE_TYPE_HOLD:${name}`);
    if(image.visibility!=='private')reasons.push(`PACKAGE_VISIBILITY_HOLD:${name}`);
    if(image.repositoryFullName!==EXPECTED_REPOSITORY)reasons.push(`PACKAGE_REPOSITORY_LINK_HOLD:${name}`);
    if(image.unauthenticatedPullDenied!==true)reasons.push(`UNAUTHENTICATED_PULL_SUCCEEDED_HOLD:${name}`);
    if(image.authenticatedPullSucceeded!==true)reasons.push(`AUTHENTICATED_PULL_FAILED_HOLD:${name}`);
    if(image.platform!=='linux/amd64')reasons.push(`PLATFORM_HOLD:${name}`);
    if(image.sourceGitSha!==EXPECTED_IMAGE_SOURCE_SHA)reasons.push(`IMAGE_SOURCE_LABEL_HOLD:${name}`);
    const expectedUser=name==='cockpit'?'61001:61000':'61000:61000';if(image.imageConfig?.user!==expectedUser)reasons.push(`RUNTIME_IDENTITY_HOLD:${name}`);
    if(image.provenancePresent!==true)reasons.push(`PROVENANCE_HOLD:${name}`);
    if(image.spdxSbomPresent!==true)reasons.push(`SPDX_SBOM_HOLD:${name}`);
    if(image.digestRetestPassed!==true)reasons.push(`DIGEST_RETEST_HOLD:${name}`);
  }
  const verdict=reasons.length===0?'PUBLISH_SUCCEEDED':'RECOVERY_HOLD';
  return Object.freeze({
    schemaVersion:'PHASE8D1_RECOVERY_MANIFEST_1',
    recoveryMode:'EXISTING_DIGEST_READ_ONLY_RECOVERY',
    recoveryWorkflowSha:value?.recoveryWorkflowSha??null,
    imageSourceSha:value?.imageSourceSha??null,
    imageSourceAncestor:value?.imageSourceAncestor===true,
    originalPublishRunId:value?.originalPublishRunId??null,
    originalArtifactsVerified:value?.originalArtifactsVerified===true,
    originalHoldPreserved:value?.originalHoldPreserved===true,
    supersedesOriginalHoldWithoutDeletion:true,
    images:images.map(image=>Object.freeze({...image})),
    baseImageDriftVerdict:value?.baseImageDriftVerdict??'HOLD',
    cleanTreeVerdict:value?.cleanTreeVerdict??'HOLD',
    zeroMutationEvidence:value?.zeroMutationEvidence===true,
    verdict,
    deploymentEligible:verdict==='PUBLISH_SUCCEEDED',
    recoveryCompleted:verdict==='PUBLISH_SUCCEEDED',
    reasons:Object.freeze([...new Set(reasons)].sort()),
  });
}

const invoked=process.argv[1]?resolve(process.argv[1]):'';
if(invoked&&invoked===fileURLToPath(import.meta.url)){
  try{
    if(process.argv[2]!=='require-success'||!process.argv[3])throw new Error('INVALID_RECOVERY_COMMAND');
    const manifestPath=resolve(process.argv[3]),bytes=readFileSync(manifestPath),value=JSON.parse(bytes.toString('utf8'));
    const sidecarPath=join(dirname(manifestPath),'recovery-manifest.sha256'),expectedSidecar=`${createHash('sha256').update(bytes).digest('hex')}  ${basename(manifestPath)}\n`;
    if(readFileSync(sidecarPath,'utf8')!==expectedSidecar)throw new Error('RECOVERY_MANIFEST_DIGEST_MISMATCH');
    const schema=JSON.parse(readFileSync('deployment/phase8d1/recovery-manifest.schema.json','utf8')),schemaErrors=validateJsonSchema(schema,value);if(schemaErrors.length)throw new Error('RECOVERY_MANIFEST_SCHEMA_INVALID');
    const contract=JSON.parse(readFileSync('deployment/phase8d1/existing-digest-recovery-contract.json','utf8'));
    if(JSON.stringify(value.originalArtifacts)!==JSON.stringify(contract.originalArtifacts))throw new Error('RECOVERY_ORIGINAL_ARTIFACTS_MISMATCH');
    const evaluated=evaluateRecoveryObservation({...value,schemaVersion:'PHASE8D1_RECOVERY_OBSERVATION_1'});
    if(evaluated.verdict!=='PUBLISH_SUCCEEDED'||evaluated.deploymentEligible!==true||evaluated.recoveryCompleted!==true||value.verdict!==evaluated.verdict||value.deploymentEligible!==evaluated.deploymentEligible||value.recoveryCompleted!==evaluated.recoveryCompleted||value.runnerVerdict!=='PASS'||value.cockpitVerdict?.unavailable!=='PASS'||value.cockpitVerdict?.provider!=='PASS'||value.reasons?.length!==0||value.executionReasons?.length!==0)throw new Error(`RECOVERY_NOT_SUCCESSFUL:${value.verdict??'UNKNOWN'}`);
    process.stdout.write('PHASE8D1_RECOVERY_SUCCESS\n');
  }catch(error){process.stderr.write(`${error instanceof SyntaxError?'INVALID_RECOVERY_JSON':error instanceof Error?error.message:'RECOVERY_GATE_FAILED'}\n`);process.exit(1);}
}
