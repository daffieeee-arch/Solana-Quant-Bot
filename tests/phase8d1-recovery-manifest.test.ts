import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { validateJsonSchema } from '../scripts/phase8d1/json-schema-subset.mjs';
import { evaluateRecoveryObservation } from '../scripts/phase8d1/recovery-state.mjs';

const schemaPath='deployment/phase8d1/recovery-manifest.schema.json';

describe('Phase 8D1-R recovery manifest schema',()=>{
  it('validates bounded success and HOLD manifests',()=>{
    expect(existsSync(schemaPath)).toBe(true);const schema=JSON.parse(readFileSync(schemaPath,'utf8')),contract=JSON.parse(readFileSync('deployment/phase8d1/existing-digest-recovery-contract.json','utf8'));
    const images=contract.images.map((image:any)=>({name:image.name,package:image.package,tag:image.tag,expectedDigest:image.digest,resolvedDigest:null,metadataStatus:403,packageExists:false,packageType:null,visibility:null,repositoryFullName:null,unauthenticatedPullDenied:true,authenticatedPullSucceeded:false,platform:null,sourceGitSha:null,provenancePresent:false,spdxSbomPresent:false,digestRetestPassed:false}));
    const base:any={schemaVersion:'PHASE8D1_RECOVERY_OBSERVATION_1',recoveryWorkflowSha:'a'.repeat(40),imageSourceSha:'9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180',imageSourceAncestor:true,originalPublishRunId:32641496527,originalArtifactsVerified:true,originalHoldPreserved:true,images,baseImageDriftVerdict:'HOLD',cleanTreeVerdict:'PASS',zeroMutationEvidence:true};
    const hold:any={...evaluateRecoveryObservation(base),rootCauseCategory:'PACKAGE_METADATA_API_EVIDENCE_NOT_PRODUCED_BEFORE_DIGEST_RETEST',originalArtifacts:contract.originalArtifacts,packageMetadata:[],runnerVerdict:null,cockpitVerdict:null,executionReasons:['RECOVERY_IMAGE_SET_INVALID'],zeroMutationDetails:{imageBuild:false,imagePush:false,tagCreate:false,tagOverwrite:false,packageVersionDelete:false,packageSettingsChange:false}};
    expect(validateJsonSchema(schema,hold)).toEqual([]);
    const invalid=structuredClone(hold);invalid.deploymentEligible=true;invalid.recoveryCompleted=true;invalid.verdict='PUBLISH_SUCCEEDED';invalid.images=[];expect(validateJsonSchema(schema,invalid)).toContain('$.images:minItems');
    expect(invalid.images).toHaveLength(0);expect(hold.reasons).toContain('PACKAGE_API_PERMISSION_HOLD:cockpit');
  });
});
