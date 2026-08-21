import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { validatePlatformManifest } from '../scripts/phase8d1/resolve-base-images.mjs';

describe('Phase 8D1 default-deny Docker build context', () => {
  it('allows exactly the source domains required by the two reviewed Dockerfiles', () => {
    const ignore = readFileSync('.dockerignore', 'utf8').split(/\r?\n/);
    for (const required of [
      '**', '!package.json', '!package-lock.json', '!tsconfig.json',
      '!src/', '!src/**', '!frontend/', '!frontend/src/', '!frontend/src/**',
      '!frontend/cockpit.html', '!frontend/vite.cockpit.config.ts',
      '!rust/',
      '!rust/old-faithful-pump-reducer/', '!rust/old-faithful-pump-reducer/Cargo.toml', '!rust/old-faithful-pump-reducer/Cargo.lock', '!rust/old-faithful-pump-reducer/src/', '!rust/old-faithful-pump-reducer/src/**',
      '!rust/linux-kernel-namespace-lock/', '!rust/linux-kernel-namespace-lock/**',
      '!rust/jetstreamer-v0-7-callback-types/', '!rust/jetstreamer-v0-7-callback-types/**',
      '!rust/solana-runtime-v3.1.12-bank-types/', '!rust/solana-runtime-v3.1.12-bank-types/**',
    ]) expect(ignore).toContain(required);
    expect(ignore).not.toContain('!rust/**');
    for (const forbidden of ['!.git/', '!.env', '!secrets/', '!data/', '!tests/']) expect(ignore).not.toContain(forbidden);
  });
});

describe('Phase 8D1 Buildx manifest digest contract', () => {
  it('uses the explicit formatted Manifest digest while sizing the separate raw platform manifest', () => {
    const digest=`sha256:${'a'.repeat(64)}`;
    const formatted={digest,mediaType:'application/vnd.oci.image.manifest.v1+json',size:123};
    const raw={schemaVersion:2,mediaType:'application/vnd.oci.image.manifest.v1+json',config:{size:1},layers:[{size:2}]};
    expect(validatePlatformManifest({digest},formatted,raw).config.size).toBe(1);
    expect(()=>validatePlatformManifest({digest},{...formatted,digest:`sha256:${'b'.repeat(64)}`},raw)).toThrow(/PLATFORM_DIGEST_DRIFT/);
  });
});
