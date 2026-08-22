#!/usr/bin/env node
import { readFileSync, renameSync, writeFileSync } from 'node:fs';

const SHA=/^[0-9a-f]{40}$/;
const DIGEST=/^sha256:[0-9a-f]{64}$/;
const names=['cockpit','runner'];
const packages={cockpit:'phase8a-research-cockpit',runner:'phase8a-bronze-runner'};
const boolFields=['private','repositoryLinked','provenancePresent','spdxSbomPresent','digestRetestPassed'];
function fail(message){throw new Error(message);}
function exactKeys(value,expected){return value&&typeof value==='object'&&!Array.isArray(value)&&JSON.stringify(Object.keys(value).sort())===JSON.stringify([...expected].sort());}
export function evaluatePartialPublishObservation(value){
  if(!exactKeys(value,['schemaVersion','sourceGitSha','releaseId','images'])||value.schemaVersion!=='PHASE8D1_PARTIAL_PUBLISH_OBSERVATION_1'||!SHA.test(value.sourceGitSha)||value.releaseId!==value.sourceGitSha||!Array.isArray(value.images)||value.images.length!==2)fail('INVALID_PARTIAL_PUBLISH_OBSERVATION');
  const seen=new Set();
  const images=value.images.map(image=>{
    if(!exactKeys(image,['name','package','tag','preflightStatus','pushStatus','registryDigest',...boolFields])||!names.includes(image.name)||seen.has(image.name)||image.package!==packages[image.name]||typeof image.tag!=='string'||!image.tag.includes(`:${value.sourceGitSha}-`)||image.tag.endsWith(':latest')||!['ABSENT','PRESENT','UNKNOWN'].includes(image.preflightStatus)||!['NOT_ATTEMPTED','PUSHED','FAILED'].includes(image.pushStatus)||!boolFields.every(field=>typeof image[field]==='boolean'))fail('INVALID_PARTIAL_PUBLISH_IMAGE');
    seen.add(image.name);
    const digestRequired=image.preflightStatus==='PRESENT'||image.pushStatus==='PUSHED';
    if(digestRequired?!DIGEST.test(image.registryDigest??''):image.registryDigest!==null)fail('INVALID_PARTIAL_PUBLISH_DIGEST');
    if(image.pushStatus!=='PUSHED'&&boolFields.some(field=>image[field]))fail('UNPUSHED_IMAGE_CANNOT_BE_VERIFIED');
    return Object.freeze({...image});
  });
  if(!names.every(name=>seen.has(name)))fail('MISSING_PARTIAL_PUBLISH_IMAGE');
  const present=images.filter(x=>x.preflightStatus==='PRESENT').length;
  const unknown=images.some(x=>x.preflightStatus==='UNKNOWN');
  const pushed=images.filter(x=>x.pushStatus==='PUSHED').length;
  const failed=images.filter(x=>x.pushStatus==='FAILED').length;
  const allVerified=images.every(x=>x.pushStatus==='PUSHED'&&boolFields.every(field=>x[field]));
  let verdict;
  if(unknown)verdict='PREFLIGHT_UNKNOWN_HOLD';
  else if(present===1)verdict='PARTIAL_PUBLISH_COLLISION_HOLD';
  else if(present===2)verdict='TAG_COLLISION_HOLD';
  else if(pushed===2&&allVerified)verdict='PUBLISH_SUCCEEDED';
  else if(pushed===2)verdict='BOTH_PUSHED_RETEST_REQUIRED_HOLD';
  else if(pushed===1)verdict='PARTIAL_PUBLISH_HOLD';
  else if(failed>0)verdict='PUBLISH_FAILED_HOLD';
  else verdict='PRE_PUSH_READY';
  const deploymentEligible=verdict==='PUBLISH_SUCCEEDED';
  return Object.freeze({
    schemaVersion:'PHASE8D1_PARTIAL_PUBLISH_STATE_1',sourceGitSha:value.sourceGitSha,releaseId:value.releaseId,verdict,deploymentEligible,
    automaticRetryAllowed:false,tagOverwriteAllowed:false,packageVersionDeletionAllowed:false,
    explicitRecoveryAuthorizationRequired:!deploymentEligible&&verdict!=='PRE_PUSH_READY',images,
  });
}

function stateToObservation(state){
  if(!exactKeys(state,['schemaVersion','sourceGitSha','releaseId','verdict','deploymentEligible','automaticRetryAllowed','tagOverwriteAllowed','packageVersionDeletionAllowed','explicitRecoveryAuthorizationRequired','images'])||state.schemaVersion!=='PHASE8D1_PARTIAL_PUBLISH_STATE_1')fail('INVALID_PARTIAL_PUBLISH_STATE');
  return {schemaVersion:'PHASE8D1_PARTIAL_PUBLISH_OBSERVATION_1',sourceGitSha:state.sourceGitSha,releaseId:state.releaseId,images:state.images};
}
function writeState(path,state){
  const temporary=`${path}.tmp-${process.pid}`;
  writeFileSync(temporary,`${JSON.stringify(state,null,2)}\n`,{mode:0o600});
  renameSync(temporary,path);
}
function readState(path){return evaluatePartialPublishObservation(stateToObservation(JSON.parse(readFileSync(path,'utf8'))));}
function record(path,name,outcome,digest){
  const current=readState(path),observation=stateToObservation(current);
  if(observation.images.some(image=>image.preflightStatus!=='ABSENT'))fail('EXPLICIT_RECOVERY_AUTHORIZATION_REQUIRED');
  if(!names.includes(name)||!['success','failure'].includes(outcome))fail('INVALID_PUSH_RECORD');
  observation.images=observation.images.map(image=>image.name!==name?image:{...image,pushStatus:outcome==='success'?'PUSHED':'FAILED',registryDigest:outcome==='success'?digest:null});
  const state=evaluatePartialPublishObservation(observation);writeState(path,state);return state;
}
function markVerified(path){
  const current=readState(path),observation=stateToObservation(current);
  if(!observation.images.every(image=>image.pushStatus==='PUSHED'))fail('BOTH_PUSHES_REQUIRED_BEFORE_VERIFICATION');
  observation.images=observation.images.map(image=>({...image,private:true,repositoryLinked:true,provenancePresent:true,spdxSbomPresent:true,digestRetestPassed:true}));
  const state=evaluatePartialPublishObservation(observation);writeState(path,state);return state;
}

if(import.meta.url===`file://${process.argv[1]}`){
  try{
    const command=process.argv[2];let state;
    if(command==='evaluate'){
      const bytes=readFileSync(0,{encoding:'utf8'});if(bytes.length>1_000_000)fail('PARTIAL_PUBLISH_INPUT_TOO_LARGE');
      state=evaluatePartialPublishObservation(JSON.parse(bytes));
    }else if(command==='record')state=record(process.argv[3],process.argv[4],process.argv[5],process.argv[6]);
    else if(command==='mark-verified')state=markVerified(process.argv[3]);
    else if(command==='require-success'){
      state=readState(process.argv[3]);if(state.verdict!=='PUBLISH_SUCCEEDED'||state.deploymentEligible!==true)fail(`PUBLISH_NOT_SUCCESSFUL:${state.verdict}`);
    }else fail('INVALID_PARTIAL_PUBLISH_COMMAND');
    process.stdout.write(`${JSON.stringify(state,null,2)}\n`);
  }catch(error){process.stderr.write(`${error instanceof SyntaxError?'INVALID_PARTIAL_PUBLISH_JSON':error instanceof Error?error.message:'PARTIAL_PUBLISH_FAILED'}\n`);process.exit(1);}
}
