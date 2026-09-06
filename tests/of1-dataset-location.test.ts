import { execFileSync, spawnSync } from 'node:child_process';
import {
  existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync,
  readdirSync, realpathSync, rmSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const codeSha = 'a'.repeat(40);
const toolchainSha = 'b'.repeat(64);
let scratch: string;
let executable: string;

function invoke(args: string[]) {
  return spawnSync(executable, args, { encoding: 'utf8', timeout: 10_000 });
}

function fixtureGit(directory: string, args: string[]) {
  const env: NodeJS.ProcessEnv = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null' };
  for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_CONFIG_COUNT']) {
    delete env[key];
  }
  return execFileSync('git', [
    '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false',
    '-c', 'user.name=Offline location fixture', '-c', 'user.email=fixture@invalid.example',
    '-C', directory, ...args,
  ], { encoding: 'utf8', env, timeout: 10_000, stdio: ['ignore', 'pipe', 'pipe'] });
}

function expectLocationRejected(root: string) {
  for (const args of [
    ['dataset-preflight', root],
    ['metadata-proposal', root, codeSha, toolchainSha],
    ['metadata-init', root, join(scratch, 'missing-plan.json'), join(scratch, 'missing-lease.json')],
  ]) {
    const result = invoke(args);
    expect(result.error).toBeUndefined();
    expect(result.status).toBe(1);
    expect(result.stdout).toBe('');
    expect(result.stderr).toContain('dataset root must be outside Git');
  }
  expect(existsSync(root)).toBe(false);
}

describe('OF1 dataset CLI location admission is shared and offline', () => {
  beforeAll(() => {
    scratch = mkdtempSync(join(tmpdir(), 'of1-dataset-location-'));
    // Discover Cargo's emitted artifact rather than assuming a target directory.
    // --offline and default-disabled features prevent acquisition capabilities.
    const output = execFileSync('cargo', [
      '+1.97.1', 'build', '--locked', '--offline', '--no-default-features',
      '--manifest-path', 'rust/of1-range-recorder/Cargo.toml',
      '--bin', 'of1-acquire', '--message-format=json',
    ], { encoding: 'utf8', timeout: 180_000, maxBuffer: 16 * 1024 * 1024 });
    const artifacts = output.trim().split('\n').filter(Boolean).map(line => JSON.parse(line))
      .filter(message => message.reason === 'compiler-artifact'
        && message.target.name === 'of1-acquire' && typeof message.executable === 'string');
    expect(artifacts).toHaveLength(1);
    executable = resolve(artifacts[0].executable);
    expect(existsSync(executable)).toBe(true);
  }, 190_000);

  afterAll(() => {
    if (scratch) rmSync(scratch, { recursive: true, force: true });
  });

  it('allows an unchanged empty ordinary marker without creating a root or run artifacts', () => {
    const parent = join(scratch, 'empty-marker');
    const marker = join(parent, '.git');
    mkdirSync(marker, { recursive: true });
    const root = join(parent, 'run');
    const before = lstatSync(marker);
    const result = invoke(['dataset-preflight', root]);
    expect(result.error).toBeUndefined();
    expect(result.status).toBe(0);
    expect(result.stderr).toBe('');
    expect(JSON.parse(result.stdout)).toMatchObject({
      schema: 'OF1_DATASET_LOCATION_PREFLIGHT_1',
      canonical_root: join(realpathSync(parent), 'run'),
      location_status: 'OUTSIDE_GIT', read_only: true,
      networkEnabled: false, readyToRun: false,
    });
    expect(readdirSync(parent)).toEqual(['.git']);
    expect(readdirSync(marker)).toEqual([]);
    const after = lstatSync(marker);
    expect(after.isDirectory()).toBe(true);
    expect(after.isSymbolicLink()).toBe(false);
    expect([after.dev, after.ino, after.mode, after.mtimeMs])
      .toEqual([before.dev, before.ino, before.mode, before.mtimeMs]);
    expect(existsSync(root)).toBe(false);
  });

  it('gates unapproved proposal generation with the same empty-marker exception and unchanged caps', () => {
    const parent = join(scratch, 'proposal-empty-marker');
    mkdirSync(join(parent, '.git'), { recursive: true });
    const root = join(parent, 'run');
    const result = invoke(['metadata-proposal', root, codeSha, toolchainSha]);
    expect(result.error).toBeUndefined();
    expect(result.status).toBe(0);
    const proposal = JSON.parse(result.stdout);
    expect(proposal).toMatchObject({
      schema: 'OF1_METADATA_RUN_PROPOSAL_1',
      approved: false, networkEnabled: false, readyToRun: false,
      aggregate: { code_sha: codeSha, toolchain_fingerprint: toolchainSha },
      metadata_budget: {
        max_requests: 12, max_response_entity_bytes_total: 15_576_576, max_runtime_ms: 600_000,
      },
    });
    expect(proposal.approval_target_sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(readdirSync(parent)).toEqual(['.git']);
    expect(readdirSync(join(parent, '.git'))).toEqual([]);
    expect(existsSync(root)).toBe(false);
  });

  it('rejects a real checkout before proposal generation or reading any init plan', () => {
    const checkout = join(scratch, 'checkout');
    const template = join(scratch, 'empty-template');
    mkdirSync(checkout);
    mkdirSync(template);
    fixtureGit(checkout, ['init', '--quiet', `--template=${template}`]);
    expect(fixtureGit(checkout, ['rev-parse', '--is-inside-work-tree']).trim()).toBe('true');
    expectLocationRejected(join(checkout, 'run'));
  });

  it('rejects an actual linked worktree with a .git file without mutating its marker', () => {
    const checkout = join(scratch, 'worktree-source');
    const template = join(scratch, 'worktree-template');
    const worktree = join(scratch, 'linked-worktree');
    mkdirSync(checkout);
    mkdirSync(template);
    fixtureGit(checkout, ['init', '--quiet', `--template=${template}`]);
    fixtureGit(checkout, ['commit', '--quiet', '--allow-empty', '-m', 'sealed offline fixture']);
    fixtureGit(checkout, ['worktree', 'add', '--quiet', '--detach', worktree, 'HEAD']);
    const marker = join(worktree, '.git');
    expect(lstatSync(marker).isFile()).toBe(true);
    const original = readFileSync(marker);
    expectLocationRejected(join(worktree, 'run'));
    expect(readFileSync(marker)).toEqual(original);
  });

  it('admits the empty-marker location before missing-plan failure without initializing storage', () => {
    const parent = join(scratch, 'init-empty-marker');
    mkdirSync(join(parent, '.git'), { recursive: true });
    const root = join(parent, 'run');
    const result = invoke([
      'metadata-init', root, join(scratch, 'missing-plan.json'), join(scratch, 'missing-lease.json'),
    ]);
    expect(result.error).toBeUndefined();
    expect(result.status).toBe(1);
    expect(result.stderr).not.toContain('dataset root must be outside Git');
    expect(result.stderr).toMatch(/No such file|not found/);
    expect(result.stdout).toBe('');
    expect(readdirSync(parent)).toEqual(['.git']);
    expect(readdirSync(join(parent, '.git'))).toEqual([]);
    expect(existsSync(root)).toBe(false);
  });

  it('does not permit the old root-less proposal invocation to bypass location admission', () => {
    const result = invoke(['metadata-proposal', codeSha, toolchainSha]);
    expect(result.error).toBeUndefined();
    expect(result.status).toBe(1);
    expect(result.stdout).toBe('');
    expect(result.stderr).toContain('usage:');
  });
});
