import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

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
