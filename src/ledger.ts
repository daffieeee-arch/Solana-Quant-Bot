import { createHash, randomUUID } from 'node:crypto';
import { constants as fsConstants } from 'node:fs';
import type { BigIntStats } from 'node:fs';
import { link, mkdir, open, rename, unlink } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { TextDecoder } from 'node:util';
import { flock } from 'fs-ext';
import { makeTradeId, type Portfolio } from './portfolio.js';

export type PaperLedgerEvent = {
  type: 'paper_entry' | 'paper_exit' | 'rejected' | 'duplicate_suppressed' | 'scan_complete' | 'scan_error' |
    'position_quarantined' | 'administrative_capital_adjustment';
  at: string;
  [key: string]: unknown;
};

export type PaperLedger = {
  schemaVersion: 1;
  portfolio: Portfolio;
  realizedPnlLamports: number;
  updatedAt: string;
};

export type CanonicalLedgerRecord = {
  schemaVersion: 2;
  sequence: number;
  previousRecordSha256: string;
  state: PaperLedger;
  event?: PaperLedgerEvent;
};

export const CANONICAL_LEDGER_FILE = 'paper-ledger-v2.ndjson';
export const CANONICAL_LEDGER_WAL_FILE = 'paper-ledger-v2.wal';
export const MAX_LEDGER_EVENT_BYTES = 1024 * 1024;
export const MAX_LEDGER_STATE_BYTES = 1024 * 1024;
export const MAX_CANONICAL_RECORD_BYTES = 3 * 1024 * 1024;
export const MAX_LEDGER_WAL_BYTES = 4 * 1024 * 1024;
export const GENESIS_PREVIOUS_RECORD_SHA256 = sha256(Buffer.alloc(0));

const WRITER_LOCK_FILE = 'paper-ledger-v2.lock';
const LEGACY_STATE_FILE = 'state.json';
const LEGACY_EVENT_FILE = 'events.ndjson';
const WRITER_LOCK_TIMEOUT_MS = 5_000;
const UTF8_DECODER = new TextDecoder('utf-8', { fatal: true });
const OPEN_READ = fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK;
const OPEN_READ_WRITE = fsConstants.O_RDWR | fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK;

type Identity = { dev: string; ino: string };
type ExpectedCanonical = {
  sequence: number;
  lastRecordSha256: string;
  lastRecordOffset: number;
  lastRecordLength: number;
  size: number;
  ctimeNs: bigint;
  state: PaperLedger;
};
type ValidatedCanonicalPrefix = ExpectedCanonical & { prefixSha256: string };
type CanonicalWalCommon = {
  ownershipTag: string;
  canonicalDevice: string;
  canonicalInode: string;
  expectedOffset: number;
  expectedSequence: number;
  previousRecordSha256: string;
  lineBase64: string;
  lineSha256: string;
};
type CanonicalWalV2 = CanonicalWalCommon & {
  schemaVersion: 2;
  expectedPrefixSha256: string;
};
type CanonicalWalV3 = CanonicalWalCommon & { schemaVersion: 3 };
type CanonicalWal = CanonicalWalV2 | CanonicalWalV3;

export type PaperLedgerStoreOptions = {
  afterInitializationHandlesOpen?: () => void | Promise<void>;
  afterWalPublish?: () => void | Promise<void>;
  beforeAppend?: () => void | Promise<void>;
  duringAppend?: () => void | Promise<void>;
  afterAppend?: () => void | Promise<void>;
  beforeWalRemoval?: () => void | Promise<void>;
  /** Optional test instrumentation; receives bytes read from the canonical descriptor. */
  canonicalReadObserver?: (bytesRead: number) => void;
};

const directoryMutexes = new Map<string, Promise<void>>();

export class PaperLedgerPersistenceError extends Error {
  readonly cause: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = 'PaperLedgerPersistenceError';
    this.cause = cause;
  }
}

export class PaperLedgerStore {
  private directoryHandle?: FileHandle;
  private canonicalHandle?: FileHandle;
  private directoryIdentity?: Identity;
  private canonicalIdentity?: Identity;
  private activeLockIdentity?: Identity;
  private expected?: ExpectedCanonical;
  private initialization?: Promise<PaperLedger>;
  private saveQueue: Promise<void> = Promise.resolve();
  private poisoned?: Error;
  private closing = false;
  private closed = false;
  private closePromise?: Promise<void>;

  constructor(private readonly dataDir: string, private readonly options: PaperLedgerStoreOptions = {}) {}

  async loadOrCreate(initialPortfolio: Portfolio): Promise<PaperLedger> {
    if (this.closing || this.closed) throw new Error('Paper ledger store is closed');
    if (this.expected) return cloneJson(this.expected.state);
    if (!this.initialization) {
      this.initialization = this.initialize(initialPortfolio).catch(async (error) => {
        await this.closeHandles();
        this.initialization = undefined;
        throw error;
      });
    }
    const initialized = await this.initialization;
    const current = this.expected as ExpectedCanonical | undefined;
    return cloneJson(current?.state ?? initialized);
  }

  save(ledger: PaperLedger, event?: PaperLedgerEvent): Promise<void> {
    if (this.closing || this.closed) return Promise.reject(new PaperLedgerPersistenceError('Paper ledger store is closed'));
    const operation = this.saveQueue
      .then(() => this.withWriterLock(() => this.saveNow(ledger, event)))
      .catch((error) => {
        if (error instanceof PaperLedgerPersistenceError) throw error;
        throw new PaperLedgerPersistenceError(`Paper ledger commit failed: ${messageOf(error)}`, error);
      });
    this.saveQueue = operation.catch(() => undefined);
    return operation;
  }

  async close(): Promise<void> {
    if (this.closed) return;
    if (!this.closePromise) {
      this.closing = true;
      this.closePromise = this.finishClose();
    }
    await this.closePromise;
  }

  private async finishClose(): Promise<void> {
    await this.initialization?.catch(() => undefined);
    await this.saveQueue.catch(() => undefined);
    await this.closeHandles();
    this.closed = true;
  }

  async [Symbol.asyncDispose](): Promise<void> {
    await this.close();
  }

  private async initialize(initialPortfolio: Portfolio): Promise<PaperLedger> {
    await mkdir(this.dataDir, { recursive: true });
    this.directoryHandle = await open(this.dataDir, OPEN_READ | fsConstants.O_DIRECTORY);
    const directoryStats = await statRegularOrDirectory(this.directoryHandle, 'Paper ledger data directory', true);
    this.directoryIdentity = identityOf(directoryStats);

    return this.withWriterLock(async () => {
      await this.assertDirectoryPathIdentity();
      let canonical = await this.openOptionalCanonical();
      if (!canonical) {
        await this.assertWalAbsent();
        const state = await this.readValidatedLegacy(initialPortfolio);
        const genesis = serializeCanonicalRecord({
          schemaVersion: 2,
          sequence: 0,
          previousRecordSha256: GENESIS_PREVIOUS_RECORD_SHA256,
          state,
        });
        await this.createCanonicalAtomically(genesis);
        canonical = await this.openRequiredCanonical();
      }
      this.canonicalHandle = canonical.handle;
      this.canonicalIdentity = canonical.identity;
      await this.options.afterInitializationHandlesOpen?.();
      await this.recoverPendingWal();
      this.expected = await validateCanonicalFile(canonical.handle, this.options.canonicalReadObserver);
      await this.assertCanonicalPathIdentity();
      return this.expected.state;
    });
  }

  private async saveNow(ledger: PaperLedger, event?: PaperLedgerEvent): Promise<void> {
    if (this.poisoned) throw this.poisoned;
    if (!this.expected || !this.canonicalHandle || !this.canonicalIdentity) throw new Error('Paper ledger must be loaded before it can be saved');
    validateLedger(ledger, 'Paper ledger');
    if (event !== undefined) validateEvent(event, 'Paper ledger event');
    await this.assertWalAbsent();
    await this.verifyExpectedCanonical();

    const previous = this.expected;
    const record: CanonicalLedgerRecord = {
      schemaVersion: 2,
      sequence: previous.sequence + 1,
      previousRecordSha256: previous.lastRecordSha256,
      state: cloneJson(ledger),
      ...(event === undefined ? {} : { event: cloneJson(event) }),
    };
    const line = serializeCanonicalRecord(record);
    const committedSize = previous.size + line.length;
    if (!Number.isSafeInteger(committedSize)) throw new Error('Canonical paper ledger size is outside the safe integer range');
    const wal: CanonicalWalV3 = {
      schemaVersion: 3,
      ownershipTag: randomUUID(),
      canonicalDevice: this.canonicalIdentity.dev,
      canonicalInode: this.canonicalIdentity.ino,
      expectedOffset: previous.size,
      expectedSequence: record.sequence,
      previousRecordSha256: record.previousRecordSha256,
      lineBase64: line.toString('base64'),
      lineSha256: sha256(line),
    };

    let published = false;
    try {
      await this.publishWal(wal);
      published = true;
      await this.options.afterWalPublish?.();
      await this.verifyExpectedCanonical();
      await this.options.beforeAppend?.();
      await this.verifyExpectedCanonical();
      await this.appendLineWithHook(line, previous.size);
      await this.canonicalHandle.sync();
      const committedMetadata = await this.canonicalHandle.stat({ bigint: true });
      if (!matchesCanonicalSnapshot(committedMetadata, committedSize)) throw new Error('Canonical paper ledger size changed during append');
      const committedCtimeNs = committedMetadata.ctimeNs;
      await this.options.afterAppend?.();
      await this.verifyAppliedWal(wal, line, committedCtimeNs);
      await this.options.beforeWalRemoval?.();
      await this.verifyAppliedWal(wal, line, committedCtimeNs);
      await this.removeOwnedWal(wal);
      await this.verifyAppliedWal(wal, line, committedCtimeNs);
      this.expected = {
        sequence: record.sequence,
        lastRecordSha256: wal.lineSha256,
        lastRecordOffset: previous.size,
        lastRecordLength: line.length,
        size: committedSize,
        ctimeNs: committedCtimeNs,
        state: record.state,
      };
    } catch (error) {
      if (published) {
        this.poisoned = new Error(`Paper ledger persistence is poisoned after WAL publication: ${messageOf(error)}`);
        throw this.poisoned;
      }
      throw error;
    }
  }

  private async appendLineWithHook(line: Buffer, offset: number): Promise<void> {
    const handle = this.canonicalHandle as FileHandle;
    const split = this.options.duringAppend && line.length > 1 ? Math.floor(line.length / 2) : line.length;
    await writeAll(handle, line.subarray(0, split), offset);
    if (split < line.length) {
      const partialMetadata = await handle.stat({ bigint: true });
      const partialSize = offset + split;
      if (!matchesCanonicalSnapshot(partialMetadata, partialSize)) throw new Error('Canonical paper ledger size changed during split append');
      const partialCtimeNs = partialMetadata.ctimeNs;
      await this.options.duringAppend?.();
      await this.assertActiveLockPathIdentity();
      await this.assertDirectoryPathIdentity();
      await this.assertCanonicalPathIdentity();
      const afterHook = await handle.stat({ bigint: true });
      if (!matchesCanonicalSnapshot(afterHook, partialSize, partialCtimeNs)) throw new Error('Canonical paper ledger changed during split append');
      await writeAll(handle, line.subarray(split), offset + split);
    }
  }

  private async verifyExpectedCanonical(): Promise<void> {
    const expected = this.expected as ExpectedCanonical;
    await this.assertActiveLockPathIdentity();
    await this.assertDirectoryPathIdentity();
    await this.assertCanonicalPathIdentity();
    const handle = this.canonicalHandle as FileHandle;
    const metadata = await handle.stat({ bigint: true });
    if (!matchesCanonicalSnapshot(metadata, expected.size, expected.ctimeNs)) throw new Error('Canonical paper ledger changed since load; compare-and-swap rejected stale writer');
    const lastRecord = await readExact(handle, expected.lastRecordLength, expected.lastRecordOffset, this.options.canonicalReadObserver);
    if (sha256(lastRecord) !== expected.lastRecordSha256) throw new Error('Canonical paper ledger changed since load; compare-and-swap rejected stale writer');
    const afterRead = await handle.stat({ bigint: true });
    if (!matchesCanonicalSnapshot(afterRead, expected.size, expected.ctimeNs)) throw new Error('Canonical paper ledger changed since load; compare-and-swap rejected stale writer');
    await this.assertCanonicalPathIdentity();
  }

  private async verifyAppliedWal(wal: CanonicalWal, line: Buffer, committedCtimeNs: bigint): Promise<void> {
    await this.assertActiveLockPathIdentity();
    await this.assertDirectoryPathIdentity();
    await this.assertCanonicalPathIdentity();
    const handle = this.canonicalHandle as FileHandle;
    const exactSize = wal.expectedOffset + line.length;
    const metadata = await handle.stat({ bigint: true });
    if (!matchesCanonicalSnapshot(metadata, exactSize, committedCtimeNs)) throw new Error('Canonical paper ledger changed during append');
    const actual = await readExact(handle, line.length, wal.expectedOffset, this.options.canonicalReadObserver);
    if (!actual.equals(line)) throw new Error('Canonical paper ledger append bytes differ from WAL');
    const afterRead = await handle.stat({ bigint: true });
    if (!matchesCanonicalSnapshot(afterRead, exactSize, committedCtimeNs)) throw new Error('Canonical paper ledger changed during append');
    await this.assertCanonicalPathIdentity();
  }

  private async recoverPendingWal(): Promise<void> {
    const raw = await readOptionalBoundedAnchored(this.anchor(CANONICAL_LEDGER_WAL_FILE), MAX_LEDGER_WAL_BYTES, 'Canonical paper ledger WAL');
    if (!raw) return;
    const wal = parseWal(raw);
    const handle = this.canonicalHandle as FileHandle;
    const identity = this.canonicalIdentity as Identity;
    if (wal.canonicalDevice !== identity.dev || wal.canonicalInode !== identity.ino) throw new Error('Canonical paper ledger WAL targets a different inode');
    await this.assertCanonicalPathIdentity();

    const prefix = await validateCanonicalPrefix(handle, wal.expectedOffset, this.options.canonicalReadObserver);
    if ((wal.schemaVersion === 2 && prefix.prefixSha256 !== wal.expectedPrefixSha256)
      || prefix.sequence + 1 !== wal.expectedSequence
      || prefix.lastRecordSha256 !== wal.previousRecordSha256) {
      throw new Error('Canonical paper ledger prefix diverges from WAL');
    }
    const line = decodeCanonicalBase64(wal.lineBase64, 'canonical line');
    const metadata = await handle.stat({ bigint: true });
    if (!metadata.isFile() || metadata.ctimeNs !== prefix.ctimeNs) throw new Error('Canonical paper ledger changed during WAL recovery');
    const size = numberFromBigInt(metadata.size, 'Canonical paper ledger size');
    if (size < wal.expectedOffset || size > wal.expectedOffset + line.length) throw new Error('Canonical paper ledger tail diverges from WAL');
    const suffixLength = size - wal.expectedOffset;
    if (suffixLength > 0) {
      const suffix = await readExact(handle, suffixLength, wal.expectedOffset, this.options.canonicalReadObserver);
      if (!line.subarray(0, suffixLength).equals(suffix)) throw new Error('Canonical paper ledger tail diverges from WAL');
    }
    const beforeRepair = await handle.stat({ bigint: true });
    if (!matchesCanonicalSnapshot(beforeRepair, size, prefix.ctimeNs)) throw new Error('Canonical paper ledger changed during WAL recovery');
    if (suffixLength !== line.length) {
      if (suffixLength > 0) {
        await handle.truncate(wal.expectedOffset);
        await handle.sync();
      }
      await this.assertCanonicalPathIdentity();
      await writeAll(handle, line, wal.expectedOffset);
    }
    await handle.sync();
    const repairedMetadata = await handle.stat({ bigint: true });
    const repairedSize = wal.expectedOffset + line.length;
    if (!matchesCanonicalSnapshot(repairedMetadata, repairedSize)) throw new Error('Canonical paper ledger recovery size mismatch');
    const repairedCtimeNs = repairedMetadata.ctimeNs;
    await this.verifyWalBytesApplied(wal, line, repairedCtimeNs);
    await this.removeOwnedWal(wal);
    await this.verifyWalBytesApplied(wal, line, repairedCtimeNs);
  }

  private async verifyWalBytesApplied(wal: CanonicalWal, line: Buffer, repairedCtimeNs: bigint): Promise<void> {
    const handle = this.canonicalHandle as FileHandle;
    await this.assertActiveLockPathIdentity();
    await this.assertDirectoryPathIdentity();
    await this.assertCanonicalPathIdentity();
    const exactSize = wal.expectedOffset + line.length;
    const metadata = await handle.stat({ bigint: true });
    if (!matchesCanonicalSnapshot(metadata, exactSize, repairedCtimeNs)) throw new Error('Canonical paper ledger recovery changed after repair');
    if (!(await readExact(handle, line.length, wal.expectedOffset, this.options.canonicalReadObserver)).equals(line)) throw new Error('Canonical paper ledger recovery line mismatch');
    const afterRead = await handle.stat({ bigint: true });
    if (!matchesCanonicalSnapshot(afterRead, exactSize, repairedCtimeNs)) throw new Error('Canonical paper ledger recovery changed after repair');
  }

  private async publishWal(wal: CanonicalWal): Promise<void> {
    const bytes = Buffer.from(`${JSON.stringify(wal)}\n`, 'utf8');
    if (bytes.length > MAX_LEDGER_WAL_BYTES) throw new Error('Canonical paper ledger WAL exceeds its byte limit');
    const temporaryName = `${CANONICAL_LEDGER_WAL_FILE}.${wal.ownershipTag}.tmp`;
    const temporaryPath = this.anchor(temporaryName);
    const walPath = this.anchor(CANONICAL_LEDGER_WAL_FILE);
    let temporaryExists = false;
    try {
      await writeDurableExclusiveFile(temporaryPath, bytes);
      temporaryExists = true;
      await link(temporaryPath, walPath);
      await unlink(temporaryPath);
      temporaryExists = false;
      await (this.directoryHandle as FileHandle).sync();
    } finally {
      if (temporaryExists) await unlink(temporaryPath).catch(() => undefined);
    }
  }

  private async removeOwnedWal(wal: CanonicalWal): Promise<void> {
    const walPath = this.anchor(CANONICAL_LEDGER_WAL_FILE);
    const raw = await readBoundedAnchored(walPath, MAX_LEDGER_WAL_BYTES, 'Canonical paper ledger WAL');
    if (!sameWal(parseWal(raw), wal)) throw new Error('Canonical paper ledger WAL ownership changed before removal');
    const claimName = `${CANONICAL_LEDGER_WAL_FILE}.removal.${wal.ownershipTag}.${randomUUID()}`;
    const claimPath = this.anchor(claimName);
    await rename(walPath, claimPath);
    let claimed: CanonicalWal;
    try {
      claimed = parseWal(await readBoundedAnchored(claimPath, MAX_LEDGER_WAL_BYTES, 'Claimed canonical paper ledger WAL'));
    } catch (error) {
      await restoreClaim(claimPath, walPath);
      throw error;
    }
    if (!sameWal(claimed, wal)) {
      await restoreClaim(claimPath, walPath);
      throw new Error('Canonical paper ledger WAL ownership changed before removal');
    }
    await unlink(claimPath);
    await (this.directoryHandle as FileHandle).sync();
  }

  private async assertWalAbsent(): Promise<void> {
    try {
      const handle = await open(this.anchor(CANONICAL_LEDGER_WAL_FILE), OPEN_READ);
      try {
        await statRegularOrDirectory(handle, 'Canonical paper ledger WAL', false);
      } finally {
        await handle.close();
      }
      throw new Error('Canonical paper ledger has a pending WAL');
    } catch (error) {
      if (isMissingFile(error)) return;
      throw error;
    }
  }

  private async readValidatedLegacy(initialPortfolio: Portfolio): Promise<PaperLedger> {
    let state: PaperLedger;
    const rawState = await readOptionalBoundedAnchored(this.anchor(LEGACY_STATE_FILE), MAX_LEDGER_STATE_BYTES, 'Legacy paper ledger state');
    if (rawState) state = parseLedgerForLoad(rawState, 'Legacy paper ledger state');
    else {
      state = { schemaVersion: 1, portfolio: initialPortfolio, realizedPnlLamports: 0, updatedAt: initialPortfolio.createdAt };
      validateLedger(state, 'Initial paper ledger');
      state = cloneJson(state);
    }

    let legacyEvents: FileHandle | undefined;
    try {
      legacyEvents = await open(this.anchor(LEGACY_EVENT_FILE), OPEN_READ);
    } catch (error) {
      if (!isMissingFile(error)) throw error;
    }
    if (legacyEvents) {
      try {
        await statRegularOrDirectory(legacyEvents, 'Legacy paper ledger audit', false);
        for await (const record of readBoundedNdjsonHandle(legacyEvents, MAX_LEDGER_EVENT_BYTES, 'Legacy paper ledger audit')) {
          let parsed: unknown;
          try {
            parsed = JSON.parse(record.text) as unknown;
          } catch (error) {
            throw new Error(`Legacy paper ledger audit contains invalid JSON: ${messageOf(error)}`);
          }
          validateEvent(parsed, 'Legacy paper ledger audit event');
        }
      } finally {
        await legacyEvents.close();
      }
    }
    return state;
  }

  private async createCanonicalAtomically(line: Buffer): Promise<void> {
    const name = `${CANONICAL_LEDGER_FILE}.${randomUUID()}.tmp`;
    const temporaryPath = this.anchor(name);
    const canonicalPath = this.anchor(CANONICAL_LEDGER_FILE);
    let exists = false;
    try {
      await writeDurableExclusiveFile(temporaryPath, line);
      exists = true;
      await link(temporaryPath, canonicalPath);
      await unlink(temporaryPath);
      exists = false;
      await (this.directoryHandle as FileHandle).sync();
    } finally {
      if (exists) await unlink(temporaryPath).catch(() => undefined);
    }
  }

  private async openOptionalCanonical(): Promise<{ handle: FileHandle; identity: Identity } | undefined> {
    try {
      return await this.openRequiredCanonical();
    } catch (error) {
      if (isMissingFile(error)) return undefined;
      throw error;
    }
  }

  private async openRequiredCanonical(): Promise<{ handle: FileHandle; identity: Identity }> {
    const handle = await open(this.anchor(CANONICAL_LEDGER_FILE), OPEN_READ_WRITE);
    try {
      const metadata = await statRegularOrDirectory(handle, 'Canonical paper ledger', false);
      return { handle, identity: identityOf(metadata) };
    } catch (error) {
      await handle.close();
      throw error;
    }
  }

  private async assertDirectoryPathIdentity(): Promise<void> {
    const probe = await open(this.dataDir, OPEN_READ | fsConstants.O_DIRECTORY);
    try {
      const metadata = await statRegularOrDirectory(probe, 'Paper ledger data directory', true);
      if (!sameIdentity(identityOf(metadata), this.directoryIdentity as Identity)) throw new Error('Paper ledger data directory path was replaced');
    } finally {
      await probe.close();
    }
  }

  private async assertCanonicalPathIdentity(): Promise<void> {
    const probe = await open(this.anchor(CANONICAL_LEDGER_FILE), OPEN_READ);
    try {
      const metadata = await statRegularOrDirectory(probe, 'Canonical paper ledger', false);
      if (!sameIdentity(identityOf(metadata), this.canonicalIdentity as Identity)) throw new Error('Canonical paper ledger path was replaced');
    } finally {
      await probe.close();
    }
  }

  private async assertActiveLockPathIdentity(): Promise<void> {
    if (!this.activeLockIdentity) throw new Error('Paper ledger kernel lock is not active');
    const probe = await open(this.anchor(WRITER_LOCK_FILE), OPEN_READ);
    try {
      const metadata = await statRegularOrDirectory(probe, 'Paper ledger writer lock', false);
      if (!sameIdentity(identityOf(metadata), this.activeLockIdentity)) throw new Error('Paper ledger writer lock path was replaced');
    } finally {
      await probe.close();
    }
  }

  private async withWriterLock<T>(operation: () => Promise<T>): Promise<T> {
    if (!this.directoryHandle || !this.directoryIdentity) throw new Error('Paper ledger data directory is not open');
    const key = `${this.directoryIdentity.dev}:${this.directoryIdentity.ino}`;
    const previous = directoryMutexes.get(key) ?? Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const tail = previous.catch(() => undefined).then(() => gate);
    directoryMutexes.set(key, tail);
    await previous.catch(() => undefined);
    let lockHandle: FileHandle | undefined;
    try {
      lockHandle = await acquireWriterLock(this.anchor(WRITER_LOCK_FILE));
      this.activeLockIdentity = identityOf(await statRegularOrDirectory(lockHandle, 'Paper ledger writer lock', false));
      await this.assertActiveLockPathIdentity();
      return await operation();
    } finally {
      try {
        if (lockHandle) await releaseWriterLock(lockHandle);
      } finally {
        this.activeLockIdentity = undefined;
        release();
        if (directoryMutexes.get(key) === tail) directoryMutexes.delete(key);
      }
    }
  }

  private anchor(name: string): string {
    if (!this.directoryHandle) throw new Error('Paper ledger data directory is not open');
    return `/proc/self/fd/${this.directoryHandle.fd}/${name}`;
  }

  private async closeHandles(): Promise<void> {
    const canonical = this.canonicalHandle;
    const directory = this.directoryHandle;
    this.canonicalHandle = undefined;
    this.directoryHandle = undefined;
    await canonical?.close().catch(() => undefined);
    await directory?.close().catch(() => undefined);
  }
}

export async function* readCanonicalLedgerRecords(dataDir: string): AsyncGenerator<CanonicalLedgerRecord> {
  const directory = await open(dataDir, OPEN_READ | fsConstants.O_DIRECTORY);
  try {
    await statRegularOrDirectory(directory, 'Paper ledger data directory', true);
    let canonical: FileHandle;
    try {
      canonical = await open(`/proc/self/fd/${directory.fd}/${CANONICAL_LEDGER_FILE}`, OPEN_READ);
    } catch (error) {
      if (isMissingFile(error)) return;
      throw error;
    }
    try {
      await statRegularOrDirectory(canonical, 'Canonical paper ledger', false);
      let sequence = -1;
      let previous = GENESIS_PREVIOUS_RECORD_SHA256;
      for await (const raw of readBoundedNdjsonHandle(canonical, MAX_CANONICAL_RECORD_BYTES, 'Canonical paper ledger')) {
        const record = parseCanonicalRecord(raw.bytes, 'Canonical paper ledger record');
        if (record.sequence !== sequence + 1) throw new Error('Canonical paper ledger sequence is not contiguous');
        if (record.previousRecordSha256 !== previous) throw new Error('Canonical paper ledger hash chain is invalid');
        if (record.sequence === 0 && record.event !== undefined) throw new Error('Canonical paper ledger genesis record must not contain an event');
        sequence = record.sequence;
        previous = sha256(raw.line);
        yield record;
      }
      if (sequence < 0) throw new Error('Canonical paper ledger is empty');
    } finally {
      await canonical.close();
    }
  } finally {
    await directory.close();
  }
}

export async function* readBoundedNdjsonHandle(
  handle: FileHandle,
  maxRecordBytes: number,
  label: string,
  maxFileBytes = Number.MAX_SAFE_INTEGER,
): AsyncGenerator<{ text: string; bytes: Buffer; line: Buffer; offset: number }> {
  if (!Number.isSafeInteger(maxRecordBytes) || maxRecordBytes < 1) throw new Error('NDJSON record byte limit must be a positive safe integer');
  const metadata = await handle.stat({ bigint: true });
  if (!metadata.isFile()) throw new Error(`${label} path is not a regular file`);
  if (metadata.size > BigInt(maxFileBytes)) throw new Error(`${label} exceeds its file byte limit`);
  const chunk = Buffer.allocUnsafe(64 * 1024);
  let pending = Buffer.alloc(0);
  let position = 0;
  let recordOffset = 0;
  while (true) {
    const { bytesRead } = await handle.read(chunk, 0, chunk.length, position);
    if (bytesRead === 0) break;
    position += bytesRead;
    if (position > maxFileBytes) throw new Error(`${label} exceeds its file byte limit`);
    const data = pending.length === 0 ? Buffer.from(chunk.subarray(0, bytesRead)) : Buffer.concat([pending, chunk.subarray(0, bytesRead)]);
    let start = 0;
    for (let newline = data.indexOf(0x0a, start); newline !== -1; newline = data.indexOf(0x0a, start)) {
      const contentEnd = newline > start && data[newline - 1] === 0x0d ? newline - 1 : newline;
      const record = Buffer.from(data.subarray(start, contentEnd));
      if (record.length > maxRecordBytes) throw new Error(`${label} record exceeds its ${maxRecordBytes} byte limit`);
      const line = Buffer.from(data.subarray(start, newline + 1));
      yield { text: decodeUtf8(record, `${label} record`), bytes: record, line, offset: recordOffset };
      recordOffset += line.length;
      start = newline + 1;
    }
    pending = Buffer.from(data.subarray(start));
    const countable = pending.length > 0 && pending.at(-1) === 0x0d ? pending.length - 1 : pending.length;
    if (countable > maxRecordBytes) throw new Error(`${label} record exceeds its ${maxRecordBytes} byte limit`);
  }
  if (pending.length > 0) throw new Error(`${label} contains an unterminated final record`);
}

function serializeCanonicalRecord(record: CanonicalLedgerRecord): Buffer {
  validateCanonicalRecord(record, 'Canonical paper ledger record');
  const stateBytes = Buffer.byteLength(JSON.stringify(record.state), 'utf8');
  if (stateBytes > MAX_LEDGER_STATE_BYTES) throw new Error('Paper ledger state exceeds its byte limit');
  if (record.event !== undefined && Buffer.byteLength(JSON.stringify(record.event), 'utf8') > MAX_LEDGER_EVENT_BYTES) {
    throw new Error('Ledger event exceeds its 1 MiB hard limit');
  }
  const line = Buffer.from(`${JSON.stringify(record)}\n`, 'utf8');
  if (line.length - 1 > MAX_CANONICAL_RECORD_BYTES) throw new Error('Canonical paper ledger record exceeds its byte limit');
  return line;
}

async function validateCanonicalFile(handle: FileHandle, observer?: (bytesRead: number) => void): Promise<ExpectedCanonical> {
  const { prefixSha256: _prefixSha256, ...expected } = await validateCanonicalPrefix(handle, undefined, observer);
  return expected;
}

async function validateCanonicalPrefix(
  handle: FileHandle,
  exactLength?: number,
  observer?: (bytesRead: number) => void,
): Promise<ValidatedCanonicalPrefix> {
  const metadata = await handle.stat({ bigint: true });
  if (!metadata.isFile()) throw new Error('Canonical paper ledger path is not a regular file');
  const size = exactLength ?? numberFromBigInt(metadata.size, 'Canonical paper ledger size');
  if (!Number.isSafeInteger(size) || size < 0 || BigInt(size) > metadata.size) {
    throw new Error('Canonical paper ledger prefix size is invalid');
  }
  const hash = createHash('sha256');
  let sequence = -1;
  let previous = GENESIS_PREVIOUS_RECORD_SHA256;
  let state: PaperLedger | undefined;
  let consumed = 0;
  let lastRecordOffset = 0;
  let lastRecordLength = 0;
  for await (const raw of readBoundedNdjsonPrefix(handle, size, observer)) {
    const record = parseCanonicalRecord(raw.bytes, 'Canonical paper ledger record');
    if (record.sequence !== sequence + 1) throw new Error('Canonical paper ledger sequence is not contiguous');
    if (record.previousRecordSha256 !== previous) throw new Error('Canonical paper ledger hash chain is invalid');
    if (record.sequence === 0 && record.event !== undefined) throw new Error('Canonical paper ledger genesis record must not contain an event');
    sequence = record.sequence;
    previous = sha256(raw.line);
    state = record.state;
    lastRecordOffset = consumed;
    lastRecordLength = raw.line.length;
    consumed += raw.line.length;
    hash.update(raw.line);
  }
  if (consumed !== size) throw new Error('Canonical paper ledger prefix does not end on a record boundary');
  if (sequence < 0 || !state) throw new Error('Canonical paper ledger is empty');
  const afterValidation = await handle.stat({ bigint: true });
  if (!afterValidation.isFile()
    || !sameIdentity(identityOf(afterValidation), identityOf(metadata))
    || afterValidation.size !== metadata.size
    || afterValidation.ctimeNs !== metadata.ctimeNs) {
    throw new Error('Canonical paper ledger changed while validating');
  }
  return {
    sequence,
    lastRecordSha256: previous,
    lastRecordOffset,
    lastRecordLength,
    prefixSha256: hash.digest('hex'),
    size,
    ctimeNs: metadata.ctimeNs,
    state,
  };
}

async function* readBoundedNdjsonPrefix(handle: FileHandle, length: number, observer?: (bytesRead: number) => void): AsyncGenerator<{ bytes: Buffer; line: Buffer }> {
  const chunk = Buffer.allocUnsafe(64 * 1024);
  let pending = Buffer.alloc(0);
  let position = 0;
  while (position < length) {
    const requested = Math.min(chunk.length, length - position);
    const { bytesRead } = await handle.read(chunk, 0, requested, position);
    if (bytesRead <= 0) throw new Error('Canonical paper ledger changed while validating');
    observer?.(bytesRead);
    position += bytesRead;
    const data = pending.length === 0 ? Buffer.from(chunk.subarray(0, bytesRead)) : Buffer.concat([pending, chunk.subarray(0, bytesRead)]);
    let start = 0;
    for (let newline = data.indexOf(0x0a, start); newline !== -1; newline = data.indexOf(0x0a, start)) {
      const contentEnd = newline > start && data[newline - 1] === 0x0d ? newline - 1 : newline;
      const bytes = Buffer.from(data.subarray(start, contentEnd));
      if (bytes.length > MAX_CANONICAL_RECORD_BYTES) throw new Error(`Canonical paper ledger record exceeds its ${MAX_CANONICAL_RECORD_BYTES} byte limit`);
      const line = Buffer.from(data.subarray(start, newline + 1));
      decodeUtf8(bytes, 'Canonical paper ledger record');
      yield { bytes, line };
      start = newline + 1;
    }
    pending = Buffer.from(data.subarray(start));
    const countable = pending.length > 0 && pending.at(-1) === 0x0d ? pending.length - 1 : pending.length;
    if (countable > MAX_CANONICAL_RECORD_BYTES) throw new Error(`Canonical paper ledger record exceeds its ${MAX_CANONICAL_RECORD_BYTES} byte limit`);
  }
  if (pending.length > 0) throw new Error('Canonical paper ledger contains an unterminated final record');
}

function parseCanonicalRecord(raw: Buffer, label: string): CanonicalLedgerRecord {
  let value: unknown;
  try {
    value = JSON.parse(decodeUtf8(raw, label)) as unknown;
  } catch (error) {
    throw new Error(`${label} contains invalid JSON: ${messageOf(error)}`);
  }
  validateCanonicalRecord(value, label);
  return value;
}

function validateCanonicalRecord(value: unknown, label: string): asserts value is CanonicalLedgerRecord {
  if (!isRecord(value)) throw new Error(`${label} must be an object`);
  const allowed = new Set(['schemaVersion', 'sequence', 'previousRecordSha256', 'state', 'event']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new Error(`${label} contains unknown fields`);
  if (value.schemaVersion !== 2 || !isNonNegativeSafeInteger(value.sequence) || !isSha256(value.previousRecordSha256)) {
    throw new Error(`${label} has invalid schema, sequence, or previous hash`);
  }
  validateLedger(value.state, `${label} state`);
  if (Object.hasOwn(value, 'event')) validateEvent(value.event, `${label} event`);
}

function parseWal(raw: Buffer): CanonicalWal {
  let value: unknown;
  try {
    value = JSON.parse(decodeUtf8(raw, 'Canonical paper ledger WAL')) as unknown;
  } catch (error) {
    throw new Error(`Canonical paper ledger WAL is invalid: ${messageOf(error)}`);
  }
  if (!isRecord(value)) throw new Error('Canonical paper ledger WAL is invalid');
  const commonKeys = ['schemaVersion', 'ownershipTag', 'canonicalDevice', 'canonicalInode', 'expectedOffset', 'expectedSequence', 'previousRecordSha256', 'lineBase64', 'lineSha256'];
  const keys = value.schemaVersion === 2 ? [...commonKeys, 'expectedPrefixSha256'] : commonKeys;
  if ((value.schemaVersion !== 2 && value.schemaVersion !== 3)
    || Object.keys(value).length !== keys.length
    || keys.some((key) => !Object.hasOwn(value, key))) {
    throw new Error('Canonical paper ledger WAL has invalid fields');
  }
  if (!isUuid(value.ownershipTag) || typeof value.canonicalDevice !== 'string' || typeof value.canonicalInode !== 'string'
    || !isNonNegativeSafeInteger(value.expectedOffset)
    || !isPositiveSafeInteger(value.expectedSequence)
    || (value.schemaVersion === 2 && !isSha256(value.expectedPrefixSha256))
    || !isSha256(value.previousRecordSha256) || typeof value.lineBase64 !== 'string' || !isSha256(value.lineSha256)) {
    throw new Error('Canonical paper ledger WAL has invalid metadata');
  }
  const line = decodeCanonicalBase64(value.lineBase64, 'canonical line');
  if (line.length < 2 || line.at(-1) !== 0x0a || line.length - 1 > MAX_CANONICAL_RECORD_BYTES || sha256(line) !== value.lineSha256) {
    throw new Error('Canonical paper ledger WAL has invalid line bytes');
  }
  const record = parseCanonicalRecord(line.subarray(0, -1), 'Canonical paper ledger WAL record');
  if (record.sequence !== value.expectedSequence || record.previousRecordSha256 !== value.previousRecordSha256) {
    throw new Error('Canonical paper ledger WAL record metadata mismatch');
  }
  return value as CanonicalWal;
}

function sameWal(first: CanonicalWal, second: CanonicalWal): boolean {
  return JSON.stringify(first) === JSON.stringify(second);
}

async function restoreClaim(claimPath: string, walPath: string): Promise<void> {
  try {
    await link(claimPath, walPath);
    await unlink(claimPath);
  } catch (error) {
    throw new Error(`Foreign canonical paper ledger WAL was preserved at ${claimPath}: ${messageOf(error)}`);
  }
}

async function writeDurableExclusiveFile(path: string, contents: Buffer): Promise<void> {
  const handle = await open(path, fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK, 0o600);
  try {
    await statRegularOrDirectory(handle, 'Paper ledger temporary file', false);
    await writeAll(handle, contents, 0);
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function acquireWriterLock(path: string): Promise<FileHandle> {
  const deadline = Date.now() + WRITER_LOCK_TIMEOUT_MS;
  const handle = await open(path, fsConstants.O_RDWR | fsConstants.O_CREAT | fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK, 0o600);
  try {
    await statRegularOrDirectory(handle, 'Paper ledger writer lock', false);
    while (true) {
      try {
        await flockAsync(handle.fd, 'exnb');
        return handle;
      } catch (error) {
        if (!isLockBusy(error)) throw error;
        if (Date.now() >= deadline) throw new Error('Paper ledger writer lock is held');
        await delay(10);
      }
    }
  } catch (error) {
    await handle.close().catch(() => undefined);
    throw error;
  }
}

async function releaseWriterLock(handle: FileHandle): Promise<void> {
  try {
    await flockAsync(handle.fd, 'un');
  } finally {
    await handle.close();
  }
}

function flockAsync(fd: number, operation: 'exnb' | 'un'): Promise<void> {
  return new Promise((resolve, reject) => flock(fd, operation, (error) => error ? reject(error) : resolve()));
}

async function statRegularOrDirectory(handle: FileHandle, label: string, directory: boolean): Promise<BigIntStats> {
  const metadata = await handle.stat({ bigint: true });
  if (directory ? !metadata.isDirectory() : !metadata.isFile()) throw new Error(`${label} path is not a ${directory ? 'directory' : 'regular file'}`);
  return metadata;
}

function identityOf(metadata: BigIntStats): Identity {
  return { dev: metadata.dev.toString(), ino: metadata.ino.toString() };
}

function sameIdentity(first: Identity, second: Identity): boolean {
  return first.dev === second.dev && first.ino === second.ino;
}

function matchesCanonicalSnapshot(metadata: BigIntStats, size: number, ctimeNs?: bigint): boolean {
  return metadata.isFile()
    && metadata.size === BigInt(size)
    && (ctimeNs === undefined || metadata.ctimeNs === ctimeNs);
}

async function readBoundedAnchored(path: string, limit: number, label: string): Promise<Buffer> {
  const handle = await open(path, OPEN_READ);
  try {
    const metadata = await statRegularOrDirectory(handle, label, false);
    if (metadata.size > BigInt(limit)) throw new Error(`${label} exceeds its byte limit`);
    const size = numberFromBigInt(metadata.size, `${label} size`);
    const raw = Buffer.allocUnsafe(size + 1);
    let read = 0;
    while (read < raw.length) {
      const { bytesRead } = await handle.read(raw, read, raw.length - read, read);
      if (bytesRead === 0) break;
      read += bytesRead;
    }
    if (read > limit) throw new Error(`${label} exceeds its byte limit`);
    return raw.subarray(0, read);
  } finally {
    await handle.close();
  }
}

async function readOptionalBoundedAnchored(path: string, limit: number, label: string): Promise<Buffer | undefined> {
  try {
    return await readBoundedAnchored(path, limit, label);
  } catch (error) {
    if (isMissingFile(error)) return undefined;
    throw error;
  }
}

async function readExact(handle: FileHandle, length: number, position: number, observer?: (bytesRead: number) => void): Promise<Buffer> {
  const output = Buffer.allocUnsafe(length);
  let read = 0;
  while (read < length) {
    const result = await handle.read(output, read, length - read, position + read);
    if (result.bytesRead <= 0) throw new Error('Paper ledger changed while reading exact bytes');
    observer?.(result.bytesRead);
    read += result.bytesRead;
  }
  return output;
}

async function writeAll(handle: FileHandle, contents: Buffer, position: number): Promise<void> {
  let written = 0;
  while (written < contents.length) {
    const result = await handle.write(contents, written, contents.length - written, position + written);
    if (result.bytesWritten <= 0) throw new Error('Paper ledger write made no progress');
    written += result.bytesWritten;
  }
}

function sha256(value: Buffer): string {
  return createHash('sha256').update(value).digest('hex');
}

function parseLedgerForLoad(raw: Buffer, label: string): PaperLedger {
  let parsed: unknown;
  try {
    parsed = JSON.parse(decodeUtf8(raw, label)) as unknown;
  } catch (error) {
    throw new Error(`${label} contains invalid JSON: ${messageOf(error)}`);
  }
  try {
    validateLedger(parsed, label);
    return cloneJson(parsed);
  } catch (strictError) {
    if (!isLegacyLedger(parsed)) throw strictError;
    const migrated = { ...parsed, portfolio: migratePortfolio(parsed.portfolio, parsed.updatedAt) };
    validateLedger(migrated, label);
    return cloneJson(migrated);
  }
}

function validateLedger(value: unknown, label: string): asserts value is PaperLedger {
  if (!isRecord(value) || value.schemaVersion !== 1 || !isSafeInteger(value.realizedPnlLamports) || !isTimestamp(value.updatedAt)) {
    throw new Error(`${label} has an unsupported or invalid ledger schema`);
  }
  validatePortfolio(value.portfolio, label);
  validateJsonValue(value, label);
  if (Buffer.byteLength(JSON.stringify(value), 'utf8') > MAX_LEDGER_STATE_BYTES) throw new Error(`${label} exceeds its byte limit`);
}

function isLegacyLedger(value: unknown): value is PaperLedger {
  if (!isRecord(value) || value.schemaVersion !== 1 || !isSafeInteger(value.realizedPnlLamports) || !isTimestamp(value.updatedAt)) return false;
  const portfolio = value.portfolio;
  if (!isRecord(portfolio) || !isTimestamp(portfolio.createdAt) || !isNonNegativeSafeInteger(portfolio.availableLamports)
    || !isNonNegativeSafeInteger(portfolio.dailyRealizedLossLamports)
    || (portfolio.dailyLossDateUtc !== undefined && !isUtcDay(portfolio.dailyLossDateUtc))
    || (portfolio.consecutiveLosses !== undefined && !isNonNegativeSafeInteger(portfolio.consecutiveLosses))
    || !Array.isArray(portfolio.positions)) return false;
  return portfolio.positions.every((position) => isRecord(position)
    && (position.tradeId === undefined || isNonEmptyString(position.tradeId))
    && (position.learningSchemaVersion === undefined || position.learningSchemaVersion === 2)
    && isNonEmptyString(position.pairId) && isNonEmptyString(position.mint) && isNonEmptyString(position.symbol)
    && isTimestamp(position.openedAt) && isPositiveFinite(position.entryPriceUsd) && isPositiveFinite(position.highPriceUsd)
    && position.highPriceUsd >= position.entryPriceUsd && isPositiveSafeInteger(position.allocatedLamports)
    && isPositiveSafeInteger(position.entryCostLamports) && isPositiveFinite(position.dynamicStopPercent));
}

function validatePortfolio(value: unknown, label: string): asserts value is Portfolio {
  if (!isRecord(value) || !isTimestamp(value.createdAt) || !isNonNegativeSafeInteger(value.availableLamports)
    || !isNonNegativeSafeInteger(value.dailyRealizedLossLamports) || !isUtcDay(value.dailyLossDateUtc)
    || !isNonNegativeSafeInteger(value.consecutiveLosses) || !Array.isArray(value.positions)) {
    throw new Error(`${label} has an invalid portfolio`);
  }
  const identities = new Set<string>();
  const tradeIds = new Set<string>();
  for (const position of value.positions) {
    validatePosition(position, label);
    const identity = `${position.pairId}\u0000${position.mint}`;
    if (identities.has(identity) || tradeIds.has(position.tradeId)) throw new Error(`${label} has duplicate positions`);
    identities.add(identity);
    tradeIds.add(position.tradeId);
  }
}

function validatePosition(value: unknown, label: string): asserts value is Portfolio['positions'][number] {
  if (!isRecord(value) || !isNonEmptyString(value.tradeId)
    || (value.learningSchemaVersion !== undefined && value.learningSchemaVersion !== 2)
    || !isNonEmptyString(value.pairId) || !isNonEmptyString(value.mint) || !isNonEmptyString(value.symbol)
    || !isTimestamp(value.openedAt) || !isPositiveFinite(value.entryPriceUsd) || !isPositiveFinite(value.highPriceUsd)
    || value.highPriceUsd < value.entryPriceUsd || !isPositiveSafeInteger(value.allocatedLamports)
    || !isPositiveSafeInteger(value.entryCostLamports) || !isPositiveFinite(value.dynamicStopPercent)
    || value.tradeId !== makeTradeId(value.mint, value.openedAt)) throw new Error(`${label} has an invalid position`);
}

function validateEvent(value: unknown, label: string): asserts value is PaperLedgerEvent {
  const types = new Set(['paper_entry', 'paper_exit', 'rejected', 'duplicate_suppressed', 'scan_complete', 'scan_error', 'position_quarantined', 'administrative_capital_adjustment']);
  if (!isRecord(value) || typeof value.type !== 'string' || !types.has(value.type) || !isTimestamp(value.at)) {
    throw new Error(`${label} has an invalid type or timestamp`);
  }
  validateJsonValue(value, label);
  if (Buffer.byteLength(JSON.stringify(value), 'utf8') > MAX_LEDGER_EVENT_BYTES) throw new Error('Ledger event exceeds its 1 MiB hard limit');
}

function validateJsonValue(value: unknown, label: string): void {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error(`${label} contains a non-finite number`);
    if (Number.isInteger(value) && !Number.isSafeInteger(value)) throw new Error(`${label} contains an unsafe integer: ${value}`);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) validateJsonValue(item, label);
    return;
  }
  if (isRecord(value)) {
    for (const item of Object.values(value)) {
      if (item === undefined) continue;
      validateJsonValue(item, label);
    }
    return;
  }
  if (value === undefined) return;
  throw new Error(`${label} contains a non-JSON value`);
}

function decodeCanonicalBase64(value: string, label: string): Buffer {
  const decoded = Buffer.from(value, 'base64');
  if (decoded.toString('base64') !== value) throw new Error(`Canonical paper ledger WAL ${label} is not canonical base64`);
  return decoded;
}

function decodeUtf8(raw: Uint8Array, label: string): string {
  try {
    return UTF8_DECODER.decode(raw);
  } catch {
    throw new Error(`${label} contains invalid UTF-8`);
  }
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function isNonEmptyString(value: unknown): value is string { return typeof value === 'string' && value.length > 0; }
function isSafeInteger(value: unknown): value is number { return typeof value === 'number' && Number.isSafeInteger(value); }
function isNonNegativeSafeInteger(value: unknown): value is number { return isSafeInteger(value) && value >= 0; }
function isPositiveSafeInteger(value: unknown): value is number { return isSafeInteger(value) && value > 0; }
function isPositiveFinite(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value) && value > 0; }
function isSha256(value: unknown): value is string { return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value); }
function isUuid(value: unknown): value is string { return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value); }
function isTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString() === value;
}
function isUtcDay(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const timestamp = Date.parse(`${value}T00:00:00.000Z`);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value;
}
function isMissingFile(error: unknown): error is NodeJS.ErrnoException { return isRecord(error) && error.code === 'ENOENT'; }
function isLockBusy(error: unknown): error is NodeJS.ErrnoException { return isRecord(error) && (error.code === 'EAGAIN' || error.code === 'EWOULDBLOCK'); }
function messageOf(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function delay(milliseconds: number): Promise<void> { return new Promise((resolve) => setTimeout(resolve, milliseconds)); }
function numberFromBigInt(value: bigint, label: string): number {
  if (value < 0n || value > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error(`${label} is outside the safe integer range`);
  return Number(value);
}

function migratePortfolio(portfolio: Portfolio, updatedAt: string): Portfolio {
  const legacy = portfolio as Portfolio & { dailyLossDateUtc?: unknown; consecutiveLosses?: unknown };
  const hasTrustedDailyLossDate = typeof legacy.dailyLossDateUtc === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(legacy.dailyLossDateUtc);
  const dailyLossDateUtc = hasTrustedDailyLossDate ? legacy.dailyLossDateUtc as string : utcDay(updatedAt) ?? utcDay(portfolio.createdAt) ?? '1970-01-01';
  const dailyRealizedLossLamports = isNonNegativeSafeInteger(portfolio.dailyRealizedLossLamports)
    ? portfolio.dailyRealizedLossLamports : 0;
  const consecutiveLosses = typeof legacy.consecutiveLosses === 'number' && Number.isInteger(legacy.consecutiveLosses) && legacy.consecutiveLosses >= 0
    ? legacy.consecutiveLosses : 0;
  const positions = portfolio.positions.map((position) => ({
    ...position,
    tradeId: typeof position.tradeId === 'string' && position.tradeId.length > 0 ? position.tradeId : makeTradeId(position.mint, position.openedAt),
  }));
  return { ...portfolio, dailyLossDateUtc, dailyRealizedLossLamports, consecutiveLosses, positions };
}
function utcDay(value: string): string | undefined {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString().slice(0, 10) : undefined;
}
