#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const B4_OFFLINE_REMAINDER_SCHEMA = 'B4_OFFLINE_REMAINDER_CAPTURE_1';
export const B4_OFFLINE_REMAINDER_RECEIPT_VERSION = 'B4_OFFLINE_REMAINDER_RECEIPT_1';
export const CANDIDATE_JETSTREAMER_V0_7_0_GIT_SHA = 'cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24';
export const OFFICIAL_OF1_HOST = 'files.old-faithful.net';
export const SLOTS_PER_EPOCH = 432_000;
export const PROVISIONAL_EXAMPLE_SLOT_RANGE = Object.freeze({
  startSlot: 422_506_000,
  endSlot: 422_506_128,
  note: 'provisional example only; not an approved plan',
});

export const DENIED_OVERRIDE_KEYS = Object.freeze([
  'JETSTREAMER_HTTP_BASE_URL',
  'ARCHIVE_BASE',
  'ARCHIVE_BACKEND',
  'JETSTREAMER_S3_BUCKET',
  'JETSTREAMER_S3_ENDPOINT',
  'ARCHIVE_S3',
  'ARCHIVE_S3_BUCKET',
  'ARCHIVE_S3_ENDPOINT',
  'S3_ENDPOINT',
  'AWS_ENDPOINT_URL',
]);

export const COVERAGE = Object.freeze({
  UNAVAILABLE: 'UNAVAILABLE',
  GAP: 'GAP',
  QUARANTINED: 'QUARANTINED',
  CLOSED: 'CLOSED',
});

export class B4OfflineRemainderError extends Error {
  constructor(message, code, metadata = {}) {
    super(message);
    this.name = 'B4OfflineRemainderError';
    this.code = code;
    this.metadata = metadata;
  }
}

function assert(condition, code, message, metadata = {}) {
  if (!condition) {
    throw new B4OfflineRemainderError(message, code, metadata);
  }
}

export function sha256Bytes(value) {
  return createHash('sha256').update(value).digest('hex');
}

function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

export function assertOverrideDeny(env = {}) {
  const denied = [];
  for (const key of DENIED_OVERRIDE_KEYS) {
    const raw = env[key];
    if (raw !== undefined && raw !== null && String(raw).trim() !== '') {
      denied.push(key);
    }
  }
  assert(denied.length === 0, 'OVERRIDE_DENIED', 'Jetstreamer HTTP/S3/backend overrides are default-deny', {
    denied,
  });
  return { ok: true, deniedKeys: [...DENIED_OVERRIDE_KEYS] };
}

export function epochSlotBounds(epoch) {
  assert(Number.isSafeInteger(epoch) && epoch >= 0, 'EPOCH_INVALID', 'epoch must be a non-negative integer', { epoch });
  return {
    epoch,
    firstSlot: epoch * SLOTS_PER_EPOCH,
    endExclusive: (epoch + 1) * SLOTS_PER_EPOCH,
  };
}

export function closeSlotRange(input) {
  const startSlot = input?.startSlot;
  const endSlot = input?.endSlot;
  const objectKind = input?.objectKind ?? 'SLOT_WINDOW';
  const epoch = input?.epoch;

  assert(Number.isSafeInteger(startSlot), 'SLOT_RANGE_INVALID', 'startSlot must be an integer');
  assert(Number.isSafeInteger(endSlot), 'SLOT_RANGE_INVALID', 'endSlot must be an integer');
  assert(startSlot < endSlot, 'SLOT_RANGE_INVALID', 'slot range must be a non-empty half-open window [startSlot, endSlot)');
  assert(objectKind !== 'FULL_EPOCH_CAR', 'FULL_CAR_NOT_SLOT_WINDOW', 'a full-epoch CAR is not a V2 slot-range closure', {
    objectKind,
  });

  if (Number.isSafeInteger(epoch)) {
    const bounds = epochSlotBounds(epoch);
    assert(
      startSlot !== bounds.firstSlot || endSlot !== bounds.endExclusive,
      'FULL_CAR_NOT_SLOT_WINDOW',
      'refusing to treat a full-epoch span as the requested slot window',
      { epoch, startSlot, endSlot },
    );
    assert(
      startSlot >= bounds.firstSlot && endSlot <= bounds.endExclusive,
      'SLOT_RANGE_OUTSIDE_EPOCH',
      'requested slot window is outside the named epoch',
      { epoch, startSlot, endSlot, ...bounds },
    );
  }

  return {
    startSlot,
    endSlot,
    length: endSlot - startSlot,
    objectKind: 'SLOT_WINDOW',
    provisionalExample: startSlot === PROVISIONAL_EXAMPLE_SLOT_RANGE.startSlot
      && endSlot === PROVISIONAL_EXAMPLE_SLOT_RANGE.endSlot,
    approvedPlan: false,
  };
}

export function classifySlotCoverage({ requested, observedSlots = [], quarantinedSlots = [] }) {
  const window = closeSlotRange(requested);
  const observed = new Set(observedSlots);
  const quarantined = new Set(quarantinedSlots);
  const missingSlots = [];
  const presentSlots = [];
  const badSlots = [];

  for (let slot = window.startSlot; slot < window.endSlot; slot += 1) {
    if (quarantined.has(slot)) {
      badSlots.push(slot);
      continue;
    }
    if (observed.has(slot)) {
      presentSlots.push(slot);
      continue;
    }
    missingSlots.push(slot);
  }

  let status = COVERAGE.CLOSED;
  if (presentSlots.length === 0 && badSlots.length === 0) {
    status = COVERAGE.UNAVAILABLE;
  } else if (badSlots.length > 0) {
    status = COVERAGE.QUARANTINED;
  } else if (missingSlots.length > 0) {
    status = COVERAGE.GAP;
  }

  return {
    status,
    requested: { startSlot: window.startSlot, endSlot: window.endSlot },
    observedSlots: presentSlots,
    missingSlots,
    quarantinedSlots: badSlots,
    filledMissingWithZero: false,
  };
}

function asBytes(rawBytes) {
  if (rawBytes instanceof Uint8Array) return rawBytes;
  if (Buffer.isBuffer(rawBytes)) return rawBytes;
  throw new B4OfflineRemainderError('rawBytes must be a Uint8Array', 'RAW_BYTES_INVALID');
}

export async function persistRawBeforeParse({
  workingDir,
  requested,
  rawBytes,
  identity = {},
  hostClass = 'FIXTURE',
  acquiredAt,
  env = {},
}) {
  assertOverrideDeny(env);
  const window = closeSlotRange(requested);
  const payload = asBytes(rawBytes);
  assert(payload.byteLength > 0, 'RAW_BYTES_EMPTY', 'raw object bytes are required before parse');
  const sha256 = identity.sha256 ?? sha256Bytes(payload);
  assert(typeof sha256 === 'string' && /^[0-9a-f]{64}$/i.test(sha256), 'IDENTITY_SHA256_INVALID', 'identity.sha256 must be sha256 hex');
  assert(sha256Bytes(payload) === sha256, 'IDENTITY_SHA256_MISMATCH', 'supplied sha256 does not match raw bytes');
  const cid = identity.cid ?? null;
  if (cid !== null) {
    assert(isNonEmptyString(cid), 'IDENTITY_CID_INVALID', 'identity.cid must be a non-empty string when present');
  }

  const acquired = acquiredAt ?? new Date().toISOString();
  const receipt = {
    schemaVersion: B4_OFFLINE_REMAINDER_SCHEMA,
    receiptVersion: B4_OFFLINE_REMAINDER_RECEIPT_VERSION,
    hostClass,
    sourceHost: OFFICIAL_OF1_HOST,
    requestedRange: { startSlot: window.startSlot, endSlot: window.endSlot },
    byteCount: payload.byteLength,
    sha256,
    cid,
    acquired_at: acquired,
    acquiredAtRole: 'operational_provenance_only',
    networkEnabled: false,
    parseAttempted: false,
    jetstreamerCandidateSha: CANDIDATE_JETSTREAMER_V0_7_0_GIT_SHA,
    approved: false,
  };

  await mkdir(workingDir, { recursive: true });
  const rawPath = `${workingDir}/b4-offline-raw.bin`;
  const identityPath = `${workingDir}/b4-offline-identity.json`;
  const receiptPath = `${workingDir}/b4-offline-receipt.json`;
  await writeFile(rawPath, payload);
  await writeFile(identityPath, `${JSON.stringify({ sha256, cid, byteCount: payload.byteLength }, null, 2)}\n`);
  await writeFile(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`);

  return {
    rawPath,
    identityPath,
    receiptPath,
    receipt,
    parseAttempted: false,
  };
}

export async function assertPersistedBeforeParse(workingDir) {
  const rawPath = `${workingDir}/b4-offline-raw.bin`;
  const identityPath = `${workingDir}/b4-offline-identity.json`;
  const receiptPath = `${workingDir}/b4-offline-receipt.json`;
  let raw;
  let identity;
  let receipt;
  try {
    raw = await readFile(rawPath);
    identity = JSON.parse(await readFile(identityPath, 'utf8'));
    receipt = JSON.parse(await readFile(receiptPath, 'utf8'));
  } catch (error) {
    throw new B4OfflineRemainderError(
      'raw bytes, identity and receipt must exist before parse',
      'PARSE_BEFORE_PERSIST',
      { error: String(error) },
    );
  }
  assert(receipt.parseAttempted === false, 'PARSE_BEFORE_PERSIST', 'receipt must record parseAttempted=false at persist time');
  assert(identity.sha256 === sha256Bytes(raw), 'IDENTITY_SHA256_MISMATCH', 'persisted identity does not match raw bytes');
  return { raw, identity, receipt, decodeInScope: false };
}

export function renderProgress(state, write) {
  const writer = write ?? ((chunk) => {
    throw new B4OfflineRemainderError('progress writer is required; refusing process.stdout default in library calls', 'PROGRESS_WRITER_REQUIRED');
  });
  const lines = [
    'B4-offline remainder',
    `networkEnabled=${state.networkEnabled === false ? 'false' : 'INVALID'}`,
    `overrides=${state.overridesDenied ? 'denied' : 'INVALID'}`,
    `slotRange=[${state.startSlot},${state.endSlot})`,
    `coverage=${state.coverage}`,
    `rawSha256=${state.rawSha256}`,
    `receipt=${state.receiptWritten ? 'written' : 'missing'}`,
    `parse=${state.parseAttempted ? 'INVALID' : 'not-attempted-before-persist'}`,
    `approved=${state.approved === false ? 'false' : 'INVALID'}`,
    `lease=unauthorized`,
  ];
  const text = `${lines.join('\n')}\n`;
  writer(text);
  return text;
}

export async function runInjectedDemo({
  workingDir,
  env = {},
  write,
  rawBytes = new TextEncoder().encode('b4-offline-remainder-fixture'),
  requested = { startSlot: 10, endSlot: 12, objectKind: 'SLOT_WINDOW' },
  observedSlots = [10],
  quarantinedSlots = [],
  acquiredAt = '2026-09-05T00:00:00.000Z',
  hostClass = 'FIXTURE',
} = {}) {
  assertOverrideDeny(env);
  const persisted = await persistRawBeforeParse({
    workingDir,
    requested,
    rawBytes,
    hostClass,
    acquiredAt,
    env,
  });
  const coverage = classifySlotCoverage({ requested, observedSlots, quarantinedSlots });
  const text = renderProgress({
    networkEnabled: false,
    overridesDenied: true,
    startSlot: persisted.receipt.requestedRange.startSlot,
    endSlot: persisted.receipt.requestedRange.endSlot,
    coverage: coverage.status,
    rawSha256: persisted.receipt.sha256,
    receiptWritten: true,
    parseAttempted: persisted.parseAttempted,
    approved: false,
  }, write);
  return { persisted, coverage, text };
}

async function runCli(env = process.env) {
  const workingDir = await mkdtemp(join(tmpdir(), 'b4-offline-remainder-cli-'));
  await runInjectedDemo({
    workingDir,
    env,
    write: (chunk) => {
      process.stdout.write(chunk);
    },
  });
  process.stdout.write(`workingDir=${workingDir}\n`);
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath && invokedPath === fileURLToPath(import.meta.url)) {
  runCli().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
}
