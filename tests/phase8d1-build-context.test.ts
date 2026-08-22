import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { classifyRegistryFailure, validatePlatformManifest } from '../scripts/phase8d1/resolve-base-images.mjs';

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
  it('retries only bounded transient registry transport failures', () => {
    for(const value of ['read: connection reset by peer','unexpected EOF','TLS handshake timeout','503 Service Unavailable'])expect(classifyRegistryFailure(value)).toBe('RETRY');
    for(const value of ['toomanyrequests: rate limit','429 Too Many Requests','401 Unauthorized','403 Forbidden','manifest unknown'])expect(classifyRegistryFailure(value)).toBe('STOP');
  });
  it('never retries image pulls so the reviewed two-cache download budget remains valid', () => {
    const resolver=readFileSync('scripts/phase8d1/resolve-base-images.mjs','utf8');
    expect(resolver).toContain("run('docker', ['pull', '--platform', contract.platform, row.digestRef])");
    expect(resolver).not.toContain("runRegistry('docker', ['pull'");
  });
});

describe('Phase 8D1 prebuild cleanup ownership', () => {
  it('returns both root-created temporary build trees to the invoking runner even on container exit', () => {
    const script=readFileSync('scripts/phase8d1/prebuild-hashes.sh','utf8');
    expect(script).toContain('-e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)"');
    expect(script).toContain('trap "chown -R \\"$HOST_UID:$HOST_GID\\" /app" EXIT');
    expect(script).toContain('trap "chown -R \\"$HOST_UID:$HOST_GID\\" /src" EXIT');
  });
});

describe('Phase 8D1 verification shell initialization', () => {
  it('initializes the runner replay index before deriving paths under set -u', () => {
    const script=readFileSync('scripts/phase8d1/verify-images.sh','utf8');
    expect(script).toContain('local index="$1"\n  local out="$TMP/output-$index" name="phase8d1-runner-$index-${GITHUB_RUN_ID:-local}"');
    expect(script).not.toContain('local index="$1" out="$TMP/output-$index"');
  });
  it('reclaims only the temporary output parent before reading immutable runner output', () => {
    const script=readFileSync('scripts/phase8d1/verify-images.sh','utf8');
    expect(script).toContain('sudo chown "$(id -u):$(id -g)" "$out"; sudo chmod 0750 "$out"\n  local run="$out/run"');
    expect(script).not.toContain('chown -R "$(id -u):$(id -g)" "$out"');
  });
  it('never derives a local value from a variable first declared on the same line under set -u', () => {
    const lines=readFileSync('scripts/phase8d1/verify-images.sh','utf8').split(/\r?\n/);
    for(const line of lines){const declared=/^\s*local\s+([A-Za-z_][A-Za-z0-9_]*)=/.exec(line)?.[1];if(declared)expect(line).not.toMatch(new RegExp(`\\$(?:${declared}(?:[^A-Za-z0-9_]|$)|\\{${declared}\\})`));}
  });
  it('tolerates only transient unpublished-port probes and still requires a loopback binding', () => {
    const script=readFileSync('scripts/phase8d1/verify-images.sh','utf8');
    expect(script).toContain("docker port \"$name\" 3000/tcp 2>/dev/null | sed -nE 's#^127\\.0\\.0\\.1:([0-9]+)$#\\1#p' || true");
    expect(script).toContain('test -n "$port"\n  curl -fsS "http://127.0.0.1:$port/healthz"');
    expect(script).toContain('-p 127.0.0.1::3000');
  });
});
