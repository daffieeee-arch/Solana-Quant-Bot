import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { validatePhase8CContracts } from '../scripts/assert-phase8c-contracts.mjs';

const fixture = () => ({
  images: JSON.parse(readFileSync('deployment/phase8c/image-contracts.json', 'utf8')),
  dataset: JSON.parse(readFileSync('deployment/phase8c/posix-fixture-dataset-changeplan.json', 'utf8')),
  deployment: JSON.parse(readFileSync('deployment/phase8c/truenas-deployment-plan.json', 'utf8')),
  cockpitDockerfile: readFileSync('containers/Dockerfile.cockpit', 'utf8'),
  runnerDockerfile: readFileSync('containers/Dockerfile.phase8a-runner', 'utf8'),
});

describe('Phase 8C contract policy', () => {
  it('accepts the closed unapplied contracts', () => {
    expect(validatePhase8CContracts(fixture())).toEqual([]);
    const pkg = JSON.parse(readFileSync('package.json', 'utf8')) as any;
    expect(pkg.scripts['verify:phase8c-contracts']).toBe('node scripts/assert-phase8c-contracts.mjs');
    expect(pkg.scripts.build).toContain('npm run verify:phase8c-contracts');
  });

  it.each([
    ['non-POSIX ACL', (x: any) => { x.dataset.properties.acltype = 'NFSV4'; }],
    ['missing quota', (x: any) => { delete x.dataset.properties.quota; }],
    ['exec enabled', (x: any) => { x.dataset.properties.exec = 'ON'; }],
    ['setuid enabled', (x: any) => { x.dataset.properties.setuid = 'ON'; }],
    ['devices enabled', (x: any) => { x.dataset.properties.devices = 'ON'; }],
    ['runner/cockpit rights swapped', (x: any) => { x.dataset.mounts.runner.access = 'READ_ONLY'; x.dataset.mounts.cockpit.access = 'READ_WRITE'; }],
    ['fixture and pilot dataset mixed', (x: any) => { x.dataset.realPilotDataset = x.dataset.dataset; }],
    ['invented UID', (x: any) => { x.dataset.owner.uid = 12345; }],
    ['share enabled', (x: any) => { x.dataset.shares.nfs = true; }],
    ['mutable image tag', (x: any) => { x.images.images[0].mutableTagAllowed = true; x.images.images[0].tagContract = 'latest'; }],
    ['existing app mutation', (x: any) => { x.deployment.existingApp.mutationAuthorized = true; }],
    ['mutable cockpit base image', (x: any) => { x.cockpitDockerfile = x.cockpitDockerfile.replace('FROM ${NODE_BUILDER_IMAGE}', 'FROM node:22').replace('FROM ${NODE_RUNTIME_IMAGE}', 'FROM node:22-slim'); }],
    ['missing image resources', (x: any) => { delete x.images.images[0].resources; }],
    ['missing image hash bindings', (x: any) => { delete x.images.images[0].hashBindings; }],
    ['image host PID enabled', (x: any) => { x.images.images[0].security.hostPid = true; }],
    ['missing dataset compression', (x: any) => { delete x.dataset.properties.compression; }],
    ['missing post-replay immutability', (x: any) => { delete x.dataset.postReplay; }],
    ['missing dataset rollback', (x: any) => { delete x.dataset.rollback; }],
    ['runner host networking', (x: any) => { x.deployment.apps[0].network = 'HOST'; }],
    ['runner host port', (x: any) => { x.deployment.apps[0].hostPorts = [3000]; }],
    ['wildcard cockpit binding', (x: any) => { x.deployment.apps[1].hostBinding.wildcardAllowed = true; }],
    ['zero deployment resource cap', (x: any) => { x.deployment.apps[1].resources.memoryBytes = 0; }],
    ['unbounded deployment resource cap', (x: any) => { x.deployment.apps[1].resources.memoryBytes = Number.MAX_SAFE_INTEGER; }],
    ['privileged cockpit app', (x: any) => { x.deployment.apps[1].security.privileged = true; }],
    ['missing app rollback', (x: any) => { delete x.deployment.apps[1].rollback; }],
  ])('rejects %s', (_name, mutate) => {
    const input = structuredClone(fixture());
    mutate(input);
    expect(validatePhase8CContracts(input).join('\n')).toMatch(/forbidden|invalid|must|unresolved|immutable|dataset|share|mutation/i);
  });
});
