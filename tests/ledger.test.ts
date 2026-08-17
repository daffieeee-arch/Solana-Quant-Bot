import { createHash } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import {
  appendFile, mkdtemp, open, readFile, rename, rm, stat, symlink, writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import {
  CANONICAL_LEDGER_FILE,
  CANONICAL_LEDGER_WAL_FILE,
  MAX_CANONICAL_RECORD_BYTES,
  MAX_LEDGER_EVENT_BYTES,
  PaperLedgerPersistenceError,
  PaperLedgerStore,
} from '../src/ledger.js';
import { createPortfolio } from '../src/portfolio.js';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
});

const canonicalName = CANONICAL_LEDGER_FILE;
const walName = CANONICAL_LEDGER_WAL_FILE;
const lockName = 'paper-ledger-v2.lock';
const digest = (value: Buffer) => createHash('sha256').update(value).digest('hex');

function initialPortfolio(at = '2026-07-30T00:00:00.000Z') {
  return createPortfolio(config, at);
}

function legacyState(at = '2026-07-30T00:00:00.000Z'): Buffer {
  return Buffer.from(`${JSON.stringify({
    schemaVersion: 1,
    portfolio: initialPortfolio(at),
    realizedPnlLamports: 0,
    updatedAt: at,
  })}\n`);
}

async function growCanonicalLedger(
  directory: string,
  targetBytes: number,
  state: Awaited<ReturnType<PaperLedgerStore['loadOrCreate']>>,
): Promise<void> {
  const path = join(directory, canonicalName);
  const genesis = await readFile(path);
  let previousRecordSha256 = digest(genesis);
  let size = genesis.length;
  const handle = await open(path, 'a');
  try {
    for (let sequence = 1; size < targetBytes; sequence += 1) {
      const eventBase = { type: 'scan_complete' as const, at: state.updatedAt, sequence, padding: '' };
      const event = {
        ...eventBase,
        padding: 'x'.repeat(MAX_LEDGER_EVENT_BYTES - Buffer.byteLength(JSON.stringify(eventBase))),
      };
      const line = Buffer.from(`${JSON.stringify({ schemaVersion: 2, sequence, previousRecordSha256, state, event })}\n`);
      if (line.length - 1 > MAX_CANONICAL_RECORD_BYTES) throw new Error('large-ledger fixture record exceeds canonical limit');
      await handle.write(line);
      previousRecordSha256 = digest(line);
      size += line.length;
    }
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function mutateCanonicalSameInode(directory: string): Promise<void> {
  const path = join(directory, canonicalName);
  const bytes = await readFile(path);
  const marker = bytes.indexOf('10000000000');
  if (marker < 0) throw new Error('canonical mutation marker not found');
  const before = await stat(path, { bigint: true });
  const handle = await open(path, 'r+');
  try {
    await new Promise((resolve) => setTimeout(resolve, 10));
    await handle.write(Buffer.from('9'), 0, 1, marker);
    await handle.sync();
  } finally {
    await handle.close();
  }
  const after = await stat(path, { bigint: true });
  expect(after.ino).toBe(before.ino);
  expect(after.size).toBe(before.size);
  expect(after.ctimeNs).not.toBe(before.ctimeNs);
}

async function makeFifo(path: string): Promise<void> {
  const result = spawnSync('mkfifo', [path], { encoding: 'utf8' });
  if (result.status !== 0) throw new Error(`mkfifo failed: ${result.stderr}`);
}

async function waitForLine(child: ReturnType<typeof spawn>, expected: string, timeoutMs = 5_000): Promise<{ stdout: () => string; stderr: () => string }> {
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; });
  child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
  await new Promise<void>((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error(`child timeout waiting for ${expected}: ${stdout} ${stderr}`)), timeoutMs);
    const poll = setInterval(() => {
      if (stdout.includes(`${expected}\n`)) {
        clearTimeout(deadline);
        clearInterval(poll);
        resolve();
      }
    }, 5);
  });
  return { stdout: () => stdout, stderr: () => stderr };
}

describe('PaperLedgerStore canonical validation and migration', () => {
  it.each([
    ['legacy state invalid UTF-8', async (directory: string) => writeFile(join(directory, 'state.json'), Buffer.from([0xff]))],
    ['legacy audit invalid UTF-8', async (directory: string) => {
      await writeFile(join(directory, 'state.json'), legacyState());
      await writeFile(join(directory, 'events.ndjson'), Buffer.from([0xff, 0x0a]));
    }],
    ['legacy audit invalid JSON', async (directory: string) => {
      await writeFile(join(directory, 'state.json'), legacyState());
      await writeFile(join(directory, 'events.ndjson'), '{broken}\n');
    }],
    ['legacy audit unterminated tail', async (directory: string) => {
      await writeFile(join(directory, 'state.json'), legacyState());
      await writeFile(join(directory, 'events.ndjson'), JSON.stringify({ type: 'scan_complete', at: '2026-07-30T00:00:00.000Z' }));
    }],
    ['legacy audit oversized record', async (directory: string) => {
      await writeFile(join(directory, 'state.json'), legacyState());
      await writeFile(join(directory, 'events.ndjson'), `${'x'.repeat(MAX_LEDGER_EVENT_BYTES + 1)}\n`);
    }],
  ])('fully validates %s before creating canonical genesis', async (_case, arrange) => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-malformed-legacy-'));
    await arrange(directory);
    await expect(new PaperLedgerStore(directory).loadOrCreate(initialPortfolio())).rejects.toThrow();
    await expect(stat(join(directory, canonicalName))).rejects.toMatchObject({ code: 'ENOENT' });
  });

  it('migrates a legacy portfolio only in canonical RAM state and never rewrites the archive', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-legacy-portfolio-'));
    const portfolio = initialPortfolio('2026-07-29T00:00:00.000Z') as Partial<ReturnType<typeof initialPortfolio>>;
    delete portfolio.dailyLossDateUtc;
    delete portfolio.consecutiveLosses;
    portfolio.dailyRealizedLossLamports = 123;
    const bytes = Buffer.from(JSON.stringify({
      schemaVersion: 1, portfolio, realizedPnlLamports: 0, updatedAt: '2026-07-30T12:00:00.000Z',
    }));
    await writeFile(join(directory, 'state.json'), bytes);
    const store = new PaperLedgerStore(directory);
    const migrated = await store.loadOrCreate(initialPortfolio('never'));
    expect(migrated.portfolio).toMatchObject({ dailyLossDateUtc: '2026-07-30', dailyRealizedLossLamports: 123, consecutiveLosses: 0 });
    expect(await readFile(join(directory, 'state.json'))).toEqual(bytes);
    await store.close();
  });

  it.each([
    ['invalid UTF-8', (bytes: Buffer) => Buffer.concat([Buffer.from([0xff]), bytes.subarray(1)])],
    ['invalid JSON', (_bytes: Buffer) => Buffer.from('{broken}\n')],
    ['noncontiguous genesis sequence', (bytes: Buffer) => {
      const record = JSON.parse(bytes.toString('utf8'));
      record.sequence = 1;
      return Buffer.from(`${JSON.stringify(record)}\n`);
    }],
    ['invalid full state', (bytes: Buffer) => {
      const record = JSON.parse(bytes.toString('utf8'));
      record.state.updatedAt = 'invalid';
      return Buffer.from(`${JSON.stringify(record)}\n`);
    }],
    ['invalid genesis event', (bytes: Buffer) => {
      const record = JSON.parse(bytes.toString('utf8'));
      record.event = { type: 'scan_complete', at: 'invalid' };
      return Buffer.from(`${JSON.stringify(record)}\n`);
    }],
    ['oversized record', (_bytes: Buffer) => Buffer.from(`${'x'.repeat(MAX_CANONICAL_RECORD_BYTES + 1)}\n`)],
  ])('rejects canonical %s at startup', async (_case, corrupt) => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-corrupt-canonical-'));
    const store = new PaperLedgerStore(directory);
    await store.loadOrCreate(initialPortfolio());
    await store.close();
    const path = join(directory, canonicalName);
    await writeFile(path, corrupt(await readFile(path)));
    await expect(new PaperLedgerStore(directory).loadOrCreate(initialPortfolio('never'))).rejects.toThrow();
  });

  it('rejects a sparse canonical file above 8 GiB by the record bound with less than 128 MiB RSS growth', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-sparse-record-bound-'));
    const path = join(directory, canonicalName);
    const handle = await open(path, 'w');
    try {
      await handle.write(Buffer.alloc(MAX_CANONICAL_RECORD_BYTES + 1, 0x78), 0, MAX_CANONICAL_RECORD_BYTES + 1, 0);
      await handle.truncate((8 * 1024 * 1024 * 1024) + 1);
    } finally {
      await handle.close();
    }
    expect((await stat(path)).size).toBeGreaterThan(8 * 1024 * 1024 * 1024);

    const rssBefore = process.memoryUsage().rss;
    const startedAt = performance.now();
    await expect(new PaperLedgerStore(directory).loadOrCreate(initialPortfolio())).rejects.toThrow(
      new RegExp(`record exceeds its ${MAX_CANONICAL_RECORD_BYTES} byte limit`, 'i'),
    );
    expect(performance.now() - startedAt).toBeLessThan(2_000);
    expect(process.memoryUsage().rss - rssBefore).toBeLessThan(128 * 1024 * 1024);
  });
});

describe('PaperLedgerStore lifecycle', () => {
  it('waits for in-flight initialization before close completes and closes every retained descriptor', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-close-initialization-'));
    let signalInitialization!: () => void;
    const initializationStarted = new Promise<void>((resolve) => { signalInitialization = resolve; });
    let releaseInitialization!: () => void;
    const initializationGate = new Promise<void>((resolve) => { releaseInitialization = resolve; });
    const order: string[] = [];
    const store = new PaperLedgerStore(directory, {
      afterInitializationHandlesOpen: async () => {
        signalInitialization();
        await initializationGate;
      },
    });

    const loading = store.loadOrCreate(initialPortfolio()).then((ledger) => {
      order.push('load');
      return ledger;
    });
    await initializationStarted;
    const handles = store as unknown as { directoryHandle: Awaited<ReturnType<typeof open>>; canonicalHandle: Awaited<ReturnType<typeof open>> };
    const directoryHandle = handles.directoryHandle;
    const canonicalHandle = handles.canonicalHandle;
    const closing = store.close().then(() => { order.push('close'); });
    const closeCompletedWhileInitializationWasBlocked = await Promise.race([
      closing.then(() => true),
      new Promise<false>((resolve) => setTimeout(() => resolve(false), 50)),
    ]);
    releaseInitialization();

    const [loadResult, closeResult] = await Promise.allSettled([loading, closing]);
    expect(closeCompletedWhileInitializationWasBlocked).toBe(false);
    expect(closeResult.status).toBe('fulfilled');
    if (loadResult.status === 'fulfilled') {
      expect(order).toEqual(['load', 'close']);
    } else {
      expect(loadResult.reason).toMatchObject({ message: expect.stringMatching(/closed/i) });
    }
    await expect(directoryHandle.stat()).rejects.toMatchObject({ code: 'EBADF' });
    await expect(canonicalHandle.stat()).rejects.toMatchObject({ code: 'EBADF' });
    await expect(Promise.all([store.close(), store.close()])).resolves.toEqual([undefined, undefined]);
  });
});

describe('PaperLedgerStore nonregular path safety', () => {
  it.each([canonicalName, walName, lockName])('rejects symlink %s without touching its target', async (name) => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-symlink-'));
    const targetDirectory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-symlink-target-'));
    const target = join(targetDirectory, 'target');
    const bytes = Buffer.from('foreign target');
    await writeFile(target, bytes);
    await symlink(target, join(directory, name));
    await expect(new PaperLedgerStore(directory).loadOrCreate(initialPortfolio())).rejects.toThrow(/symbolic|symlink|loop|no.?follow/i);
    expect(await readFile(target)).toEqual(bytes);
  });

  it.each([canonicalName, walName, lockName])('rejects FIFO %s without blocking', async (name) => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-fifo-'));
    await makeFifo(join(directory, name));
    await expect(new PaperLedgerStore(directory).loadOrCreate(initialPortfolio())).rejects.toThrow(/regular file/i);
  });
});

describe('PaperLedgerStore WAL recovery', () => {
  it('fails closed on a divergent tail and preserves canonical and WAL bytes', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-divergent-tail-'));
    const interrupted = new PaperLedgerStore(directory, { afterWalPublish: () => { throw new Error('stop'); } });
    const ledger = await interrupted.loadOrCreate(initialPortfolio());
    await expect(interrupted.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' })).rejects.toThrow(/poisoned/i);
    await interrupted.close();
    await appendFile(join(directory, canonicalName), 'foreign-tail\n');
    const canonicalBefore = await readFile(join(directory, canonicalName));
    const walBefore = await readFile(join(directory, walName));

    await expect(new PaperLedgerStore(directory).loadOrCreate(initialPortfolio('never'))).rejects.toThrow(/tail diverges/i);
    expect(await readFile(join(directory, canonicalName))).toEqual(canonicalBefore);
    expect(await readFile(join(directory, walName))).toEqual(walBefore);
  });

  it('fails closed on a same-size divergent prefix and preserves the WAL', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-divergent-prefix-'));
    const interrupted = new PaperLedgerStore(directory, { afterWalPublish: () => { throw new Error('stop'); } });
    const ledger = await interrupted.loadOrCreate(initialPortfolio());
    await expect(interrupted.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' })).rejects.toThrow(/poisoned/i);
    await interrupted.close();
    const path = join(directory, canonicalName);
    const bytes = await readFile(path);
    const marker = bytes.indexOf('10000000000');
    expect(marker).toBeGreaterThanOrEqual(0);
    bytes[marker] = 0x39;
    await writeFile(path, bytes);
    const walBefore = await readFile(join(directory, walName));

    await expect(new PaperLedgerStore(directory).loadOrCreate(initialPortfolio('never'))).rejects.toThrow(/prefix diverges/i);
    expect(await readFile(join(directory, walName))).toEqual(walBefore);
  });

  it('never deletes a foreign WAL swapped at the ownership boundary', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-wal-owner-'));
    const walPath = join(directory, walName);
    const foreign = Buffer.from('{"foreign":"owner"}\n');
    const store = new PaperLedgerStore(directory, {
      beforeWalRemoval: async () => {
        await rm(walPath);
        await writeFile(walPath, foreign);
      },
    });
    const ledger = await store.loadOrCreate(initialPortfolio());
    await expect(store.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' })).rejects.toThrow(/ownership|invalid|poisoned/i);
    expect(await readFile(walPath)).toEqual(foreign);
    await store.close();
  });
});

describe('PaperLedgerStore append and compare-and-swap', () => {
  it('rejects a same-inode same-size mutation in beforeAppend and preserves the WAL', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-before-append-mutation-'));
    const store = new PaperLedgerStore(directory, {
      beforeAppend: () => mutateCanonicalSameInode(directory),
    });
    const ledger = await store.loadOrCreate(initialPortfolio());
    let rejection: unknown;
    try {
      await store.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' });
    } catch (error) {
      rejection = error;
    }
    await store.close();

    expect(rejection).toMatchObject({ message: expect.stringMatching(/poisoned|changed|stale/i) });
    await expect(stat(join(directory, walName))).resolves.toMatchObject({ size: expect.any(Number) });
  });

  it('rejects a same-inode same-size mutation during split append and preserves the WAL', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-during-append-mutation-'));
    const store = new PaperLedgerStore(directory, {
      duringAppend: () => mutateCanonicalSameInode(directory),
    });
    const ledger = await store.loadOrCreate(initialPortfolio());
    let rejection: unknown;
    try {
      await store.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' });
    } catch (error) {
      rejection = error;
    }
    await store.close();

    expect(rejection).toMatchObject({ message: expect.stringMatching(/poisoned|changed|split/i) });
    await expect(stat(join(directory, walName))).resolves.toMatchObject({ size: expect.any(Number) });
  });

  it('rejects a same-inode same-size mutation in afterAppend before entering WAL removal', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-after-append-mutation-'));
    let reachedWalRemoval = false;
    const store = new PaperLedgerStore(directory, {
      afterAppend: () => mutateCanonicalSameInode(directory),
      beforeWalRemoval: () => { reachedWalRemoval = true; },
    });
    const ledger = await store.loadOrCreate(initialPortfolio());
    let rejection: unknown;
    try {
      await store.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' });
    } catch (error) {
      rejection = error;
    }
    await store.close();

    expect(rejection).toMatchObject({ message: expect.stringMatching(/poisoned|changed|append/i) });
    expect(reachedWalRemoval).toBe(false);
    await expect(stat(join(directory, walName))).resolves.toMatchObject({ size: expect.any(Number) });
  });

  it('rejects a same-inode same-size mutation in beforeWalRemoval without deleting the WAL', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-before-wal-removal-mutation-'));
    const store = new PaperLedgerStore(directory, {
      beforeWalRemoval: () => mutateCanonicalSameInode(directory),
    });
    const ledger = await store.loadOrCreate(initialPortfolio());
    let rejection: unknown;
    try {
      await store.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' });
    } catch (error) {
      rejection = error;
    }
    await store.close();

    expect(rejection).toMatchObject({ message: expect.stringMatching(/poisoned|changed|append/i) });
    await expect(stat(join(directory, walName))).resolves.toMatchObject({ size: expect.any(Number) });
  });

  it('reports canonical bytes read during a normal save', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-read-observer-'));
    let bytesRead = 0;
    const store = new PaperLedgerStore(directory, {
      canonicalReadObserver: (count) => { bytesRead += count; },
    });
    const ledger = await store.loadOrCreate(initialPortfolio());
    bytesRead = 0;

    await store.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' });

    expect(bytesRead).toBeGreaterThan(0);
    await store.close();
  });

  it.each([64, 128])('reads only a bounded record-sized amount when appending to a valid %i MiB ledger', async (mib) => {
    const directory = await mkdtemp(join(tmpdir(), `paper-ledger-v2-bounded-${mib}-`));
    const setup = new PaperLedgerStore(directory);
    const ledger = await setup.loadOrCreate(initialPortfolio());
    await setup.close();
    await growCanonicalLedger(directory, mib * 1024 * 1024, ledger);

    let bytesRead = 0;
    const store = new PaperLedgerStore(directory, {
      canonicalReadObserver: (count) => { bytesRead += count; },
    });
    await store.loadOrCreate(initialPortfolio('never'));
    bytesRead = 0;

    await store.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' });

    expect(bytesRead).toBeGreaterThan(0);
    expect(bytesRead).toBeLessThanOrEqual(8 * MAX_CANONICAL_RECORD_BYTES);
    await store.close();
  }, 30_000);

  it('preserves the original validation failure as cause and publishes no WAL', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-cause-'));
    const store = new PaperLedgerStore(directory);
    const ledger = await store.loadOrCreate(initialPortfolio());
    let rejection: unknown;
    try {
      await store.save({ ...ledger, updatedAt: 'invalid' });
    } catch (error) {
      rejection = error;
    }
    expect(rejection).toBeInstanceOf(PaperLedgerPersistenceError);
    expect((rejection as PaperLedgerPersistenceError).cause).toBeInstanceOf(Error);
    await expect(stat(join(directory, walName))).rejects.toMatchObject({ code: 'ENOENT' });
    await store.close();
  });

  it('enforces the serialized event byte boundary before publishing WAL', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-event-bound-'));
    const store = new PaperLedgerStore(directory);
    const ledger = await store.loadOrCreate(initialPortfolio());
    const base = { type: 'scan_complete' as const, at: '2026-07-30T00:01:00.000Z', padding: '' };
    const baseBytes = Buffer.byteLength(JSON.stringify(base));
    const exact = { ...base, padding: 'x'.repeat(MAX_LEDGER_EVENT_BYTES - baseBytes) };
    expect(Buffer.byteLength(JSON.stringify(exact))).toBe(MAX_LEDGER_EVENT_BYTES);
    await expect(store.save({ ...ledger, updatedAt: base.at }, exact)).resolves.toBeUndefined();
    const oversized = { ...base, at: '2026-07-30T00:02:00.000Z', padding: `${'x'.repeat(MAX_LEDGER_EVENT_BYTES - baseBytes - 1)}é` };
    await expect(store.save({ ...ledger, updatedAt: oversized.at }, oversized)).rejects.toThrow(/event exceeds.*1 MiB/i);
    await expect(stat(join(directory, walName))).rejects.toMatchObject({ code: 'ENOENT' });
    await store.close();
  });

  it('serializes concurrent in-process saves in call order with contiguous hashes', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-queue-'));
    const store = new PaperLedgerStore(directory);
    const ledger = await store.loadOrCreate(initialPortfolio());
    await Promise.all(Array.from({ length: 20 }, (_, index) => {
      const at = `2026-07-30T00:00:${String(index).padStart(2, '0')}.000Z`;
      return store.save({ ...ledger, updatedAt: at }, { type: 'scan_complete', at, index });
    }));
    const lines = (await readFile(join(directory, canonicalName), 'utf8')).trimEnd().split('\n');
    const records = lines.map((line) => JSON.parse(line));
    expect(records.map((record) => record.sequence)).toEqual(Array.from({ length: 21 }, (_, index) => index));
    for (let index = 1; index < records.length; index += 1) {
      expect(records[index].previousRecordSha256).toBe(digest(Buffer.from(`${lines[index - 1]}\n`)));
    }
    expect(records.slice(1).map((record) => record.event.index)).toEqual(Array.from({ length: 20 }, (_, index) => index));
    await store.close();
  });

  it('rejects a stale in-process store instead of appending a competing state', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-stale-'));
    const first = new PaperLedgerStore(directory);
    const second = new PaperLedgerStore(directory);
    const ledger = await first.loadOrCreate(initialPortfolio());
    await second.loadOrCreate(initialPortfolio('never'));
    await first.save({ ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' });
    await expect(second.save({ ...ledger, updatedAt: '2026-07-30T00:02:00.000Z' })).rejects.toThrow(/changed since load|stale|compare-and-swap/i);
    const records = (await readFile(join(directory, canonicalName), 'utf8')).trimEnd().split('\n');
    expect(records).toHaveLength(2);
    await first.close();
    await second.close();
  });

  it('serializes real child-process writers and rejects exactly one stale CAS loser', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-child-cas-'));
    const setup = new PaperLedgerStore(directory);
    await setup.loadOrCreate(initialPortfolio());
    await setup.close();
    const moduleUrl = new URL('../src/ledger.ts', import.meta.url).href;
    const source = `
      import { PaperLedgerStore } from ${JSON.stringify(moduleUrl)};
      const initial = { createdAt: '2026-07-30T00:00:00.000Z', availableLamports: 1, dailyRealizedLossLamports: 0, dailyLossDateUtc: '2026-07-30', positions: [], consecutiveLosses: 0 };
      const store = new PaperLedgerStore(process.env.DATA_DIR);
      const ledger = await store.loadOrCreate(initial);
      process.stdout.write('READY\\n');
      process.stdin.once('data', async () => {
        try {
          await store.save({ ...ledger, updatedAt: process.env.AT }, { type: 'scan_complete', at: process.env.AT, writer: process.env.AT });
          process.stdout.write(JSON.stringify({ ok: true, at: process.env.AT }) + '\\n');
        } catch (error) {
          process.stdout.write(JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }) + '\\n');
        } finally {
          await store.close();
        }
      });
    `;
    const run = (at: string) => {
      const child = spawn(process.execPath, ['--import', 'tsx', '--input-type=module', '--eval', source], {
        cwd: join(import.meta.dirname, '..'), env: { ...process.env, DATA_DIR: directory, AT: at }, stdio: ['pipe', 'pipe', 'pipe'],
      });
      let stdout = '';
      let stderr = '';
      child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; });
      child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
      const ready = new Promise<void>((resolve, reject) => {
        const deadline = setTimeout(() => reject(new Error(`child not ready: ${stdout} ${stderr}`)), 5_000);
        const poll = setInterval(() => {
          if (stdout.includes('READY\n')) { clearTimeout(deadline); clearInterval(poll); resolve(); }
        }, 5);
      });
      const result = new Promise<{ ok: boolean; at?: string; error?: string }>((resolve, reject) => {
        child.once('error', reject);
        child.once('exit', (code) => {
          const line = stdout.trimEnd().split('\n').find((candidate) => candidate.startsWith('{'));
          if (code !== 0 || !line) reject(new Error(`child failed: ${code} ${stdout} ${stderr}`));
          else resolve(JSON.parse(line));
        });
      });
      return { child, ready, result };
    };
    const children = [run('2026-07-30T00:01:00.000Z'), run('2026-07-30T00:02:00.000Z')];
    await Promise.all(children.map((entry) => entry.ready));
    for (const entry of children) entry.child.stdin.end('GO\n');
    const results = await Promise.all(children.map((entry) => entry.result));
    expect(results.filter((result) => result.ok)).toHaveLength(1);
    expect(results.filter((result) => !result.ok)).toHaveLength(1);
    expect(results.find((result) => !result.ok)?.error).toMatch(/changed since load|stale|compare-and-swap/i);
  });
});

describe('PaperLedgerStore real SIGKILL recovery', () => {
  it('releases the kernel lock and recovers the fsynced WAL after a real SIGKILL', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-sigkill-'));
    const moduleUrl = new URL('../src/ledger.ts', import.meta.url).href;
    const source = `
      import { PaperLedgerStore } from ${JSON.stringify(moduleUrl)};
      const keepAlive = setInterval(() => {}, 1000);
      const initial = { createdAt: '2026-07-30T00:00:00.000Z', availableLamports: 1, dailyRealizedLossLamports: 0, dailyLossDateUtc: '2026-07-30', positions: [], consecutiveLosses: 0 };
      const at = '2026-07-30T00:05:00.000Z';
      const store = new PaperLedgerStore(process.env.DATA_DIR, {
        afterWalPublish: async () => {
          process.stdout.write('WAL_READY\\n');
          await new Promise(() => {});
        },
      });
      const ledger = await store.loadOrCreate(initial);
      await store.save({ ...ledger, updatedAt: at }, { type: 'scan_complete', at, killed: true });
    `;
    const child = spawn(process.execPath, ['--import', 'tsx', '--input-type=module', '--eval', source], {
      cwd: join(import.meta.dirname, '..'), env: { ...process.env, DATA_DIR: directory }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    const output = await waitForLine(child, 'WAL_READY');
    child.kill('SIGKILL');
    await new Promise<void>((resolve, reject) => {
      child.once('exit', () => resolve());
      child.once('error', reject);
    });
    expect(output.stderr()).toBe('');
    await expect(stat(join(directory, walName))).resolves.toMatchObject({ size: expect.any(Number) });

    const recovered = new PaperLedgerStore(directory);
    await expect(recovered.loadOrCreate(initialPortfolio('never'))).resolves.toMatchObject({ updatedAt: '2026-07-30T00:05:00.000Z' });
    await expect(stat(join(directory, walName))).rejects.toMatchObject({ code: 'ENOENT' });
    await recovered.close();
  }, 10_000);
});
