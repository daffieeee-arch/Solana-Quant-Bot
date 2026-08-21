import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

function json(path: string) { return JSON.parse(readFileSync(path, 'utf8')) as any; }

describe('Phase 8C image, dataset and deployment contracts', () => {
  it('keeps cockpit and runner images separate, non-root and provenance-bound', () => {
    const cockpit = readFileSync('containers/Dockerfile.cockpit', 'utf8');
    const runner = readFileSync('containers/Dockerfile.phase8a-runner', 'utf8');
    expect(cockpit).toContain('ARG SOURCE_GIT_SHA');
    expect(cockpit).toMatch(/^ARG NODE_IMAGE$/m);
    expect(cockpit).not.toMatch(/^ARG NODE_IMAGE=/m);
    expect(cockpit).toContain('ARG PACKAGE_LOCK_SHA256');
    expect(cockpit).toContain('ARG COCKPIT_ENTRYPOINT_SHA256');
    expect(cockpit).toContain('ARG FRONTEND_BUILD_SHA256');
    expect(cockpit).toMatch(/USER \$\{COCKPIT_UID\}:\$\{COCKPIT_GID\}/);
    expect(cockpit).toContain('HEALTHCHECK');
    expect(cockpit).not.toMatch(/dist\/main\.js|src\/scanner|src\/ledger|Cargo|rustc/);
    expect(runner).toContain('ARG SOURCE_GIT_SHA');
    expect(runner).toMatch(/USER \$\{RUNNER_UID\}:\$\{RUNNER_GID\}/);
    expect(runner).toContain('ENTRYPOINT ["/phase8a-bronze-runner"]');
    expect(runner).not.toMatch(/node_modules|package\.json|dist\/main\.js|node /);
    const contract = json('deployment/phase8c/image-contracts.json');
    expect(contract.images.map((row: any) => row.name).sort()).toEqual(['phase8a-bronze-runner', 'phase8a-research-cockpit']);
    for (const image of contract.images) {
      expect(image.tagPolicy).toBe('IMMUTABLE_UNIQUE_TAG_REQUIRED');
      expect(image.mutableTagAllowed).toBe(false);
      expect(image.security).toMatchObject({ nonRoot: true, readOnlyRootFilesystem: true, capDrop: ['ALL'], noNewPrivileges: true, privileged: false, dockerSocket: false });
      expect(image.uid).toBeNull();
      expect(image.gid).toBeNull();
      expect(image.identityStatus).toBe('UNRESOLVED_REQUIRES_OPERATIONS_APPROVAL');
      expect(image.baseImageStatus).toBe('UNRESOLVED_EXACT_DIGEST_REQUIRED_NO_PULL_PERFORMED');
    }
  });

  it('defines a POSIX fixture dataset plan without inventing UID/GID or creating shares', () => {
    const plan = json('deployment/phase8c/posix-fixture-dataset-changeplan.json');
    expect(plan.applied).toBe(false);
    expect(plan.dataset).toBe('fastdisk/apps/solana-phase8a-fixtures');
    expect(plan.properties).toMatchObject({ acltype: 'POSIX', aclmode: 'DISCARD', xattr: 'SA', atime: 'OFF', exec: 'OFF', setuid: 'OFF', devices: 'OFF', quota: '10 GiB', casesensitivity: 'SENSITIVE' });
    expect(plan.owner.uid).toBeNull();
    expect(plan.owner.gid).toBeNull();
    expect(plan.owner.status).toBe('UNRESOLVED_REQUIRES_OPERATIONS_APPROVAL');
    expect(plan.shares).toEqual({ smb: false, nfs: false, hostExport: false });
    expect(plan.mounts.runner.access).toBe('READ_WRITE');
    expect(plan.mounts.cockpit.access).toBe('READ_ONLY');
    expect(plan.realPilotDataset).not.toBe(plan.dataset);
    expect(plan.snapshotNameContract).toMatch(/phase8a-fixture/);
    expect(plan.rollback.length).toBeGreaterThan(2);
    expect(plan.quotaAlarm).toBeDefined();
  });

  it('keeps runner and cockpit as separate unapplied TrueNAS apps with bounded resources', () => {
    const plan = json('deployment/phase8c/truenas-deployment-plan.json');
    expect(plan.applied).toBe(false);
    expect(plan.existingApp).toMatchObject({ name: 'solana-bot', configuredImage: 'solana-bot:contra-audit16-offline-pump-3e95a3c', requiredState: 'STOPPED', mutationAuthorized: false });
    expect(plan.apps).toHaveLength(2);
    const runner = plan.apps.find((row: any) => row.name === 'phase8a-fixture-runner');
    const cockpit = plan.apps.find((row: any) => row.name === 'phase8a-research-cockpit');
    expect(runner).toMatchObject({ lifecycle: 'ONE_SHOT', network: 'NONE', hostPorts: [] });
    expect(runner.mounts.output.access).toBe('READ_WRITE');
    expect(cockpit.lifecycle).toBe('LONG_RUNNING_INERT');
    expect(cockpit.mounts.output.access).toBe('READ_ONLY');
    expect(cockpit.hostBinding.status).toBe('UNRESOLVED_REQUIRES_EXPLICIT_OPERATIONS_APPROVAL');
    for (const app of plan.apps) expect(app.resources).toMatchObject({ cpus: expect.any(Number), memoryBytes: expect.any(Number), pids: expect.any(Number) });
  });
});
