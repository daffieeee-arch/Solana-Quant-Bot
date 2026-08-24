import { describe, expect, it } from 'vitest';
import { existsSync } from 'node:fs';
import { evaluatePackageMetadataAttempts, evaluateRecoveryObservation } from '../scripts/phase8d1/recovery-state.mjs';

const imageSourceSha='9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180';
const recoveryWorkflowSha='a'.repeat(40);
const images={
  cockpit:{name:'cockpit',package:'phase8a-research-cockpit',tag:`ghcr.io/daffieeee-arch/phase8a-research-cockpit:${imageSourceSha}-dec80aec28fd-b6799c7bb168`,expectedDigest:'sha256:6963e72814a3c26c6454f3de670cb92ec78e6dc3258fe7a0e2869075788e32fd',resolvedDigest:'sha256:6963e72814a3c26c6454f3de670cb92ec78e6dc3258fe7a0e2869075788e32fd',metadataStatus:200,packageExists:true,packageType:'container',visibility:'private',repositoryFullName:'daffieeee-arch/solana-paper-scanner',unauthenticatedPullDenied:true,authenticatedPullSucceeded:true,platform:'linux/amd64',sourceGitSha:imageSourceSha,provenancePresent:true,spdxSbomPresent:true,digestRetestPassed:true,imageConfig:{user:'61001:61000'}},
  runner:{name:'runner',package:'phase8a-bronze-runner',tag:`ghcr.io/daffieeee-arch/phase8a-bronze-runner:${imageSourceSha}-0e93202ac05c`,expectedDigest:'sha256:76af7eac2bd1b04045ba570f8bec26033c503ded6d78b6e30d1eabfa590b429a',resolvedDigest:'sha256:76af7eac2bd1b04045ba570f8bec26033c503ded6d78b6e30d1eabfa590b429a',metadataStatus:200,packageExists:true,packageType:'container',visibility:'private',repositoryFullName:'daffieeee-arch/solana-paper-scanner',unauthenticatedPullDenied:true,authenticatedPullSucceeded:true,platform:'linux/amd64',sourceGitSha:imageSourceSha,provenancePresent:true,spdxSbomPresent:true,digestRetestPassed:true,imageConfig:{user:'61000:61000'}},
};
const observation=(overrides:any={})=>({schemaVersion:'PHASE8D1_RECOVERY_OBSERVATION_1',recoveryWorkflowSha,imageSourceSha,imageSourceAncestor:true,originalPublishRunId:32641496527,originalArtifactsVerified:true,originalHoldPreserved:true,images:[structuredClone(images.cockpit),structuredClone(images.runner)],baseImageDriftVerdict:'PASS',cleanTreeVerdict:'PASS',zeroMutationEvidence:true,...overrides});

describe('Phase 8D1-R recovery state evaluator',()=>{
  it('ships a dedicated existing-digest recovery evaluator',()=>{
    expect(existsSync('scripts/phase8d1/recovery-state.mjs')).toBe(true);
  });
  it('exports the recovery observation evaluator',()=>{
    expect(typeof evaluateRecoveryObservation).toBe('function');
  });
  it('exports bounded package metadata retry evaluation',()=>{
    expect(typeof evaluatePackageMetadataAttempts).toBe('function');
  });
  it('stops package metadata immediately on 401 or 403',()=>{
    for(const status of [401,403])expect(evaluatePackageMetadataAttempts([{status}])).toEqual({verdict:'PACKAGE_API_PERMISSION_HOLD',terminal:true,attemptsUsed:1,nextDelaySeconds:0});
  });
  it('accepts eventual consistency when bounded 404s are followed by success',()=>{
    const result=evaluatePackageMetadataAttempts([{status:404},{status:404},{status:200,metadata:{name:'phase8a-research-cockpit'}}]);
    expect(result).toEqual({verdict:'PACKAGE_METADATA_READY',terminal:true,attemptsUsed:3,nextDelaySeconds:0});
  });
  it('holds after six bounded 404 attempts',()=>{
    const result=evaluatePackageMetadataAttempts(Array.from({length:6},()=>({status:404})));
    expect(result).toEqual({verdict:'PACKAGE_METADATA_NOT_VISIBLE_HOLD',terminal:true,attemptsUsed:6,nextDelaySeconds:0});
  });
  it('requests at most a 15-second retry before the six-attempt limit',()=>{
    const result=evaluatePackageMetadataAttempts([{status:404},{status:404}]);
    expect(result).toEqual({verdict:'PACKAGE_METADATA_RETRY',terminal:false,attemptsUsed:2,nextDelaySeconds:15});
  });
  it('succeeds only for complete double-digest recovery evidence',()=>{
    const result=evaluateRecoveryObservation(observation());
    expect(result).toMatchObject({recoveryMode:'EXISTING_DIGEST_READ_ONLY_RECOVERY',recoveryWorkflowSha,imageSourceSha,originalPublishRunId:32641496527,verdict:'PUBLISH_SUCCEEDED',deploymentEligible:true,recoveryCompleted:true,reasons:[]});
    expect(result.supersedesOriginalHoldWithoutDeletion).toBe(true);
  });
  it('holds a wrong image source SHA',()=>{
    const result=evaluateRecoveryObservation(observation({imageSourceSha:'b'.repeat(40)}));
    expect(result).toMatchObject({verdict:'RECOVERY_HOLD',deploymentEligible:false,recoveryCompleted:false});expect(result.reasons).toContain('IMAGE_SOURCE_SHA_MISMATCH');
  });
  it('holds when the image source is not an ancestor of the recovery workflow',()=>{
    const result=evaluateRecoveryObservation(observation({imageSourceAncestor:false}));
    expect(result.reasons).toContain('IMAGE_SOURCE_NOT_ANCESTOR');expect(result.deploymentEligible).toBe(false);
  });
  it('holds a wrong immutable tag or digest',()=>{
    const wrongTag=observation();wrongTag.images[0].tag+='-forged';expect(evaluateRecoveryObservation(wrongTag).reasons).toContain('TAG_IDENTITY_MISMATCH:cockpit');
    const wrongDigest=observation();wrongDigest.images[1].resolvedDigest=`sha256:${'0'.repeat(64)}`;expect(evaluateRecoveryObservation(wrongDigest).reasons).toContain('TAG_DIGEST_DRIFT:runner');
  });
  it('maps package API and metadata visibility failures to explicit HOLD reasons',()=>{
    const permission=observation();permission.images[0].metadataStatus=403;expect(evaluateRecoveryObservation(permission).reasons).toContain('PACKAGE_API_PERMISSION_HOLD:cockpit');
    const missing=observation();missing.images[1].metadataStatus=404;missing.images[1].packageExists=false;expect(evaluateRecoveryObservation(missing).reasons).toContain('PACKAGE_METADATA_NOT_VISIBLE_HOLD:runner');
  });
  it('holds public or unlinked packages',()=>{
    const publicPackage=observation();publicPackage.images[0].visibility='public';expect(evaluateRecoveryObservation(publicPackage).reasons).toContain('PACKAGE_VISIBILITY_HOLD:cockpit');
    const unlinked=observation();unlinked.images[1].repositoryFullName=null;expect(evaluateRecoveryObservation(unlinked).reasons).toContain('PACKAGE_REPOSITORY_LINK_HOLD:runner');
  });
  it('requires unauthenticated denial and authenticated pull success',()=>{
    const publicPull=observation();publicPull.images[0].unauthenticatedPullDenied=false;expect(evaluateRecoveryObservation(publicPull).reasons).toContain('UNAUTHENTICATED_PULL_SUCCEEDED_HOLD:cockpit');
    const denied=observation();denied.images[1].authenticatedPullSucceeded=false;expect(evaluateRecoveryObservation(denied).reasons).toContain('AUTHENTICATED_PULL_FAILED_HOLD:runner');
  });
  it('requires provenance, SPDX, exact source/platform, runtime identity, and both digest retests',()=>{
    const provenance=observation();provenance.images[0].provenancePresent=false;expect(evaluateRecoveryObservation(provenance).reasons).toContain('PROVENANCE_HOLD:cockpit');
    const sbom=observation();sbom.images[1].spdxSbomPresent=false;expect(evaluateRecoveryObservation(sbom).reasons).toContain('SPDX_SBOM_HOLD:runner');
    const platform=observation();platform.images[0].platform='linux/arm64';expect(evaluateRecoveryObservation(platform).reasons).toContain('PLATFORM_HOLD:cockpit');
    const source=observation();source.images[1].sourceGitSha='b'.repeat(40);expect(evaluateRecoveryObservation(source).reasons).toContain('IMAGE_SOURCE_LABEL_HOLD:runner');
    const identity=observation();identity.images[0].imageConfig.user='0:0';expect(evaluateRecoveryObservation(identity).reasons).toContain('RUNTIME_IDENTITY_HOLD:cockpit');
    const retest=observation();retest.images[0].digestRetestPassed=false;expect(evaluateRecoveryObservation(retest).reasons).toContain('DIGEST_RETEST_HOLD:cockpit');
  });
  it('can never succeed when only one package is valid',()=>{
    const one=observation();one.images.pop();const result=evaluateRecoveryObservation(one);expect(result.verdict).toBe('RECOVERY_HOLD');expect(result.reasons).toContain('RECOVERY_IMAGE_SET_INVALID');
  });
  it('preserves the old HOLD evidence and requires base, clean-tree and zero-mutation proof',()=>{
    for(const [field,reason] of [['originalArtifactsVerified','ORIGINAL_ARTIFACT_CHAIN_HOLD'],['originalHoldPreserved','ORIGINAL_HOLD_PRESERVATION_HOLD'],['zeroMutationEvidence','ZERO_MUTATION_HOLD']] as const){const input=observation({[field]:false});expect(evaluateRecoveryObservation(input).reasons).toContain(reason);}
    expect(evaluateRecoveryObservation(observation({baseImageDriftVerdict:'HOLD'})).reasons).toContain('BASE_IMAGE_DRIFT_HOLD');
    expect(evaluateRecoveryObservation(observation({cleanTreeVerdict:'HOLD'})).reasons).toContain('CLEAN_TREE_HOLD');
  });
});
