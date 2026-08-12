import { describe, expect, it } from 'vitest';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const projectRoot = process.cwd();

describe('TrueNAS read-only helper', () => {
  it('rejects a mutating method before reading credentials or opening a connection', () => {
    const result = spawnSync(process.execPath, [join(projectRoot, 'scripts/truenas-wss-readonly-check.mjs')], {
      encoding: 'utf8',
      env: {
        ...process.env,
        TRUENAS_API_METHOD: 'app.update',
        TRUENAS_API_KEY_PATH: '/definitely/not/present',
      },
    });

    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('not allowlisted for read-only use: app.update');
    expect(result.stderr).not.toContain('ENOENT');
    expect(result.stdout).toBe('');
  });

  it('keeps obsolete manual learning triggers disabled and fail-closed', () => {
    for (const script of ['trigger-learn.mjs', 'trigger-learn2.mjs']) {
      const result = spawnSync(process.execPath, [join(projectRoot, script)], { encoding: 'utf8' });
      expect(result.status).not.toBe(0);
      expect(result.stderr).toContain('disabled');
      expect(result.stdout).toBe('');
    }
  });

  it('keeps deployment fail-closed until Gate C supplies an immutable reviewed application image', () => {
    const expectedBase = 'sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3';
    const disabledDigest = '0'.repeat(64);
    const compose = readFileSync(join(projectRoot, 'truenas-custom-app.yaml'), 'utf8');
    const dockerfile = readFileSync(join(projectRoot, 'Dockerfile'), 'utf8');
    expect(compose).toContain(`image: registry.invalid/solana-paper-scanner-disabled@sha256:${disabledDigest}`);
    expect(compose).toContain('exit 78');
    expect(compose).toContain('restart: "no"');
    expect(compose).not.toMatch(/^\s*target:\s*\/app\s*$/m);
    expect(dockerfile.match(new RegExp(`^FROM node:22-bookworm-slim@${expectedBase}`, 'gm'))).toHaveLength(2);
    expect(dockerfile).toContain('ARG SOURCE_FREEZE_SHA256');
    expect(dockerfile).toContain('org.opencontainers.image.source-freeze.sha256');
    expect(dockerfile).not.toMatch(/^FROM node:22-bookworm-slim(?:\s|$)/m);
  });
});