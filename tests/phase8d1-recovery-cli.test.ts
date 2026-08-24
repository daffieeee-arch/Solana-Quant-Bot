import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { evaluateRecoveryObservation } from '../scripts/phase8d1/recovery-state.mjs';

const contract=JSON.parse(readFileSync('deployment/phase8d1/existing-digest-recovery-contract.json','utf8'));
const source=contract.imageSourceSha;
const validManifest=()=>{
  const images=contract.images.map((image:any)=>({name:image.name,package:image.package,tag:image.tag,expectedDigest:image.digest,resolvedDigest:image.digest,metadataStatus:200,packageExists:true,packageType:'container',visibility:'private',repositoryFullName:contract.repository,unauthenticatedPullDenied:true,authenticatedPullSucceeded:true,platform:'linux/amd64',sourceGitSha:source,provenancePresent:true,spdxSbomPresent:true,digestRetestPassed:true,imageConfig:{user:image.name==='cockpit'?'61001:61000':'61000:61000'}}));
  const evaluated=evaluateRecoveryObservation({schemaVersion:'PHASE8D1_RECOVERY_OBSERVATION_1',recoveryWorkflowSha:'a'.repeat(40),imageSourceSha:source,imageSourceAncestor:true,originalPublishRunId:contract.originalPublishRunId,originalArtifactsVerified:true,originalHoldPreserved:true,images,baseImageDriftVerdict:'PASS',cleanTreeVerdict:'PASS',zeroMutationEvidence:true});
  return {...evaluated,rootCauseCategory:contract.rootCauseCategory,originalArtifacts:contract.originalArtifacts,packageMetadata:[],runnerVerdict:'PASS',cockpitVerdict:{unavailable:'PASS',provider:'PASS'},executionReasons:[],zeroMutationDetails:{imageBuild:false,imagePush:false,tagCreate:false,tagOverwrite:false,packageVersionDelete:false,packageSettingsChange:false}};
};

describe('Phase 8D1-R recovery manifest CLI gate',()=>{
  it('rejects forged or tampered success and accepts only a complete schema/evaluator/sidecar-bound manifest',()=>{
    const root=mkdtempSync(join(tmpdir(),'phase8d1-recovery-cli-')),path=join(root,'recovery-manifest.json'),sidecar=join(root,'recovery-manifest.sha256');
    const write=(value:any,correct=true)=>{const bytes=`${JSON.stringify(value,null,2)}\n`;writeFileSync(path,bytes);writeFileSync(sidecar,`${correct?createHash('sha256').update(bytes).digest('hex'):'0'.repeat(64)}  recovery-manifest.json\n`);};
    const invoke=()=>spawnSync(process.execPath,['scripts/phase8d1/recovery-state.mjs','require-success',path],{encoding:'utf8'});
    try{
      write({verdict:'PUBLISH_SUCCEEDED',deploymentEligible:true,recoveryCompleted:true});let result=invoke();expect(result.status).not.toBe(0);
      write({...validManifest(),images:[]});result=invoke();expect(result.status).not.toBe(0);
      write(validManifest(),false);result=invoke();expect(result.status).not.toBe(0);expect(result.stderr).toContain('RECOVERY_MANIFEST_DIGEST_MISMATCH');
      write(validManifest());result=invoke();expect(result.status,result.stderr).toBe(0);expect(result.stdout).toContain('PHASE8D1_RECOVERY_SUCCESS');
    }finally{rmSync(root,{recursive:true,force:true});}
  });
});
