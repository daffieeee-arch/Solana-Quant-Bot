import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';

const path='deployment/phase8d1/existing-digest-recovery-contract.json';

describe('Phase 8D1-R fixed recovery contract',()=>{
  it('pins the original release, artifacts, retry budget, and zero-mutation boundary',()=>{
    expect(existsSync(path)).toBe(true);
    const c=JSON.parse(readFileSync(path,'utf8'));
    expect(c).toMatchObject({schemaVersion:'PHASE8D1_EXISTING_DIGEST_RECOVERY_CONTRACT_1',recoveryMode:'EXISTING_DIGEST_READ_ONLY_RECOVERY',imageSourceSha:'9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180',originalPublishRunId:32641496527,confirmation:'RECOVER_EXISTING_PHASE8D_IMAGES',packageMetadataRetry:{maxAttempts:6,maxDelaySeconds:15,maxTotalWaitSeconds:90},mutations:{imageBuild:false,imagePush:false,tagCreate:false,tagOverwrite:false,packageVersionDelete:false,packageSettingsChange:false}});
    expect(c.images).toEqual([
      {name:'cockpit',package:'phase8a-research-cockpit',tag:'ghcr.io/daffieeee-arch/phase8a-research-cockpit:9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180-dec80aec28fd-b6799c7bb168',digest:'sha256:6963e72814a3c26c6454f3de670cb92ec78e6dc3258fe7a0e2869075788e32fd',uid:61001,gid:61000},
      {name:'runner',package:'phase8a-bronze-runner',tag:'ghcr.io/daffieeee-arch/phase8a-bronze-runner:9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180-0e93202ac05c',digest:'sha256:76af7eac2bd1b04045ba570f8bec26033c503ded6d78b6e30d1eabfa590b429a',uid:61000,gid:61000},
    ]);
    expect(c.originalArtifacts).toHaveLength(5);expect(c.originalArtifacts.map((a:any)=>a.id)).toEqual([9493911850,9493915717,9493917239,9493919367,9493819797]);
    for(const a of c.originalArtifacts){expect(a.sha256).toMatch(/^[0-9a-f]{64}$/);expect(a.bytes).toBeGreaterThan(0);}
  });
});
