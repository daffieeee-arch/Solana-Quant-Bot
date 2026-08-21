import { describe, expect, it } from 'vitest';
import { validatePhase8D1ReleaseManifest } from '../scripts/phase8d1/validate-release-manifest.mjs';
import { readFileSync } from 'node:fs';

const sha = 'a'.repeat(64);
const base = (name: string) => ({ name, registry: 'docker.io', repository: `library/base-${name.toLowerCase()}`, versionTag: '1.0', manifestListDigest: `sha256:${sha}`, linuxAmd64Digest: `sha256:${sha}`, digestRef: `docker.io/library/base-${name.toLowerCase()}@sha256:${sha}`, os: 'linux', architecture: 'amd64', softwareVersion: 'exact', compressedSizeBytes: 1, observedAt: '2026-08-21T00:00:00.000Z' });
const valid = () => ({
  schemaVersion: 'PHASE8D1_RELEASE_MANIFEST_1', status: 'VERIFY_ONLY_SUCCEEDED', sourceGitSha: 'b'.repeat(40), workflowRunId: '1', workflowCommitSha: 'b'.repeat(40), builderRunnerImage: 'ubuntu-24.04', platform: 'linux/amd64',
  runtimeIdentities: { runnerUid: 61000, cockpitUid: 61001, fixtureReadGid: 61000, status: 'SELECTED_READ_ONLY_NOT_APPLIED' },
  baseImages: [base('nodeBuilder'), base('nodeRuntime'), base('rust'), base('runnerRuntime')],
  prebuildHashes: { packageLockSha256: sha, cargoLockSha256: sha, cockpitEntrypointSha256: sha, cockpitRuntimeTreeSha256: sha, cockpitFrontendTreeSha256: sha, runnerBinarySha256: sha },
  images: ['phase8a-research-cockpit','phase8a-bronze-runner'].map(name => ({ name, candidateTag: `local/${name}:b`, imageId: `sha256:${sha}`, registryDigest: null, inspectionVerdict: 'PASS', ociProvenancePresent: false, spdxSbomPresent: false, finalDigestRetestVerdict: 'NOT_APPLICABLE_VERIFY_ONLY' })),
  runnerVerification: { verdict: 'PASS', independentRuns: 2, retainedFiles: 20, byteIdentical: true, fixtureInputSha256: 'a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82', perFileManifestSha256: 'aa18a485c53caeb99098e366bce57d3f15d922bf8d60db60205d9c38d2aca258', runId: 'phase8a-fixture-a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82', aggregateHash: '7123c27ffcfd3388b6fa2b98cc3c59be0b42028cf6778404ba645b83254f4791', semanticRerunHash: '94c954098b0bbf1b772932395333af28c1b5317c8e1c0f6c88381f0e2ab5e3b0', outboundDenied: true, writeDenied: true, orphanContainers: 0 },
  cockpitVerification: { unavailableVerdict: 'PASS', providerVerdict: 'PASS', outboundDenied: true, outputUnchanged: true, cleanShutdown: true },
  packageVisibility: 'NOT_APPLICABLE_VERIFY_ONLY',
  nonActions: { imagePush: false, trueNasMutation: false, datasetCreated: false, deployment: false, grafanaModified: false, clickhouseModified: false, payloadRetrieved: false, pilotExecuted: false },
});

describe('Phase 8D1 release manifest fail-closed validation', () => {
  it('binds workflowCommitSha to checked-out source SHA rather than pull-request merge GITHUB_SHA', () => {
    const producer=readFileSync('scripts/phase8d1/verify-images.sh','utf8');
    expect(producer).toContain('--arg workflowCommitSha "$SOURCE_SHA"');
    expect(producer).not.toContain('--arg workflowCommitSha "${GITHUB_SHA:-$SOURCE_SHA}"');
    const row:any=valid(); row.sourceGitSha='b'.repeat(40); row.workflowCommitSha='c'.repeat(40);
    expect(validatePhase8D1ReleaseManifest(row).join('\n')).toMatch(/workflow_identity/i);
  });
  it('accepts the complete verify-only evidence shape', () => expect(validatePhase8D1ReleaseManifest(valid())).toEqual([]));
  it('rejects publish success without immutable digests, SBOM, provenance and digest retest', () => {
    const row: any = valid(); row.status = 'PUBLISH_SUCCEEDED'; row.packageVisibility = 'private'; row.nonActions.imagePush = true;
    expect(validatePhase8D1ReleaseManifest(row).join('\n')).toMatch(/publish|digest|provenance|sbom|retest/i);
  });
  it('rejects a false runner, cockpit or no-deployment gate', () => {
    for (const mutate of [
      (x:any)=>{x.runnerVerification.byteIdentical=false;}, (x:any)=>{x.cockpitVerification.outboundDenied=false;}, (x:any)=>{x.nonActions.trueNasMutation=true;},
    ]) { const row:any=valid(); mutate(row); expect(validatePhase8D1ReleaseManifest(row).join('\n')).not.toBe(''); }
  });
  it('enforces the JSON Schema surface and committed 20-file manifest binding', () => {
    for (const mutate of [
      (x:any)=>{x.extra='forbidden';},
      (x:any)=>{delete x.baseImages[0].versionTag;},
      (x:any)=>{x.baseImages[0].observedAt='not-a-date';},
      (x:any)=>{x.baseImages[0].observedAt='2026-02-30T00:00:00Z';},
      (x:any)=>{x.baseImages[0].digestRef=`docker.io/library/other@sha256:${sha}`;},
      (x:any)=>{x.workflowCommitSha='c'.repeat(40);},
      (x:any)=>{x.runnerVerification.perFileManifestSha256='0'.repeat(64);},
    ]) { const row:any=valid(); mutate(row); expect(validatePhase8D1ReleaseManifest(row).join('\n')).not.toBe(''); }
  });
});
