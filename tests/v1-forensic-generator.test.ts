import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, readdir, readlink, rename, rm, stat, symlink, truncate, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const repositoryRoot = resolve(import.meta.dirname, '..');
const generator = resolve(repositoryRoot, 'scripts/build-v1-forensic-manifest.py');
const tableUuid = '17c42dd2-2249-496e-8558-c6f41bedd731';
const exactDdl = `ATTACH TABLE _ UUID '${tableUuid}'
(
    \`slot\` UInt32,
    \`timestamp\` DateTime('UTC'),
    \`program_id\` String,
    \`signature\` String,
    \`mint\` String,
    \`kind\` String,
    \`price_lamports_per_token\` Float64,
    \`base_raw_delta\` UInt64,
    \`wsol_raw_delta\` UInt64,
    \`base_decimals\` UInt8
)
ENGINE = ReplacingMergeTree(slot)
ORDER BY (signature)
SETTINGS index_granularity = 8192
`;
const exactColumns = `columns format version: 1
10 columns:
\`slot\` UInt32
\`timestamp\` DateTime('UTC')
\`program_id\` String
\`signature\` String
\`mint\` String
\`kind\` String
\`price_lamports_per_token\` Float64
\`base_raw_delta\` UInt64
\`wsol_raw_delta\` UInt64
\`base_decimals\` UInt8
`;
const scratch: string[] = [];

afterEach(async () => {
  await Promise.all(scratch.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

type Fixture = {
  root: string;
  tableRoot: string;
  ddl: string;
  provenanceRoot: string;
  binary: string;
  statusDir: string;
  part: string;
};

async function fixture(partName = 'all_1_1_0'): Promise<Fixture> {
  const root = await mkdtemp(join(tmpdir(), 'v1-forensic-generator-'));
  scratch.push(root);
  const tableRoot = join(root, tableUuid);
  const part = join(tableRoot, partName);
  const provenanceRoot = join(root, 'provenance');
  const parserSource = join(provenanceRoot, 'memecoin-backfill/src');
  const statusDir = join(root, 'status');
  const ddl = join(root, 'memecoin_swaps.sql');
  const binary = join(root, 'memecoin-backfill');
  await Promise.all([
    mkdir(part, { recursive: true }),
    mkdir(parserSource, { recursive: true }),
    mkdir(statusDir, { recursive: true }),
  ]);
  await Promise.all([
    writeFile(join(part, 'count.txt'), '1\n', 'ascii'),
    writeFile(join(part, 'checksums.txt'), 'checksums format version: 4\nfixture-checksums\n', 'ascii'),
    writeFile(join(part, 'columns.txt'), exactColumns, 'ascii'),
    writeFile(join(part, 'data.bin'), 'payload\n', 'ascii'),
    writeFile(join(part, 'data.cmrk2'), 'marks\n', 'ascii'),
    writeFile(join(parserSource, 'main.rs'), 'fn main() {}\n', 'utf8'),
    writeFile(join(provenanceRoot, 'PROVENANCE.md'), '- Epochs volledig: 1-2\n', 'utf8'),
    writeFile(join(statusDir, 'A.status'), 'EP 1 done\nEP 2 failed\n', 'ascii'),
    writeFile(binary, 'fixture-binary\n', 'ascii'),
    writeFile(ddl, exactDdl, 'utf8'),
  ]);
  for (const args of [
    ['init', '-q'],
    ['add', '.'],
    ['-c', 'user.name=Hermes Test', '-c', 'user.email=hermes@example.invalid', 'commit', '-qm', 'fixture'],
  ]) {
    const result = spawnSync('git', args, { cwd: provenanceRoot, encoding: 'utf8' });
    if (result.status !== 0) throw new Error(result.stderr || `git ${args.join(' ')} failed`);
  }
  return { root, tableRoot, ddl, provenanceRoot, binary, statusDir, part };
}

function args(value: Fixture, observedAt = '2026-08-17T00:00:00.000Z') {
  return [
    generator,
    '--table-root', value.tableRoot,
    '--ddl', value.ddl,
    '--provenance-root', value.provenanceRoot,
    '--binary', value.binary,
    '--status-dir', value.statusDir,
    '--supervisor-start', '1',
    '--supervisor-end', '2',
    '--requested-start', '1',
    '--requested-end', '2',
    '--observed-at', observedAt,
  ];
}

function run(value: Fixture, extra: string[] = []) {
  return spawnSync('python3', [...args(value), ...extra], { cwd: repositoryRoot, encoding: 'utf8' });
}

describe('v1 forensic manifest generator', () => {
  it('emits a neutral read-only snapshot claim without asserting ClickHouse stopped', async () => {
    const value = await fixture();
    const result = run(value);
    expect(result.status, result.stderr).toBe(0);
    const manifest = JSON.parse(result.stdout);
    expect(manifest.observationMode).toBe('READ_ONLY_FILESYSTEM_SNAPSHOT');
    expect(manifest.writerState).toBe('NOT_VERIFIED_BY_GENERATOR');
    expect(manifest.observedAtSource).toBe('OPERATOR_SUPPLIED_NOT_VERIFIED_BY_GENERATOR');
    expect(result.stdout).not.toContain('CLICKHOUSE_STOPPED');
  });

  it('requires a canonical observed-at timestamp', async () => {
    const value = await fixture();
    const missingArgs = args(value).slice(0, -2);
    const missing = spawnSync('python3', missingArgs, { cwd: repositoryRoot, encoding: 'utf8' });
    expect(missing.status).not.toBe(0);
    expect(run(value, ['--observed-at', 'not-a-timestamp']).status).not.toBe(0);
  });

  it('rejects DDL that does not match the pinned table contract', async () => {
    const value = await fixture();
    await writeFile(value.ddl, `ATTACH TABLE _ UUID '${tableUuid}'\n(x UInt8)\nENGINE = TinyLog\n`, 'utf8');
    const result = run(value);
    expect(result.status).not.toBe(0);
  });

  it('rejects caller override of the pinned v1 table UUID', async () => {
    const value = await fixture();
    const otherUuid = '11111111-2222-4333-8444-555555555555';
    await writeFile(value.ddl, exactDdl.replace(tableUuid, otherUuid), 'utf8');
    expect(run(value, ['--table-uuid', otherUuid]).status).not.toBe(0);
  });

  it('rejects a comment-spoofed effective DDL', async () => {
    const value = await fixture();
    await writeFile(value.ddl, `-- ATTACH TABLE _ UUID '${tableUuid}' ENGINE = ReplacingMergeTree(slot) ORDER BY signature\nATTACH TABLE _ UUID '11111111-2222-4333-8444-555555555555' (x UInt8) ENGINE = TinyLog\n`, 'utf8');
    expect(run(value).status).not.toBe(0);
  });

  it('requires the table-root basename to be the pinned ClickHouse UUID', async () => {
    const value = await fixture();
    const renamed = join(value.root, 'not-the-pinned-table');
    await rename(value.tableRoot, renamed);
    expect(run({ ...value, tableRoot: renamed }).status).not.toBe(0);
  });

  it('rejects symlinked source inputs', async () => {
    const value = await fixture();
    const realCount = join(value.root, 'real-count.txt');
    await writeFile(realCount, '1\n', 'ascii');
    await rm(join(value.part, 'count.txt'));
    await symlink(realCount, join(value.part, 'count.txt'));
    const result = run(value);
    expect(result.status).not.toBe(0);
  });

  it('rejects a noncanonical part name for the exact unpartitioned table', async () => {
    const value = await fixture('p1_2_2_0');
    expect(run(value).status).not.toBe(0);
  });

  it.each(['all_01_01_00', 'all_1_1_0_0'])(
    'rejects non-round-tripping or zero-mutation part name %s',
    async (partName) => {
      const value = await fixture(partName);
      expect(run(value).status).not.toBe(0);
    },
  );

  it('rejects a merely part-like directory without ClickHouse payload structure', async () => {
    const value = await fixture();
    await Promise.all([
      rm(join(value.part, 'columns.txt')),
      rm(join(value.part, 'data.bin')),
      rm(join(value.part, 'data.cmrk2')),
      writeFile(join(value.part, 'checksums.txt'), 'arbitrary checksums bytes\n', 'ascii'),
    ]);
    expect(run(value).status).not.toBe(0);
  });

  it('rejects a payload and mark file with different stems', async () => {
    const value = await fixture();
    await rename(join(value.part, 'data.cmrk2'), join(value.part, 'other.cmrk2'));
    expect(run(value).status).not.toBe(0);
  });

  it.each([
    ['orphan payload', 'orphan.bin'],
    ['orphan mark', 'orphan.mrk3'],
  ])('rejects an otherwise valid part with an %s', async (_label, orphanName) => {
    const value = await fixture();
    await writeFile(join(value.part, orphanName), 'orphan\n', 'ascii');
    expect(run(value).status).not.toBe(0);
  });

  it('does not refresh or rewrite the parser repository Git index', async () => {
    const value = await fixture();
    const parserSource = join(value.provenanceRoot, 'memecoin-backfill/src/main.rs');
    const sourceStats = await stat(parserSource);
    await utimes(parserSource, sourceStats.atime, new Date(sourceStats.mtimeMs + 10_000));
    const index = join(value.provenanceRoot, '.git/index');
    const beforeBytes = await readFile(index);
    const before = await stat(index, { bigint: true });
    const result = spawnSync('python3', args(value), {
      cwd: repositoryRoot,
      encoding: 'utf8',
      env: { ...process.env, GIT_OPTIONAL_LOCKS: '1' },
    });
    expect(result.status, result.stderr).toBe(0);
    const afterBytes = await readFile(index);
    const after = await stat(index, { bigint: true });
    expect(afterBytes.equals(beforeBytes)).toBe(true);
    expect(after.mtimeNs).toBe(before.mtimeNs);
    expect(after.ctimeNs).toBe(before.ctimeNs);
  });

  it('rejects a symlink in an ancestor component of an input path', async () => {
    const value = await fixture();
    const aliasRoot = await mkdtemp(join(tmpdir(), 'v1-forensic-symlink-parent-'));
    scratch.push(aliasRoot);
    const alias = join(aliasRoot, 'alias');
    await symlink(value.root, alias, 'dir');
    expect(run({ ...value, tableRoot: join(alias, tableUuid) }).status).not.toBe(0);
  });

  it('rejects overlapping active part block ranges', async () => {
    const value = await fixture('all_1_2_0');
    const overlapping = join(value.tableRoot, 'all_2_3_0');
    await mkdir(overlapping);
    await Promise.all([
      writeFile(join(overlapping, 'count.txt'), '1\n', 'ascii'),
      writeFile(join(overlapping, 'checksums.txt'), 'checksums format version: 4\nfixture-checksums\n', 'ascii'),
      writeFile(join(overlapping, 'columns.txt'), exactColumns, 'ascii'),
      writeFile(join(overlapping, 'data.bin'), 'payload\n', 'ascii'),
      writeFile(join(overlapping, 'data.cmrk2'), 'marks\n', 'ascii'),
    ]);
    expect(run(value).status).not.toBe(0);
  });

  it('rejects external DDL drift after its descriptor read completes', async () => {
    const value = await fixture();
    await truncate(value.binary, 1024 * 1024 * 1024);
    const child = spawn('python3', args(value), { cwd: repositoryRoot, stdio: ['ignore', 'pipe', 'pipe'] });
    const deadline = Date.now() + 5_000;
    let binaryOpen = false;
    while (Date.now() < deadline && child.exitCode === null) {
      try {
        const descriptors = await readdir(`/proc/${child.pid}/fd`);
        for (const descriptor of descriptors) {
          try {
            if (await readlink(`/proc/${child.pid}/fd/${descriptor}`) === value.binary) binaryOpen = true;
          } catch { /* descriptor closed between discovery and readlink */ }
        }
      } catch { /* process may be exiting */ }
      if (binaryOpen) break;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 2));
    }
    expect(binaryOpen).toBe(true);
    await writeFile(value.ddl, `${exactDdl}\n`, 'utf8');
    const status = await new Promise<number | null>((resolveStatus) => child.once('close', resolveStatus));
    expect(status).not.toBe(0);
  }, 15_000);

  it('rejects parser repository HEAD or clean-state drift across the complete observation', async () => {
    const value = await fixture();
    await writeFile(join(value.provenanceRoot, 'memecoin-backfill/src/main.rs'), 'fn main() { println!("second"); }\n', 'utf8');
    for (const gitArgs of [
      ['add', '.'],
      ['-c', 'user.name=Hermes Test', '-c', 'user.email=hermes@example.invalid', 'commit', '-qm', 'second'],
    ]) {
      const result = spawnSync('git', gitArgs, { cwd: value.provenanceRoot, encoding: 'utf8' });
      expect(result.status, result.stderr).toBe(0);
    }
    const commitB = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: value.provenanceRoot, encoding: 'utf8' }).stdout.trim();
    expect(spawnSync('git', ['reset', '--hard', 'HEAD^'], { cwd: value.provenanceRoot }).status).toBe(0);
    await truncate(value.binary, 1024 * 1024 * 1024);
    const child = spawn('python3', args(value), { cwd: repositoryRoot, stdio: ['ignore', 'pipe', 'pipe'] });
    const deadline = Date.now() + 5_000;
    let binaryOpen = false;
    while (Date.now() < deadline && child.exitCode === null) {
      try {
        const descriptors = await readdir(`/proc/${child.pid}/fd`);
        for (const descriptor of descriptors) {
          try {
            if (await readlink(`/proc/${child.pid}/fd/${descriptor}`) === value.binary) binaryOpen = true;
          } catch { /* descriptor closed between discovery and readlink */ }
        }
      } catch { /* process may be exiting */ }
      if (binaryOpen) break;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 2));
    }
    expect(binaryOpen).toBe(true);
    expect(spawnSync('git', ['update-ref', 'HEAD', commitB], { cwd: value.provenanceRoot }).status).toBe(0);
    const status = await new Promise<number | null>((resolveStatus) => child.once('close', resolveStatus));
    expect(status).not.toBe(0);
  }, 15_000);

  it('rejects concurrent filesystem drift across the complete observation', async () => {
    const value = await fixture();
    await truncate(value.binary, 128 * 1024 * 1024);
    const child = spawn('python3', args(value), { cwd: repositoryRoot, stdio: ['ignore', 'pipe', 'pipe'] });
    let toggle = false;
    const mutator = setInterval(() => {
      toggle = !toggle;
      void writeFile(join(value.part, 'count.txt'), toggle ? '1\n' : '2\n', 'ascii');
    }, 1);
    const status = await new Promise<number | null>((resolveStatus) => child.once('close', resolveStatus));
    clearInterval(mutator);
    expect(status).not.toBe(0);
  }, 15_000);
});
