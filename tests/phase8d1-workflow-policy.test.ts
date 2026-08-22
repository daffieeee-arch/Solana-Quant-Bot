import { describe, expect, it } from 'vitest';
import { loadPhase8D1Inputs, validatePhase8D1Inputs } from '../scripts/phase8d1/assert-phase8d1-supply-chain.mjs';
import { execFileSync } from 'node:child_process';

function candidate() { return loadPhase8D1Inputs(process.cwd()); }

function rejected(name: string, mutate: (input: any) => void) {
  it(`rejects ${name}`, () => {
    const input = structuredClone(candidate());
    mutate(input);
    expect(validatePhase8D1Inputs(input).join('\n')).toMatch(/invalid|forbidden|required|missing|untrusted|digest|permission|publish|private|manifest|socket|truenas/i);
  });
}

describe('Phase 8D1 remote image supply-chain policy', () => {
  it('accepts only the closed verify/publish and release contracts', () => {
    expect(validatePhase8D1Inputs(candidate())).toEqual([]);
  });

  it('installs locked Node 22 dependencies before executing the production policy', () => {
    const steps=candidate().verifyWorkflow.jobs['verify-images-no-push'].steps;
    const setup=steps.findIndex((step:any)=>String(step.uses??'').startsWith('actions/setup-node@')&&step.with?.['node-version']==='22.23.2');
    const install=steps.findIndex((step:any)=>step.run==='npm ci');
    const policy=steps.findIndex((step:any)=>step.run==='node scripts/phase8d1/assert-phase8d1-supply-chain.mjs');
    expect(setup).toBeGreaterThan(-1);expect(install).toBeGreaterThan(setup);expect(policy).toBeGreaterThan(install);
  });

  it('disables automatic Buildx record artifacts and retains one bounded seven-day evidence upload', () => {
    const job=candidate().verifyWorkflow.jobs['verify-images-no-push'];
    expect(job.env?.DOCKER_BUILD_RECORD_UPLOAD).toBe('false');
    const uploads=job.steps.filter((step:any)=>String(step.uses??'').startsWith('actions/upload-artifact@'));
    expect(uploads).toHaveLength(1);expect(uploads[0].with['retention-days']).toBe(7);expect(uploads[0].with.path).toBe('phase8d1-evidence/*.json\nphase8d1-evidence/*.sha256\n');expect(uploads[0].with.path).not.toMatch(/\.dockerbuild|\.tar|layer/i);
  });

  it('keeps every directly invoked Phase 8D1 shell script executable in Git', () => {
    for(const path of ['prebuild-hashes.sh','publish-gates.sh','verify-images.sh','verify-published-images.sh']){
      const mode=execFileSync('git',['ls-files','--stage','--',`scripts/phase8d1/${path}`],{encoding:'utf8'}).trim().split(/\s+/)[0];
      expect(mode).toBe('100755');
    }
  });

  it('requires real GITHUB_TOKEN API authentication, Docker seccomp EPERM, and env/rootfs content secret scans', () => {
    const input = candidate();
    expect(input.publishText).not.toContain('Authorization: Bearer ***');
    expect(input.publishScript).not.toContain('Authorization: Bearer ***');
    expect(input.publishScript).toContain('gh api');
    expect(input.verifyScript).toContain('seccomp=');
    expect(input.verifyScript).toContain('error.code==="EPERM"');
    expect(input.verifyScript).toContain('Config.Env');
    expect(input.verifyScript).toContain('credentialContentFindings');
  });

  rejected('pull_request_target', x => { x.verifyWorkflow.on.pull_request_target = {}; });
  rejected('PR packages write', x => { x.verifyWorkflow.permissions.packages = 'write'; });
  rejected('PR registry login', x => { x.verifyText += '\nuses: docker/login-action@184bdaa0721073962dff0199f1fb9940f07167d1'; });
  rejected('PR image push', x => { x.verifyText = x.verifyText.replace('push: false', 'push: true'); });
  rejected('PR Buildx record artifact upload', x => { x.verifyWorkflow.jobs['verify-images-no-push'].env ??={};x.verifyWorkflow.jobs['verify-images-no-push'].env.DOCKER_BUILD_RECORD_UPLOAD='true'; });
  rejected('PR secret use', x => { x.verifyText += '\nrun: echo ${{ secrets.GITHUB_TOKEN }}'; });
  rejected('untrusted action ref', x => { x.verifyText = x.verifyText.replace(/actions\/checkout@[0-9a-f]{40}/, 'actions/checkout@v7'); });
  rejected('uncommented action release', x => { x.verifyText = x.verifyText.replace(/ # v7\.0\.1/, ''); });
  rejected('latest image reference', x => { x.contract.baseImages.nodeBuilder.versionTag = 'latest'; });
  rejected('missing linux amd64', x => { x.contract.platform = 'linux/arm64'; });
  rejected('missing base digest resolution', x => { x.verifyScript = x.verifyScript.replaceAll('@sha256:', '@shaXXX:'); });
  rejected('reviewed base lock drift', x => { x.baseLock.images[0].linuxAmd64Digest = `sha256:${'0'.repeat(64)}`; x.baseLockText = JSON.stringify(x.baseLock); });
  rejected('public GnuTLS KAT allowlist drift', x => { x.publicKeyAllowlist ??={entries:[{}]};x.publicKeyAllowlist.entries[0].path='usr/lib/forged.so'; });
  rejected('more than 3 GiB pull budget', x => { x.contract.maxCompressedBaseImageBytes = 4 * 1024 ** 3; });
  rejected('Docker socket mount', x => { x.verifyText += '\n-v /var/run/docker.sock:/var/run/docker.sock'; });
  rejected('TrueNAS operation', x => { x.verifyText += '\ntruenas app.update'; });
  rejected('missing networkless runner', x => { x.verifyScript = x.verifyScript.replace('docker run --name "$name" --platform linux/amd64 --network none', 'docker run --name "$name" --platform linux/amd64 --network bridge'); });
  rejected('missing double runner replay', x => { x.contract.runnerTests.independentRuns = 1; });
  rejected('missing 20 file equality', x => { x.contract.runnerTests.retainedFiles = 19; });
  rejected('missing unavailable cockpit test', x => { x.contract.cockpitTests.unavailable = false; });
  rejected('cockpit network route instead of network none', x => { x.contract.cockpitTests.networkIsolation='BRIDGE_WITH_SECCOMP'; });
  rejected('missing provider cockpit test', x => { x.contract.cockpitTests.syntheticProvider = false; });
  rejected('publish trigger other than dispatch', x => { x.publishWorkflow.on.push = { branches: ['main'] }; });
  rejected('wrong confirmation', x => { x.contract.publish.confirmation = 'YES'; });
  rejected('publish id token', x => { x.publishWorkflow.jobs.publish.permissions['id-token'] = 'write'; });
  rejected('publish attestations', x => { x.publishWorkflow.jobs.publish.permissions.attestations = 'write'; });
  rejected('actions attest', x => { x.publishText += '\nuses: actions/attest@0123456789012345678901234567890123456789'; });
  rejected('PAT use', x => { x.publishText += '\n${{ secrets.PAT }}'; });
  rejected('publish without main gate', x => { x.publishScript = x.publishScript.replace('refs/heads/main', 'refs/heads/dev'); });
  rejected('publish without live-main equality', x => { x.publishScript = x.publishScript.replaceAll('LIVE_MAIN_SHA', 'IGNORED_LIVE_MAIN'); });
  rejected('publish from merge ref', x => { x.publishScript = x.publishScript.replaceAll('reject_merge_ref', 'allow_merge_ref'); });
  rejected('tag collision allowed', x => { x.publishScript = x.publishScript.replace('TAG_COLLISION_REJECTED', 'TAG_COLLISION_IGNORED'); });
  rejected('tag overwrite allowed', x => { x.contract.publish.overwriteExistingTag = true; });
  rejected('deployment by tag', x => { x.contract.publish.deploymentIdentity = 'TAG'; });
  rejected('missing provenance max', x => { x.contract.publish.provenance = false; });
  rejected('missing SPDX SBOM', x => { x.contract.publish.sbom = false; });
  rejected('public package', x => { x.contract.publish.packageVisibility = 'public'; });
  rejected('secret build arg', x => { x.contract.forbiddenBuildArgs.push('SAFE_VALUE'); x.contract.allowedBuildArgs.push('API_TOKEN'); });
  rejected('publish success without digest retest', x => { x.contract.publish.finalDigestRetestRequired = false; });
  rejected('release manifest without provenance', x => { delete x.releaseSchema.properties.images.items.properties.ociProvenancePresent; });
  rejected('release manifest without SBOM', x => { delete x.releaseSchema.properties.images.items.properties.spdxSbomPresent; });
  rejected('release manifest without no-deployment', x => { delete x.releaseSchema.properties.nonActions; });
});
