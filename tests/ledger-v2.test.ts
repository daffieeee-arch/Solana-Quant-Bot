import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rename, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { CANONICAL_LEDGER_FILE, PaperLedgerStore } from '../src/ledger.js';
import type { PaperLedgerStoreOptions } from '../src/ledger.js';
import { loadScannerHistory } from '../src/history.js';
import { createPortfolio } from '../src/portfolio.js';

const config = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data',
});

const digest = (value: Buffer) => createHash('sha256').update(value).digest('hex');

describe('PaperLedgerStore canonical v2 ledger', () => {
  it('creates one validated genesis record and retains its anchored descriptors until idempotent close', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-genesis-'));
    const initial = createPortfolio(config, '2026-07-30T00:00:00.000Z');
    const store = new PaperLedgerStore(directory);

    const state = await store.loadOrCreate(initial);

    expect(state).toMatchObject({ schemaVersion: 1, portfolio: initial, realizedPnlLamports: 0 });
    const bytes = await readFile(join(directory, CANONICAL_LEDGER_FILE));
    expect(bytes.at(-1)).toBe(0x0a);
    const [line, trailing] = bytes.toString('utf8').split('\n');
    expect(trailing).toBe('');
    const record = JSON.parse(line);
    expect(record).toEqual({
      schemaVersion: 2,
      sequence: 0,
      previousRecordSha256: digest(Buffer.alloc(0)),
      state,
    });
    expect((await stat(join(directory, CANONICAL_LEDGER_FILE))).isFile()).toBe(true);

    await store.close();
    await store.close();
    await expect(store.save(state)).rejects.toThrow(/closed/i);
  });

  it('migrates validated legacy bytes once and history combines legacy with non-genesis canonical events without duplication', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-migration-'));
    const initial = createPortfolio(config, '2026-07-30T00:00:00.000Z');
    const legacyState = Buffer.from(`${JSON.stringify({
      schemaVersion: 1,
      portfolio: initial,
      realizedPnlLamports: 0,
      updatedAt: initial.createdAt,
    }, null, 2)}\n`);
    const legacyEvent = Buffer.from(`${JSON.stringify({
      type: 'scan_complete', at: '2026-07-30T00:00:00.000Z', equityLamports: 10,
      decisions: [{ type: 'rejected', pairId: 'legacy-pair' }],
    })}\n`);
    await writeFile(join(directory, 'state.json'), legacyState);
    await writeFile(join(directory, 'events.ndjson'), legacyEvent);
    const store = new PaperLedgerStore(directory);

    const migrated = await store.loadOrCreate(createPortfolio(config, 'never'));
    const at = '2026-07-30T00:01:00.000Z';
    await store.save({ ...migrated, updatedAt: at }, {
      type: 'scan_complete', at, equityLamports: 11,
      decisions: [{ type: 'rejected', pairId: 'canonical-pair' }],
    });

    expect(await readFile(join(directory, 'state.json'))).toEqual(legacyState);
    expect(await readFile(join(directory, 'events.ndjson'))).toEqual(legacyEvent);
    const records = (await readFile(join(directory, CANONICAL_LEDGER_FILE), 'utf8')).trimEnd().split('\n').map((line) => JSON.parse(line));
    expect(records.map((record) => record.sequence)).toEqual([0, 1]);
    expect(records[0]).not.toHaveProperty('event');
    expect(records[1].event).toMatchObject({ at, equityLamports: 11 });
    const history = await loadScannerHistory(directory);
    expect(history.recentDecisions.map((decision) => decision.pairId)).toEqual(['legacy-pair', 'canonical-pair']);
    expect(history.equityHistory).toEqual([
      { at: '2026-07-30T00:00:00.000Z', equityLamports: 10 },
      { at, equityLamports: 11 },
    ]);
    await store.close();
  });

  it('fails closed and preserves the WAL when the kernel-lock directory entry is swapped after publication', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-lock-swap-'));
    const lockPath = join(directory, 'paper-ledger-v2.lock');
    const walPath = join(directory, 'paper-ledger-v2.wal');
    const store = new PaperLedgerStore(directory, {
      afterWalPublish: async () => {
        await rename(lockPath, `${lockPath}.owned`);
        await writeFile(lockPath, 'foreign lock inode');
      },
    });
    const ledger = await store.loadOrCreate(createPortfolio(config, '2026-07-30T00:00:00.000Z'));

    await expect(store.save(
      { ...ledger, updatedAt: '2026-07-30T00:01:00.000Z' },
      { type: 'scan_complete', at: '2026-07-30T00:01:00.000Z' },
    )).rejects.toThrow(/lock.*replaced|poisoned/i);
    await expect(stat(walPath)).resolves.toMatchObject({ size: expect.any(Number) });
    expect(await readFile(lockPath, 'utf8')).toBe('foreign lock inode');
    await store.close();
  });

  it('returns the newest derived in-RAM state on repeated load without consulting rewritten legacy inputs', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-ledger-v2-derived-state-'));
    const store = new PaperLedgerStore(directory);
    const ledger = await store.loadOrCreate(createPortfolio(config, '2026-07-30T00:00:00.000Z'));
    const at = '2026-07-30T00:02:00.000Z';
    await store.save({ ...ledger, updatedAt: at });
    await writeFile(join(directory, 'state.json'), '{malformed legacy state');
    await writeFile(join(directory, 'events.ndjson'), Buffer.from([0xff, 0x0a]));

    await expect(store.loadOrCreate(createPortfolio(config, 'never'))).resolves.toMatchObject({ updatedAt: at });
    await store.close();

    const restarted = new PaperLedgerStore(directory);
    await expect(restarted.loadOrCreate(createPortfolio(config, 'never'))).resolves.toMatchObject({ updatedAt: at });
    await restarted.close();
  });

  it.each([
    ['no append', 'afterWalPublish'],
    ['matching partial tail', 'duringAppend'],
    ['exact full append', 'afterAppend'],
  ] as const)('recovers a published WAL after %s', async (_case, hook) => {
    const directory = await mkdtemp(join(tmpdir(), `paper-ledger-v2-recovery-${hook}-`));
    const options: PaperLedgerStoreOptions = {
      [hook]: () => { throw new Error(`SIGKILL seam ${hook}`); },
    };
    const interrupted = new PaperLedgerStore(directory, options);
    const ledger = await interrupted.loadOrCreate(createPortfolio(config, '2026-07-30T00:00:00.000Z'));
    const at = '2026-07-30T00:03:00.000Z';
    await expect(interrupted.save(
      { ...ledger, updatedAt: at },
      { type: 'scan_complete', at, marker: hook },
    )).rejects.toThrow(/poisoned/i);
    await interrupted.close();
    await expect(stat(join(directory, 'paper-ledger-v2.wal'))).resolves.toMatchObject({ size: expect.any(Number) });

    const recovered = new PaperLedgerStore(directory);
    await expect(recovered.loadOrCreate(createPortfolio(config, 'never'))).resolves.toMatchObject({ updatedAt: at });
    await expect(stat(join(directory, 'paper-ledger-v2.wal'))).rejects.toMatchObject({ code: 'ENOENT' });
    const records = (await readFile(join(directory, CANONICAL_LEDGER_FILE), 'utf8')).trimEnd().split('\n');
    expect(records).toHaveLength(2);
    expect(JSON.parse(records[1]).event).toMatchObject({ marker: hook });
    await recovered.close();
  });

  it.each([
    'afterWalPublish',
    'beforeAppend',
    'duringAppend',
    'afterAppend',
    'beforeWalRemoval',
  ] as const)('poisons, rejects, and preserves WAL on canonical path swap at %s', async (hook) => {
    const directory = await mkdtemp(join(tmpdir(), `paper-ledger-v2-path-swap-${hook}-`));
    const canonicalPath = join(directory, CANONICAL_LEDGER_FILE);
    const foreignBytes = Buffer.from('foreign canonical inode\n');
    const options: PaperLedgerStoreOptions = {
      [hook]: async () => {
        await rename(canonicalPath, `${canonicalPath}.owned-${hook}`);
        await writeFile(canonicalPath, foreignBytes);
      },
    };
    const store = new PaperLedgerStore(directory, options);
    const ledger = await store.loadOrCreate(createPortfolio(config, '2026-07-30T00:00:00.000Z'));

    await expect(store.save(
      { ...ledger, updatedAt: '2026-07-30T00:04:00.000Z' },
      { type: 'scan_complete', at: '2026-07-30T00:04:00.000Z', hook },
    )).rejects.toThrow(/poisoned|path was replaced/i);
    expect(await readFile(canonicalPath)).toEqual(foreignBytes);
    await expect(stat(join(directory, 'paper-ledger-v2.wal'))).resolves.toMatchObject({ size: expect.any(Number) });
    await store.close();
  });
});
