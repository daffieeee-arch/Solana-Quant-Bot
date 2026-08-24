import { describe, expect, it } from 'vitest';
import { loadPhase8D1Inputs, validatePhase8D1Inputs } from '../scripts/phase8d1/assert-phase8d1-supply-chain.mjs';

const candidate=()=>loadPhase8D1Inputs(process.cwd());

describe('Phase 8D1-R production recovery policy',()=>{
  it('accepts the exact read-only recovery contract',()=>{
    const input:any=candidate();expect(input.recoveryWorkflow).toBeDefined();expect(input.recoveryContract).toMatchObject({schemaVersion:'PHASE8D1_EXISTING_DIGEST_RECOVERY_CONTRACT_1',recoveryMode:'EXISTING_DIGEST_READ_ONLY_RECOVERY'});expect(input.recoverySchema).toBeDefined();expect(validatePhase8D1Inputs(input)).toEqual([]);
  });
  it('rejects write permissions and non-dispatch triggers',()=>{
    const write:any=structuredClone(candidate());write.recoveryWorkflow.permissions.packages='write';expect(validatePhase8D1Inputs(write).join('\n')).toMatch(/recovery.*permission/i);
    const trigger:any=structuredClone(candidate());trigger.recoveryWorkflow.on.push={branches:['main']};expect(validatePhase8D1Inputs(trigger).join('\n')).toMatch(/recovery.*trigger/i);
  });
  it('rejects build, push, delete, overwrite, settings mutation, and source conflation',()=>{
    for(const marker of ['docker buildx build --push .','gh api --method DELETE /package','packageVersionDelete:true','tagOverwrite:true','inputs.image_source_sha == github.sha']){
      const input:any=structuredClone(candidate());input.recoveryText+=`\nrun: ${marker}`;expect(validatePhase8D1Inputs(input).join('\n'),marker).toMatch(/recovery.*(?:mutation|source|workflow)/i);
    }
  });
  it('rejects weakened metadata retry and missing old artifacts',()=>{
    const retry:any=structuredClone(candidate());retry.recoveryContract.packageMetadataRetry.maxAttempts=7;expect(validatePhase8D1Inputs(retry).join('\n')).toMatch(/recovery.*contract/i);
    const artifact:any=structuredClone(candidate());artifact.recoveryContract.originalArtifacts.pop();expect(validatePhase8D1Inputs(artifact).join('\n')).toMatch(/recovery.*contract/i);
  });
});
