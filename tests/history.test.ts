import { describe, expect, it } from 'vitest';
import { mkdtemp, open, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BoundedBuffer, loadScannerHistory, readBoundedNdjsonLines } from '../src/history.js';

describe('loadScannerHistory', () => {
  it('does not eagerly allocate maximum-safe history limits when the audit is absent', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-absent-'));
    const history = await loadScannerHistory(directory, {
      recentDecisions: Number.MAX_SAFE_INTEGER,
      closedTrades: Number.MAX_SAFE_INTEGER,
      equityHistory: Number.MAX_SAFE_INTEGER,
    });

    expect(history).toMatchObject({ recentDecisions: [], duplicateSuppressed: 0, closedTrades: [], equityHistory: [] });
    expect(history.firstSeenByPair.size).toBe(0);
  });

  it('rejects a whitespace-only audit record instead of skipping corruption', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-whitespace-'));
    await writeFile(join(directory, 'events.ndjson'), '   \n');
    await expect(loadScannerHistory(directory)).rejects.toThrow(/JSON/i);
  });

  it('rejects an empty audit record instead of skipping corruption', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-empty-'));
    await writeFile(join(directory, 'events.ndjson'), '\n');

    await expect(loadScannerHistory(directory)).rejects.toThrow(/JSON/i);
  });

  it('uses constant-time overwrite while preserving chronological order', () => {
    const buffer = new BoundedBuffer<number>(3);
    const originalShift = Array.prototype.shift;
    let thrown: unknown;
    try {
      Array.prototype.shift = function forbiddenShift() { throw new Error('Array.shift was called'); };
      try {
        for (let value = 0; value < 10; value += 1) buffer.push(value);
      } catch (error) {
        thrown = error;
      }
    } finally {
      Array.prototype.shift = originalShift;
    }

    expect(thrown).toBeUndefined();
    expect(buffer.values()).toEqual([7, 8, 9]);
  });

  it('enforces a per-record UTF-8 byte cap without limiting cumulative bytes', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-record-cap-'));
    const acceptedPath = join(directory, 'accepted.ndjson');
    const exactRecord = 'x'.repeat(64);
    await writeFile(acceptedPath, `${exactRecord}\n${exactRecord}\n`);

    await expect(collect(readBoundedNdjsonLines(acceptedPath, 64))).resolves.toEqual([exactRecord, exactRecord]);

    const rejectedPath = join(directory, 'rejected.ndjson');
    await writeFile(rejectedPath, `${'x'.repeat(63)}é\n`);
    await expect(collect(readBoundedNdjsonLines(rejectedPath, 64))).rejects.toThrow(/record exceeds.*64 byte/i);
  });

  it('does not count a split CRLF delimiter against the record byte cap', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-split-crlf-'));
    const acceptedPath = join(directory, 'accepted.ndjson');
    const maxRecordBytes = 65_535;
    const exactRecord = 'x'.repeat(maxRecordBytes);
    // createReadStream reads 64 KiB chunks: the first chunk ends in CR, the next starts with LF.
    await writeFile(acceptedPath, `${exactRecord}\r\n`);
    await expect(collect(readBoundedNdjsonLines(acceptedPath, maxRecordBytes))).resolves.toEqual([exactRecord]);

    const rejectedPath = join(directory, 'rejected.ndjson');
    await writeFile(rejectedPath, `${'x'.repeat(maxRecordBytes + 1)}\r\n`);
    await expect(collect(readBoundedNdjsonLines(rejectedPath, maxRecordBytes))).rejects.toThrow(/record exceeds.*65535 byte/i);
  });

  it('rejects invalid UTF-8 instead of replacing bytes silently', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-invalid-utf8-'));
    const path = join(directory, 'events.ndjson');
    await writeFile(path, Buffer.from([0xc3, 0x28, 0x0a]));

    await expect(collect(readBoundedNdjsonLines(path, 64))).rejects.toThrow(/invalid UTF-8/i);
  });

  it('rejects an unterminated final audit record', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-partial-record-'));
    const path = join(directory, 'events.ndjson');
    await writeFile(path, JSON.stringify({ type: 'scan_complete', at: '2026-07-30T00:00:00.000Z' }));

    await expect(collect(readBoundedNdjsonLines(path, 1024))).rejects.toThrow(/unterminated final record/i);
  });

  it('replays valid history larger than the former 128 MiB aggregate cap', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-legacy-large-'));
    const path = join(directory, 'events.ndjson');
    const handle = await open(path, 'w');
    try {
      for (let index = 0; index < 130; index += 1) {
        const marker = index === 129 ? [decision('rejected', 'large-ledger-marker')] : [];
        const record = { type: 'scan_complete', at: `2026-07-30T00:00:${String(index % 60).padStart(2, '0')}.000Z`, equityLamports: index, decisions: marker, padding: '' };
        const targetBytes = 1_040_000;
        record.padding = 'x'.repeat(targetBytes - Buffer.byteLength(JSON.stringify(record), 'utf8'));
        const serialized = JSON.stringify(record);
        expect(Buffer.byteLength(serialized, 'utf8')).toBe(targetBytes);
        await handle.write(`${serialized}\n`);
      }
    } finally {
      await handle.close();
    }

    try {
      expect((await stat(path)).size).toBeGreaterThan(128 * 1024 * 1024);
      const history = await loadScannerHistory(directory);
      expect(history.recentDecisions.at(-1)?.pairId).toBe('large-ledger-marker');
      expect(history.equityHistory.at(-1)?.equityLamports).toBe(129);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it('streams one NDJSON pass while retaining only bounded recent records', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-'));
    const records = [
      event('2026-07-27T00:00:00.000Z', 1, [decision('rejected', 'r0')]),
      event('2026-07-27T00:00:01.000Z', 2, [{ type: 'duplicate_suppressed', pairId: 'dup' }]),
      event('2026-07-27T00:00:02.000Z', 3, [decision('paper_exit', 'x1')]),
      event('2026-07-27T00:00:03.000Z', 4, [decision('rejected', 'r1')]),
      event('2026-07-27T00:00:04.000Z', 5, [decision('paper_exit', 'x2')]),
      event('2026-07-27T00:00:05.000Z', 6, [decision('rejected', 'r2')]),
      event('2026-07-27T00:00:06.000Z', 7, [decision('paper_exit', 'x3')]),
    ];
    await writeFile(join(directory, 'events.ndjson'), `${records.map((record) => JSON.stringify(record)).join('\n')}\n`);

    const history = await loadScannerHistory(directory, {
      recentDecisions: 3,
      closedTrades: 2,
      equityHistory: 2,
    });

    expect(history.duplicateSuppressed).toBe(1);
    expect(history.recentDecisions.map((decision) => decision.pairId)).toEqual(['x2', 'r2', 'x3']);
    expect(history.closedTrades.map((decision) => decision.pairId)).toEqual(['x2', 'x3']);
    expect(history.equityHistory).toEqual([
      { at: '2026-07-27T00:00:05.000Z', equityLamports: 6 },
      { at: '2026-07-27T00:00:06.000Z', equityLamports: 7 },
    ]);
    expect(history.recentDecisions.every((decision) => typeof decision.at === 'string')).toBe(true);
  });

  it('does not retain unknown or oversized decision objects', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-decision-bounds-'));
    const at = '2026-07-30T00:00:00.000Z';
    await writeFile(join(directory, 'events.ndjson'), `${JSON.stringify({
      type: 'scan_complete', at, decisions: [
        { type: 'invented', pairId: 'unknown-pair' },
        { type: 'rejected', pairId: 'oversized-pair', padding: 'x'.repeat(17 * 1024) },
      ],
    })}\n`);

    const history = await loadScannerHistory(directory);
    expect(history.recentDecisions).toEqual([]);
  });

  it('bounds identifiers and timestamps before retaining history', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-field-bounds-'));
    const oversizedPairId = 'p'.repeat(257);
    const oversizedTimestamp = 't'.repeat(65);
    await writeFile(join(directory, 'events.ndjson'), `${JSON.stringify({
      type: 'scan_complete', at: oversizedTimestamp, equityLamports: 123,
      snapshots: [{ pairId: oversizedPairId, firstSeenAt: '2026-07-30T00:00:00.000Z' }],
      decisions: [{ type: 'rejected', pairId: oversizedPairId }],
    })}\n`);

    const history = await loadScannerHistory(directory);
    expect(history.equityHistory).toEqual([]);
    expect(history.recentDecisions).toEqual([]);
    expect(history.firstSeenByPair.size).toBe(0);
  });

  it('backfills historical decision timing from its scan snapshot without rewriting history', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'paper-history-'));
    const at = '2026-07-29T13:01:50.000Z';
    await writeFile(join(directory, 'events.ndjson'), `${JSON.stringify({
      type: 'scan_complete', at, equityLamports: 10_000_000_000,
      snapshots: [{
        pairId: 'pump-pair', observedAt: '2026-07-29T13:01:49.500Z',
        firstSeenAt: '2026-07-29T13:01:40.000Z',
        pairCreatedAt: '2026-07-29T12:44:33.000Z',
      }],
      decisions: [{
        type: 'rejected', pairId: 'pump-pair', mint: 'pump-mint', symbol: 'PUMP',
        reason: 'daily_loss_limit', score: 81.83, source: 'birdeye',
      }],
    })}\n`);

    const history = await loadScannerHistory(directory);

    expect(history.recentDecisions).toEqual([expect.objectContaining({
      pairCreatedAt: '2026-07-29T12:44:33.000Z',
      firstSeenAt: '2026-07-29T13:01:40.000Z',
      observedAt: '2026-07-29T13:01:49.500Z',
      evaluatedAt: at,
      detectionDelayMs: 1_027_000,
    })]);
    expect(history.firstSeenByPair.get('pump-pair')).toBe('2026-07-29T13:01:40.000Z');
  });

  it('does not hydrate a provider first-seen timestamp after its persisted evaluation', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'scanner-history-future-first-seen-'));
    await writeFile(join(directory, 'events.ndjson'), `${JSON.stringify({
      type: 'scan_complete', at: '2026-07-29T13:01:50.000Z',
      snapshots: [{ pairId: 'future-pair', firstSeenAt: '2026-07-29T13:02:50.000Z' }],
      decisions: [{
        type: 'rejected', pairId: 'future-pair', mint: 'future-mint', symbol: 'FUT',
        reason: 'future_market_data', firstSeenAt: '2026-07-29T13:02:50.000Z',
      }],
    })}\n`);
    const history = await loadScannerHistory(directory);
    expect(history.firstSeenByPair.get('future-pair')).toBe('2026-07-29T13:01:50.000Z');
    expect(history.recentDecisions[0]?.firstSeenAt).toBe('2026-07-29T13:01:50.000Z');
  });

  it('hydrates first-seen from a rejection when its unsafe snapshot was not persisted', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'scanner-history-rejection-first-seen-'));
    await writeFile(join(directory, 'events.ndjson'), `${JSON.stringify({
      type: 'scan_complete', at: '2026-07-29T13:01:50.000Z', snapshots: [],
      decisions: [{
        type: 'rejected', pairId: 'rejected-pair', mint: 'rejected-mint', symbol: 'REJ',
        reason: 'future_market_data', firstSeenAt: '2026-07-29T13:01:40.000Z',
        evaluatedAt: '2026-07-29T13:01:50.000Z',
      }],
    })}\n`);
    const history = await loadScannerHistory(directory);
    expect(history.firstSeenByPair.get('rejected-pair')).toBe('2026-07-29T13:01:40.000Z');
  });

  it('retains at most 10,000 historical first-seen pairs', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'scanner-history-first-seen-cap-'));
    const records = Array.from({ length: 10_001 }, (_, index) => JSON.stringify({
      type: 'scan_complete', at: '2026-07-29T13:01:50.000Z',
      snapshots: [{ pairId: `pair-${index}`, firstSeenAt: '2026-07-29T13:01:40.000Z' }],
      decisions: [],
    }));
    await writeFile(join(directory, 'events.ndjson'), `${records.join('\n')}\n`);

    const history = await loadScannerHistory(directory);
    expect(history.firstSeenByPair.size).toBe(10_000);
    expect(history.firstSeenByPair.has('pair-0')).toBe(false);
    expect(history.firstSeenByPair.get('pair-10000')).toBe('2026-07-29T13:01:40.000Z');
  });
});

function event(at: string, equityLamports: number, decisions: Array<Record<string, unknown>>) {
  return { type: 'scan_complete', at, equityLamports, decisions };
}

function decision(type: 'rejected' | 'paper_exit', pairId: string) {
  return {
    type,
    pairId,
    mint: `${pairId}-mint`,
    symbol: pairId.toUpperCase(),
    reason: type === 'paper_exit' ? 'stop_loss' : 'liquidity_below_minimum',
    pnlLamports: type === 'paper_exit' ? -1 : undefined,
  };
}

async function collect(lines: AsyncIterable<string>): Promise<string[]> {
  const records: string[] = [];
  for await (const line of lines) records.push(line);
  return records;
}
