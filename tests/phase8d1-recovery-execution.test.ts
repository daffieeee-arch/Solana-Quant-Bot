import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

describe('Phase 8D1-R read-only recovery execution',()=>{
  it('ships an executable shell entrypoint and bounded Node orchestrator',()=>{
    const shell='scripts/phase8d1/recover-existing-digests.sh',node='scripts/phase8d1/recover-existing-digests.mjs';expect(existsSync(shell)).toBe(true);expect(existsSync(node)).toBe(true);
    expect(execFileSync('git',['ls-files','--stage','--',shell],{encoding:'utf8'}).trim().split(/\s+/)[0]).toBe('100755');
    const text=`${readFileSync(shell,'utf8')}\n${readFileSync(node,'utf8')}`;
    expect(text).toContain('existing-digest-recovery-contract.json');expect(text).toContain('recovery-manifest.json');expect(text).toContain('originalArtifacts');expect(text).toContain('evaluateRecoveryObservation');expect(text).toContain('FINAL_TAG_DIGEST_DRIFT');expect(text).toContain('inspect-artifact-zip.py');
    const retest=text.indexOf("scripts/phase8d1/verify-images.sh");for(const marker of ['PACKAGE_TYPE_HOLD','PACKAGE_VISIBILITY_HOLD','PACKAGE_REPOSITORY_LINK_HOLD']){const gate=text.indexOf(marker);expect(gate,marker).toBeGreaterThan(-1);expect(gate,marker).toBeLessThan(retest);}
    expect(text).not.toMatch(/docker\s+(?:build|push|tag)|build-push-action|gh\s+api\s+--method\s+(?:DELETE|PATCH|POST|PUT)|packageVersionDelete\s*:\s*true|packageSettingsChange\s*:\s*true|tagOverwrite\s*:\s*true/i);
  });
});
