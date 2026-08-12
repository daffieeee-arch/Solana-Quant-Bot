import { describe, expect, it } from 'vitest';
import {
  lstat, mkdir, mkdtemp, open, readFile, readdir, rename, rm, stat, symlink, truncate, writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import {
  MAX_COMPLETED_TRADE_IDS, OpportunityStorage, type OpportunityRecord,
} from '../src/opportunity-storage.js';

const base: OpportunityRecord = {
  learningSchemaVersion: 2,
  at: '2026-07-28T10:00:00.000Z',
  strategyId: 'v1.0',
  token: { mint: 'mint-1', symbol: 'ONE', pairId: 'pair-1' },
  dex: 'raydium',
  source: 'birdeye',
  priceUsd: 0.01,
  liquidityUsd: 50_000,
  volumeM5Usd: 12_000,
  priceChangeM5Percent: 15,
  buysM5: 80,
  sellsM5: 30,
  decision: 'entry',
  tradeId: 'mint-1:2026-07-28T10:00:00.000Z',
  entryPriceUsd: 0.0101,
  score: 68,
};

describe('OpportunityStorage completed trades', () => {
  it('links an exit to its entry context and deduplicates repeated tradeId completions', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-'));
    const storage = new OpportunityStorage(directory);
    storage.log(base);
    storage.log({ ...base, learningSchemaVersion: undefined, tradeId: 'legacy-trade', pnlSol: -0.1, exitReason: 'stop_loss' });
    const exit: OpportunityRecord = {
      learningSchemaVersion: 2,
      strategyId: 'v2.0',
      at: '2026-07-28T10:05:00.000Z',
      token: { mint: 'different-mint', symbol: 'EVIL', pairId: 'pair-2' },
      dex: 'raydium',
      source: 'scanner',
      decision: 'exit',
      tradeId: base.tradeId,
      priceUsd: 0.012,
      entryPriceUsd: 999,
      score: 0,
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    };
    expect(storage.log(exit)).toBe(true);
    expect(storage.log({ ...exit })).toBe(false); // exact duplicate must be coalesced
    storage.log(base); // full entry+exit replay must also be coalesced
    expect(storage.log({ ...exit })).toBe(false);
    storage.log({ ...exit, decision: 'rejected', pnlSol: 99, exitReason: 'not_an_exit' });
    storage.log({ ...exit, strategyId: 'v1.0', tradeId: 'orphan-v2-exit' }); // position opened before v2; must not enter the clean cohort
    const invalidPnlEntry = { ...base, tradeId: 'mint-2:2026-07-28T10:00:00.000Z', token: { ...base.token, mint: 'mint-2' } };
    storage.log(invalidPnlEntry);
    expect(() => storage.log({ ...invalidPnlEntry, decision: 'exit', pnlSol: Number.NaN })).toThrow(/finite safe number/i);
    const incompleteExitEntry = { ...base, tradeId: 'mint-3:2026-07-28T10:00:00.000Z', token: { ...base.token, mint: 'mint-3' } };
    storage.log(incompleteExitEntry);
    expect(storage.log({ ...incompleteExitEntry, decision: 'exit', pnlSol: 0.1, exitPriceUsd: 1.2 })).toBe(false);

    const restarted = new OpportunityStorage(directory);
    expect(restarted.completedCount).toBe(1);
    const completed = await restarted.loadCompletedTrades('v1.0');
    expect(completed).toHaveLength(1);
    expect(completed[0]).toMatchObject({
      tradeId: base.tradeId,
      score: 68,
      entryPriceUsd: 0.0101,
      priceUsd: 0.012,
      token: base.token,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      decision: 'exit',
    });
  });

  it('caps pending entries and reconciles them against open ledger trade IDs', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-'));
    const storage = new OpportunityStorage(directory);
    for (let index = 0; index < 105; index += 1) {
      storage.log({
        ...base,
        tradeId: `pending-${index}`,
        token: { ...base.token, mint: `mint-${index}` },
      });
    }
    const completeExit = (tradeId: string): OpportunityRecord => ({
      ...base,
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit',
      tradeId,
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    });
    expect(storage.log(completeExit('pending-0'))).toBe(false);
    storage.reconcilePending(new Set(['pending-104']));
    const restarted = new OpportunityStorage(directory);
    expect(restarted.log(completeExit('pending-103'))).toBe(false);
    expect(restarted.log(completeExit('pending-104'))).toBe(true);
    expect(restarted.completedCount).toBe(1);
  });

  it('log and reconcile mutate only the canonical audit and leave legacy files byte-exact', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-no-derived-mutation-'));
    const legacy = new Map([
      ['learning-index-v2.json', 'legacy-index'],
      ['completed-trade-ids-v2', 'legacy-completed'],
      ['opportunity-derived-state-v3.ndjson', 'legacy-derived'],
      ['.opportunity-derived-state-v3.ndjson.leftover.tmp', 'legacy-temp'],
    ]);
    for (const [name, content] of legacy) await writeFile(join(directory, name), content);
    const storage = new OpportunityStorage(directory);
    const entry = { ...base, tradeId: 'audit-only-runtime' };

    storage.log(entry);
    storage.reconcilePending(new Set());

    for (const [name, content] of legacy) {
      expect(await readFile(join(directory, name), 'utf8')).toBe(content);
    }
    expect((await readdir(directory)).sort()).toEqual([
      '.opportunity-derived-state-v3.ndjson.leftover.tmp',
      'completed-trade-ids-v2',
      'learning-index-v2.json',
      'opportunities.ndjson',
      'opportunity-derived-state-v3.ndjson',
    ]);
  });

  it('appends a bounded opportunity after the live audit exceeds 128 MiB', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-cap-'));
    const path = join(directory, 'opportunities.ndjson');
    const cap = 128 * 1024 * 1024;
    const storage = new OpportunityStorage(directory);
    await writeFile(path, 'x');
    await truncate(path, cap);

    expect(() => storage.log(base)).not.toThrow();
    expect((await stat(path)).size).toBeGreaterThan(cap);
  });

  it('streams valid opportunities larger than the former 64 MiB aggregate cap', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-large-stream-'));
    const path = join(directory, 'opportunities.ndjson');
    const handle = await open(path, 'w');
    try {
      for (let index = 0; index < 1_025; index += 1) {
        const record = { ...base, at: `2026-07-29T13:${String(index % 60).padStart(2, '0')}:00.000Z`, padding: '' };
        const targetBytes = 65_500;
        record.padding = 'x'.repeat(targetBytes - Buffer.byteLength(JSON.stringify(record), 'utf8'));
        const serialized = JSON.stringify(record);
        expect(Buffer.byteLength(serialized, 'utf8')).toBe(targetBytes);
        await handle.write(`${serialized}\n`);
      }
    } finally {
      await handle.close();
    }

    try {
      expect((await stat(path)).size).toBeGreaterThan(64 * 1024 * 1024);
      const storage = new OpportunityStorage(directory);
      let count = 0;
      for await (const record of storage.stream()) {
        expect(record.decision).toBe(base.decision);
        count += 1;
      }
      expect(count).toBe(1_025);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it('enforces the 64 KiB serialized record boundary before audit mutation', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-record-cap-'));
    const auditPath = join(directory, 'opportunities.ndjson');
    const storage = new OpportunityStorage(directory);
    const limit = 64 * 1024;
    const exactBase = { ...base, padding: '' };
    const exactRecord = { ...exactBase, padding: 'x'.repeat(limit - Buffer.byteLength(JSON.stringify(exactBase), 'utf8')) };
    expect(Buffer.byteLength(JSON.stringify(exactRecord), 'utf8')).toBe(limit);
    expect(() => storage.log(exactRecord)).not.toThrow();

    const auditBeforeRejectedLog = await readFile(auditPath, 'utf8');
    const oversizedBase = {
      ...base,
      learningSchemaVersion: 2 as const,
      decision: 'entry' as const,
      tradeId: 'oversized-entry',
      entryPriceUsd: base.priceUsd,
      score: 80,
      padding: '',
    };
    const oversizedRecord = {
      ...oversizedBase,
      padding: `${'x'.repeat(limit - Buffer.byteLength(JSON.stringify(oversizedBase), 'utf8') - 1)}é`,
    };
    expect(Buffer.byteLength(JSON.stringify(oversizedRecord), 'utf8')).toBe(limit + 1);

    expect(() => storage.log(oversizedRecord)).toThrow(/opportunity record exceeds.*64 KiB/i);
    expect(await readFile(auditPath, 'utf8')).toBe(auditBeforeRejectedLog);
  });

  it('rejects an oversized opportunity record while streaming', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-stream-record-cap-'));
    const path = join(directory, 'opportunities.ndjson');
    const storage = new OpportunityStorage(directory);
    const limit = 64 * 1024;
    const record = { ...base, padding: '' };
    record.padding = 'x'.repeat(limit + 1 - Buffer.byteLength(JSON.stringify(record), 'utf8'));
    expect(Buffer.byteLength(JSON.stringify(record), 'utf8')).toBe(limit + 1);
    await writeFile(path, `${JSON.stringify(record)}\n`);

    await expect(collect(storage.stream())).rejects.toThrow(/record exceeds.*65536 byte/i);
  });

  it.each([
    ['malformed JSON', 'not-json\n'],
    ['an empty record', '\n'],
  ])('rejects %s instead of skipping audit corruption', async (_label, content) => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-corrupt-stream-'));
    const storage = new OpportunityStorage(directory);
    await writeFile(join(directory, 'opportunities.ndjson'), content);

    await expect(collect(storage.stream())).rejects.toThrow();
  });

  it.each([
    ['null', null],
    ['an array', []],
    ['a missing required field', { ...base, source: undefined }],
    ['an unknown decision', { ...base, decision: 'invented' }],
    ['an oversized required string', { ...base, source: 's'.repeat(257) }],
    ['a null numeric field', { ...base, liquidityUsd: null }],
    ['a non-safe numeric field', { ...base, priceUsd: Number.MAX_SAFE_INTEGER + 1 }],
  ])('rejects %s opportunity data before yielding it', async (_label, candidate) => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-schema-'));
    const storage = new OpportunityStorage(directory);
    await writeFile(join(directory, 'opportunities.ndjson'), `${JSON.stringify(candidate)}\n`);

    await expect(collect(storage.stream())).rejects.toThrow(/opportunity record/i);
  });

  it('reads only the exact bounded legacy empty-field forms while keeping new writes strict', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-legacy-fields-'));
    const legacyEntry = { ...base, learningSchemaVersion: undefined, tradeId: undefined, at: '', dex: '' };
    const historicalV2Exit = { ...base, decision: 'exit' as const, dex: '', pnlSol: 0.1, exitPriceUsd: 1.1, exitReason: 'take_profit', holdingMinutes: 5 };
    await writeFile(join(directory, 'opportunities.ndjson'), `${JSON.stringify(legacyEntry)}\n${JSON.stringify(historicalV2Exit)}\n`);

    const storage = new OpportunityStorage(directory);
    await expect(collect(storage.stream())).resolves.toEqual([legacyEntry, historicalV2Exit]);
    expect(() => storage.log({ ...base, dex: '' })).toThrow(/dex/i);
    expect(() => storage.log({ ...base, at: '' })).toThrow(/at/i);
  });

  it('requires priceUsd and validates it before mutating audit or index state', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-log-schema-'));
    const storage = new OpportunityStorage(directory);

    expect(() => storage.log({ ...base, priceUsd: Number.NaN })).toThrow(/priceUsd.*finite safe number/i);
    expect(() => storage.log({ ...base, priceUsd: undefined } as unknown as OpportunityRecord)).toThrow(/priceUsd.*finite safe number/i);
    await expect(readFile(join(directory, 'opportunities.ndjson'), 'utf8')).resolves.toBe('');
  });

  it('detaches unknown nested JSON from the caller so live completion exactly matches audit replay', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-nested-mutation-'));
    const metadata = { marker: 'audit-value', nested: [{ label: 'original' }] };
    const candidate = {
      ...base,
      tradeId: 'nested-immutable-entry',
      metadata,
    } as OpportunityRecord & { metadata: typeof metadata };
    const storage = new OpportunityStorage(directory);
    storage.log(candidate);

    metadata.marker = 'mutated-after-audit';
    metadata.nested[0]!.label = 'mutated-after-audit';
    expect(storage.log({
      ...base,
      tradeId: candidate.tradeId,
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit',
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    })).toBe(true);

    const live = await storage.loadCompletedTrades('v1.0');
    const restarted = new OpportunityStorage(directory);
    const replayed = await restarted.loadCompletedTrades('v1.0');
    expect(live).toEqual(replayed);
    expect(live[0]).toMatchObject({
      metadata: { marker: 'audit-value', nested: [{ label: 'original' }] },
    });
  });

  it('validates the exact serialized representation before appending or mutating RAM', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-serialized-validation-'));
    const storage = new OpportunityStorage(directory);
    const candidate = {
      ...base,
      toJSON: () => 'not-an-opportunity-record',
    } as unknown as OpportunityRecord;

    expect(() => storage.log(candidate)).toThrow(/opportunity record.*object/i);
    await expect(readFile(join(directory, 'opportunities.ndjson'), 'utf8')).resolves.toBe('');
    expect(() => storage.log(base)).not.toThrow();
  });

  it('returns deeply detached completed records instead of exposing retained nested runtime objects', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-public-detach-'));
    const metadata = { marker: 'retained', nested: [{ label: 'retained' }] };
    const entry = { ...base, tradeId: 'public-detach', metadata } as OpportunityRecord & { metadata: typeof metadata };
    const storage = new OpportunityStorage(directory);
    storage.log(entry);
    storage.log({
      ...base,
      tradeId: entry.tradeId,
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit',
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    });

    const first = await storage.loadCompletedTrades('v1.0') as Array<OpportunityRecord & { metadata: typeof metadata }>;
    const second = await storage.loadCompletedTrades('v1.0') as Array<OpportunityRecord & { metadata: typeof metadata }>;
    expect(first[0]!.metadata).not.toBe(second[0]!.metadata);
    expect(first[0]!.metadata.nested[0]).not.toBe(second[0]!.metadata.nested[0]);
  });

  it('rejects unknown JSON nesting beyond the explicit depth bound before audit mutation', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-json-depth-'));
    const storage = new OpportunityStorage(directory);
    const metadata: Record<string, unknown> = {};
    let cursor = metadata;
    for (let depth = 0; depth < 65; depth += 1) {
      const next: Record<string, unknown> = {};
      cursor.next = next;
      cursor = next;
    }

    expect(() => storage.log({ ...base, metadata } as OpportunityRecord)).toThrow(/JSON depth.*64/i);
    await expect(readFile(join(directory, 'opportunities.ndjson'), 'utf8')).resolves.toBe('');
  });

  it('rejects unknown JSON beyond the explicit node bound before audit mutation', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-json-nodes-'));
    const storage = new OpportunityStorage(directory);
    const metadata = { values: Array.from({ length: 10_000 }, () => null) };
    expect(Buffer.byteLength(JSON.stringify({ ...base, metadata }), 'utf8')).toBeLessThan(64 * 1024);

    expect(() => storage.log({ ...base, metadata } as OpportunityRecord)).toThrow(/JSON node count.*10000/i);
    await expect(readFile(join(directory, 'opportunities.ndjson'), 'utf8')).resolves.toBe('');
  });

  it('detaches and freezes validated entries so caller mutation cannot rewrite completion context', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-mutation-'));
    const storage = new OpportunityStorage(directory);
    const candidate: OpportunityRecord = {
      ...base,
      tradeId: 'immutable-entry',
      token: { ...base.token, mint: 'immutable-mint' },
    };
    storage.log(candidate);

    candidate.strategyId = 'v2.0';
    candidate.score = 0;
    candidate.token.mint = 'mutated-mint';
    expect(storage.log({
      ...base,
      tradeId: 'immutable-entry',
      token: { ...base.token, mint: 'exit-mint' },
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit',
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    })).toBe(true);

    await expect(storage.loadCompletedTrades('v1.0')).resolves.toEqual([
      expect.objectContaining({ score: 68, token: expect.objectContaining({ mint: 'immutable-mint' }) }),
    ]);
    await expect(storage.loadCompletedTrades('v2.0')).resolves.toEqual([]);
  });

  it.each([0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1])(
    'rejects invalid public load limit %s',
    async (limit) => {
      const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-load-limit-'));
      const storage = new OpportunityStorage(directory);
      await expect(storage.load(limit)).rejects.toThrow(/positive safe integer/i);
    },
  );

  it('caps public load deterministically at 10,000 records', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-load-cap-'));
    const records = Array.from({ length: 10_001 }, (_, index) => JSON.stringify({
      ...base,
      at: new Date(Date.parse(base.at) + index).toISOString(),
      tradeId: `load-${index}`,
    }));
    await writeFile(join(directory, 'opportunities.ndjson'), `${records.join('\n')}\n`);

    const storage = new OpportunityStorage(directory);
    const loaded = await storage.load(Number.MAX_SAFE_INTEGER);
    expect(loaded).toHaveLength(10_000);
    expect(loaded[0]?.tradeId).toBe('load-0');
    expect(loaded.at(-1)?.tradeId).toBe('load-9999');
  });

  it('replays and deduplicates worst-case bounded UTF-8 trade IDs without a completed-ID cache', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-completed-encoding-'));
    const storage = new OpportunityStorage(directory);
    const tradeId = '\u0001'.repeat(128);
    const entry = { ...base, tradeId };
    storage.log(entry);
    expect(storage.log({
      ...entry,
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit',
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    })).toBe(true);

    storage.close();
    const restarted = new OpportunityStorage(directory);
    restarted.log(entry);
    expect(restarted.log({
      ...entry,
      at: '2026-07-28T10:06:00.000Z',
      decision: 'exit',
      exitPriceUsd: 0.013,
      exitReason: 'duplicate',
      pnlSol: 0.05,
      holdingMinutes: 6,
    })).toBe(false);
    expect(await readdir(directory)).toEqual(['opportunities.ndjson']);
  });

  it('rebuilds exact completed-trade dedupe from the authoritative audit beyond 5,000 completions', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-exact-dedupe-'));
    const records: string[] = [];
    for (let index = 0; index < 5_001; index += 1) {
      const entry = { ...base, tradeId: `completed-${index}`, token: { ...base.token, mint: `mint-${index}` } };
      records.push(JSON.stringify(entry));
      records.push(JSON.stringify({
        ...entry,
        at: '2026-07-28T10:05:00.000Z',
        decision: 'exit',
        exitPriceUsd: 0.012,
        exitReason: 'trailing_stop',
        pnlSol: 0.04,
        holdingMinutes: 5,
      }));
    }
    await writeFile(join(directory, 'opportunities.ndjson'), `${records.join('\n')}\n`);

    const storage = new OpportunityStorage(directory);
    expect(storage.completedCount).toBe(5_001);
    expect(await readdir(directory)).toEqual(['opportunities.ndjson']);
    storage.log({ ...base, tradeId: 'completed-0' });
    expect(storage.log({
      ...base,
      at: '2026-07-28T10:06:00.000Z',
      decision: 'exit',
      tradeId: 'completed-0',
      exitPriceUsd: 0.013,
      exitReason: 'replay',
      pnlSol: 0.05,
      holdingMinutes: 6,
    })).toBe(false);
    expect(storage.completedCount).toBe(5_001);
  });

  it('replays only the audit without reading, changing, or creating legacy derived files', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-canonical-audit-'));
    const tradeId = 'canonical-completion';
    const entry = { ...base, tradeId };
    const exit = {
      ...entry,
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit' as const,
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    };
    await writeFile(join(directory, 'opportunities.ndjson'), `${JSON.stringify(entry)}\n${JSON.stringify(exit)}\n`);
    const legacy = new Map([
      ['learning-index-v2.json', 'not-even-valid-json'],
      ['completed-trade-ids-v2', 'legacy-id-cache'],
      ['opportunity-derived-state-v3.ndjson', 'legacy-v3-cache'],
      ['.opportunity-derived-state-v3.ndjson.stale.tmp', 'legacy-temp'],
    ]);
    for (const [name, content] of legacy) await writeFile(join(directory, name), content);

    const storage = new OpportunityStorage(directory);
    expect(storage.completedCount).toBe(1);
    for (const [name, content] of legacy) {
      expect(await readFile(join(directory, name), 'utf8')).toBe(content);
    }
    storage.close();

    const emptyDirectory = await mkdtemp(join(tmpdir(), 'opportunity-storage-no-derived-'));
    const empty = new OpportunityStorage(emptyDirectory);
    expect(await readdir(emptyDirectory)).toEqual(['opportunities.ndjson']);
    empty.close();
  });

  it('fails closed and poisons when the retained audit inode is moved after fsync', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-post-append-swap-'));
    const auditPath = join(directory, 'opportunities.ndjson');
    const movedAudit = join(directory, 'moved-after-append.ndjson');
    let armed = false;
    const storage = new OpportunityStorage(directory, {
      auditBoundaryHook: (boundary) => {
        if (armed && boundary === 'after-fsync-before-path-verification') {
          execFileSync('mv', [auditPath, movedAudit]);
        }
      },
    });
    armed = true;

    expect(() => storage.log({ ...base, tradeId: 'moved-after-append' })).toThrow(/audit path.*retained/i);
    expect(() => storage.log(base)).toThrow(/poisoned/i);
    await expect(storage.loadCompletedTrades('v1.0', 0)).rejects.toThrow(/poisoned/i);
    expect(await readFile(movedAudit, 'utf8')).toBe(`${JSON.stringify({ ...base, tradeId: 'moved-after-append' })}\n`);
    await expect(readFile(auditPath, 'utf8')).rejects.toMatchObject({ code: 'ENOENT' });

    const restarted = new OpportunityStorage(directory);
    expect(restarted.completedCount).toBe(0);
  });

  it('recovers the learning index idempotently from an audit tail committed before the index', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-recovery-'));
    const storage = new OpportunityStorage(directory);
    storage.log(base);
    const exit = {
      ...base,
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit' as const,
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    };
    const auditPath = join(directory, 'opportunities.ndjson');
    await writeFile(auditPath, `${await readFile(auditPath, 'utf8')}${JSON.stringify(exit)}\n`);

    const recovered = new OpportunityStorage(directory);
    expect(recovered.completedCount).toBe(1);
    expect(recovered.log(exit)).toBe(false);
    expect(new OpportunityStorage(directory).completedCount).toBe(1);
  });

  it('poisons on an audit symlink swap without appending to either inode', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-audit-symlink-swap-'));
    const outsideDirectory = await mkdtemp(join(tmpdir(), 'opportunity-storage-outside-'));
    const outside = join(outsideDirectory, 'sentinel');
    const auditPath = join(directory, 'opportunities.ndjson');
    const movedAudit = join(directory, 'moved-audit.ndjson');
    await writeFile(outside, 'do-not-touch');
    const storage = new OpportunityStorage(directory);
    await rename(auditPath, movedAudit);
    await symlink(outside, auditPath);

    expect(() => storage.log(base)).toThrow(/audit path.*retained|symbolic link|regular file/i);
    expect(() => storage.log(base)).toThrow(/poisoned/i);
    await expect(readFile(outside, 'utf8')).resolves.toBe('do-not-touch');
    await expect(readFile(movedAudit, 'utf8')).resolves.toBe('');
  });

  it('streams from the one retained audit descriptor instead of a replacement path entry', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-retained-stream-'));
    const auditPath = join(directory, 'opportunities.ndjson');
    const movedAudit = join(directory, 'retained-audit.ndjson');
    const storage = new OpportunityStorage(directory);
    storage.log({ ...base, tradeId: 'retained-record' });
    await rename(auditPath, movedAudit);
    await writeFile(auditPath, `${JSON.stringify({ ...base, tradeId: 'replacement-record' })}\n`);

    const records = await collect(storage.stream());
    expect(records.map((record) => record.tradeId)).toEqual(['retained-record']);
  });

  it('rejects an audit FIFO swapped in after construction without blocking', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-audit-fifo-swap-'));
    const script = `
      import { execFileSync } from 'node:child_process';
      import { rmSync } from 'node:fs';
      import { OpportunityStorage } from './src/opportunity-storage.ts';
      const storage = new OpportunityStorage(${JSON.stringify(directory)});
      rmSync(${JSON.stringify(join(directory, 'opportunities.ndjson'))});
      execFileSync('mkfifo', [${JSON.stringify(join(directory, 'opportunities.ndjson'))}]);
      try { storage.log(${JSON.stringify(base)}); }
      catch (error) { console.log(String(error)); process.exit(0); }
      process.exit(2);
    `;

    const result = spawnSync(join(process.cwd(), 'node_modules/.bin/tsx'), ['--eval', script], {
      cwd: process.cwd(),
      encoding: 'utf8',
      timeout: 1_000,
    });
    expect(result.status, `signal=${result.signal} stderr=${result.stderr}`).toBe(0);
    expect(result.stdout).toMatch(/retained audit file/i);
  });

  it('keeps every later audit operation anchored to the originally opened data directory', async () => {
    const parent = await mkdtemp(join(tmpdir(), 'opportunity-storage-directory-replacement-'));
    const configured = join(parent, 'data');
    const original = join(parent, 'original');
    await mkdir(configured);
    const storage = new OpportunityStorage(configured);
    await rename(configured, original);
    await mkdir(configured);

    storage.log({ ...base, tradeId: 'anchored-directory' });

    await expect(readFile(join(original, 'opportunities.ndjson'), 'utf8')).resolves.toContain('anchored-directory');
    expect(await readdir(configured)).toEqual([]);
    expect(await readdir(original)).toEqual(['opportunities.ndjson']);
  });

  it('has an idempotent explicit close lifecycle that fails closed after disposal', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-close-'));
    const storage = new OpportunityStorage(directory);
    storage.close();
    expect(() => storage.close()).not.toThrow();
    expect(() => storage.log(base)).toThrow(/closed/i);
    expect(() => storage.reconcilePending(new Set())).toThrow(/closed/i);
    await expect(collect(storage.stream())).rejects.toThrow(/closed/i);
    await expect(storage.loadCompletedTrades('v1.0', 0)).rejects.toThrow(/closed/i);
  });

  it('ignores legacy-cache symlinks at construction and runtime without touching their targets', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-legacy-symlinks-'));
    const outside = join(await mkdtemp(join(tmpdir(), 'opportunity-storage-outside-')), 'sentinel');
    await writeFile(outside, 'do-not-touch');
    for (const targetName of [
      'learning-index-v2.json',
      'completed-trade-ids-v2',
      'opportunity-derived-state-v3.ndjson',
      '.opportunity-derived-state-v3.ndjson.legacy.tmp',
    ]) {
      await symlink(outside, join(directory, targetName));
    }

    const storage = new OpportunityStorage(directory);
    const entry = { ...base, tradeId: 'legacy-symlink-ignored' };
    storage.log(entry);
    expect(storage.log({
      ...entry,
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit',
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    })).toBe(true);
    storage.reconcilePending(new Set());
    await expect(readFile(outside, 'utf8')).resolves.toBe('do-not-touch');
  });

  it('fails closed at the absolute in-memory completed-ID cap before appending the completion', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-completed-cap-'));
    const storage = new OpportunityStorage(directory);
    const entry = { ...base, tradeId: 'cap-candidate' };
    storage.log(entry);
    const auditPath = join(directory, 'opportunities.ndjson');
    const auditBefore = await readFile(auditPath, 'utf8');
    const internals = storage as unknown as { completedTradeIds: Set<string> };
    for (let index = 0; index < MAX_COMPLETED_TRADE_IDS; index += 1) {
      internals.completedTradeIds.add(`cap-${index}`);
    }

    expect(() => storage.log({
      ...entry,
      at: '2026-07-28T10:05:00.000Z',
      decision: 'exit',
      exitPriceUsd: 0.012,
      exitReason: 'trailing_stop',
      pnlSol: 0.04,
      holdingMinutes: 5,
    })).toThrow(/trade-ID cap/i);
    await expect(readFile(auditPath, 'utf8')).resolves.toBe(auditBefore);
  });

  it('ignores nonregular legacy caches and stale temps without blocking or deleting content', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-nonregular-legacy-'));
    const completedPath = join(directory, 'completed-trade-ids-v2');
    await mkdir(completedPath);
    await writeFile(join(completedPath, 'existing-marker'), 'preserve-me');
    execFileSync('mkfifo', [join(directory, 'learning-index-v2.json')]);
    const outside = join(await mkdtemp(join(tmpdir(), 'opportunity-storage-outside-')), 'sentinel');
    await writeFile(outside, 'do-not-touch');
    await symlink(outside, join(directory, '.opportunity-derived-state-v3.ndjson.legacy.tmp'));
    const script = `
      import { OpportunityStorage } from './src/opportunity-storage.ts';
      const storage = new OpportunityStorage(${JSON.stringify(directory)});
      storage.close();
    `;

    const result = spawnSync(join(process.cwd(), 'node_modules/.bin/tsx'), ['--eval', script], {
      cwd: process.cwd(),
      encoding: 'utf8',
      timeout: 1_000,
    });
    expect(result.status, `signal=${result.signal} stderr=${result.stderr}`).toBe(0);
    await expect(readFile(join(completedPath, 'existing-marker'), 'utf8')).resolves.toBe('preserve-me');
    await expect(readFile(outside, 'utf8')).resolves.toBe('do-not-touch');
  });

  it('rejects corrupt canonical audit replay without changing any legacy cache', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-corrupt-constructor-'));
    await writeFile(join(directory, 'opportunities.ndjson'), `${JSON.stringify(base)}\nnot-json\n`);
    const legacy = new Map([
      ['completed-trade-ids-v2', 'existing-completed-state'],
      ['learning-index-v2.json', 'existing-index-state'],
      ['opportunity-derived-state-v3.ndjson', 'existing-derived-state'],
    ]);
    for (const [name, content] of legacy) await writeFile(join(directory, name), content);

    expect(() => new OpportunityStorage(directory)).toThrow();
    for (const [name, content] of legacy) {
      await expect(readFile(join(directory, name), 'utf8')).resolves.toBe(content);
    }
  });

  it.each([
    ['a FIFO', (path: string) => execFileSync('mkfifo', [path])],
    ['a symlink', (path: string) => execFileSync('ln', ['-s', '/dev/null', path])],
    ['a device symlink', (path: string) => execFileSync('ln', ['-s', '/dev/zero', path])],
  ])('rejects %s as the configured audit without blocking', async (_label, createTarget) => {
    const directory = await mkdtemp(join(tmpdir(), 'opportunity-storage-audit-target-'));
    createTarget(join(directory, 'opportunities.ndjson'));
    expect(() => new OpportunityStorage(directory)).toThrow(/regular file|symbolic link/i);
  });
});

async function collect(records: AsyncIterable<OpportunityRecord>): Promise<OpportunityRecord[]> {
  const result: OpportunityRecord[] = [];
  for await (const record of records) result.push(record);
  return result;
}
