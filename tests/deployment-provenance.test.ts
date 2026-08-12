import { readFile } from 'node:fs/promises';
import { describe, expect, it } from 'vitest';

const root = new URL('../', import.meta.url);

describe('deployment artifact provenance contract', () => {
  it('keeps the checked-in TrueNAS manifest fail-closed and never bind-mounts executable /app code', async () => {
    const yaml = await readFile(new URL('truenas-custom-app.yaml', root), 'utf8');
    expect(yaml).not.toMatch(/target:\s*\/app\s*$/m);
    expect(yaml).not.toContain('/mnt/fastdisk/ai/hermes/solana-paper-scanner\n');
    expect(yaml).toMatch(/image:\s*registry\.invalid\/solana-paper-scanner-disabled@sha256:0{64}/);
    expect(yaml).toMatch(/restart:\s*["']?no["']?/);
    expect(yaml).toMatch(/exit 78/);
  });

  it('requires the Docker build to carry the exact reviewed freeze aggregate as an OCI label', async () => {
    const dockerfile = await readFile(new URL('Dockerfile', root), 'utf8');
    expect(dockerfile).toContain('ARG SOURCE_FREEZE_SHA256');
    expect(dockerfile).toContain("grep -Eq '^[0-9a-f]{64}$'");
    expect(dockerfile).toContain('LABEL org.opencontainers.image.source-freeze.sha256="$SOURCE_FREEZE_SHA256"');
  });

  it('uses a default-deny Docker context that excludes runtime and generated state', async () => {
    const dockerignore = await readFile(new URL('.dockerignore', root), 'utf8');
    expect(dockerignore.split(/\r?\n/)[1]).toBe('**');
    expect(dockerignore).toContain('!src/**');
    expect(dockerignore).toContain('!frontend/src/**');
    expect(dockerignore).not.toMatch(/^!(?:data|dist|node_modules|secrets)(?:\/|$)/m);
  });
});
