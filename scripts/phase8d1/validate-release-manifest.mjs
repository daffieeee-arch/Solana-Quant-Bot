#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { validateJsonSchema } from './json-schema-subset.mjs';

const SHA256=/^[0-9a-f]{64}$/;
const DIGEST=/^sha256:[0-9a-f]{64}$/;
const GIT=/^[0-9a-f]{40}$/;
const record=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const exact=(value,expected)=>JSON.stringify(value)===JSON.stringify(expected);

export function validatePhase8D1ReleaseManifest(value) {
  const schema=JSON.parse(readFileSync('deployment/phase8d1/release-manifest.schema.json','utf8'));
  const e=validateJsonSchema(schema,value).map(error=>`schema:${error}`);
  if (!record(value)) return ['manifest_not_object'];
  if (value.schemaVersion!=='PHASE8D1_RELEASE_MANIFEST_1') e.push('invalid_schema');
  if (!['VERIFY_ONLY_SUCCEEDED','PUBLISH_SUCCEEDED'].includes(value.status)) e.push('invalid_status');
  if (!GIT.test(value.sourceGitSha??'') || !GIT.test(value.workflowCommitSha??'') || value.workflowCommitSha!==value.sourceGitSha || !/^\d+$/.test(value.workflowRunId??'')) e.push('invalid_workflow_identity');
  if (value.platform!=='linux/amd64' || typeof value.builderRunnerImage!=='string' || !value.builderRunnerImage) e.push('invalid_builder_platform');
  const ids=value.runtimeIdentities;
  if (!record(ids)||ids.runnerUid!==61000||ids.cockpitUid!==61001||ids.fixtureReadGid!==61000||ids.status!=='SELECTED_READ_ONLY_NOT_APPLIED') e.push('invalid_runtime_identities');
  if (!Array.isArray(value.baseImages)||value.baseImages.length!==4||new Set(value.baseImages.map(x=>x.name)).size!==4||!['nodeBuilder','nodeRuntime','rust','runnerRuntime'].every(name=>value.baseImages.some(x=>x.name===name))) e.push('invalid_base_images');
  else for(const row of value.baseImages){
    if(row.registry!=='docker.io'||!String(row.repository??'').startsWith('library/')||row.versionTag==='latest'||!DIGEST.test(row.manifestListDigest??'')||!DIGEST.test(row.linuxAmd64Digest??'')||row.digestRef!==`${row.registry}/${row.repository}@${row.linuxAmd64Digest}`||row.os!=='linux'||row.architecture!=='amd64'||!Number.isSafeInteger(row.compressedSizeBytes)||row.compressedSizeBytes<1||!row.observedAt||!row.softwareVersion)e.push(`invalid_base_image:${row.name}`);
  }
  const hashes=value.prebuildHashes;
  for(const key of ['packageLockSha256','cargoLockSha256','cockpitEntrypointSha256','cockpitRuntimeTreeSha256','cockpitFrontendTreeSha256','runnerBinarySha256']) if(!SHA256.test(hashes?.[key]??'')) e.push(`invalid_hash:${key}`);
  if(!Array.isArray(value.images)||value.images.length!==2||new Set(value.images.map(x=>x.name)).size!==2||!['phase8a-research-cockpit','phase8a-bronze-runner'].every(name=>value.images.some(x=>x.name===name)))e.push('invalid_images');
  else for(const image of value.images){
    if(!['phase8a-research-cockpit','phase8a-bronze-runner'].includes(image.name)||!image.candidateTag||image.candidateTag.length>128||/(?:^|:)latest$/.test(image.candidateTag)||!DIGEST.test(image.imageId??'')||image.inspectionVerdict!=='PASS')e.push(`invalid_image:${image.name}`);
  }
  const runner=value.runnerVerification;
  if(!record(runner)||runner.verdict!=='PASS'||runner.independentRuns!==2||runner.retainedFiles!==20||runner.byteIdentical!==true||runner.fixtureInputSha256!=='a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82'||runner.perFileManifestSha256!=='aa18a485c53caeb99098e366bce57d3f15d922bf8d60db60205d9c38d2aca258'||runner.runId!=='phase8a-fixture-a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82'||runner.aggregateHash!=='7123c27ffcfd3388b6fa2b98cc3c59be0b42028cf6778404ba645b83254f4791'||runner.semanticRerunHash!=='94c954098b0bbf1b772932395333af28c1b5317c8e1c0f6c88381f0e2ab5e3b0'||runner.outboundDenied!==true||runner.writeDenied!==true||runner.orphanContainers!==0)e.push('invalid_runner_verification');
  const cockpit=value.cockpitVerification;
  if(!record(cockpit)||cockpit.unavailableVerdict!=='PASS'||cockpit.providerVerdict!=='PASS'||cockpit.outboundDenied!==true||cockpit.outputUnchanged!==true||cockpit.cleanShutdown!==true)e.push('invalid_cockpit_verification');
  const non=value.nonActions;
  if(!record(non)||non.trueNasMutation!==false||non.datasetCreated!==false||non.deployment!==false||non.grafanaModified!==false||non.clickhouseModified!==false||non.payloadRetrieved!==false||non.pilotExecuted!==false)e.push('invalid_no_deployment_nonactions');
  if(value.status==='VERIFY_ONLY_SUCCEEDED'){
    if(value.packageVisibility!=='NOT_APPLICABLE_VERIFY_ONLY'||non?.imagePush!==false)e.push('invalid_verify_only_publish_state');
    for(const image of value.images??[])if(image.registryDigest!==null||image.ociProvenancePresent!==false||image.spdxSbomPresent!==false||image.finalDigestRetestVerdict!=='NOT_APPLICABLE_VERIFY_ONLY')e.push('invalid_verify_only_image_state');
  }
  if(value.status==='PUBLISH_SUCCEEDED'){
    if(value.packageVisibility!=='private'||non?.imagePush!==true)e.push('invalid_publish_visibility');
    for(const image of value.images??[])if(!DIGEST.test(image.registryDigest??'')||image.ociProvenancePresent!==true||image.spdxSbomPresent!==true||image.finalDigestRetestVerdict!=='PASS')e.push(`publish_requires_digest_provenance_sbom_retest:${image.name}`);
  }
  return [...new Set(e)].sort();
}

if(import.meta.url===`file://${process.argv[1]}`){
  const path=process.argv[2];
  if(!path)throw new Error('usage: validate-release-manifest.mjs <manifest.json>');
  const errors=validatePhase8D1ReleaseManifest(JSON.parse(readFileSync(path,'utf8')));
  if(errors.length){console.error(errors.join('\n'));process.exit(1)}
  console.log('Phase 8D1 release manifest PASS');
}
