#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { evaluatePackageMetadataAttempts, evaluateRecoveryObservation, verifyRepositoryPackageAccess } from './recovery-state.mjs';
import { validateArtifactEntries, validateOriginalArtifactMetadata, validateOriginalStateChain } from './recovery-artifacts.mjs';
import { validateJsonSchema } from './json-schema-subset.mjs';
import { downloadGitHubArtifactZip, fetchAllPackageVersions, fetchBytesBounded, fetchHeadBounded, fetchJsonBounded, fetchUnauthenticatedRegistryManifestStatuses } from './recovery-http.mjs';
import { buildExpectedBuilderId, evaluateAttestationEvidence, evaluateAttestationManifestBinding, verifyAttestationLayerBytes } from './recovery-oci.mjs';

const CONTRACT_PATH='deployment/phase8d1/existing-digest-recovery-contract.json';
const API='https://api.github.com';
const REGISTRY='https://ghcr.io';
const MANIFEST_ACCEPT='application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json';
const MAX_EVIDENCE_BYTES=1_048_576;
const required=name=>{const value=process.env[name];if(!value)throw new Error(`MISSING_ENV:${name}`);return value;};
const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
const canonicalVersions=versions=>(versions??[]).map(version=>({id:version.id,tags:[...(version.metadata?.container?.tags??version.tags??[])].sort()})).sort((a,b)=>String(a.id).localeCompare(String(b.id)));
const safeReason=value=>String(value??'RECOVERY_EXECUTION_HOLD').replace(/[^A-Z0-9_.:-]/g,'_').slice(0,160);
function run(command,args,{cwd=process.cwd(),env=process.env,input,allowFailure=false}={}){
  const result=spawnSync(command,args,{cwd,env,input,encoding:'utf8',maxBuffer:8*1024*1024});
  if(result.error)throw new Error(`COMMAND_UNAVAILABLE:${basename(command)}`);
  if(result.status!==0&&!allowFailure)throw new Error(`COMMAND_FAILED:${basename(command)}:${result.status}`);
  return result;
}
function walkFiles(root){
  const rows=[];
  for(const name of readdirSync(root,{withFileTypes:true})){
    const path=join(root,name.name),st=lstatSync(path);if(st.isSymbolicLink())throw new Error('ARTIFACT_SYMLINK_FORBIDDEN');
    if(st.isDirectory())rows.push(...walkFiles(path));else if(st.isFile())rows.push(path);else throw new Error('ARTIFACT_SPECIAL_FILE_FORBIDDEN');
  }
  return rows;
}
function writeJson(path,value){const bytes=`${JSON.stringify(value,null,2)}\n`;if(Buffer.byteLength(bytes)>MAX_EVIDENCE_BYTES)throw new Error('RECOVERY_EVIDENCE_TOO_LARGE');writeFileSync(path,bytes,{mode:0o600});}
async function githubGet(repository,path,token){
  const response=await fetchJsonBounded(`${API}/repos/${repository}${path}`,{deadlineMs:Date.now()+30_000,timeoutMs:30_000,method:'GET',headers:{Authorization:`Bearer ${token}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'phase8d1-existing-digest-recovery'}});return {status:response.status,body:response.body};
}
async function packageGet(owner,packageName,token,suffix='',deadlineMs=Date.now()+15_000){
  const response=await fetchJsonBounded(`${API}/users/${owner}/packages/container/${packageName}${suffix}`,{deadlineMs,timeoutMs:15_000,method:'GET',headers:{Authorization:`Bearer ${token}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'phase8d1-existing-digest-recovery'}});return {status:response.status,body:response.body};
}
async function registryToken(owner,packageName,actor,token){
  const basic=Buffer.from(`${actor}:${token}`).toString('base64'),scope=`repository:${owner}/${packageName}:pull`;
  const response=await fetchJsonBounded(`${REGISTRY}/token?service=ghcr.io&scope=${encodeURIComponent(scope)}`,{deadlineMs:Date.now()+30_000,timeoutMs:30_000,headers:{Authorization:`Basic ${basic}`,'User-Agent':'phase8d1-existing-digest-recovery'}});if(response.status!==200)throw new Error(`REGISTRY_TOKEN_HTTP_${response.status}`);const bearer=response.body?.token??response.body?.access_token;if(!bearer)throw new Error('REGISTRY_TOKEN_MISSING');return bearer;
}
async function registryFetch(owner,packageName,kind,reference,bearer,{method='GET',accept=MANIFEST_ACCEPT}={}){
  const options={deadlineMs:Date.now()+30_000,timeoutMs:30_000,headers:{...(bearer?{Authorization:`Bearer ${bearer}`}:{}),Accept:accept,'User-Agent':'phase8d1-existing-digest-recovery'}};if(method==='HEAD'){const response=await fetchHeadBounded(`${REGISTRY}/v2/${owner}/${packageName}/${kind}/${reference}`,options);return {status:response.status,digest:response.digest,bytes:Buffer.alloc(0)};}const response=await fetchBytesBounded(`${REGISTRY}/v2/${owner}/${packageName}/${kind}/${reference}`,{...options,method});return {status:response.status,digest:response.digest,bytes:response.bytes};
}
async function inspectRegistryImage(image,owner,actor,token,expectedBuilderId,builderIdentity){
  const packageName=image.package,bearer=await registryToken(owner,packageName,actor,token);
  const unauth=await registryFetch(owner,packageName,'manifests',image.digest,null,{method:'HEAD'});
  const tagHead=await registryFetch(owner,packageName,'manifests',image.tag.split(':').at(-1),bearer,{method:'HEAD'});
  const indexResponse=await registryFetch(owner,packageName,'manifests',image.digest,bearer);
  if(indexResponse.status!==200||indexResponse.digest!==image.digest)throw new Error(`TAG_DIGEST_DRIFT:${image.name}`);
  const index=JSON.parse(indexResponse.bytes.toString('utf8')),manifests=index.manifests??[];
  const linux=manifests.filter(row=>row.platform?.os==='linux'&&row.platform?.architecture==='amd64');if(linux.length!==1)throw new Error(`PLATFORM_HOLD:${image.name}`);const linuxDescriptor=linux[0];
  const attestations=manifests.filter(row=>row.platform?.os==='unknown'&&row.platform?.architecture==='unknown'),attestationRecords=[],candidateSummaries=[];let acceptedManifestCount=0;
  for(const descriptor of attestations){
    const summary={descriptor:{mediaType:descriptor.mediaType??null,digest:descriptor.digest??null,size:descriptor.size??null,platform:descriptor.platform??null,referenceType:descriptor.annotations?.['vnd.docker.reference.type']??null,referenceDigest:descriptor.annotations?.['vnd.docker.reference.digest']??null},manifestStatus:0,artifactType:null,subject:null,config:null,bindingFormat:null,bindingReason:null,layers:[]};
    const manifestResponse=await registryFetch(owner,packageName,'manifests',descriptor.digest,bearer);summary.manifestStatus=manifestResponse.status;
    if(manifestResponse.status!==200){summary.bindingReason='ATTESTATION_MANIFEST_UNREADABLE';candidateSummaries.push(summary);continue;}
    let manifest;try{manifest=JSON.parse(manifestResponse.bytes.toString('utf8'));}catch{summary.bindingReason='ATTESTATION_MANIFEST_JSON_INVALID';candidateSummaries.push(summary);continue;}
    summary.artifactType=manifest.artifactType??null;summary.subject=manifest.subject?{mediaType:manifest.subject.mediaType??null,digest:manifest.subject.digest??null,size:manifest.subject.size??null}:null;summary.config=manifest.config?{mediaType:manifest.config.mediaType??null,digest:manifest.config.digest??null,size:manifest.config.size??null}:null;
    const binding=evaluateAttestationManifestBinding({descriptor,manifest,linuxDescriptor});summary.bindingFormat=binding.format;summary.bindingReason=binding.reason;
    if(!binding.accepted){candidateSummaries.push(summary);continue;}
    if((manifest.layers??[]).some(layer=>layer.mediaType!=='application/vnd.in-toto+json')){summary.bindingReason='ATTESTATION_LAYER_MEDIA_TYPE_MISMATCH';candidateSummaries.push(summary);continue;}
    acceptedManifestCount++;
    for(const layer of manifest.layers??[]){
      const predicateType=layer.annotations?.['in-toto.io/predicate-type']??'',blob=await registryFetch(owner,packageName,'blobs',layer.digest,bearer,{accept:'application/octet-stream'}),layerDigestVerified=verifyAttestationLayerBytes(layer,blob.status,blob.bytes);let payload=null;
      if(layerDigestVerified){try{payload=JSON.parse(blob.bytes.toString('utf8'));}catch{payload=null;}}
      attestationRecords.push({referenceDigest:binding.referenceDigest,manifestFormat:binding.format,layerMediaType:layer.mediaType,layerDigest:layer.digest,layerDigestVerified,predicateType,blobStatus:blob.status,payload});
      summary.layers.push({mediaType:layer.mediaType??null,digest:layer.digest??null,size:layer.size??null,predicateType,blobStatus:blob.status,digestVerified:layerDigestVerified,payloadSha256:layerDigestVerified?sha256(blob.bytes):null,payloadType:payload?._type??null,payloadPredicateType:payload?.predicateType??null,payloadBuilderId:payload?.predicate?.runDetails?.builder?.id??null,payloadSubjectDigests:Array.isArray(payload?.subject)?payload.subject.map(subject=>subject?.digest?.sha256??null).slice(0,8):[],sourceShaAtBuildKitPath:payload?.predicate?.buildDefinition?.externalParameters?.request?.args?.['build-arg:SOURCE_GIT_SHA']===process.env.IMAGE_SOURCE_SHA,spdxDocumentDescribesCount:Array.isArray(payload?.predicate?.documentDescribes)?payload.predicate.documentDescribes.length:0,spdxDescribesRelationshipCount:Array.isArray(payload?.predicate?.relationships)?payload.predicate.relationships.filter(row=>row?.spdxElementId==='SPDXRef-DOCUMENT'&&row?.relationshipType==='DESCRIBES').length:0});
    }
    candidateSummaries.push(summary);
  }
  const acceptedRecords=acceptedManifestCount===1?attestationRecords:[],attestationEvidence=evaluateAttestationEvidence({linuxManifestDigest:linuxDescriptor.digest,imageSourceSha:process.env.IMAGE_SOURCE_SHA,records:acceptedRecords,expectedBuilderId,builderIdentity}),attestationSummary={rootMediaType:index.mediaType??null,linuxDescriptor:{mediaType:linuxDescriptor.mediaType,digest:linuxDescriptor.digest,size:linuxDescriptor.size},unknownDescriptorCount:attestations.length,acceptedManifestCount,candidates:candidateSummaries};
  const linuxManifestResponse=await registryFetch(owner,packageName,'manifests',linuxDescriptor.digest,bearer);if(linuxManifestResponse.status!==200)throw new Error(`IMAGE_MANIFEST_HOLD:${image.name}`);const linuxManifest=JSON.parse(linuxManifestResponse.bytes.toString('utf8'));
  const configResponse=await registryFetch(owner,packageName,'blobs',linuxManifest.config.digest,bearer,{accept:'application/octet-stream'});if(configResponse.status!==200)throw new Error(`IMAGE_CONFIG_HOLD:${image.name}`);const config=JSON.parse(configResponse.bytes.toString('utf8'));
  return {unauthenticatedPullDenied:[401,403,404].includes(unauth.status),tagResolvedDigest:tagHead.digest,authenticatedManifestReadable:indexResponse.status===200,platform:'linux/amd64',linuxManifestDigest:linuxDescriptor.digest,imageConfigDigest:linuxManifest.config.digest,runtimeUser:config.config?.User??null,sourceGitSha:config.config?.Labels?.['org.opencontainers.image.revision']??null,attestationSummary,...attestationEvidence,bearer};
}
function safeExtract(zipPath,destination){
  const inspection=run('python3',['scripts/phase8d1/inspect-artifact-zip.py',zipPath,destination]),declared=JSON.parse(inspection.stdout);if(declared.schemaVersion!=='PHASE8D1_ARTIFACT_ZIP_INSPECTION_1')throw new Error('ARTIFACT_ZIP_INSPECTION_INVALID');
  const files=walkFiles(destination),entries=files.map(path=>({path:path.slice(destination.length+1),bytes:readFileSync(path)})),errors=validateArtifactEntries(entries);if(errors.length)throw new Error(errors[0]);return entries;
}
async function downloadOriginalArtifacts(contract,repository,token,evidenceDir){
  const response=await githubGet(repository,`/actions/runs/${contract.originalPublishRunId}/artifacts?per_page=100`,token);if(response.status!==200)throw new Error(`ARTIFACT_API_HTTP_${response.status}`);const metadataErrors=validateOriginalArtifactMetadata(response.body.artifacts,contract);if(metadataErrors.length)throw new Error(metadataErrors[0]);const root=join(evidenceDir,'original');mkdirSync(root,{recursive:true});const extracted={};const evidence=[];
  for(const expected of contract.originalArtifacts){
    const downloaded=await downloadGitHubArtifactZip({repository,artifactId:expected.id,role:expected.role,token,deadlineMs:Date.now()+30_000});if(downloaded.length!==expected.bytes||sha256(downloaded)!==expected.sha256)throw new Error(`ARTIFACT_ZIP_MISMATCH:${expected.role}`);
    const zipPath=join(root,`${expected.role}.zip`),destination=join(root,expected.role);writeFileSync(zipPath,downloaded,{mode:0o600});const entries=safeExtract(zipPath,destination);extracted[expected.role]={destination,entries};evidence.push({role:expected.role,id:expected.id,name:expected.name,bytes:expected.bytes,sha256:expected.sha256,entryCount:entries.length});
  }
  const readJson=(role,path)=>JSON.parse(readFileSync(join(extracted[role].destination,path),'utf8'));
  const states={preflight:readJson('preflight','publish-state.json'),cockpit:readJson('cockpit-push','publish-state.json'),runner:readJson('runner-push','publish-state.json'),final:readJson('final-hold','publish-state.json'),verify:readJson('verify-support','release-manifest.json')};const chainErrors=validateOriginalStateChain(states,contract);if(chainErrors.length)throw new Error(chainErrors[0]);writeJson(join(evidenceDir,'original-artifact-chain.json'),{schemaVersion:'PHASE8D1_RECOVERY_ARTIFACT_CHAIN_1',originalPublishRunId:contract.originalPublishRunId,imageSourceSha:contract.imageSourceSha,artifacts:evidence,verdict:'PASS',originalHoldPreserved:true});return {extracted,states,evidence};
}
async function readPackageMetadata(image,owner,token,contract){
  const attempts=[];let metadata=null,versions=[],versionPages=0,finalDecision=null;const deadlineMs=Date.now()+contract.packageMetadataRetry.maxTotalWaitSeconds*1000;
  for(let attempt=1;attempt<=contract.packageMetadataRetry.maxAttempts;attempt++){
    let response;
    try{response=await packageGet(owner,image.package,token,'',deadlineMs);}
    catch(error){finalDecision={verdict:safeReason(error instanceof Error?error.message:error),terminal:true,attemptsUsed:attempts.length,nextDelaySeconds:0};console.log(JSON.stringify({package:image.package,attempt,httpStatus:0,reasonCode:finalDecision.verdict}));break;}
    attempts.push({status:response.status,metadata:response.body});const decision=evaluatePackageMetadataAttempts(attempts);console.log(JSON.stringify({package:image.package,attempt,httpStatus:response.status,reasonCode:decision.verdict}));
    if(decision.verdict==='PACKAGE_METADATA_READY'){
      metadata=response.body;
      try{const paged=await fetchAllPackageVersions({initialUrl:`${API}/users/${owner}/packages/container/${image.package}/versions?per_page=100`,token,deadlineMs});versions=[...paged.versions];versionPages=paged.pages;finalDecision=decision;console.log(JSON.stringify({package:image.package,attempt,httpStatus:200,reasonCode:'PACKAGE_VERSIONS_COMPLETE',pages:versionPages}));}
      catch(error){finalDecision={verdict:safeReason(error instanceof Error?error.message:error),terminal:true,attemptsUsed:attempts.length,nextDelaySeconds:0};console.log(JSON.stringify({package:image.package,attempt,httpStatus:0,reasonCode:finalDecision.verdict,endpoint:'versions'}));}
      break;
    }
    if(decision.terminal){finalDecision=decision;break;}
    const delayMs=decision.nextDelaySeconds*1000;if(Date.now()+delayMs>deadlineMs){finalDecision={verdict:'PACKAGE_METADATA_TOTAL_TIMEOUT_HOLD',terminal:true,attemptsUsed:attempts.length,nextDelaySeconds:0};break;}await delay(delayMs);
  }
  const decision=finalDecision??evaluatePackageMetadataAttempts(attempts.slice(0,6));const exactTag=image.tag.slice(image.tag.lastIndexOf(':')+1),matching=versions.filter(version=>(version.metadata?.container?.tags??[]).includes(exactTag));return {attempts:attempts.map(({status})=>({status})),decision,metadata,versionPages,allVersions:canonicalVersions(versions),matchingVersions:matching.map(version=>({id:version.id,name:version.name,tags:version.metadata?.container?.tags??[],createdAt:version.created_at}))};
}
function unauthenticatedDockerPullDenied(image){
  const config=mkdtempSync(join(tmpdir(),'phase8d1-unauth-')),ref=`${image.tag}@${image.digest}`;
  try{return run('docker',['pull','--platform','linux/amd64',ref],{env:{...process.env,DOCKER_CONFIG:config},allowFailure:true}).status!==0;}
  finally{rmSync(config,{recursive:true,force:true});}
}
function dockerPullEvidence(image,actor,token){
  const authConfig=mkdtempSync(join(tmpdir(),'phase8d1-auth-')),ref=`${image.tag}@${image.digest}`;const unauthenticatedPullDenied=unauthenticatedDockerPullDenied(image);let authenticatedPullSucceeded=false;
  try{
    const login=run('docker',['login','ghcr.io','--username',actor,'--password-stdin'],{env:{...process.env,DOCKER_CONFIG:authConfig},input:token,allowFailure:true});if(login.status===0){const pull=run('docker',['pull','--platform','linux/amd64',ref],{env:{...process.env,DOCKER_CONFIG:authConfig},allowFailure:true});authenticatedPullSucceeded=pull.status===0;}
    return {unauthenticatedPullDenied,authenticatedPullSucceeded,authConfig};
  }finally{if(!authenticatedPullSucceeded)rmSync(authConfig,{recursive:true,force:true});}
}
async function main(){
  const contract=JSON.parse(readFileSync(CONTRACT_PATH,'utf8')),evidenceDir=resolve(required('RECOVERY_EVIDENCE_DIR')),repository=required('GITHUB_REPOSITORY'),owner=required('GITHUB_REPOSITORY_OWNER'),actor=required('GITHUB_ACTOR'),token=required('GH_TOKEN'),workflowSha=required('RECOVERY_WORKFLOW_SHA'),imageSourceSha=required('IMAGE_SOURCE_SHA'),sourceDir=resolve(required('IMAGE_SOURCE_DIR'));
  const builderIdentity={repository:contract.repository,originalPublishRunId:contract.originalPublishRunId,originalPublishRunAttempt:contract.originalPublishRunAttempt},expectedBuilderId=buildExpectedBuilderId(builderIdentity);
  mkdirSync(evidenceDir,{recursive:true});const images=contract.images.map(image=>({name:image.name,package:image.package,tag:image.tag,expectedDigest:image.digest,resolvedDigest:null,metadataStatus:0,packageExists:false,packageType:null,visibility:null,repositoryFullName:null,repositoryAccessVerified:false,unauthenticatedPullDenied:false,authenticatedPullSucceeded:false,platform:null,sourceGitSha:null,provenancePresent:false,spdxSbomPresent:false,digestRetestPassed:false}));let originalArtifactsVerified=false,originalHoldPreserved=false,imageSourceAncestor=false,baseImageDriftVerdict='HOLD',cleanTreeVerdict='HOLD',zeroMutationEvidence=false,runnerVerdict=null,cockpitVerdict=null;const boundedReasons=[],packageEvidence=[];
  try{
    if(!expectedBuilderId)throw new Error('RECOVERY_BUILDER_IDENTITY_HOLD');
    if(repository!==contract.repository)throw new Error('RECOVERY_REPOSITORY_CONTEXT_HOLD');
    if(imageSourceSha!==contract.imageSourceSha||required('COCKPIT_TAG')!==contract.images[0].tag||required('COCKPIT_DIGEST')!==contract.images[0].digest||required('RUNNER_TAG')!==contract.images[1].tag||required('RUNNER_DIGEST')!==contract.images[1].digest||Number(required('ORIGINAL_PUBLISH_RUN_ID'))!==contract.originalPublishRunId)throw new Error('RECOVERY_INPUT_MISMATCH');
    if(required('GITHUB_EVENT_NAME')!=='workflow_dispatch'||required('GITHUB_REF')!=='refs/heads/main'||required('GITHUB_SHA')!==workflowSha)throw new Error('RECOVERY_WORKFLOW_IDENTITY_HOLD');
    imageSourceAncestor=run('git',['merge-base','--is-ancestor',imageSourceSha,workflowSha],{allowFailure:true}).status===0;if(!imageSourceAncestor)throw new Error('IMAGE_SOURCE_NOT_ANCESTOR');
    const artifacts=await downloadOriginalArtifacts(contract,repository,token,evidenceDir);originalArtifactsVerified=true;originalHoldPreserved=artifacts.states.final.verdict===contract.originalFinalVerdict;
    const beforeVersions={};for(const item of contract.images){const pkg=await readPackageMetadata(item,owner,token,contract);packageEvidence.push({name:item.name,...pkg});beforeVersions[item.name]=pkg.allVersions;if(pkg.metadata){const target=images.find(image=>image.name===item.name);target.metadataStatus=200;target.packageExists=true;target.packageType=pkg.metadata.package_type??null;target.visibility=pkg.metadata.visibility??null;target.repositoryFullName=pkg.metadata.repository?.full_name??null;}else{images.find(image=>image.name===item.name).metadataStatus=pkg.attempts.at(-1)?.status??0;}}
    writeJson(join(evidenceDir,'package-metadata.json'),{schemaVersion:'PHASE8D1_RECOVERY_PACKAGE_METADATA_1',packages:packageEvidence.map(entry=>({name:entry.name,attempts:entry.attempts,decision:entry.decision,metadata:entry.metadata?{name:entry.metadata.name,packageType:entry.metadata.package_type,visibility:entry.metadata.visibility,repositoryFullName:entry.metadata.repository?.full_name??null}:null,versionPages:entry.versionPages,allVersionsBefore:entry.allVersions,allVersionsBeforeCount:entry.allVersions.length,allVersionsBeforeSha256:sha256(Buffer.from(JSON.stringify(entry.allVersions))),matchingVersions:entry.matchingVersions}))});
    if(packageEvidence.some(entry=>entry.decision.verdict!=='PACKAGE_METADATA_READY'||entry.matchingVersions.length!==1))throw new Error(packageEvidence.find(entry=>entry.decision.verdict!=='PACKAGE_METADATA_READY')?.decision.verdict??'PACKAGE_TAG_METADATA_HOLD');
    for(const item of contract.images){const target=images.find(image=>image.name===item.name);if(target.packageType!=='container')throw new Error(`PACKAGE_TYPE_HOLD:${item.name}`);if(target.visibility!=='private')throw new Error(`PACKAGE_VISIBILITY_HOLD:${item.name}`);}
    const authConfigs=[];
    try{
      for(const item of contract.images){
        const target=images.find(image=>image.name===item.name),packageProof=packageEvidence.find(entry=>entry.name===item.name),registry=await inspectRegistryImage(item,owner,actor,token,expectedBuilderId,builderIdentity),pull=dockerPullEvidence(item,actor,token);authConfigs.push(pull.authConfig);
        Object.assign(target,{resolvedDigest:registry.tagResolvedDigest,unauthenticatedPullDenied:pull.unauthenticatedPullDenied,authenticatedPullSucceeded:pull.authenticatedPullSucceeded,platform:registry.platform,sourceGitSha:registry.sourceGitSha,provenancePresent:registry.provenancePresent&&registry.provenanceSourceShaPresent,spdxSbomPresent:registry.spdxSbomPresent,imageConfig:{digest:registry.imageConfigDigest,user:registry.runtimeUser,linuxManifestDigest:registry.linuxManifestDigest}});
        target.repositoryAccessVerified=verifyRepositoryPackageAccess({runtimeRepository:repository,expectedPackage:item.package,observedPackage:packageProof?.metadata?.name,metadataStatus:target.metadataStatus,packageExists:target.packageExists,packageType:target.packageType,visibility:target.visibility,versionInventoryComplete:packageProof?.decision?.verdict==='PACKAGE_METADATA_READY'&&packageProof.versionPages>=1,matchingVersionCount:packageProof?.matchingVersions?.length,unauthenticatedManifestDenied:registry.unauthenticatedPullDenied,unauthenticatedPullDenied:pull.unauthenticatedPullDenied,authenticatedManifestReadable:registry.authenticatedManifestReadable,authenticatedPullSucceeded:pull.authenticatedPullSucceeded,expectedDigest:item.digest,resolvedDigest:registry.tagResolvedDigest});
        writeJson(join(evidenceDir,`${item.name}-registry-evidence.json`),{schemaVersion:'PHASE8D1_RECOVERY_REGISTRY_EVIDENCE_1',name:item.name,tag:item.tag,expectedDigest:item.digest,resolvedDigest:registry.tagResolvedDigest,platform:registry.platform,imageConfigDigest:registry.imageConfigDigest,runtimeUser:registry.runtimeUser,sourceGitSha:registry.sourceGitSha,repositoryFullName:target.repositoryFullName,repositoryAccessVerified:target.repositoryAccessVerified,attestationSummary:registry.attestationSummary,provenancePresent:target.provenancePresent,spdxSbomPresent:target.spdxSbomPresent,unauthenticatedPullDenied:pull.unauthenticatedPullDenied,authenticatedPullSucceeded:pull.authenticatedPullSucceeded});
      }
      if(images.some(image=>image.resolvedDigest!==image.expectedDigest||!image.repositoryAccessVerified||!image.unauthenticatedPullDenied||!image.authenticatedPullSucceeded||!image.provenancePresent||!image.spdxSbomPresent))throw new Error('REGISTRY_EVIDENCE_HOLD');
      const verifySupport=artifacts.extracted['verify-support'].destination,finalDir=join(evidenceDir,'digest-retest');mkdirSync(finalDir,{recursive:true});
      const env={...process.env,DOCKER_CONFIG:authConfigs[0],COCKPIT_IMAGE:`${contract.images[0].tag}@${contract.images[0].digest}`,RUNNER_IMAGE:`${contract.images[1].tag}@${contract.images[1].digest}`,COCKPIT_CANDIDATE_TAG:contract.images[0].tag,RUNNER_CANDIDATE_TAG:contract.images[1].tag,PUBLISH_MODE:'publish',COCKPIT_REGISTRY_DIGEST:contract.images[0].digest,RUNNER_REGISTRY_DIGEST:contract.images[1].digest,EVIDENCE_DIR:finalDir,BASE_MANIFEST:join(verifySupport,'base-images.json'),PREBUILD_MANIFEST:join(verifySupport,'prebuild-hashes.json')};run('bash',['scripts/phase8d1/verify-images.sh'],{cwd:sourceDir,env});const release=JSON.parse(readFileSync(join(finalDir,'release-manifest.json'),'utf8'));
      const runner=release.runnerVerification,cockpit=release.cockpitVerification;runnerVerdict=runner?.verdict??null;cockpitVerdict={unavailable:cockpit?.unavailableVerdict??null,provider:cockpit?.providerVerdict??null};const fixed=runner?.independentRuns===2&&runner?.retainedFiles===20&&runner?.byteIdentical===true&&runner?.runId==='phase8a-fixture-a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82'&&runner?.aggregateHash==='7123c27ffcfd3388b6fa2b98cc3c59be0b42028cf6778404ba645b83254f4791'&&runner?.semanticRerunHash==='94c954098b0bbf1b772932395333af28c1b5317c8e1c0f6c88381f0e2ab5e3b0'&&runner?.perFileManifestSha256==='aa18a485c53caeb99098e366bce57d3f15d922bf8d60db60205d9c38d2aca258'&&runner?.outboundDenied&&runner?.writeDenied&&runner?.orphanContainers===0&&cockpit?.unavailableVerdict==='PASS'&&cockpit?.providerVerdict==='PASS'&&cockpit?.outboundDenied&&cockpit?.outputUnchanged&&cockpit?.cleanShutdown;
      if(!fixed)throw new Error('DIGEST_RETEST_HOLD');for(const image of images)image.digestRetestPassed=true;
      run(process.execPath,['scripts/phase8d1/resolve-base-images.mjs','compare',join(verifySupport,'base-images.json')]);baseImageDriftVerdict='PASS';
      const rootClean=run('git',['status','--porcelain','--untracked-files=no'],{allowFailure:true}).stdout.trim()==='',sourceClean=run('git',['status','--porcelain','--untracked-files=no'],{cwd:sourceDir,allowFailure:true}).stdout.trim()==='';cleanTreeVerdict=rootClean&&sourceClean?'PASS':'HOLD';
      const afterVersions={},afterPages={},finalReadonlyChecks=[],finalDeadlineMs=Date.now()+contract.packageMetadataRetry.maxTotalWaitSeconds*1000;
      for(const item of contract.images){
        const target=images.find(image=>image.name===item.name);let metadataResponse={status:0,body:null},paged={versions:[],pages:0},authenticatedTagStatus=0,resolvedDigest=null,metadataError=null,versionError=null,tagError=null;
        try{metadataResponse=await packageGet(owner,item.package,token,'',finalDeadlineMs);}catch(error){metadataError=safeReason(error instanceof Error?error.message:error);}
        if(metadataResponse.status===200){try{paged=await fetchAllPackageVersions({initialUrl:`${API}/users/${owner}/packages/container/${item.package}/versions?per_page=100`,token,deadlineMs:finalDeadlineMs});}catch(error){versionError=safeReason(error instanceof Error?error.message:error);}}
        const canonical=canonicalVersions(paged.versions),tag=item.tag.slice(item.tag.lastIndexOf(':')+1),matching=canonical.filter(version=>version.tags.includes(tag));afterVersions[item.name]=canonical;afterPages[item.name]=paged.pages;
        try{const bearer=await registryToken(owner,item.package,actor,token),head=await registryFetch(owner,item.package,'manifests',tag,bearer,{method:'HEAD'});authenticatedTagStatus=head.status;resolvedDigest=head.digest;}catch(error){tagError=safeReason(error instanceof Error?error.message:error);}
        const privacy=await fetchUnauthenticatedRegistryManifestStatuses({url:`${REGISTRY}/v2/${owner}/${item.package}/manifests/${item.digest}`,deadlineMs:finalDeadlineMs});
        finalReadonlyChecks.push({name:item.name,package:item.package,packageHttpStatus:metadataResponse.status,packageType:metadataResponse.body?.package_type??null,visibility:metadataResponse.body?.visibility??null,repositoryFullName:metadataResponse.body?.repository?.full_name??null,repositoryAccessVerified:target.repositoryAccessVerified,versionPages:paged.pages,versionCount:canonical.length,matchingVersionCount:matching.length,tag:item.tag,expectedDigest:item.digest,authenticatedTagStatus,resolvedDigest,unauthenticatedHeadStatus:privacy.unauthenticatedHeadStatus,unauthenticatedGetStatus:privacy.unauthenticatedGetStatus,metadataError,versionError,tagError});
      }
      const inventory={schemaVersion:'PHASE8D1_RECOVERY_PACKAGE_VERSION_INVENTORY_1',packages:contract.images.map(item=>({name:item.name,beforePages:packageEvidence.find(entry=>entry.name===item.name).versionPages,afterPages:afterPages[item.name],before:beforeVersions[item.name],after:afterVersions[item.name],beforeSha256:sha256(Buffer.from(JSON.stringify(beforeVersions[item.name]))),afterSha256:sha256(Buffer.from(JSON.stringify(afterVersions[item.name])))}))};
      writeJson(join(evidenceDir,'package-version-inventory.json'),inventory);writeJson(join(evidenceDir,'final-readonly-recheck.json'),{schemaVersion:'PHASE8D1_RECOVERY_FINAL_READONLY_RECHECK_1',packages:finalReadonlyChecks,verdict:'OBSERVED'});
      if(finalReadonlyChecks.some(row=>row.packageHttpStatus!==200||row.packageType!=='container'||row.visibility!=='private'||row.matchingVersionCount!==1||row.repositoryAccessVerified!==true))throw new Error('FINAL_PACKAGE_METADATA_HOLD');
      if(finalReadonlyChecks.some(row=>row.authenticatedTagStatus!==200||row.resolvedDigest!==row.expectedDigest))throw new Error('FINAL_TAG_DIGEST_DRIFT');
      if(finalReadonlyChecks.some(row=>![401,403,404].includes(row.unauthenticatedHeadStatus)))throw new Error('FINAL_UNAUTHENTICATED_HEAD_HOLD');
      if(finalReadonlyChecks.some(row=>![401,403,404].includes(row.unauthenticatedGetStatus)))throw new Error('FINAL_UNAUTHENTICATED_GET_HOLD');
      writeJson(join(evidenceDir,'final-readonly-recheck.json'),{schemaVersion:'PHASE8D1_RECOVERY_FINAL_READONLY_RECHECK_1',packages:finalReadonlyChecks,verdict:'PASS'});
      zeroMutationEvidence=JSON.stringify(beforeVersions)===JSON.stringify(afterVersions);if(!zeroMutationEvidence)throw new Error('ZERO_MUTATION_HOLD');
    }finally{for(const path of authConfigs)rmSync(path,{recursive:true,force:true});}
  }catch(error){boundedReasons.push(safeReason(error instanceof Error?error.message:error));}
  const observation={schemaVersion:'PHASE8D1_RECOVERY_OBSERVATION_1',recoveryWorkflowSha:workflowSha,imageSourceSha,imageSourceAncestor,originalPublishRunId:Number(process.env.ORIGINAL_PUBLISH_RUN_ID??0),originalArtifactsVerified,originalHoldPreserved,images,baseImageDriftVerdict,cleanTreeVerdict,zeroMutationEvidence};const evaluated=evaluateRecoveryObservation(observation),manifest={...evaluated,rootCauseCategory:contract.rootCauseCategory,originalArtifacts:contract.originalArtifacts,packageMetadata:packageEvidence.map(entry=>({name:entry.name,attempts:entry.attempts,decision:entry.decision,metadata:entry.metadata?{name:entry.metadata.name,packageType:entry.metadata.package_type,visibility:entry.metadata.visibility,repositoryFullName:entry.metadata.repository?.full_name??null}:null,versionPages:entry.versionPages,allVersionsBeforeCount:entry.allVersions.length,allVersionsBeforeSha256:sha256(Buffer.from(JSON.stringify(entry.allVersions))),matchingVersions:entry.matchingVersions})),runnerVerdict,cockpitVerdict,executionReasons:boundedReasons,zeroMutationDetails:{imageBuild:false,imagePush:false,tagCreate:false,tagOverwrite:false,packageVersionDelete:false,packageSettingsChange:false}};
  const schema=JSON.parse(readFileSync('deployment/phase8d1/recovery-manifest.schema.json','utf8')),schemaErrors=validateJsonSchema(schema,manifest);if(schemaErrors.length)boundedReasons.push('RECOVERY_MANIFEST_SCHEMA_HOLD');
  const finalManifest=schemaErrors.length?{...manifest,verdict:'RECOVERY_HOLD',deploymentEligible:false,recoveryCompleted:false,executionReasons:boundedReasons}:manifest;
  writeJson(join(evidenceDir,'recovery-manifest.json'),finalManifest);writeFileSync(join(evidenceDir,'recovery-manifest.sha256'),`${sha256(readFileSync(join(evidenceDir,'recovery-manifest.json')))}  recovery-manifest.json\n`,{mode:0o600});const files=walkFiles(evidenceDir).filter(path=>!path.includes(`${join(evidenceDir,'original')}/`)),total=files.reduce((sum,path)=>sum+lstatSync(path).size,0);if(total>MAX_EVIDENCE_BYTES)throw new Error('RECOVERY_ARTIFACT_TOO_LARGE');
  console.log(JSON.stringify({verdict:finalManifest.verdict,deploymentEligible:finalManifest.deploymentEligible,recoveryCompleted:finalManifest.recoveryCompleted,reasons:finalManifest.reasons,executionReasons:finalManifest.executionReasons}));
}

main().catch(error=>{process.stderr.write(`${safeReason(error instanceof Error?error.message:error)}\n`);process.exit(1);});
