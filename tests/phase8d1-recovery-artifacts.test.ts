import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { validateArtifactEntries, validateOriginalArtifactMetadata, validateOriginalStateChain } from '../scripts/phase8d1/recovery-artifacts.mjs';

const contract=JSON.parse(readFileSync('deployment/phase8d1/existing-digest-recovery-contract.json','utf8'));
const source=contract.imageSourceSha,cockpit=contract.images[0],runner=contract.images[1];
const metadata=()=>contract.originalArtifacts.map((a:any)=>({id:a.id,name:a.name,size_in_bytes:a.bytes,digest:`sha256:${a.sha256}`,expired:false,workflow_run:{id:contract.originalPublishRunId,head_sha:source}}));
const image=(item:any,preflightStatus:string,pushStatus:string,digest:any)=>({name:item.name,package:item.package,tag:item.tag,preflightStatus,pushStatus,registryDigest:digest});
const states=()=>({
  preflight:{sourceGitSha:source,releaseId:source,verdict:'PRE_PUSH_READY',images:[image(cockpit,'ABSENT','NOT_ATTEMPTED',null),image(runner,'ABSENT','NOT_ATTEMPTED',null)]},
  cockpit:{sourceGitSha:source,releaseId:source,verdict:'PARTIAL_PUBLISH_HOLD',images:[image(cockpit,'ABSENT','PUSHED',cockpit.digest),image(runner,'ABSENT','NOT_ATTEMPTED',null)]},
  runner:{sourceGitSha:source,releaseId:source,verdict:'BOTH_PUSHED_RETEST_REQUIRED_HOLD',images:[image(cockpit,'ABSENT','PUSHED',cockpit.digest),image(runner,'ABSENT','PUSHED',runner.digest)]},
  final:{sourceGitSha:source,releaseId:source,verdict:'BOTH_PUSHED_RETEST_REQUIRED_HOLD',images:[image(cockpit,'ABSENT','PUSHED',cockpit.digest),image(runner,'ABSENT','PUSHED',runner.digest)]},
  verify:{sourceGitSha:source,status:'VERIFY_ONLY_SUCCEEDED'},
});

describe('Phase 8D1-R original artifact chain',()=>{
  it('ships a dedicated immutable artifact-chain verifier',()=>{
    expect(existsSync('scripts/phase8d1/recovery-artifacts.mjs')).toBe(true);
  });
  it('exports metadata, payload, and state-chain verification',()=>{
    for(const fn of [validateOriginalArtifactMetadata,validateArtifactEntries,validateOriginalStateChain])expect(typeof fn).toBe('function');
  });
  it('accepts only the exact five original artifact metadata records',()=>{
    expect(validateOriginalArtifactMetadata(metadata(),contract)).toEqual([]);
    const wrongId=metadata();wrongId[0].id++;expect(validateOriginalArtifactMetadata(wrongId,contract)).toContain('ARTIFACT_ID_MISMATCH:preflight');
    const wrongHash=metadata();wrongHash[3].digest=`sha256:${'0'.repeat(64)}`;expect(validateOriginalArtifactMetadata(wrongHash,contract)).toContain('ARTIFACT_DIGEST_MISMATCH:final-hold');
  });
  it('rejects traversal, image payloads, credentials, and unbounded artifact entries',()=>{
    expect(validateArtifactEntries([{path:'publish-state.json',bytes:Buffer.from('{}')}])).toEqual([]);
    expect(validateArtifactEntries([{path:'../escape.json',bytes:Buffer.from('{}')}])).toContain('ARTIFACT_PATH_UNSAFE');
    expect(validateArtifactEntries([{path:'image.tar',bytes:Buffer.from('x')}])).toContain('ARTIFACT_IMAGE_PAYLOAD_FORBIDDEN');
    const token=['github','pat', 'A'.repeat(30)].join('_');expect(validateArtifactEntries([{path:'state.txt',bytes:Buffer.from(token)}])).toContain('ARTIFACT_CREDENTIAL_CONTENT');
    expect(validateArtifactEntries([{path:'huge.json',bytes:Buffer.alloc(1_048_577)}])).toContain('ARTIFACT_BYTES_EXCEEDED');
  });
  it('accepts only the immutable PRE_PUSH to final HOLD chain',()=>{
    expect(validateOriginalStateChain(states(),contract)).toEqual([]);
    const wrongSource=states();wrongSource.final.sourceGitSha='b'.repeat(40);expect(validateOriginalStateChain(wrongSource,contract)).toContain('ARTIFACT_SOURCE_MISMATCH:final');
    const wrongHold=states();wrongHold.final.verdict='PUBLISH_SUCCEEDED';expect(validateOriginalStateChain(wrongHold,contract)).toContain('ARTIFACT_VERDICT_MISMATCH:final');
    const wrongDigest=states();wrongDigest.runner.images[1].registryDigest=`sha256:${'0'.repeat(64)}`;expect(validateOriginalStateChain(wrongDigest,contract)).toContain('ARTIFACT_DIGEST_MISMATCH:runner:runner');
  });
});
