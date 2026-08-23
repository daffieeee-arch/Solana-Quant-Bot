import { describe, expect, it } from 'vitest';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const sha='a'.repeat(40),digest=`sha256:${'b'.repeat(64)}`;
const image=(name:string,pushStatus='NOT_ATTEMPTED',overrides:Record<string,unknown>={})=>({
  name,
  package:name==='cockpit'?'phase8a-research-cockpit':'phase8a-bronze-runner',
  tag:`ghcr.io/example/${name}:${sha}-immutable`,
  preflightStatus:'ABSENT',
  pushStatus,
  registryDigest:pushStatus==='PUSHED'?digest:null,
  private:false,repositoryLinked:false,provenancePresent:false,spdxSbomPresent:false,digestRetestPassed:false,
  ...overrides,
});
function evaluate(images:any[]){
  const input={schemaVersion:'PHASE8D1_PARTIAL_PUBLISH_OBSERVATION_1',sourceGitSha:sha,releaseId:sha,images};
  const result=spawnSync(process.execPath,['scripts/phase8d1/partial-publish-state.mjs','evaluate'],{input:JSON.stringify(input),encoding:'utf8'});
  expect(result.status,result.stderr).toBe(0);
  return JSON.parse(result.stdout);
}

describe('Phase 8D1 partial publish state',()=>{
  it('holds when cockpit push passes and runner push fails',()=>{
    const state=evaluate([image('cockpit','PUSHED'),image('runner','FAILED')]);
    expect(state.verdict).toBe('PARTIAL_PUBLISH_HOLD');expect(state.deploymentEligible).toBe(false);expect(state.explicitRecoveryAuthorizationRequired).toBe(true);
  });
  it('holds when runner push passes and cockpit push fails',()=>{
    const state=evaluate([image('cockpit','FAILED'),image('runner','PUSHED')]);
    expect(state.verdict).toBe('PARTIAL_PUBLISH_HOLD');expect(state.deploymentEligible).toBe(false);
  });
  it('reports a one-tag collision as partial-publish recovery HOLD',()=>{
    const state=evaluate([image('cockpit','NOT_ATTEMPTED',{preflightStatus:'PRESENT',registryDigest:digest}),image('runner')]);
    expect(state.verdict).toBe('PARTIAL_PUBLISH_COLLISION_HOLD');expect(state.explicitRecoveryAuthorizationRequired).toBe(true);expect(state.deploymentEligible).toBe(false);
  });
  it('never authorizes automatic retry, overwrite or package-version deletion',()=>{
    const state=evaluate([image('cockpit','PUSHED'),image('runner','FAILED')]);
    expect(state.automaticRetryAllowed).toBe(false);expect(state.tagOverwriteAllowed).toBe(false);expect(state.packageVersionDeletionAllowed).toBe(false);
  });
  it('requires both pushes plus private linkage attestations and digest retests for success',()=>{
    const pushed=[image('cockpit','PUSHED'),image('runner','PUSHED')];
    expect(evaluate(pushed).verdict).toBe('BOTH_PUSHED_RETEST_REQUIRED_HOLD');
    const verified=pushed.map(row=>({...row,private:true,repositoryLinked:true,provenancePresent:true,spdxSbomPresent:true,digestRetestPassed:true}));
    const state=evaluate(verified);expect(state.verdict).toBe('PUBLISH_SUCCEEDED');expect(state.deploymentEligible).toBe(true);expect(state.explicitRecoveryAuthorizationRequired).toBe(false);
    for(const field of ['private','repositoryLinked','provenancePresent','spdxSbomPresent','digestRetestPassed']){
      const broken=structuredClone(verified);broken[1][field]=false;expect(evaluate(broken).deploymentEligible,field).toBe(false);
    }
  });
  it('durably records each push and refuses success until the digest retest is marked',()=>{
    const root=mkdtempSync(join(tmpdir(),'phase8d1-partial-state-')),statePath=join(root,'state.json');
    try{
      writeFileSync(statePath,`${JSON.stringify(evaluate([image('cockpit'),image('runner')]),null,2)}\n`);
      let result=spawnSync(process.execPath,['scripts/phase8d1/partial-publish-state.mjs','record',statePath,'cockpit','success',digest],{encoding:'utf8'});expect(result.status,result.stderr).toBe(0);
      expect(JSON.parse(readFileSync(statePath,'utf8')).verdict).toBe('PARTIAL_PUBLISH_HOLD');
      result=spawnSync(process.execPath,['scripts/phase8d1/partial-publish-state.mjs','record',statePath,'runner','success',digest],{encoding:'utf8'});expect(result.status,result.stderr).toBe(0);
      expect(JSON.parse(readFileSync(statePath,'utf8')).verdict).toBe('BOTH_PUSHED_RETEST_REQUIRED_HOLD');
      result=spawnSync(process.execPath,['scripts/phase8d1/partial-publish-state.mjs','require-success',statePath],{encoding:'utf8'});expect(result.status).not.toBe(0);
      result=spawnSync(process.execPath,['scripts/phase8d1/partial-publish-state.mjs','mark-verified',statePath],{encoding:'utf8'});expect(result.status,result.stderr).toBe(0);
      expect(JSON.parse(readFileSync(statePath,'utf8')).verdict).toBe('PUBLISH_SUCCEEDED');
      result=spawnSync(process.execPath,['scripts/phase8d1/partial-publish-state.mjs','require-success',statePath],{encoding:'utf8'});expect(result.status,result.stderr).toBe(0);
    }finally{rmSync(root,{recursive:true,force:true});}
  });
});
