import { createHash } from 'node:crypto';
import bs58 from 'bs58';
import { deriveBondingCurve, PUMP_PROGRAM_ID } from '../pump-address.js';
import { PUMP_DISCRIMINATORS } from '../pump-parser.js';
import {
  capturePumpV2BronzeTransaction,
  type PumpV2BronzeTransaction,
  type PumpV2Candidate,
  type PumpV2LocatedInstruction,
  type PumpV2Quarantine,
} from './pump-v2-bronze.js';

export type OldFaithfulEpochSourceManifest = {
  schemaVersion: 'OLD_FAITHFUL_EPOCH_SOURCE_1';
  epoch: number;
  epochCid: string;
  carSha256: string;
  carFileSizeBytes: string;
  slotsFileSha256: string;
  slotsFileSizeBytes: number;
  slotsFileEntryCount: number;
  slotsFirst: number;
  slotsLast: number;
  slotRange: { startInclusive: number; endExclusive: number };
};

export type JetstreamerAdapterProvenance = {
  schemaVersion: 'JETSTREAMER_ADAPTER_PROVENANCE_1';
  jetstreamerGitSha: string;
  pluginGitSha: string;
  pluginSourceSha256: string;
};

export type OldFaithfulPumpV2Observation = {
  schemaVersion: 'OLD_FAITHFUL_PUMP_V2_OBSERVATION_1';
  sourceManifest: OldFaithfulEpochSourceManifest;
  sourceManifestSha256: string;
  adapterProvenance: JetstreamerAdapterProvenance;
  adapterProvenanceSha256: string;
  staticAccountCount: number;
  bronze: PumpV2BronzeTransaction;
};

export type JetstreamerTransactionBlockRecord = {
  schemaVersion: 'JETSTREAMER_TRANSACTION_BLOCK_1';
  block: { slot: number; blockTimeUnixSeconds: number };
  transaction: {
    slot: number;
    transactionSlotIndex: number;
    signature: string;
    isVote: boolean;
    message: { staticAccountKeys: string[]; instructions: unknown[] };
    meta: {
      err: unknown;
      feeLamports: string;
      logMessages: string[];
      loadedAddresses: { writable: string[]; readonly: string[] };
      preBalancesLamports: string[];
      postBalancesLamports: string[];
      innerInstructionGroups: unknown[];
      preTokenBalances: unknown[];
      postTokenBalances: unknown[];
    };
  };
};

export type OldFaithfulCoverageRange = { startInclusive: number; endExclusive: number };

export type OldFaithfulCoverageLedger = {
  schemaVersion: 'OLD_FAITHFUL_COVERAGE_LEDGER_1';
  sourceManifestSha256: string;
  adapterProvenanceSha256: string;
  slotRange: OldFaithfulCoverageRange;
  callbackRangeComplete: boolean;
  archiveSlotInventoryReconciled: boolean;
  coverageStatus: 'ARCHIVE_SLOT_INVENTORY_RECONCILED' | 'CALLBACK_RANGE_ONLY_NOT_ARCHIVE_VERIFIED';
  researchReady: false;
  observedRanges: OldFaithfulCoverageRange[];
  missingRanges: OldFaithfulCoverageRange[];
  blockCount: number;
  possibleLeaderSkippedCount: number;
  resolvedPossibleLeaderSkippedCount: number;
  duplicateBlockObservationCount: number;
  transactionProjection: 'PUMP_V2_NON_VOTE_ONLY';
  transactionObservationCount: number;
  duplicateTransactionObservationCount: number;
  pumpCandidateCount: number;
  quarantinedCandidateCount: number;
  observationsSha256: string;
};

const SHA256_HEX = /^[0-9a-f]{64}$/;
const GIT_SHA1_HEX = /^[0-9a-f]{40}$/;
const BASE58_TEXT = /^[1-9A-HJ-NP-Za-km-z]+$/;
const CANONICAL_HEX = /^(?:[0-9a-f]{2})*$/;
const CANONICAL_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const UNSIGNED_DECIMAL = /^(0|[1-9]\d*)$/;
const CID_BASE32 = /^b[a-z2-7]{58}$/;
const BASE32_ALPHABET = 'abcdefghijklmnopqrstuvwxyz234567';
const MAX_EPOCH_SLOTS = 432_000;
const MAX_BLOCK_OBSERVATIONS = 1_000_000;
const MAX_TRANSACTION_OBSERVATIONS = 1_000_000;
const MAX_CANDIDATES_PER_TRANSACTION = 4_352;
const MAX_TOP_LEVEL_INSTRUCTIONS = 256;
const MAX_INNER_INSTRUCTIONS = 4_096;
const MAX_INSTRUCTION_ACCOUNT_REFERENCES = 4_096;
const MAX_LOG_MESSAGES = 1_024;
const MAX_LOG_BYTES = 10_000;
const MAX_SLOTS_FILE_BYTES = 16 * 1_024 * 1_024;
const MAX_CANONICAL_OBSERVATION_BYTES = 4 * 1_024 * 1_024;
const MAX_CANONICAL_NODES = 100_000;
const MAX_CANONICAL_DEPTH = 32;
const MAX_UNIX_SECONDS = 253_402_300_799;
const MAX_U64 = (1n << 64n) - 1n;

const SOURCE_MANIFEST_KEYS = [
  'carFileSizeBytes', 'carSha256', 'epoch', 'epochCid', 'schemaVersion', 'slotRange',
  'slotsFileEntryCount', 'slotsFileSha256', 'slotsFileSizeBytes', 'slotsFirst', 'slotsLast',
] as const;
const ADAPTER_PROVENANCE_KEYS = [
  'jetstreamerGitSha', 'pluginGitSha', 'pluginSourceSha256', 'schemaVersion',
] as const;
const OBSERVATION_KEYS = [
  'adapterProvenance', 'adapterProvenanceSha256', 'bronze', 'schemaVersion',
  'sourceManifest', 'sourceManifestSha256', 'staticAccountCount',
] as const;
const BRONZE_KEYS = [
  'accountKeys', 'blockTime', 'executionStatus', 'feeLamports', 'instructions', 'logMessages',
  'postBalancesLamports', 'postTokenBalances', 'preBalancesLamports', 'preTokenBalances',
  'pumpCandidates', 'quarantines', 'schemaVersion', 'signature', 'slot', 'transactionIndex',
] as const;
const CANDIDATE_KEYS = [
  'curve', 'discriminatorHex', 'discriminatorSource', 'eventKey', 'executionStatus',
  'instructionIndex', 'instructionLocation', 'kind', 'mint', 'parserStatus', 'variant',
] as const;
const QUARANTINE_KEYS = ['discriminatorHex', 'eventKey', 'reason'] as const;
const TOKEN_BALANCE_KEYS = ['accountIndex', 'amount', 'decimals', 'mint', 'owner', 'programId'] as const;
const KNOWN_PUMP_CANDIDATES = new Map<string, {
  variant: string;
  kind: 'buy' | 'sell';
  source: 'official_idl' | 'observed_runtime';
}>([
  [PUMP_DISCRIMINATORS.buy, { variant: 'buy', kind: 'buy', source: 'official_idl' }],
  [PUMP_DISCRIMINATORS.sell, { variant: 'sell', kind: 'sell', source: 'official_idl' }],
  [PUMP_DISCRIMINATORS.buyV2, { variant: 'buy_v2', kind: 'buy', source: 'official_idl' }],
  [PUMP_DISCRIMINATORS.sellV2, { variant: 'sell_v2', kind: 'sell', source: 'official_idl' }],
  [PUMP_DISCRIMINATORS.buyExactQuoteInV2, { variant: 'buy_exact_quote_in_v2', kind: 'buy', source: 'official_idl' }],
  [PUMP_DISCRIMINATORS.buyExactSolIn, { variant: 'buy_exact_sol_in', kind: 'buy', source: 'official_idl' }],
  [PUMP_DISCRIMINATORS.liveBuy, { variant: 'live_buy_dispatcher', kind: 'buy', source: 'observed_runtime' }],
  [PUMP_DISCRIMINATORS.liveBuyV2, { variant: 'live_buy_v2_dispatcher', kind: 'buy', source: 'observed_runtime' }],
  [PUMP_DISCRIMINATORS.liveBuyExactSolIn, { variant: 'live_buy_exact_sol_in_dispatcher', kind: 'buy', source: 'observed_runtime' }],
  [PUMP_DISCRIMINATORS.liveSell, { variant: 'live_sell_dispatcher', kind: 'sell', source: 'observed_runtime' }],
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function safeNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function denseArray(value: unknown, maximum: number): value is unknown[] {
  if (!Array.isArray(value) || value.length > maximum) return false;
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(value, index)) return false;
  }
  return true;
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const observed = Object.keys(value).sort();
  const expected = [...keys].sort();
  return observed.length === expected.length && observed.every((key, index) => key === expected[index]);
}

function hasKeysWithOptional(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
): boolean {
  const observed = Object.keys(value);
  return required.every((key) => observed.includes(key))
    && observed.every((key) => required.includes(key) || optional.includes(key));
}

function validU64Text(value: unknown): value is string {
  return typeof value === 'string'
    && value.length <= 20
    && UNSIGNED_DECIMAL.test(value)
    && BigInt(value) <= MAX_U64;
}

function validPublicKey(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 32 || value.length > 44 || !BASE58_TEXT.test(value)) return false;
  try {
    return bs58.decode(value).length === 32;
  } catch {
    return false;
  }
}

function validSignature(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 64 || value.length > 88 || !BASE58_TEXT.test(value)) return false;
  try {
    return bs58.decode(value).length === 64;
  } catch {
    return false;
  }
}

function validCanonicalUtc(value: unknown): value is string {
  if (typeof value !== 'string' || !CANONICAL_UTC.test(value)) return false;
  const milliseconds = Date.parse(value);
  return Number.isFinite(milliseconds) && new Date(milliseconds).toISOString() === value;
}

function validEpochCid(value: unknown): value is string {
  if (typeof value !== 'string' || !CID_BASE32.test(value)) return false;
  let accumulator = 0;
  let bits = 0;
  const bytes: number[] = [];
  for (const character of value.slice(1)) {
    const digit = BASE32_ALPHABET.indexOf(character);
    if (digit < 0) return false;
    accumulator = (accumulator << 5) | digit;
    bits += 5;
    while (bits >= 8) {
      bits -= 8;
      bytes.push((accumulator >> bits) & 0xff);
      accumulator &= (1 << bits) - 1;
    }
  }
  if (bits > 0 && accumulator !== 0) return false;
  return bytes.length === 36
    && bytes[0] === 0x01
    && bytes[1] === 0x71
    && bytes[2] === 0x12
    && bytes[3] === 0x20;
}

function normalizeSourceManifest(value: unknown): OldFaithfulEpochSourceManifest {
  if (!isRecord(value)
    || !hasExactKeys(value, SOURCE_MANIFEST_KEYS)
    || value.schemaVersion !== 'OLD_FAITHFUL_EPOCH_SOURCE_1'
    || !safeNonNegativeInteger(value.epoch)
    || !validEpochCid(value.epochCid)
    || typeof value.carSha256 !== 'string'
    || !SHA256_HEX.test(value.carSha256)
    || !validU64Text(value.carFileSizeBytes)
    || value.carFileSizeBytes === '0'
    || typeof value.slotsFileSha256 !== 'string'
    || !SHA256_HEX.test(value.slotsFileSha256)
    || !safeNonNegativeInteger(value.slotsFileSizeBytes)
    || value.slotsFileSizeBytes === 0
    || value.slotsFileSizeBytes > MAX_SLOTS_FILE_BYTES
    || !safeNonNegativeInteger(value.slotsFileEntryCount)
    || value.slotsFileEntryCount === 0
    || value.slotsFileEntryCount > MAX_EPOCH_SLOTS + 1
    || !safeNonNegativeInteger(value.slotsFirst)
    || !safeNonNegativeInteger(value.slotsLast)
    || value.slotsLast < value.slotsFirst
    || !isRecord(value.slotRange)
    || !hasExactKeys(value.slotRange, ['endExclusive', 'startInclusive'])
    || !safeNonNegativeInteger(value.slotRange.startInclusive)
    || !safeNonNegativeInteger(value.slotRange.endExclusive)) {
    throw new Error('invalid_source_manifest');
  }
  const expectedStart = value.epoch * MAX_EPOCH_SLOTS;
  const expectedEnd = expectedStart + MAX_EPOCH_SLOTS;
  if (!Number.isSafeInteger(expectedStart)
    || !Number.isSafeInteger(expectedEnd)
    || value.slotRange.startInclusive !== expectedStart
    || value.slotRange.endExclusive !== expectedEnd
    || value.slotsFirst < Math.max(0, expectedStart - 1)
    || value.slotsFirst >= expectedEnd
    || value.slotsLast >= expectedEnd) {
    throw new Error('invalid_source_manifest');
  }
  return {
    schemaVersion: 'OLD_FAITHFUL_EPOCH_SOURCE_1',
    epoch: value.epoch,
    epochCid: value.epochCid,
    carSha256: value.carSha256,
    carFileSizeBytes: value.carFileSizeBytes,
    slotsFileSha256: value.slotsFileSha256,
    slotsFileSizeBytes: value.slotsFileSizeBytes,
    slotsFileEntryCount: value.slotsFileEntryCount,
    slotsFirst: value.slotsFirst,
    slotsLast: value.slotsLast,
    slotRange: { startInclusive: expectedStart, endExclusive: expectedEnd },
  };
}

function normalizeAdapterProvenance(value: unknown): JetstreamerAdapterProvenance {
  if (!isRecord(value)
    || !hasExactKeys(value, ADAPTER_PROVENANCE_KEYS)
    || value.schemaVersion !== 'JETSTREAMER_ADAPTER_PROVENANCE_1'
    || typeof value.jetstreamerGitSha !== 'string'
    || !GIT_SHA1_HEX.test(value.jetstreamerGitSha)
    || typeof value.pluginGitSha !== 'string'
    || !GIT_SHA1_HEX.test(value.pluginGitSha)
    || typeof value.pluginSourceSha256 !== 'string'
    || !SHA256_HEX.test(value.pluginSourceSha256)) {
    throw new Error('invalid_adapter_provenance');
  }
  return {
    schemaVersion: 'JETSTREAMER_ADAPTER_PROVENANCE_1',
    jetstreamerGitSha: value.jetstreamerGitSha,
    pluginGitSha: value.pluginGitSha,
    pluginSourceSha256: value.pluginSourceSha256,
  };
}

function sourceManifestBytes(manifest: OldFaithfulEpochSourceManifest): string {
  return JSON.stringify({
    schemaVersion: manifest.schemaVersion,
    epoch: manifest.epoch,
    epochCid: manifest.epochCid,
    carSha256: manifest.carSha256,
    carFileSizeBytes: manifest.carFileSizeBytes,
    slotsFileSha256: manifest.slotsFileSha256,
    slotsFileSizeBytes: manifest.slotsFileSizeBytes,
    slotsFileEntryCount: manifest.slotsFileEntryCount,
    slotsFirst: manifest.slotsFirst,
    slotsLast: manifest.slotsLast,
    slotRange: manifest.slotRange,
  });
}

function adapterProvenanceBytes(provenance: JetstreamerAdapterProvenance): string {
  return JSON.stringify({
    schemaVersion: provenance.schemaVersion,
    jetstreamerGitSha: provenance.jetstreamerGitSha,
    pluginGitSha: provenance.pluginGitSha,
    pluginSourceSha256: provenance.pluginSourceSha256,
  });
}

function sourceManifestSha256(manifest: OldFaithfulEpochSourceManifest): string {
  return createHash('sha256')
    .update(`OLD_FAITHFUL_OF1_SOURCE_MANIFEST_1\n${sourceManifestBytes(manifest)}`)
    .digest('hex');
}

function adapterProvenanceSha256(provenance: JetstreamerAdapterProvenance): string {
  return createHash('sha256')
    .update(`JETSTREAMER_ADAPTER_PROVENANCE_1\n${adapterProvenanceBytes(provenance)}`)
    .digest('hex');
}

function normalizeRecord(value: unknown): JetstreamerTransactionBlockRecord {
  if (!isRecord(value)
    || value.schemaVersion !== 'JETSTREAMER_TRANSACTION_BLOCK_1'
    || !isRecord(value.block)
    || !safeNonNegativeInteger(value.block.slot)
    || !isRecord(value.transaction)
    || !safeNonNegativeInteger(value.transaction.slot)
    || !safeNonNegativeInteger(value.transaction.transactionSlotIndex)
    || typeof value.transaction.signature !== 'string'
    || typeof value.transaction.isVote !== 'boolean'
    || !isRecord(value.transaction.message)
    || !Array.isArray(value.transaction.message.staticAccountKeys)
    || !Array.isArray(value.transaction.message.instructions)
    || !isRecord(value.transaction.meta)) {
    throw new Error('invalid_jetstreamer_record');
  }
  if (!safeNonNegativeInteger(value.block.blockTimeUnixSeconds)
    || value.block.blockTimeUnixSeconds > MAX_UNIX_SECONDS) {
    throw new Error('invalid_block_time');
  }
  return value as unknown as JetstreamerTransactionBlockRecord;
}

export function adaptOldFaithfulJetstreamerPumpRecord(
  sourceManifest: unknown,
  adapterProvenance: unknown,
  sourceRecord: unknown,
): OldFaithfulPumpV2Observation {
  const manifest = normalizeSourceManifest(sourceManifest);
  const provenance = normalizeAdapterProvenance(adapterProvenance);
  const record = normalizeRecord(sourceRecord);
  if (record.block.slot !== record.transaction.slot) throw new Error('jetstreamer_slot_mismatch');
  if (record.transaction.slot < manifest.slotRange.startInclusive
    || record.transaction.slot >= manifest.slotRange.endExclusive) {
    throw new Error('transaction_outside_source_range');
  }
  if (record.transaction.isVote) throw new Error('vote_transaction_not_supported');
  const bronze = capturePumpV2BronzeTransaction({
    slot: record.transaction.slot,
    transactionIndex: record.transaction.transactionSlotIndex,
    signature: record.transaction.signature,
    blockTime: new Date(record.block.blockTimeUnixSeconds * 1_000).toISOString(),
    err: record.transaction.meta.err,
    feeLamports: record.transaction.meta.feeLamports,
    logMessages: record.transaction.meta.logMessages,
    staticAccountKeys: record.transaction.message.staticAccountKeys,
    loadedAddresses: record.transaction.meta.loadedAddresses,
    preBalancesLamports: record.transaction.meta.preBalancesLamports,
    postBalancesLamports: record.transaction.meta.postBalancesLamports,
    topLevelInstructions: record.transaction.message.instructions,
    innerInstructionGroups: record.transaction.meta.innerInstructionGroups,
    preTokenBalances: record.transaction.meta.preTokenBalances,
    postTokenBalances: record.transaction.meta.postTokenBalances,
  });
  return {
    schemaVersion: 'OLD_FAITHFUL_PUMP_V2_OBSERVATION_1',
    sourceManifest: manifest,
    sourceManifestSha256: sourceManifestSha256(manifest),
    adapterProvenance: provenance,
    adapterProvenanceSha256: adapterProvenanceSha256(provenance),
    staticAccountCount: record.transaction.message.staticAccountKeys.length,
    bronze,
  };
}

function normalizeRequestedRange(value: unknown, source: OldFaithfulCoverageRange): OldFaithfulCoverageRange {
  if (!isRecord(value)
    || !hasExactKeys(value, ['endExclusive', 'startInclusive'])
    || !safeNonNegativeInteger(value.startInclusive)
    || !safeNonNegativeInteger(value.endExclusive)
    || value.endExclusive <= value.startInclusive
    || value.startInclusive < source.startInclusive
    || value.endExclusive > source.endExclusive) {
    throw new Error('invalid_requested_slot_range');
  }
  return { startInclusive: value.startInclusive, endExclusive: value.endExclusive };
}

type JetstreamerBlockObservation = {
  schemaVersion: 'JETSTREAMER_BLOCK_1';
  slot: number;
  status: 'block' | 'possible_leader_skipped';
  blockTimeUnixSeconds: number | null;
};

function normalizeBlockObservation(value: unknown, range: OldFaithfulCoverageRange): JetstreamerBlockObservation {
  if (!isRecord(value)
    || !hasExactKeys(value, ['blockTimeUnixSeconds', 'schemaVersion', 'slot', 'status'])
    || value.schemaVersion !== 'JETSTREAMER_BLOCK_1'
    || !safeNonNegativeInteger(value.slot)
    || value.slot < range.startInclusive
    || value.slot >= range.endExclusive
    || (value.status !== 'block' && value.status !== 'possible_leader_skipped')) {
    throw new Error('invalid_block_observation');
  }
  if (value.status === 'block') {
    if (!safeNonNegativeInteger(value.blockTimeUnixSeconds)
      || value.blockTimeUnixSeconds > MAX_UNIX_SECONDS) throw new Error('invalid_block_observation');
  } else if (value.blockTimeUnixSeconds !== null) {
    throw new Error('invalid_block_observation');
  }
  return value as unknown as JetstreamerBlockObservation;
}

function rangesFromSlots(slots: number[]): OldFaithfulCoverageRange[] {
  const ranges: OldFaithfulCoverageRange[] = [];
  for (const slot of slots) {
    const previous = ranges.at(-1);
    if (previous && previous.endExclusive === slot) previous.endExclusive = slot + 1;
    else ranges.push({ startInclusive: slot, endExclusive: slot + 1 });
  }
  return ranges;
}

function missingRanges(source: OldFaithfulCoverageRange, observed: OldFaithfulCoverageRange[]): OldFaithfulCoverageRange[] {
  const missing: OldFaithfulCoverageRange[] = [];
  let cursor = source.startInclusive;
  for (const range of observed) {
    if (cursor < range.startInclusive) missing.push({ startInclusive: cursor, endExclusive: range.startInclusive });
    cursor = range.endExclusive;
  }
  if (cursor < source.endExclusive) missing.push({ startInclusive: cursor, endExclusive: source.endExclusive });
  return missing;
}

function parseSlotsInventory(
  sourceBytes: unknown,
  manifest: OldFaithfulEpochSourceManifest,
  requestedRange: OldFaithfulCoverageRange,
): Set<number> {
  if (typeof sourceBytes !== 'string'
    || sourceBytes.length > MAX_SLOTS_FILE_BYTES
    || Buffer.byteLength(sourceBytes, 'utf8') !== manifest.slotsFileSizeBytes
    || createHash('sha256').update(sourceBytes).digest('hex') !== manifest.slotsFileSha256
    || !/^(?:0|[1-9]\d*)\n(?:[1-9]\d*\n)*$/.test(sourceBytes)) {
    throw new Error('invalid_slots_inventory');
  }
  const lines = sourceBytes.slice(0, -1).split('\n');
  if (lines.length !== manifest.slotsFileEntryCount) throw new Error('invalid_slots_inventory');
  const expectedBlocks = new Set<number>();
  let previous: number | undefined;
  for (const line of lines) {
    const slot = Number(line);
    if (!safeNonNegativeInteger(slot)
      || (previous !== undefined && slot <= previous)
      || (slot !== manifest.slotRange.startInclusive - 1
        && (slot < manifest.slotRange.startInclusive || slot >= manifest.slotRange.endExclusive))) {
      throw new Error('invalid_slots_inventory');
    }
    if (slot >= requestedRange.startInclusive && slot < requestedRange.endExclusive) expectedBlocks.add(slot);
    previous = slot;
  }
  if (Number(lines[0]) !== manifest.slotsFirst || previous !== manifest.slotsLast) {
    throw new Error('invalid_slots_inventory');
  }
  return expectedBlocks;
}

function validCandidate(
  value: unknown,
  executionStatus: 'succeeded' | 'failed',
): value is Record<string, unknown> {
  if (!isRecord(value)
    || !hasKeysWithOptional(value, CANDIDATE_KEYS, ['parentInstructionIndex'])
    || typeof value.eventKey !== 'string'
    || value.eventKey.length === 0
    || value.eventKey.length > 512
    || (value.instructionLocation !== 'top_level' && value.instructionLocation !== 'inner')
    || !safeNonNegativeInteger(value.instructionIndex)
    || (value.instructionLocation === 'inner') !== Object.hasOwn(value, 'parentInstructionIndex')
    || (Object.hasOwn(value, 'parentInstructionIndex') && !safeNonNegativeInteger(value.parentInstructionIndex))
    || typeof value.discriminatorHex !== 'string'
    || value.discriminatorHex.length !== 16
    || !CANONICAL_HEX.test(value.discriminatorHex)
    || (value.discriminatorSource !== null
      && value.discriminatorSource !== 'official_idl'
      && value.discriminatorSource !== 'observed_runtime')
    || (value.parserStatus !== 'known_discriminator' && value.parserStatus !== 'quarantined')
    || (value.variant !== null && (typeof value.variant !== 'string' || value.variant.length > 128))
    || (value.kind !== null && value.kind !== 'buy' && value.kind !== 'sell')
    || (value.mint !== null && !validPublicKey(value.mint))
    || (value.curve !== null && !validPublicKey(value.curve))
    || value.executionStatus !== executionStatus) return false;
  if (typeof value.mint === 'string'
    && typeof value.curve === 'string'
    && deriveBondingCurve(value.mint) !== value.curve) return false;
  const known = KNOWN_PUMP_CANDIDATES.get(value.discriminatorHex);
  if (value.parserStatus === 'known_discriminator') {
    return known !== undefined
      && value.discriminatorSource === known.source
      && value.variant === known.variant
      && value.kind === known.kind
      && validPublicKey(value.mint)
      && validPublicKey(value.curve);
  }
  if (known) {
    return value.discriminatorSource === known.source
      && value.variant === known.variant
      && value.kind === known.kind
      && (value.mint === null || value.curve === null);
  }
  return value.discriminatorSource === null
    && value.variant === null
    && value.kind === null;
}

function validQuarantine(value: unknown): value is Record<string, unknown> {
  return isRecord(value)
    && hasExactKeys(value, QUARANTINE_KEYS)
    && typeof value.eventKey === 'string'
    && value.eventKey.length > 0
    && value.eventKey.length <= 512
    && typeof value.discriminatorHex === 'string'
    && value.discriminatorHex.length === 16
    && CANONICAL_HEX.test(value.discriminatorHex)
    && (value.reason === 'unknown_pump_discriminator' || value.reason === 'unresolved_pump_identity');
}

function validInstruction(value: unknown, accountKeys: string[]): value is PumpV2LocatedInstruction {
  if (!isRecord(value)
    || !hasKeysWithOptional(value, [
      'accountIndices', 'accounts', 'dataHex', 'instructionIndex', 'instructionLocation',
      'programId', 'programIdIndex',
    ], ['parentInstructionIndex', 'stackHeight'])
    || (value.instructionLocation !== 'top_level' && value.instructionLocation !== 'inner')
    || !safeNonNegativeInteger(value.instructionIndex)
    || !safeNonNegativeInteger(value.programIdIndex)
    || value.programIdIndex >= accountKeys.length
    || !validPublicKey(value.programId)
    || value.programId !== accountKeys[value.programIdIndex]
    || !denseArray(value.accountIndices, 256)
    || !value.accountIndices.every(safeNonNegativeInteger)
    || !denseArray(value.accounts, 256)
    || !value.accounts.every(validPublicKey)
    || value.accountIndices.length !== value.accounts.length
    || !value.accountIndices.every((accountIndex, position) => (
      (accountIndex as number) < accountKeys.length
      && (value.accounts as string[])[position] === accountKeys[accountIndex as number]
    ))
    || typeof value.dataHex !== 'string'
    || value.dataHex.length > 2_464
    || !CANONICAL_HEX.test(value.dataHex)
    || (value.instructionLocation === 'inner') !== Object.hasOwn(value, 'parentInstructionIndex')
    || (Object.hasOwn(value, 'parentInstructionIndex') && !safeNonNegativeInteger(value.parentInstructionIndex))) return false;
  const hasStackHeight = Object.hasOwn(value, 'stackHeight');
  if (!hasStackHeight) return true;
  if (!safeNonNegativeInteger(value.stackHeight)) return false;
  return value.instructionLocation === 'top_level'
    ? value.stackHeight === 1
    : value.stackHeight >= 2 && value.stackHeight <= 9;
}

function validInstructionSequence(instructions: PumpV2LocatedInstruction[]): boolean {
  let topLevelCount = 0;
  let innerCount = 0;
  let accountReferenceCount = 0;
  let currentParent = -1;
  let nextInnerIndex = 0;
  for (const instruction of instructions) {
    accountReferenceCount += instruction.accountIndices.length;
    if (accountReferenceCount > MAX_INSTRUCTION_ACCOUNT_REFERENCES) return false;
    if (instruction.instructionLocation === 'top_level') {
      if (instruction.instructionIndex !== topLevelCount) return false;
      topLevelCount += 1;
      if (topLevelCount > MAX_TOP_LEVEL_INSTRUCTIONS) return false;
      currentParent = instruction.instructionIndex;
      nextInnerIndex = 0;
      continue;
    }
    innerCount += 1;
    if (innerCount > MAX_INNER_INSTRUCTIONS
      || instruction.parentInstructionIndex !== currentParent
      || instruction.instructionIndex !== nextInnerIndex) return false;
    nextInnerIndex += 1;
  }
  return true;
}

function uniquePumpIdentity(
  accounts: string[],
  pdaCache: Map<string, string>,
): { mint: string; curve: string } | undefined {
  const accountSet = new Set(accounts);
  const pairs = new Map<string, { mint: string; curve: string }>();
  for (const mint of accountSet) {
    if (!pdaCache.has(mint)) pdaCache.set(mint, deriveBondingCurve(mint));
    const curve = pdaCache.get(mint) as string;
    if (curve !== mint && accountSet.has(curve)) pairs.set(`${mint}:${curve}`, { mint, curve });
  }
  return pairs.size === 1 ? [...pairs.values()][0] : undefined;
}

function expectedPumpProjection(bronze: PumpV2BronzeTransaction): {
  pumpCandidates: PumpV2Candidate[];
  quarantines: PumpV2Quarantine[];
} {
  const pumpCandidates: PumpV2Candidate[] = [];
  const quarantines: PumpV2Quarantine[] = [];
  const pdaCache = new Map<string, string>();
  for (const instruction of bronze.instructions) {
    if (instruction.programId !== PUMP_PROGRAM_ID) continue;
    const discriminatorHex = instruction.dataHex.slice(0, 16);
    const known = KNOWN_PUMP_CANDIDATES.get(discriminatorHex);
    const identity = uniquePumpIdentity(instruction.accounts, pdaCache);
    const eventKey = [
      bronze.slot,
      bronze.transactionIndex,
      bronze.signature,
      instruction.instructionLocation,
      instruction.parentInstructionIndex ?? '-',
      instruction.instructionIndex,
      instruction.programId,
      discriminatorHex,
    ].join(':');
    const reason = !known
      ? 'unknown_pump_discriminator' as const
      : !identity
        ? 'unresolved_pump_identity' as const
        : undefined;
    pumpCandidates.push({
      eventKey,
      instructionLocation: instruction.instructionLocation,
      ...(instruction.parentInstructionIndex === undefined
        ? {}
        : { parentInstructionIndex: instruction.parentInstructionIndex }),
      instructionIndex: instruction.instructionIndex,
      discriminatorHex,
      discriminatorSource: known?.source ?? null,
      parserStatus: reason === undefined ? 'known_discriminator' : 'quarantined',
      variant: known?.variant ?? null,
      kind: known?.kind ?? null,
      mint: identity?.mint ?? null,
      curve: identity?.curve ?? null,
      executionStatus: bronze.executionStatus,
    });
    if (reason) quarantines.push({ eventKey, reason, discriminatorHex });
  }
  return { pumpCandidates, quarantines };
}

function validTokenBalance(value: unknown, accountCount: number): value is Record<string, unknown> {
  return isRecord(value)
    && hasExactKeys(value, TOKEN_BALANCE_KEYS)
    && safeNonNegativeInteger(value.accountIndex)
    && value.accountIndex < accountCount
    && validPublicKey(value.mint)
    && validPublicKey(value.owner)
    && validPublicKey(value.programId)
    && safeNonNegativeInteger(value.decimals)
    && value.decimals <= 255
    && validU64Text(value.amount);
}

function validTokenBalances(values: unknown[], accountCount: number): boolean {
  let previousIndex = -1;
  for (const value of values) {
    if (!validTokenBalance(value, accountCount)) return false;
    const accountIndex = value.accountIndex as number;
    if (accountIndex <= previousIndex) return false;
    previousIndex = accountIndex;
  }
  return true;
}

function canonicalJson(value: unknown): string {
  const seen = new Set<object>();
  const state = { bytes: 0, nodes: 0 };
  const account = (text: string): string => {
    state.bytes += Buffer.byteLength(text, 'utf8');
    if (state.bytes > MAX_CANONICAL_OBSERVATION_BYTES) throw new Error('invalid_transaction_observation');
    return text;
  };
  const visit = (entry: unknown, depth: number): string => {
    state.nodes += 1;
    if (state.nodes > MAX_CANONICAL_NODES || depth > MAX_CANONICAL_DEPTH) {
      throw new Error('invalid_transaction_observation');
    }
    if (entry === null) return account('null');
    if (typeof entry === 'boolean') return account(entry ? 'true' : 'false');
    if (typeof entry === 'number') {
      if (!Number.isSafeInteger(entry)) throw new Error('invalid_transaction_observation');
      return account(String(entry));
    }
    if (typeof entry === 'string') {
      if (entry.length > MAX_CANONICAL_OBSERVATION_BYTES) throw new Error('invalid_transaction_observation');
      return account(JSON.stringify(entry));
    }
    if (typeof entry !== 'object' || entry === undefined) throw new Error('invalid_transaction_observation');
    if (seen.has(entry)) throw new Error('invalid_transaction_observation');
    seen.add(entry);
    try {
      if (Array.isArray(entry)) {
        if (!denseArray(entry, MAX_CANONICAL_NODES)) throw new Error('invalid_transaction_observation');
        const values = entry.map((item) => visit(item, depth + 1));
        return account(`[${values.join(',')}]`);
      }
      const prototype = Object.getPrototypeOf(entry);
      if (prototype !== Object.prototype && prototype !== null) throw new Error('invalid_transaction_observation');
      const keys = Object.keys(entry).sort();
      if (keys.length !== Object.getOwnPropertyNames(entry).length
        || Object.getOwnPropertySymbols(entry).length > 0) {
        throw new Error('invalid_transaction_observation');
      }
      const values: string[] = [];
      for (const key of keys) {
        values.push(`${account(JSON.stringify(key))}:${visit((entry as Record<string, unknown>)[key], depth + 1)}`);
      }
      return account(`{${values.join(',')}}`);
    } finally {
      seen.delete(entry);
    }
  };
  return visit(value, 0);
}

function validateBronze(
  value: unknown,
  staticAccountCount: number,
): asserts value is PumpV2BronzeTransaction {
  if (!isRecord(value)
    || !hasExactKeys(value, BRONZE_KEYS)
    || value.schemaVersion !== 'PUMP_V2_BRONZE_TRANSACTION_1'
    || !safeNonNegativeInteger(value.slot)
    || !safeNonNegativeInteger(value.transactionIndex)
    || !validSignature(value.signature)
    || !validCanonicalUtc(value.blockTime)
    || (value.executionStatus !== 'succeeded' && value.executionStatus !== 'failed')
    || !validU64Text(value.feeLamports)
    || !denseArray(value.logMessages, MAX_LOG_MESSAGES)
    || !value.logMessages.every((entry) => typeof entry === 'string')
    || !denseArray(value.accountKeys, 256)
    || !value.accountKeys.every(validPublicKey)
    || !denseArray(value.preBalancesLamports, 256)
    || !value.preBalancesLamports.every(validU64Text)
    || !denseArray(value.postBalancesLamports, 256)
    || !value.postBalancesLamports.every(validU64Text)
    || value.preBalancesLamports.length !== value.accountKeys.length
    || value.postBalancesLamports.length !== value.accountKeys.length
    || !denseArray(value.instructions, MAX_CANDIDATES_PER_TRANSACTION)
    || !value.instructions.every((instruction) => validInstruction(instruction, value.accountKeys as string[]))
    || !denseArray(value.pumpCandidates, MAX_CANDIDATES_PER_TRANSACTION)
    || !value.pumpCandidates.every((candidate) => validCandidate(candidate, value.executionStatus as 'succeeded' | 'failed'))
    || !denseArray(value.quarantines, MAX_CANDIDATES_PER_TRANSACTION)
    || !value.quarantines.every(validQuarantine)
    || !denseArray(value.preTokenBalances, 256)
    || !validTokenBalances(value.preTokenBalances, value.accountKeys.length)
    || !denseArray(value.postTokenBalances, 256)
    || !validTokenBalances(value.postTokenBalances, value.accountKeys.length)) {
    throw new Error('invalid_transaction_observation');
  }
  const bronze = value as unknown as PumpV2BronzeTransaction;
  canonicalJson(bronze);
  let logBytes = 0;
  for (const message of bronze.logMessages) {
    logBytes += Buffer.byteLength(message, 'utf8');
    if (Buffer.byteLength(message, 'utf8') > MAX_LOG_BYTES || logBytes > MAX_LOG_BYTES) {
      throw new Error('invalid_transaction_observation');
    }
  }
  if (staticAccountCount <= 0
    || staticAccountCount > bronze.accountKeys.length
    || new Set(bronze.accountKeys).size !== bronze.accountKeys.length
    || !validInstructionSequence(bronze.instructions)
    || bronze.instructions.some((instruction) => (
      instruction.instructionLocation === 'top_level'
      && instruction.programIdIndex >= staticAccountCount
    ))) {
    throw new Error('invalid_transaction_observation');
  }
  const expectedProjection = expectedPumpProjection(bronze);
  if (canonicalJson(bronze.pumpCandidates) !== canonicalJson(expectedProjection.pumpCandidates)
    || canonicalJson(bronze.quarantines) !== canonicalJson(expectedProjection.quarantines)) {
    throw new Error('invalid_transaction_observation');
  }
  const candidates = new Map<string, Record<string, unknown>>();
  for (const candidate of value.pumpCandidates as unknown as Record<string, unknown>[]) {
    if (candidates.has(candidate.eventKey as string)) throw new Error('invalid_transaction_observation');
    candidates.set(candidate.eventKey as string, candidate);
  }
  const quarantines = new Map<string, Record<string, unknown>>();
  for (const quarantine of value.quarantines as unknown as Record<string, unknown>[]) {
    const eventKey = quarantine.eventKey as string;
    if (quarantines.has(eventKey)) throw new Error('invalid_transaction_observation');
    const candidate = candidates.get(eventKey);
    if (!candidate
      || candidate.parserStatus !== 'quarantined'
      || candidate.discriminatorHex !== quarantine.discriminatorHex) {
      throw new Error('invalid_transaction_observation');
    }
    quarantines.set(eventKey, quarantine);
  }
  for (const candidate of candidates.values()) {
    if ((candidate.parserStatus === 'quarantined') !== quarantines.has(candidate.eventKey as string)) {
      throw new Error('invalid_transaction_observation');
    }
  }
}

type NormalizedTransactionObservation = {
  slot: number;
  transactionIndex: number;
  signature: string;
  bytesHash: string;
  blockTime: string;
  pumpCandidateCount: number;
  quarantinedCandidateCount: number;
};

function normalizeTransactionObservation(
  value: unknown,
  manifest: OldFaithfulEpochSourceManifest,
  provenance: JetstreamerAdapterProvenance,
  sourceHash: string,
  adapterHash: string,
  requestedRange: OldFaithfulCoverageRange,
): NormalizedTransactionObservation {
  if (!isRecord(value)
    || !hasExactKeys(value, OBSERVATION_KEYS)
    || value.schemaVersion !== 'OLD_FAITHFUL_PUMP_V2_OBSERVATION_1'
    || value.sourceManifestSha256 !== sourceHash
    || value.adapterProvenanceSha256 !== adapterHash
    || !safeNonNegativeInteger(value.staticAccountCount)
    || value.staticAccountCount === 0) {
    throw new Error('invalid_transaction_observation');
  }
  const observedManifest = normalizeSourceManifest(value.sourceManifest);
  const observedProvenance = normalizeAdapterProvenance(value.adapterProvenance);
  if (sourceManifestSha256(observedManifest) !== sourceHash) throw new Error('transaction_source_manifest_mismatch');
  if (adapterProvenanceSha256(observedProvenance) !== adapterHash) throw new Error('transaction_adapter_provenance_mismatch');
  validateBronze(value.bronze, value.staticAccountCount);
  if (value.bronze.slot < requestedRange.startInclusive || value.bronze.slot >= requestedRange.endExclusive) {
    throw new Error('transaction_outside_requested_range');
  }
  const normalized = {
    schemaVersion: 'OLD_FAITHFUL_PUMP_V2_OBSERVATION_1',
    sourceManifest: manifest,
    sourceManifestSha256: sourceHash,
    adapterProvenance: provenance,
    adapterProvenanceSha256: adapterHash,
    staticAccountCount: value.staticAccountCount,
    bronze: value.bronze,
  };
  const bytes = canonicalJson(normalized);
  return {
    slot: value.bronze.slot,
    transactionIndex: value.bronze.transactionIndex,
    signature: value.bronze.signature,
    bytesHash: createHash('sha256')
      .update(`OLD_FAITHFUL_PUMP_V2_OBSERVATION_1\n${bytes}`)
      .digest('hex'),
    blockTime: value.bronze.blockTime,
    pumpCandidateCount: value.bronze.pumpCandidates.length,
    quarantinedCandidateCount: value.bronze.quarantines.length,
  };
}

export function buildOldFaithfulCoverageLedger(
  sourceManifest: unknown,
  adapterProvenance: unknown,
  requestedSlotRange: unknown,
  slotsFileBytes: unknown,
  blockObservations: unknown,
  transactionObservations: unknown,
): OldFaithfulCoverageLedger {
  const manifest = normalizeSourceManifest(sourceManifest);
  const provenance = normalizeAdapterProvenance(adapterProvenance);
  const requestedRange = normalizeRequestedRange(requestedSlotRange, manifest.slotRange);
  const expectedBlockSlots = parseSlotsInventory(slotsFileBytes, manifest, requestedRange);
  if (!denseArray(blockObservations, MAX_BLOCK_OBSERVATIONS)
    || !denseArray(transactionObservations, MAX_TRANSACTION_OBSERVATIONS)) {
    throw new Error('invalid_coverage_input');
  }

  const bySlot = new Map<number, JetstreamerBlockObservation>();
  const resolvedProvisionalSlots = new Set<number>();
  let duplicateBlockObservationCount = 0;
  let resolvedPossibleLeaderSkippedCount = 0;
  for (const raw of blockObservations) {
    const observation = normalizeBlockObservation(raw, requestedRange);
    const existing = bySlot.get(observation.slot);
    if (!existing) {
      bySlot.set(observation.slot, observation);
      continue;
    }
    if (existing.status === observation.status) {
      if (existing.blockTimeUnixSeconds !== observation.blockTimeUnixSeconds) {
        throw new Error('conflicting_block_observation');
      }
      duplicateBlockObservationCount += 1;
      continue;
    }
    if (!expectedBlockSlots.has(observation.slot)) throw new Error('conflicting_block_observation');
    const resolved = existing.status === 'block' ? existing : observation;
    bySlot.set(observation.slot, resolved);
    if (resolvedProvisionalSlots.has(observation.slot)) duplicateBlockObservationCount += 1;
    else {
      resolvedProvisionalSlots.add(observation.slot);
      resolvedPossibleLeaderSkippedCount += 1;
    }
  }

  const ordered = [...bySlot.values()].sort((left, right) => left.slot - right.slot);
  const observedRanges = rangesFromSlots(ordered.map((entry) => entry.slot));
  const missing = missingRanges(requestedRange, observedRanges);
  const callbackRangeComplete = missing.length === 0;
  const archiveSlotInventoryReconciled = callbackRangeComplete
    && [...expectedBlockSlots].every((slot) => bySlot.get(slot)?.status === 'block')
    && ordered.every((observation) => (
      observation.status === 'block'
        ? expectedBlockSlots.has(observation.slot)
        : !expectedBlockSlots.has(observation.slot)
    ));

  const sourceHash = sourceManifestSha256(manifest);
  const adapterHash = adapterProvenanceSha256(provenance);
  const transactionsByCoordinate = new Map<string, NormalizedTransactionObservation>();
  const coordinateBySignature = new Map<string, string>();
  let duplicateTransactionObservationCount = 0;
  for (const raw of transactionObservations) {
    const observation = normalizeTransactionObservation(
      raw,
      manifest,
      provenance,
      sourceHash,
      adapterHash,
      requestedRange,
    );
    const coveredBlock = bySlot.get(observation.slot);
    if (!coveredBlock || coveredBlock.status !== 'block') throw new Error('transaction_without_covered_block');
    const expectedBlockTime = new Date((coveredBlock.blockTimeUnixSeconds as number) * 1_000).toISOString();
    if (observation.blockTime !== expectedBlockTime) throw new Error('transaction_block_time_mismatch');
    const coordinate = `${observation.slot}:${observation.transactionIndex}`;
    const existingAtCoordinate = transactionsByCoordinate.get(coordinate);
    const existingCoordinateForSignature = coordinateBySignature.get(observation.signature);
    if (existingAtCoordinate) {
      if (existingAtCoordinate.signature !== observation.signature
        || existingAtCoordinate.bytesHash !== observation.bytesHash) {
        throw new Error('conflicting_transaction_observation');
      }
      duplicateTransactionObservationCount += 1;
      continue;
    }
    if (existingCoordinateForSignature !== undefined && existingCoordinateForSignature !== coordinate) {
      throw new Error('conflicting_transaction_observation');
    }
    transactionsByCoordinate.set(coordinate, observation);
    coordinateBySignature.set(observation.signature, coordinate);
  }

  const orderedTransactions = [...transactionsByCoordinate.values()].sort((left, right) => (
    left.slot - right.slot
    || left.transactionIndex - right.transactionIndex
    || left.signature.localeCompare(right.signature)
  ));
  const blockBytes = ordered
    .map((entry) => `${entry.slot}:${entry.status}:${entry.blockTimeUnixSeconds === null ? 'null' : entry.blockTimeUnixSeconds}`)
    .join('\n');
  const transactionBytes = orderedTransactions
    .map((entry) => `${entry.slot}:${entry.transactionIndex}:${entry.signature}:${entry.bytesHash}`)
    .join('\n');

  return {
    schemaVersion: 'OLD_FAITHFUL_COVERAGE_LEDGER_1',
    sourceManifestSha256: sourceHash,
    adapterProvenanceSha256: adapterHash,
    slotRange: requestedRange,
    callbackRangeComplete,
    archiveSlotInventoryReconciled,
    coverageStatus: archiveSlotInventoryReconciled
      ? 'ARCHIVE_SLOT_INVENTORY_RECONCILED'
      : 'CALLBACK_RANGE_ONLY_NOT_ARCHIVE_VERIFIED',
    researchReady: false,
    observedRanges,
    missingRanges: missing,
    blockCount: ordered.filter((entry) => entry.status === 'block').length,
    possibleLeaderSkippedCount: ordered.filter((entry) => entry.status === 'possible_leader_skipped').length,
    resolvedPossibleLeaderSkippedCount,
    duplicateBlockObservationCount,
    transactionProjection: 'PUMP_V2_NON_VOTE_ONLY',
    transactionObservationCount: orderedTransactions.length,
    duplicateTransactionObservationCount,
    pumpCandidateCount: orderedTransactions.reduce((sum, entry) => sum + entry.pumpCandidateCount, 0),
    quarantinedCandidateCount: orderedTransactions.reduce((sum, entry) => sum + entry.quarantinedCandidateCount, 0),
    observationsSha256: createHash('sha256')
      .update(`OLD_FAITHFUL_COVERAGE_LEDGER_1\n${sourceHash}\n${adapterHash}\n${requestedRange.startInclusive}:${requestedRange.endExclusive}\nblocks\n${blockBytes}\ntransactions\n${transactionBytes}`)
      .digest('hex'),
  };
}
