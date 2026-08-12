import {
  closeSync, constants, fstatSync, fsyncSync, lstatSync, mkdirSync, openSync, readSync,
  writeFileSync,
} from 'node:fs';
import { join } from 'node:path';
import { TextDecoder } from 'node:util';
import type { StrategyId } from './strategy-registry.js';

/** Every market opportunity is recorded, even if not traded */
export interface OpportunityRecord {
  learningSchemaVersion?: 2;
  at: string;
  strategyId: StrategyId;
  token: { mint: string; symbol: string; pairId: string };
  dex: string;
  source: string;
  priceUsd: number;
  liquidityUsd?: number;
  volumeM5Usd?: number;
  priceChangeM5Percent?: number;
  buysM5?: number;
  sellsM5?: number;
  marketCap?: number;
  holders?: number;
  decision: 'entry' | 'exit' | 'rejected' | 'duplicate' | 'skipped' | 'too_late';
  rejectReason?: string;
  entryPriceUsd?: number;
  score?: number;
  confidenceScore?: number;
  tradeId?: string;
  exitPriceUsd?: number;
  exitReason?: string;
  pnlSol?: number;
  holdingMinutes?: number;
  highPriceUsd?: number;
  lowPriceUsd?: number;
  latencyMs?: number;
  simulatedSlippageBps?: number;
}

export type OpportunityAuditBoundary = 'after-fsync-before-path-verification';

export type OpportunityStorageOptions = {
  /** Deterministic audit-path race injection for storage verification. */
  auditBoundaryHook?: (boundary: OpportunityAuditBoundary) => void;
};

const MAX_RECENT_COMPLETED = 5_000;
const MAX_PENDING_ENTRIES = 100;
const MAX_PUBLIC_LOAD = 10_000;
export const MAX_OPPORTUNITY_RECORD_BYTES = 64 * 1024;
export const MAX_COMPLETED_TRADE_IDS = 300_000;
const MAX_TIMESTAMP_BYTES = 64;
const MAX_STRATEGY_ID_BYTES = 64;
const MAX_SYMBOL_BYTES = 64;
const MAX_TRADE_ID_BYTES = 128;
const MAX_REQUIRED_STRING_BYTES = 256;
const MAX_REASON_BYTES = 1_024;
const MAX_JSON_DEPTH = 64;
const MAX_JSON_NODES = 10_000;
const RECONCILED_PENDING_REASON = 'pending_reconciled_closed';
const DECISIONS = new Set<OpportunityRecord['decision']>(['entry', 'exit', 'rejected', 'duplicate', 'skipped', 'too_late']);
const NUMERIC_FIELDS = [
  'priceUsd', 'liquidityUsd', 'volumeM5Usd', 'priceChangeM5Percent', 'buysM5', 'sellsM5',
  'marketCap', 'holders', 'entryPriceUsd', 'score', 'confidenceScore', 'exitPriceUsd', 'pnlSol',
  'holdingMinutes', 'highPriceUsd', 'lowPriceUsd', 'latencyMs', 'simulatedSlippageBps',
] as const;

function hasCompleteExitMetadata(record: OpportunityRecord): boolean {
  return typeof record.pnlSol === 'number' && Number.isFinite(record.pnlSol)
    && typeof record.exitPriceUsd === 'number' && Number.isFinite(record.exitPriceUsd) && record.exitPriceUsd > 0
    && typeof record.exitReason === 'string' && record.exitReason.trim().length > 0
    && typeof record.holdingMinutes === 'number' && Number.isFinite(record.holdingMinutes) && record.holdingMinutes >= 0
    && Number.isFinite(Date.parse(record.at));
}

function hasCompleteEntryMetadata(record: OpportunityRecord): boolean {
  return record.learningSchemaVersion === 2 && record.decision === 'entry' && isBoundedString(record.tradeId, MAX_TRADE_ID_BYTES)
    && isBoundedString(record.token?.mint, MAX_REQUIRED_STRING_BYTES)
    && typeof record.entryPriceUsd === 'number' && Number.isFinite(record.entryPriceUsd) && record.entryPriceUsd > 0
    && typeof record.score === 'number' && Number.isFinite(record.score) && record.score >= 0 && record.score <= 100
    && Number.isFinite(Date.parse(record.at));
}

function isBoundedString(value: unknown, maxBytes: number): value is string {
  return typeof value === 'string' && value.trim().length > 0 && Buffer.byteLength(value, 'utf8') <= maxBytes;
}

function assertOptionalBoundedString(value: unknown, maxBytes: number, field: string): void {
  if (value !== undefined && !isBoundedString(value, maxBytes)) {
    throw new Error(`Invalid opportunity record: ${field} must be a non-empty bounded string`);
  }
}

function assertBoundedJsonDepth(value: unknown): void {
  const pending: Array<{ value: unknown; depth: number }> = [{ value, depth: 0 }];
  const deepestVisit = new WeakMap<object, number>();
  let nodes = 0;
  while (pending.length > 0) {
    const current = pending.pop()!;
    nodes += 1;
    if (nodes > MAX_JSON_NODES) {
      throw new Error(`Invalid opportunity record: JSON node count exceeds ${MAX_JSON_NODES}`);
    }
    if (current.depth > MAX_JSON_DEPTH) {
      throw new Error(`Invalid opportunity record: JSON depth exceeds ${MAX_JSON_DEPTH}`);
    }
    if (current.value === null || typeof current.value !== 'object') continue;
    const previousDepth = deepestVisit.get(current.value);
    if (previousDepth !== undefined && previousDepth >= current.depth) continue;
    deepestVisit.set(current.value, current.depth);
    for (const child of Object.values(current.value)) {
      pending.push({ value: child, depth: current.depth + 1 });
    }
  }
}

function cloneAndFreezeRecord(record: OpportunityRecord): OpportunityRecord {
  const serialized = JSON.stringify(record);
  const clone = JSON.parse(serialized) as OpportunityRecord;
  const pending: object[] = [clone];
  while (pending.length > 0) {
    const value = pending.pop()!;
    for (const child of Object.values(value)) {
      if (child !== null && typeof child === 'object') pending.push(child);
    }
    Object.freeze(value);
  }
  return clone;
}

function validateOpportunityRecord(value: unknown, allowHistoricalEmptyFields = false): OpportunityRecord {
  assertBoundedJsonDepth(value);
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Invalid opportunity record: expected an object');
  }
  const record = value as Partial<OpportunityRecord> & Record<string, unknown>;
  const historicalEmptyAt = allowHistoricalEmptyFields && record.at === ''
    && record.learningSchemaVersion === undefined && record.decision === 'entry';
  if (!historicalEmptyAt && (!isBoundedString(record.at, MAX_TIMESTAMP_BYTES) || !Number.isFinite(Date.parse(record.at)))) {
    throw new Error('Invalid opportunity record: at must be a bounded timestamp');
  }
  if (!isBoundedString(record.strategyId, MAX_STRATEGY_ID_BYTES)) {
    throw new Error('Invalid opportunity record: strategyId must be a bounded string');
  }
  if (!record.token || typeof record.token !== 'object' || Array.isArray(record.token)) {
    throw new Error('Invalid opportunity record: token must be an object');
  }
  const token = record.token as Partial<OpportunityRecord['token']>;
  if (!isBoundedString(token.mint, MAX_REQUIRED_STRING_BYTES)
    || !isBoundedString(token.symbol, MAX_SYMBOL_BYTES)
    || !isBoundedString(token.pairId, MAX_REQUIRED_STRING_BYTES)) {
    throw new Error('Invalid opportunity record: token strings are required and bounded');
  }
  const historicalEmptyDex = allowHistoricalEmptyFields && record.dex === ''
    && ((record.learningSchemaVersion === undefined && record.decision === 'entry')
      || (record.learningSchemaVersion === 2 && record.decision === 'exit'));
  if ((!historicalEmptyDex && !isBoundedString(record.dex, MAX_REQUIRED_STRING_BYTES)) || !isBoundedString(record.source, MAX_REQUIRED_STRING_BYTES)) {
    throw new Error('Invalid opportunity record: dex and source are required bounded strings');
  }
  if (!DECISIONS.has(record.decision as OpportunityRecord['decision'])) {
    throw new Error('Invalid opportunity record: unknown decision');
  }
  if (record.learningSchemaVersion !== undefined && record.learningSchemaVersion !== 2) {
    throw new Error('Invalid opportunity record: unknown learning schema version');
  }
  assertOptionalBoundedString(record.rejectReason, MAX_REASON_BYTES, 'rejectReason');
  assertOptionalBoundedString(record.tradeId, MAX_TRADE_ID_BYTES, 'tradeId');
  assertOptionalBoundedString(record.exitReason, MAX_REASON_BYTES, 'exitReason');
  for (const field of NUMERIC_FIELDS) {
    const numeric = record[field];
    if ((field === 'priceUsd' && numeric === undefined)
      || (numeric !== undefined && (typeof numeric !== 'number' || !Number.isFinite(numeric)
        || Math.abs(numeric) > Number.MAX_SAFE_INTEGER))) {
      throw new Error(`Invalid opportunity record: ${field} must be a finite safe number`);
    }
  }
  return cloneAndFreezeRecord(record as unknown as OpportunityRecord);
}

export class OpportunityStorage {
  private readonly filePath: string;
  private readonly dataDirectoryDescriptor: number;
  private readonly auditDescriptor: number;
  private readonly auditBoundaryHook?: (boundary: OpportunityAuditBoundary) => void;
  private readonly pendingEntries = new Map<string, OpportunityRecord>();
  private completedTradeIds = new Set<string>();
  private recentCompletedTrades: OpportunityRecord[] = [];
  private completedCountValue = 0;
  private closed = false;
  private poisonedError: Error | undefined;

  constructor(dataDir: string, options: OpportunityStorageOptions = {}) {
    mkdirSync(dataDir, { recursive: true });
    this.dataDirectoryDescriptor = this.openDataDirectory(dataDir);
    this.auditBoundaryHook = options.auditBoundaryHook;
    const anchoredDirectory = `/proc/self/fd/${this.dataDirectoryDescriptor}`;
    this.filePath = join(anchoredDirectory, 'opportunities.ndjson');
    let auditDescriptor: number | undefined;
    try {
      auditDescriptor = this.openAudit();
      this.auditDescriptor = auditDescriptor;
      this.rebuildFromAuthoritativeAudit();
    } catch (error) {
      if (auditDescriptor !== undefined) closeSync(auditDescriptor);
      closeSync(this.dataDirectoryDescriptor);
      throw error;
    }
  }

  get completedCount(): number {
    this.assertOpen();
    return this.completedCountValue;
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    closeSync(this.auditDescriptor);
    closeSync(this.dataDirectoryDescriptor);
  }

  [Symbol.dispose](): void {
    this.close();
  }

  /** Record an opportunity. Returns true only for a newly committed, matched v2 completion. */
  log(candidate: OpportunityRecord): boolean {
    this.assertOpen();
    validateOpportunityRecord(candidate);
    const serialized = JSON.stringify(candidate);
    if (typeof serialized !== 'string') {
      throw new Error('Invalid opportunity record: serialization did not produce JSON');
    }
    if (Buffer.byteLength(serialized, 'utf8') > MAX_OPPORTUNITY_RECORD_BYTES) {
      throw new Error('Opportunity record exceeds its 64 KiB hard limit');
    }
    const record = validateOpportunityRecord(JSON.parse(serialized) as unknown);

    const tradeId = record.learningSchemaVersion === 2 ? record.tradeId : undefined;
    const entry = tradeId && record.decision === 'exit' ? this.pendingEntries.get(tradeId) : undefined;
    const willComplete = Boolean(entry && hasCompleteExitMetadata(record) && !this.isCompleted(tradeId!));
    if (willComplete && this.completedTradeIds.size >= MAX_COMPLETED_TRADE_IDS) {
      throw new Error(`Completed trade-ID cap of ${MAX_COMPLETED_TRADE_IDS} exceeded`);
    }

    // The audit is authoritative: commit it first. A crash before the derived-index
    // update is recovered by the idempotent constructor replay.
    this.appendAudit(serialized);

    if (tradeId && record.decision === 'entry' && record.pnlSol === undefined
      && hasCompleteEntryMetadata(record) && !this.isCompleted(tradeId)) {
      if (!this.pendingEntries.has(tradeId) && this.pendingEntries.size >= MAX_PENDING_ENTRIES) {
        const oldest = this.pendingEntries.keys().next().value as string | undefined;
        if (oldest) this.pendingEntries.delete(oldest);
      }
      this.pendingEntries.set(tradeId, record);
      return false;
    }

    if (tradeId && record.decision === 'exit' && entry) {
      this.pendingEntries.delete(tradeId);
      if (willComplete) {
        this.completedTradeIds.add(tradeId);
        this.completedCountValue += 1;
        this.recentCompletedTrades.push(this.makeCompletion(entry, record));
        if (this.recentCompletedTrades.length > MAX_RECENT_COMPLETED) this.recentCompletedTrades.shift();
        return true;
      }
    }
    return false;
  }

  /** Stream the append-only audit observations, never used for completion scheduling. */
  async *stream(strategyId?: StrategyId): AsyncGenerator<OpportunityRecord> {
    this.assertOpen();
    for (const record of this.readAuditRecords()) {
      if (!strategyId || record.strategyId === strategyId) yield record;
    }
  }

  async countByDecision(strategyId?: StrategyId): Promise<Record<string, number>> {
    const counts: Record<string, number> = {};
    for await (const record of this.stream(strategyId)) counts[record.decision] = (counts[record.decision] ?? 0) + 1;
    return counts;
  }

  async loadCompletedTrades(strategyId: StrategyId, limit = MAX_RECENT_COMPLETED): Promise<OpportunityRecord[]> {
    this.assertOpen();
    if (!Number.isSafeInteger(limit) || limit <= 0) return [];
    return this.recentCompletedTrades
      .filter((record) => record.strategyId === strategyId)
      .slice(-Math.min(limit, MAX_RECENT_COMPLETED))
      .map(cloneAndFreezeRecord);
  }

  reconcilePending(openTradeIds: ReadonlySet<string>): void {
    this.assertOpen();
    for (const [tradeId, entry] of Array.from(this.pendingEntries)) {
      if (openTradeIds.has(tradeId)) continue;
      const reconciliation = validateOpportunityRecord({
        ...entry,
        decision: 'skipped',
        rejectReason: RECONCILED_PENDING_REASON,
      });
      this.appendAudit(JSON.stringify(reconciliation));
      this.pendingEntries.delete(tradeId);
    }
  }

  async load(limit = MAX_PUBLIC_LOAD): Promise<OpportunityRecord[]> {
    this.assertOpen();
    if (!Number.isSafeInteger(limit) || limit <= 0) {
      throw new Error('Opportunity load limit must be a positive safe integer');
    }
    const cappedLimit = Math.min(limit, MAX_PUBLIC_LOAD);
    const results: OpportunityRecord[] = [];
    for await (const record of this.stream()) {
      results.push(record);
      if (results.length === cappedLimit) break;
    }
    return results;
  }

  private appendAudit(serialized: string): void {
    this.assertOpen();
    try {
      this.verifyAuditPathIdentity();
      writeFileSync(this.auditDescriptor, `${serialized}\n`, { encoding: 'utf8' });
      fsyncSync(this.auditDescriptor);
      this.auditBoundaryHook?.('after-fsync-before-path-verification');
      this.verifyAuditPathIdentity();
    } catch (error) {
      this.poisonedError = error instanceof Error ? error : new Error(String(error));
      throw error;
    }
  }

  private verifyAuditPathIdentity(): void {
    const retained = fstatSync(this.auditDescriptor, { bigint: true });
    let pathEntry;
    try {
      pathEntry = lstatSync(this.filePath, { bigint: true });
    } catch {
      throw new Error('Opportunity audit path no longer resolves to the retained audit file');
    }
    if (!pathEntry.isFile() || pathEntry.dev !== retained.dev || pathEntry.ino !== retained.ino) {
      throw new Error('Opportunity audit path no longer resolves to the retained audit file');
    }
  }

  private openDataDirectory(dataDir: string): number {
    let descriptor: number;
    try {
      descriptor = openSync(dataDir, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ELOOP') {
        throw new Error('Opportunity data directory must not be a symbolic link');
      }
      throw error;
    }
    if (!fstatSync(descriptor).isDirectory()) {
      closeSync(descriptor);
      throw new Error('Opportunity data path must be a directory');
    }
    return descriptor;
  }

  private openAudit(): number {
    let descriptor: number;
    try {
      descriptor = openSync(
        this.filePath,
        constants.O_RDWR | constants.O_APPEND | constants.O_CREAT | constants.O_NOFOLLOW
          | constants.O_NONBLOCK,
        0o600,
      );
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === 'ELOOP') throw new Error('Opportunity audit must be a regular file, not a symbolic link');
      if (code === 'ENXIO' || code === 'EISDIR') throw new Error('Opportunity audit must be a regular file');
      throw error;
    }
    if (!fstatSync(descriptor).isFile()) {
      closeSync(descriptor);
      throw new Error('Opportunity audit must be a regular file');
    }
    return descriptor;
  }

  private assertOpen(): void {
    if (this.closed) throw new Error('Opportunity storage is closed');
    if (this.poisonedError) {
      throw new Error(`Opportunity storage is poisoned: ${this.poisonedError.message}`, { cause: this.poisonedError });
    }
  }

  private rebuildFromAuthoritativeAudit(): void {
    const pending = new Map<string, OpportunityRecord>();
    const recent: OpportunityRecord[] = [];
    const completedIds = new Set<string>();
    let completedCount = 0;

    for (const record of this.readAuditRecords()) {
      completedCount = this.replayRecord(record, pending, recent, completedIds, completedCount);
    }
    this.commitRebuiltState(pending, completedCount, recent, completedIds);
  }

  private *readAuditRecords(): Generator<OpportunityRecord> {
    const auditStats = fstatSync(this.auditDescriptor);
    if (!auditStats.isFile()) throw new Error('Opportunity audit must be a regular file');
    const chunk = Buffer.alloc(64 * 1024);
    let buffered = Buffer.alloc(0);
    let position = 0;
    for (;;) {
      const bytesRead = readSync(this.auditDescriptor, chunk, 0, chunk.length, position);
      if (bytesRead === 0) break;
      position += bytesRead;
      const data = buffered.length === 0
        ? Buffer.from(chunk.subarray(0, bytesRead))
        : Buffer.concat([buffered, chunk.subarray(0, bytesRead)]);
      let start = 0;
      for (let newline = data.indexOf(0x0a, start); newline !== -1; newline = data.indexOf(0x0a, start)) {
        const end = newline > start && data[newline - 1] === 0x0d ? newline - 1 : newline;
        const bytes = data.subarray(start, end);
        if (bytes.length > MAX_OPPORTUNITY_RECORD_BYTES) throw new Error('NDJSON record exceeds its 65536 byte limit');
        let line: string;
        try {
          line = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
        } catch {
          throw new Error('NDJSON record contains invalid UTF-8');
        }
        yield validateOpportunityRecord(JSON.parse(line) as unknown, true);
        start = newline + 1;
      }
      buffered = Buffer.from(data.subarray(start));
      const recordBytes = buffered.length > 0 && buffered[buffered.length - 1] === 0x0d
        ? buffered.length - 1
        : buffered.length;
      if (recordBytes > MAX_OPPORTUNITY_RECORD_BYTES) throw new Error('NDJSON record exceeds its 65536 byte limit');
    }
    if (buffered.length > 0) throw new Error('NDJSON audit contains an unterminated final record');
  }

  private replayRecord(
    record: OpportunityRecord,
    pending: Map<string, OpportunityRecord>,
    recent: OpportunityRecord[],
    completedIds: Set<string>,
    completedCount: number,
  ): number {
    if (record.learningSchemaVersion !== 2 || !record.tradeId) return completedCount;
    const tradeId = record.tradeId;
    if (record.decision === 'skipped' && record.rejectReason === RECONCILED_PENDING_REASON) {
      pending.delete(tradeId);
      return completedCount;
    }
    if (record.decision === 'entry' && record.pnlSol === undefined && hasCompleteEntryMetadata(record)
      && !completedIds.has(tradeId)) {
      if (!pending.has(tradeId) && pending.size >= MAX_PENDING_ENTRIES) {
        const oldest = pending.keys().next().value as string | undefined;
        if (oldest) pending.delete(oldest);
      }
      pending.set(tradeId, record);
      return completedCount;
    }
    if (record.decision !== 'exit') return completedCount;
    const entry = pending.get(tradeId);
    if (!entry) return completedCount;
    pending.delete(tradeId);
    if (!hasCompleteExitMetadata(record) || completedIds.has(tradeId)) return completedCount;
    if (completedIds.size >= MAX_COMPLETED_TRADE_IDS) {
      throw new Error(`Completed trade-ID cap of ${MAX_COMPLETED_TRADE_IDS} exceeded`);
    }
    completedIds.add(tradeId);
    recent.push(this.makeCompletion(entry, record));
    if (recent.length > MAX_RECENT_COMPLETED) recent.shift();
    return completedCount + 1;
  }

  private makeCompletion(entry: OpportunityRecord, exit: OpportunityRecord): OpportunityRecord {
    return cloneAndFreezeRecord({
      ...entry,
      at: exit.at,
      strategyId: entry.strategyId,
      priceUsd: exit.exitPriceUsd!,
      decision: 'exit',
      exitPriceUsd: exit.exitPriceUsd,
      exitReason: exit.exitReason,
      pnlSol: exit.pnlSol,
      holdingMinutes: exit.holdingMinutes,
      highPriceUsd: exit.highPriceUsd,
      lowPriceUsd: exit.lowPriceUsd,
      latencyMs: exit.latencyMs,
      simulatedSlippageBps: exit.simulatedSlippageBps,
    });
  }

  private isCompleted(tradeId: string): boolean {
    return this.completedTradeIds.has(tradeId);
  }

  private applyRuntimeState(
    pendingEntries: Map<string, OpportunityRecord>,
    completedCount: number,
    recentCompletedTrades: OpportunityRecord[],
    completedIds: ReadonlySet<string> = this.completedTradeIds,
  ): void {
    this.pendingEntries.clear();
    pendingEntries.forEach((entry, tradeId) => this.pendingEntries.set(tradeId, cloneAndFreezeRecord(entry)));
    this.completedCountValue = completedCount;
    this.recentCompletedTrades = recentCompletedTrades.map(cloneAndFreezeRecord);
    this.completedTradeIds = new Set(completedIds);
  }

  private commitRebuiltState(
    pendingEntries: Map<string, OpportunityRecord>,
    completedCount: number,
    recentCompletedTrades: OpportunityRecord[],
    completedIds: ReadonlySet<string>,
  ): void {
    this.applyRuntimeState(pendingEntries, completedCount, recentCompletedTrades, completedIds);
  }
}
