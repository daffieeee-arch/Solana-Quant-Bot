import { createReadStream } from 'node:fs';
import { TextDecoder } from 'node:util';
import type { DashboardDecision } from './dashboard.js';
import { MAX_LEDGER_EVENT_BYTES, readCanonicalLedgerRecords } from './ledger.js';

export type ScannerHistory = {
  recentDecisions: DashboardDecision[];
  duplicateSuppressed: number;
  closedTrades: DashboardDecision[];
  equityHistory: Array<{ at: string; equityLamports: number }>;
  firstSeenByPair: ReadonlyMap<string, string>;
};

type HistoryLimits = {
  recentDecisions: number;
  closedTrades: number;
  equityHistory: number;
};

const DEFAULT_LIMITS: HistoryLimits = {
  recentDecisions: 500,
  closedTrades: 200,
  equityHistory: 525600,
};
const MAX_FIRST_SEEN_PAIRS = 10_000;
const MAX_HISTORY_IDENTIFIER_BYTES = 256;
const MAX_HISTORY_TIMESTAMP_BYTES = 64;
const MAX_RETAINED_DECISION_BYTES = 16 * 1024;
const DECISION_TYPES = new Set<DashboardDecision['type']>(['paper_entry', 'paper_exit', 'rejected', 'duplicate_suppressed']);

type SnapshotTiming = { pairId: string; pairCreatedAt?: string; firstSeenAt?: string; observedAt?: string };

export async function loadScannerHistory(
  dataDir: string,
  limits: HistoryLimits = DEFAULT_LIMITS,
): Promise<ScannerHistory> {
  const recentDecisions = new BoundedBuffer<DashboardDecision>(limits.recentDecisions);
  const closedTrades = new BoundedBuffer<DashboardDecision>(limits.closedTrades);
  const equityHistory = new BoundedBuffer<{ at: string; equityLamports: number }>(limits.equityHistory);
  const firstSeenByPair = new Map<string, string>();
  const rememberFirstSeen = (pairId: string, candidate: string | undefined, notAfter?: string): void => {
    if (!isBoundedIdentifier(pairId) || !isBoundedTimestamp(candidate)) return;
    const candidateMs = Date.parse(candidate);
    const notAfterMs = isBoundedTimestamp(notAfter) ? Date.parse(notAfter) : Number.NaN;
    if (Number.isFinite(notAfterMs) && candidateMs > notAfterMs) return;
    const previous = firstSeenByPair.get(pairId);
    if (previous && Number.isFinite(Date.parse(previous)) && Date.parse(previous) <= candidateMs) return;
    if (!previous && firstSeenByPair.size >= MAX_FIRST_SEEN_PAIRS) {
      const oldest = firstSeenByPair.keys().next().value as string | undefined;
      if (oldest) firstSeenByPair.delete(oldest);
    }
    firstSeenByPair.set(pairId, candidate);
  };
  let duplicateSuppressed = 0;

  for await (const event of readHistoryEvents(dataDir)) {
      if (!event || typeof event !== 'object') continue;
      const record = event as { at?: unknown; equityLamports?: unknown; decisions?: unknown; snapshots?: unknown };
      const at = isBoundedTimestamp(record.at) ? record.at : undefined;

      if (at && typeof record.equityLamports === 'number' && Number.isSafeInteger(record.equityLamports)) {
        equityHistory.push({ at, equityLamports: record.equityLamports });
      }
      const snapshotTimings = new Map<string, SnapshotTiming>();
      if (Array.isArray(record.snapshots)) {
        for (const rawSnapshot of record.snapshots) {
          if (!rawSnapshot || typeof rawSnapshot !== 'object') continue;
          const snapshot = rawSnapshot as { pairId?: unknown; pairCreatedAt?: unknown; firstSeenAt?: unknown; observedAt?: unknown };
          if (!isBoundedIdentifier(snapshot.pairId)) continue;
          snapshotTimings.set(snapshot.pairId, {
            pairId: snapshot.pairId,
            ...(isBoundedTimestamp(snapshot.pairCreatedAt) ? { pairCreatedAt: snapshot.pairCreatedAt } : {}),
            ...(isBoundedTimestamp(snapshot.firstSeenAt) ? { firstSeenAt: snapshot.firstSeenAt } : {}),
            ...(isBoundedTimestamp(snapshot.observedAt) ? { observedAt: snapshot.observedAt } : {}),
          });
          const snapshotFirstSeenAt = isBoundedTimestamp(snapshot.firstSeenAt) ? snapshot.firstSeenAt : undefined;
          const validSnapshotFirstSeen = snapshotFirstSeenAt && (!at || Date.parse(snapshotFirstSeenAt) <= Date.parse(at));
          rememberFirstSeen(snapshot.pairId, validSnapshotFirstSeen ? snapshotFirstSeenAt : at, at);
        }
      }
      if (!Array.isArray(record.decisions)) continue;

      for (const rawDecision of record.decisions) {
        if (!rawDecision || typeof rawDecision !== 'object') continue;
        const decision = rawDecision as DashboardDecision;
        if (!DECISION_TYPES.has(decision.type) || !isBoundedIdentifier(decision.pairId)
          || Buffer.byteLength(JSON.stringify(rawDecision), 'utf8') > MAX_RETAINED_DECISION_BYTES) continue;
        const decisionEvaluatedAt = isBoundedTimestamp(decision.evaluatedAt) ? decision.evaluatedAt : at;
        const decisionFirstSeenMs = isBoundedTimestamp(decision.firstSeenAt) ? Date.parse(decision.firstSeenAt) : Number.NaN;
        const decisionEvaluatedMs = decisionEvaluatedAt ? Date.parse(decisionEvaluatedAt) : Number.NaN;
        const validDecisionFirstSeenAt = Number.isFinite(decisionFirstSeenMs)
          && (!Number.isFinite(decisionEvaluatedMs) || decisionFirstSeenMs <= decisionEvaluatedMs)
          ? decision.firstSeenAt : undefined;
        rememberFirstSeen(decision.pairId, validDecisionFirstSeenAt, decisionEvaluatedAt);
        if (decision.type === 'duplicate_suppressed') {
          duplicateSuppressed += 1;
          continue;
        }
        const timing = snapshotTimings.get(decision.pairId);
        const pairCreatedAt = (isBoundedTimestamp(decision.pairCreatedAt) ? decision.pairCreatedAt : undefined) ?? timing?.pairCreatedAt;
        const firstSeenAt = validDecisionFirstSeenAt ?? firstSeenByPair.get(decision.pairId);
        const observedAt = (isBoundedTimestamp(decision.observedAt) ? decision.observedAt : undefined) ?? timing?.observedAt;
        const evaluatedAt = decisionEvaluatedAt;
        const createdMs = pairCreatedAt ? Date.parse(pairCreatedAt) : Number.NaN;
        const firstSeenMs = firstSeenAt ? Date.parse(firstSeenAt) : Number.NaN;
        const detectionDelayMs = decision.detectionDelayMs
          ?? (Number.isFinite(createdMs) && Number.isFinite(firstSeenMs) ? Math.max(0, firstSeenMs - createdMs) : undefined);
        const timestamped = {
          ...decision,
          at,
          ...(pairCreatedAt ? { pairCreatedAt } : {}),
          ...(firstSeenAt ? { firstSeenAt } : {}),
          ...(observedAt ? { observedAt } : {}),
          ...(evaluatedAt ? { evaluatedAt } : {}),
          ...(typeof detectionDelayMs === 'number' ? { detectionDelayMs } : {}),
        };
        recentDecisions.push(timestamped);
        if (decision.type === 'paper_exit') closedTrades.push(timestamped);
      }
  }

  return {
    recentDecisions: recentDecisions.values(),
    duplicateSuppressed,
    closedTrades: closedTrades.values(),
    equityHistory: equityHistory.values(),
    firstSeenByPair: new Map(firstSeenByPair),
  };
}

async function* readHistoryEvents(dataDir: string): AsyncGenerator<unknown> {
  try {
    for await (const line of readBoundedNdjsonLines(`${dataDir}/events.ndjson`, MAX_LEDGER_EVENT_BYTES)) {
      yield JSON.parse(line) as unknown;
    }
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  for await (const record of readCanonicalLedgerRecords(dataDir)) {
    if (record.sequence > 0 && record.event !== undefined) yield record.event;
  }
}

function isBoundedIdentifier(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0
    && Buffer.byteLength(value, 'utf8') <= MAX_HISTORY_IDENTIFIER_BYTES;
}

function isBoundedTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && Buffer.byteLength(value, 'utf8') <= MAX_HISTORY_TIMESTAMP_BYTES
    && Number.isFinite(Date.parse(value));
}

export async function* readBoundedNdjsonLines(path: string, maxRecordBytes: number): AsyncGenerator<string> {
  if (!Number.isSafeInteger(maxRecordBytes) || maxRecordBytes < 1) {
    throw new Error('NDJSON record byte limit must be a positive safe integer');
  }
  const input = createReadStream(path);
  let pending = Buffer.alloc(0);
  try {
    for await (const chunk of input) {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      const data = pending.length === 0 ? bytes : Buffer.concat([pending, bytes]);
      let start = 0;
      for (let newline = data.indexOf(0x0a, start); newline !== -1; newline = data.indexOf(0x0a, start)) {
        const end = newline > start && data[newline - 1] === 0x0d ? newline - 1 : newline;
        const record = data.subarray(start, end);
        assertRecordFits(record.length, maxRecordBytes);
        yield decodeRecord(record);
        start = newline + 1;
      }
      pending = Buffer.from(data.subarray(start));
      // A CR may be the first byte of a CRLF delimiter split across stream chunks.
      // It is not part of the record, so defer counting that one trailing byte.
      const pendingRecordBytes = pending.length > 0 && pending[pending.length - 1] === 0x0d
        ? pending.length - 1
        : pending.length;
      assertRecordFits(pendingRecordBytes, maxRecordBytes);
    }
    if (pending.length > 0) throw new Error('NDJSON audit contains an unterminated final record');
  } finally {
    input.destroy();
  }
}

function assertRecordFits(recordBytes: number, maxRecordBytes: number): void {
  if (recordBytes > maxRecordBytes) {
    throw new Error(`NDJSON record exceeds its ${maxRecordBytes} byte limit`);
  }
}

function decodeRecord(record: Buffer): string {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(record);
  } catch {
    throw new Error('NDJSON record contains invalid UTF-8');
  }
}

function emptyHistory(): ScannerHistory {
  return { recentDecisions: [], duplicateSuppressed: 0, closedTrades: [], equityHistory: [], firstSeenByPair: new Map() };
}

export class BoundedBuffer<T> {
  readonly #items: T[] = [];
  readonly #limit: number;
  #next = 0;

  constructor(limit: number) {
    if (!Number.isSafeInteger(limit) || limit < 0) throw new Error('History limit must be a non-negative safe integer');
    this.#limit = limit;
  }

  push(value: T): void {
    if (this.#limit === 0) return;
    if (this.#items.length < this.#limit) {
      this.#items.push(value);
      return;
    }
    this.#items[this.#next] = value;
    this.#next = (this.#next + 1) % this.#limit;
  }

  values(): T[] {
    if (this.#items.length < this.#limit || this.#next === 0) return [...this.#items];
    return [...this.#items.slice(this.#next), ...this.#items.slice(0, this.#next)];
  }
}