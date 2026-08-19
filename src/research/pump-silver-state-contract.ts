import { createHash } from 'node:crypto';
import { types as utilTypes } from 'node:util';
import bs58 from 'bs58';
import { PUMP_PROGRAM_ID, WSOL, deriveBondingCurve } from '../pump-address.js';
import { decodePublicKey, encodePublicKey, findProgramAddress } from '../solana-pda.js';
import {
  evaluatePumpSilverTransaction,
  type PumpSilverContractEvent,
  type PumpSilverTransactionContract,
} from './pump-silver-contract.js';
import type { PumpV2BronzeTransaction } from './pump-v2-bronze.js';

const STATE_HASH_DOMAIN = 'PUMP_SILVER_STATE_CONTRACT_1';
const SNAPSHOT_HASH_DOMAIN = 'PUMP_SILVER_STATE_SNAPSHOT_SET_1';
const RERUN_HASH_DOMAIN = 'PUMP_SILVER_STATE_RERUN_1';
const EVENT_BINDING_HASH_DOMAIN = 'PUMP_SILVER_STATE_EVENT_BINDING_HASH_1';
const CURVE_DISCRIMINATOR = '17b7f83760d8ac60';
const PUMP_IDL_SHA256 = 'b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49';
const PUMP_DOCS_COMMIT = '9c82f61cb711b044a17f770ab8ce9f9bdf78f333';
const TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const TOKEN_2022_PROGRAM = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';
const ASSOCIATED_TOKEN_PROGRAM = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL';
const SYNTHETIC_ACCOUNT_RENT_RESERVE = 1_000_000n;
const U64_MAX = (1n << 64n) - 1n;
const U128_MAX = (1n << 128n) - 1n;
const U64_TEXT = /^(0|[1-9]\d*)$/;
const HASH_HEX = /^[0-9a-f]{64}$/;
const GOLDEN_EPOCH_CID = 'bafkreiacv4rclo6minmaq75znwrqj5oxg7l5cim3ami4snfdptltigowka';
const GOLDEN_CAR_SHA256 = 'ec5ce3b235ac4e5774656598a2e548e11873c9603fcc324f7f4ca1d0077bb326';
const GOLDEN_CAR_SIZE = '4096';
const GOLDEN_INVENTORY_SHA256 = '1e188b728002793e3b64d6a7072c370d4c0f90c657ce10140f276a908b35b5b8';
const GOLDEN_INVENTORY_SIZE = '512';
const GOLDEN_INVENTORY_ENTRIES = '1';
const GOLDEN_SLOT_START = '361000001';
const GOLDEN_SLOT_END = '361000002';
const GOLDEN_PARSER_GIT_SHA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const GOLDEN_REDUCER_GIT_SHA = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const GOLDEN_ADAPTER_GIT_SHA = 'cccccccccccccccccccccccccccccccccccccccc';
const GOLDEN_FIXTURE_BINDING_HASHES = new Set<string>([
  'aa47cfd82dd2c5448a0a8a6758610dd95f069508adb3e17f02f992bef532d4f5',
  '617a8185d49cb9fd64c7cd8ae36e86e031b02dc284727f69434c523053abddb2',
  'd557dc91de3e4ae331b3d10d90736f38a9ea95b06cb503058f87654064e14468',
  '260579be7cf3aaadff2e56a96d9448c3bc9ba1470934e810a68af853a369c141',
  'a96d064df76785a0ba2f41cc0bbecc4a2def78066c876507957c81c50caddf39',
  'b39d08d9471c7bdfbaba755be8af3c4db27ac39d14cfea04827870a2223bf82a',
  'f9e936233b985dbcef96b50ade4a6750dfa0397d8339831d440270f5d97b79ff',
  'bc239d639c4e40a3446d4da99457ea06c4711189ec8174bbe6d3fe0dd189ae4d',
]);
const GOLDEN_PILOT_BUDGET = Object.freeze({
  maxBytesRead: '1048576',
  maxBytesWritten: '1048576',
  maxSlots: '1',
  maxCallbacks: '16',
  maxRuntimeMillis: '60000',
  maxStorageBytes: '2097152',
});

const EVIDENCE_KEYS = Object.freeze([
  'schemaVersion', 'eventKey', 'registry', 'snapshots', 'provenance', 'coverage', 'pilotBudget',
]);
const REGISTRY_KEYS = Object.freeze([
  'schemaVersion', 'approved', 'researchReady', 'evidenceClass', 'realActivationSlotRange',
  'activationSlotEvidence', 'officialDocsCommit', 'pumpIdlSha256', 'pumpProgramId',
  'bondingCurveLayout', 'bondingCurveDiscriminatorHex', 'legacyTokenProgramId',
  'token2022ProgramId', 'supportedToken2022Extensions', 'fixtureBindingSha256',
]);
const SNAPSHOT_KEYS = Object.freeze([
  'schemaVersion', 'accountRole', 'boundary', 'source', 'evidenceClass', 'signature', 'slot',
  'transactionIndex', 'parentInstructionLocation', 'parentInstructionIndex', 'parentStackHeight',
  'eventInstructionLocation', 'eventParentInstructionIndex', 'eventInstructionIndex', 'eventStackHeight',
  'eventKey', 'accountPubkey', 'ownerProgramId', 'lamports', 'executable', 'dataHex', 'dataSha256', 'writeOrdinal',
  'transactionWideBalanceOnly', 'stateAuthority', 'token2022Extensions',
]);
const PROVENANCE_KEYS = Object.freeze([
  'schemaVersion', 'evidenceClass', 'epochCid', 'carSha256', 'carFileSizeBytes',
  'slotInventorySha256', 'slotInventorySizeBytes', 'slotInventoryEntryCount', 'slotRange',
  'parserGitSha', 'reducerGitSha', 'adapterGitSha', 'sourceSha256', 'eventBindingSha256', 'outputSha256',
  'cumulativeSourceBytes', 'deterministicRerunHash',
]);
const COVERAGE_KEYS = Object.freeze([
  'expectedSlots', 'observedSlots', 'skippedSlots', 'quarantinedSlots',
  'expectedCallbacks', 'observedCallbacks', 'skippedCallbacks', 'quarantinedCallbacks',
  'quarantineByReason',
]);
const BUDGET_KEYS = Object.freeze([
  'maxBytesRead', 'maxBytesWritten', 'maxSlots', 'maxCallbacks', 'maxRuntimeMillis', 'maxStorageBytes',
]);
const SLOT_RANGE_KEYS = Object.freeze(['startInclusive', 'endExclusive']);
const EVENT_BINDING_KEYS = Object.freeze([
  'schemaVersion', 'eventKey', 'variant', 'kind', 'mint', 'bondingCurve', 'tokenProgramId', 'quoteTokenProgramId',
  'baseBondingCurveTokenAccount', 'quoteBondingCurveTokenAccount', 'quoteMint', 'signature', 'slot',
  'transactionIndex', 'executionStatus', 'parentInstructionLocation', 'parentInstructionIndex', 'parentStackHeight',
  'eventInstructionLocation', 'eventParentInstructionIndex', 'eventInstructionIndex', 'eventStackHeight',
  'sourcePhase6aSha256', 'creator', 'mayhemMode', 'isCashbackCoin', 'tokenDecimals', 'quoteDecimals', 'tokenAmount', 'quoteAmount',
  'virtualTokenReserves', 'virtualQuoteReserves', 'realTokenReserves', 'realQuoteReserves',
]);

export const PUMP_SILVER_STATE_QUARANTINE_REASONS = Object.freeze([
  'INVALID_INPUT_SCHEMA',
  'PHASE6A_INVALID',
  'RESEARCH_READY_FORBIDDEN',
  'SELF_APPROVED_REGISTRY',
  'REAL_ACTIVATION_RANGE_FORBIDDEN',
  'INVALID_REGISTRY',
  'MISSING_RAW_ACCOUNT_BYTES',
  'TRANSACTION_WIDE_BALANCE_ONLY',
  'EVENT_FIELDS_AS_STATE_AUTHORITY',
  'INVALID_SNAPSHOT_COORDINATES',
  'INVALID_SNAPSHOT_ORDER',
  'DUPLICATE_SNAPSHOT',
  'AMBIGUOUS_ACCOUNT_WRITES',
  'WRONG_ACCOUNT_OWNER',
  'ACCOUNT_IDENTITY_MISMATCH',
  'ACCOUNT_LAMPORTS_MISMATCH',
  'RAW_ACCOUNT_HASH_MISMATCH',
  'UNKNOWN_ACCOUNT_LAYOUT',
  'UNSUPPORTED_TOKEN_2022_EXTENSION',
  'UNSAFE_INTEGER',
  'SUPPLY_OR_DECIMAL_MISMATCH',
  'RESERVE_MISMATCH',
  'EVENT_STATE_CONFLICT',
  'INVALID_CAR_PROVENANCE',
  'INVALID_SLOT_INVENTORY',
  'INCOMPLETE_COVERAGE',
  'QUARANTINE_ACCOUNTING_MISMATCH',
  'RERUN_HASH_MISMATCH',
  'OBSERVABILITY_LABEL_FORBIDDEN',
  'OBSERVABILITY_CANONICAL_INFLUENCE_FORBIDDEN',
  'MINT_STATE_MISMATCH',
  'CURVE_STATE_MISMATCH',
  'FAILED_TRANSACTION',
  'FAILED_TRANSACTION_ROLLBACK_MISMATCH',
] as const);

export type PumpSilverStateQuarantineReason = typeof PUMP_SILVER_STATE_QUARANTINE_REASONS[number];

export const PUMP_SILVER_STATE_OBSERVABILITY_CONTRACT = Object.freeze({
  schemaVersion: 'PUMP_SILVER_STATE_OBSERVABILITY_1' as const,
  transport: 'SIDE_CHANNEL_CONSUMER_ONLY' as const,
  canonicalInfluence: false as const,
  metricsRequiredLater: Object.freeze([
    'callbacks_received_total',
    'transactions_processed_total',
    'state_snapshot_pairs_received_total',
    'silver_state_accepted_total',
    'silver_state_quarantined_total',
    'coverage_expected',
    'coverage_observed',
    'coverage_skipped',
    'coverage_quarantined',
    'rerun_hash_match_total',
    'rerun_hash_mismatch_total',
    'current_slot',
    'last_completed_slot',
    'bytes_read_total',
    'bytes_written_total',
    'queue_depth',
    'wal_result_total',
    'checkpoint_result_total',
    'callback_duration_seconds',
    'state_evaluation_duration_seconds',
  ]),
  allowedLabels: Object.freeze({
    stage: Object.freeze(['reducer', 'bronze', 'silver']),
    result: Object.freeze(['accepted', 'quarantined', 'skipped', 'match', 'mismatch', 'success', 'failure']),
    reason: PUMP_SILVER_STATE_QUARANTINE_REASONS,
  }),
  forbiddenLabels: Object.freeze([
    'mint', 'signature', 'wallet', 'accountPubkey', 'slot', 'transactionId', 'eventKey', 'errorText',
  ]),
  maximumReasonCodes: PUMP_SILVER_STATE_QUARANTINE_REASONS.length,
  maximumStageValues: 3,
  maximumResultValues: 7,
});

type DecodedBondingCurve = {
  virtualTokenReserves: string;
  virtualQuoteReserves: string;
  realTokenReserves: string;
  realQuoteReserves: string;
  tokenTotalSupply: string;
  complete: boolean;
  creator: string;
  isMayhemMode: boolean;
  isCashbackCoin: boolean;
  quoteMint: string;
};

type DecodedMint = {
  tokenProgramId: string;
  mintAuthority: string | null;
  supply: string;
  decimals: number;
  initialized: boolean;
  freezeAuthority: string | null;
  token2022Extensions: string[];
};

type DecodedTokenAccount = {
  tokenProgramId: string;
  mint: string;
  owner: string;
  amount: string;
  delegate: string | null;
  state: 'initialized' | 'frozen';
  isNativeReserve: string | null;
  delegatedAmount: string;
  closeAuthority: string | null;
  token2022Extensions: string[];
};

export type PumpSilverStateResult = {
  schemaVersion: 'PUMP_SILVER_STATE_CONTRACT_1';
  status: 'FIXTURE_VALID' | 'QUARANTINED';
  approved: false;
  researchReady: false;
  pilotEligible: false;
  eventKey: string | null;
  sourcePhase6aSha256: string | null;
  eventBindingSha256: string | null;
  evidenceClass: 'SYNTHETIC_TEST_ONLY';
  state: {
    before: {
      bondingCurve: DecodedBondingCurve;
      mint: DecodedMint;
      baseBondingCurveTokenAccount: DecodedTokenAccount;
      quoteBondingCurveTokenAccount: DecodedTokenAccount | null;
    };
    after: {
      bondingCurve: DecodedBondingCurve;
      mint: DecodedMint;
      baseBondingCurveTokenAccount: DecodedTokenAccount;
      quoteBondingCurveTokenAccount: DecodedTokenAccount | null;
    };
  } | null;
  provenance: unknown | null;
  coverage: unknown | null;
  quarantineReasons: PumpSilverStateQuarantineReason[];
  canonicalHash: string;
};

function canonicalJson(value: unknown, seen = new Set<object>()): string {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new Error('unsafe canonical number');
    return String(value);
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) throw new Error('cyclic canonical value');
    seen.add(value);
    const entries: string[] = [];
    for (let index = 0; index < value.length; index += 1) {
      if (!Object.hasOwn(value, index)) throw new Error('sparse canonical array');
      entries.push(canonicalJson(value[index], seen));
    }
    seen.delete(value);
    return `[${entries.join(',')}]`;
  }
  if (typeof value === 'object') {
    if (seen.has(value)) throw new Error('cyclic canonical value');
    seen.add(value);
    const record = value as Record<string, unknown>;
    const pairs = Object.keys(record).sort().map((key) => {
      if (record[key] === undefined) throw new Error('undefined canonical value');
      return `${JSON.stringify(key)}:${canonicalJson(record[key], seen)}`;
    });
    seen.delete(value);
    return `{${pairs.join(',')}}`;
  }
  throw new Error('unsupported canonical value');
}

function domainHash(domain: string, value: unknown): string {
  return createHash('sha256').update(`${domain}\u0000${canonicalJson(value)}`, 'utf8').digest('hex');
}

function immutableSnapshot<T>(value: T): T {
  const snapshot = structuredClone(value);
  const freeze = (candidate: unknown): void => {
    if (candidate === null || typeof candidate !== 'object' || Object.isFrozen(candidate)) return;
    for (const nested of Object.values(candidate)) freeze(nested);
    Object.freeze(candidate);
  };
  freeze(snapshot);
  return snapshot;
}

function sha256Bytes(bytes: Buffer): string {
  return createHash('sha256').update(bytes).digest('hex');
}

function canonicalUnsigned(value: unknown, maximum: bigint): value is string {
  if (typeof value !== 'string' || value.length > 39 || !U64_TEXT.test(value)) return false;
  try {
    return BigInt(value) <= maximum;
  } catch {
    return false;
  }
}

function objectValue(value: unknown): Record<string, unknown> | undefined {
  return typeof value === 'object' && value !== null && !utilTypes.isProxy(value) && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function hasExactOwnKeys(value: object, expected: readonly string[]): boolean {
  try {
    const names = Object.getOwnPropertyNames(value);
    if (Object.getOwnPropertySymbols(value).length !== 0
      || names.length !== expected.length
      || !names.every((name) => expected.includes(name))) return false;
    for (const name of names) {
      const descriptor = Object.getOwnPropertyDescriptor(value, name);
      const expectedEnumerable = Array.isArray(value) && name === 'length' ? false : true;
      if (!descriptor || descriptor.enumerable !== expectedEnumerable || !Object.hasOwn(descriptor, 'value')) return false;
    }
    return true;
  } catch {
    return false;
  }
}

function isExactArray(value: unknown, maximumLength = 128): value is unknown[] {
  try {
    if (utilTypes.isProxy(value) || !Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype
      || Object.getOwnPropertySymbols(value).length !== 0 || value.length > maximumLength) return false;
    const names = Object.getOwnPropertyNames(value);
    if (names.length !== value.length + 1 || names[names.length - 1] !== 'length') return false;
    const expected = Array.from({ length: value.length }, (_entry, index) => String(index));
    expected.push('length');
    return names.every((name, index) => name === expected[index]) && hasExactOwnKeys(value, expected);
  } catch {
    return false;
  }
}

function bytesFromHex(value: unknown): Buffer | undefined {
  if (typeof value !== 'string' || value.length === 0 || value.length > 512 || !value.length || value.length % 2 !== 0
    || !/^[0-9a-f]+$/.test(value)) return undefined;
  return Buffer.from(value, 'hex');
}

function readU64(bytes: Buffer, offset: number): string {
  return bytes.readBigUInt64LE(offset).toString();
}

function readPublicKey(bytes: Buffer, offset: number): string {
  return bs58.encode(bytes.subarray(offset, offset + 32));
}

function decodeBondingCurve(bytes: Buffer): DecodedBondingCurve | undefined {
  if (bytes.length !== 115 || bytes.subarray(0, 8).toString('hex') !== CURVE_DISCRIMINATOR) return undefined;
  const complete = bytes[48];
  const isMayhemMode = bytes[81];
  const isCashbackCoin = bytes[82];
  if ((complete !== 0 && complete !== 1) || (isMayhemMode !== 0 && isMayhemMode !== 1)
    || (isCashbackCoin !== 0 && isCashbackCoin !== 1)) return undefined;
  return {
    virtualTokenReserves: readU64(bytes, 8),
    virtualQuoteReserves: readU64(bytes, 16),
    realTokenReserves: readU64(bytes, 24),
    realQuoteReserves: readU64(bytes, 32),
    tokenTotalSupply: readU64(bytes, 40),
    complete: complete === 1,
    creator: readPublicKey(bytes, 49),
    isMayhemMode: isMayhemMode === 1,
    isCashbackCoin: isCashbackCoin === 1,
    quoteMint: readPublicKey(bytes, 83),
  };
}

function decodeCOptionPublicKey(bytes: Buffer, offset: number): string | null | undefined {
  const tag = bytes.readUInt32LE(offset);
  if (tag === 0) return bytes.subarray(offset + 4, offset + 36).every((byte) => byte === 0) ? null : undefined;
  if (tag === 1) return readPublicKey(bytes, offset + 4);
  return undefined;
}

function decodeMint(bytes: Buffer, tokenProgramId: string, extensions: unknown): DecodedMint | undefined {
  if (bytes.length !== 82 || (tokenProgramId !== TOKEN_PROGRAM && tokenProgramId !== TOKEN_2022_PROGRAM)
    || !isExactArray(extensions, 0) || extensions.length !== 0) return undefined;
  const mintAuthority = decodeCOptionPublicKey(bytes, 0);
  const freezeAuthority = decodeCOptionPublicKey(bytes, 46);
  const initialized = bytes[45];
  if (mintAuthority === undefined || freezeAuthority === undefined || (initialized !== 0 && initialized !== 1)) return undefined;
  return {
    tokenProgramId,
    mintAuthority,
    supply: readU64(bytes, 36),
    decimals: bytes[44]!,
    initialized: initialized === 1,
    freezeAuthority,
    token2022Extensions: [],
  };
}

function decodeCOptionU64(bytes: Buffer, offset: number): string | null | undefined {
  const tag = bytes.readUInt32LE(offset);
  const payload = bytes.subarray(offset + 4, offset + 12);
  if (tag === 0) return payload.every((byte) => byte === 0) ? null : undefined;
  if (tag === 1) return payload.readBigUInt64LE(0).toString();
  return undefined;
}

function decodeTokenAccount(bytes: Buffer, tokenProgramId: string, extensions: unknown): DecodedTokenAccount | undefined {
  if (bytes.length !== 165 || (tokenProgramId !== TOKEN_PROGRAM && tokenProgramId !== TOKEN_2022_PROGRAM)
    || !isExactArray(extensions, 0) || extensions.length !== 0) return undefined;
  const delegate = decodeCOptionPublicKey(bytes, 72);
  const isNativeReserve = decodeCOptionU64(bytes, 109);
  const closeAuthority = decodeCOptionPublicKey(bytes, 129);
  const stateByte = bytes[108];
  const delegatedAmount = readU64(bytes, 121);
  if (delegate === undefined || isNativeReserve === undefined || closeAuthority === undefined
    || (stateByte !== 1 && stateByte !== 2) || (delegate === null && delegatedAmount !== '0')) return undefined;
  return {
    tokenProgramId,
    mint: readPublicKey(bytes, 0),
    owner: readPublicKey(bytes, 32),
    amount: readU64(bytes, 64),
    delegate,
    state: stateByte === 1 ? 'initialized' : 'frozen',
    isNativeReserve,
    delegatedAmount,
    closeAuthority,
    token2022Extensions: [],
  };
}

function quarantine(
  reasons: Iterable<PumpSilverStateQuarantineReason>,
  eventKey: string | null = null,
  sourcePhase6aSha256: string | null = null,
  eventBindingSha256: string | null = null,
): PumpSilverStateResult {
  const reasonSet = new Set(reasons);
  const quarantineReasons = PUMP_SILVER_STATE_QUARANTINE_REASONS.filter((reason) => reasonSet.has(reason));
  const unsigned = {
    schemaVersion: 'PUMP_SILVER_STATE_CONTRACT_1' as const,
    status: 'QUARANTINED' as const,
    approved: false as const,
    researchReady: false as const,
    pilotEligible: false as const,
    eventKey,
    sourcePhase6aSha256,
    eventBindingSha256,
    evidenceClass: 'SYNTHETIC_TEST_ONLY' as const,
    state: null,
    provenance: null,
    coverage: null,
    quarantineReasons,
  };
  return immutableSnapshot({ ...unsigned, canonicalHash: domainHash(STATE_HASH_DOMAIN, unsigned) });
}

function canonicalEventKey(value: Record<string, unknown>): string {
  return [
    value.slot,
    value.transactionIndex,
    value.parentInstructionLocation,
    value.parentInstructionIndex,
    value.parentStackHeight ?? 'null',
    value.eventInstructionLocation,
    value.eventParentInstructionIndex ?? 'null',
    value.eventInstructionIndex,
    value.eventStackHeight ?? 'null',
    value.variant,
  ].join(':');
}

function phase6aEventBinding(contract: PumpSilverTransactionContract, event: PumpSilverContractEvent) {
  const payload = event.event;
  if (payload.eventType !== 'trade' || event.kind === 'create') return undefined;
  const normalized = {
    schemaVersion: 'PUMP_SILVER_STATE_EVENT_BINDING_1',
    variant: event.variant,
    kind: event.kind,
    mint: event.mint,
    bondingCurve: event.bondingCurve,
    tokenProgramId: event.roles.token_program ?? event.roles.base_token_program,
    quoteTokenProgramId: event.roles.quote_token_program ?? null,
    baseBondingCurveTokenAccount: event.roles.associated_bonding_curve ?? event.roles.associated_base_bonding_curve,
    quoteBondingCurveTokenAccount: event.roles.associated_quote_bonding_curve ?? null,
    quoteMint: payload.quoteMint,
    signature: contract.source.signature,
    slot: String(contract.source.slot),
    transactionIndex: contract.source.transactionIndex,
    executionStatus: contract.source.executionStatus,
    parentInstructionLocation: event.parentCoordinate.instructionLocation,
    parentInstructionIndex: event.parentCoordinate.instructionIndex,
    parentStackHeight: event.parentCoordinate.stackHeight ?? null,
    eventInstructionLocation: event.eventCoordinate.instructionLocation,
    eventParentInstructionIndex: event.eventCoordinate.parentInstructionIndex ?? null,
    eventInstructionIndex: event.eventCoordinate.instructionIndex,
    eventStackHeight: event.eventCoordinate.stackHeight ?? null,
    sourcePhase6aSha256: contract.canonicalHash,
    creator: payload.creator,
    mayhemMode: payload.mayhemMode,
    isCashbackCoin: false as const,
    tokenDecimals: event.tokenDecimals,
    quoteDecimals: event.quoteDecimals,
    tokenAmount: payload.tokenAmount,
    quoteAmount: payload.quoteAmount,
    virtualTokenReserves: payload.virtualTokenReserves,
    virtualQuoteReserves: payload.virtualQuoteReserves,
    realTokenReserves: payload.realTokenReserves,
    realQuoteReserves: payload.realQuoteReserves,
  };
  return { ...normalized, eventKey: canonicalEventKey(normalized) };
}

type PumpSilverStateEventBinding = NonNullable<ReturnType<typeof phase6aEventBinding>>;

function validPublicKey(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 32 || value.length > 44) return false;
  try {
    return bs58.decode(value).length === 32;
  } catch {
    return false;
  }
}

function validSignature(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 80 || value.length > 88) return false;
  try {
    return bs58.decode(value).length === 64;
  } catch {
    return false;
  }
}

function deriveAssociatedToken(owner: string, tokenProgram: string, mint: string): string {
  try {
    const derived = findProgramAddress(
      [decodePublicKey(owner), decodePublicKey(tokenProgram), decodePublicKey(mint)],
      decodePublicKey(ASSOCIATED_TOKEN_PROGRAM),
    );
    return derived ? encodePublicKey(derived.address) : '';
  } catch {
    return '';
  }
}

function validEventBinding(value: unknown): value is PumpSilverStateEventBinding {
  const candidate = objectValue(value);
  if (!candidate || !hasExactOwnKeys(candidate, EVENT_BINDING_KEYS)) return false;
  const variant = candidate.variant;
  if (typeof variant !== 'string'
    || !['buy', 'sell', 'buy_v2', 'sell_v2', 'buy_exact_sol_in', 'buy_exact_quote_in_v2'].includes(variant)) return false;
  const isV2 = variant.endsWith('_v2');
  if (candidate.schemaVersion !== 'PUMP_SILVER_STATE_EVENT_BINDING_1'
    || typeof candidate.eventKey !== 'string'
    || (candidate.kind !== 'buy' && candidate.kind !== 'sell')
    || (variant.startsWith('sell') ? candidate.kind !== 'sell' : candidate.kind !== 'buy')
    || !validPublicKey(candidate.mint) || !validPublicKey(candidate.bondingCurve)
    || !validPublicKey(candidate.baseBondingCurveTokenAccount)
    || (candidate.tokenProgramId !== TOKEN_PROGRAM && candidate.tokenProgramId !== TOKEN_2022_PROGRAM)
    || (isV2
      ? candidate.quoteTokenProgramId !== TOKEN_PROGRAM || !validPublicKey(candidate.quoteBondingCurveTokenAccount)
      : candidate.quoteTokenProgramId !== null || candidate.quoteBondingCurveTokenAccount !== null)
    || candidate.quoteMint !== WSOL || !validSignature(candidate.signature)
    || !canonicalUnsigned(candidate.slot, U64_MAX)
    || typeof candidate.transactionIndex !== 'number' || !Number.isSafeInteger(candidate.transactionIndex)
    || candidate.transactionIndex < 0 || candidate.transactionIndex > 0xffff_ffff
    || (candidate.executionStatus !== 'succeeded' && candidate.executionStatus !== 'failed')
    || candidate.parentInstructionLocation !== 'top_level'
    || typeof candidate.parentInstructionIndex !== 'number' || !Number.isSafeInteger(candidate.parentInstructionIndex)
    || candidate.parentInstructionIndex < 0 || candidate.parentInstructionIndex > 0xffff_ffff
    || candidate.parentStackHeight !== null
    || candidate.eventInstructionLocation !== 'inner'
    || candidate.eventParentInstructionIndex !== candidate.parentInstructionIndex
    || typeof candidate.eventInstructionIndex !== 'number' || !Number.isSafeInteger(candidate.eventInstructionIndex)
    || candidate.eventInstructionIndex < 0 || candidate.eventInstructionIndex > 0xffff_ffff
    || typeof candidate.eventStackHeight !== 'number' || !Number.isSafeInteger(candidate.eventStackHeight)
    || candidate.eventStackHeight < 2 || candidate.eventStackHeight > 9
    || typeof candidate.sourcePhase6aSha256 !== 'string' || !HASH_HEX.test(candidate.sourcePhase6aSha256)
    || !validPublicKey(candidate.creator)
    || typeof candidate.mayhemMode !== 'boolean'
    || candidate.isCashbackCoin !== false
    || typeof candidate.tokenDecimals !== 'number' || !Number.isSafeInteger(candidate.tokenDecimals)
    || candidate.tokenDecimals < 0 || candidate.tokenDecimals > 255
    || candidate.quoteDecimals !== 9
    || !canonicalUnsigned(candidate.tokenAmount, U64_MAX)
    || !canonicalUnsigned(candidate.quoteAmount, U64_MAX)
    || !canonicalUnsigned(candidate.virtualTokenReserves, U64_MAX)
    || !canonicalUnsigned(candidate.virtualQuoteReserves, U64_MAX)
    || !canonicalUnsigned(candidate.realTokenReserves, U64_MAX)
    || !canonicalUnsigned(candidate.realQuoteReserves, U64_MAX)
    || candidate.eventKey !== canonicalEventKey(candidate)) return false;
  try {
    if (deriveBondingCurve(candidate.mint) !== candidate.bondingCurve
      || deriveAssociatedToken(candidate.bondingCurve, candidate.tokenProgramId, candidate.mint)
        !== candidate.baseBondingCurveTokenAccount) return false;
    if (!isV2) return true;
    return typeof candidate.quoteTokenProgramId === 'string'
      && typeof candidate.quoteMint === 'string'
      && deriveAssociatedToken(candidate.bondingCurve, candidate.quoteTokenProgramId, candidate.quoteMint)
        === candidate.quoteBondingCurveTokenAccount;
  } catch {
    return false;
  }
}

function closureValid(expected: unknown, observed: unknown, skipped: unknown, quarantined: unknown): boolean {
  return canonicalUnsigned(expected, U64_MAX) && canonicalUnsigned(observed, U64_MAX)
    && canonicalUnsigned(skipped, U64_MAX) && canonicalUnsigned(quarantined, U64_MAX)
    && BigInt(expected) === BigInt(observed) + BigInt(skipped) + BigInt(quarantined);
}

export function evaluatePumpSilverStateFromBronze(
  bronze: PumpV2BronzeTransaction,
  evidenceValue: unknown,
): PumpSilverStateResult {
  let phase6a: PumpSilverTransactionContract;
  try {
    phase6a = evaluatePumpSilverTransaction(bronze);
  } catch {
    return quarantine(['PHASE6A_INVALID']);
  }
  if (phase6a.events.length !== 1) {
    return quarantine(['PHASE6A_INVALID'], null, phase6a.canonicalHash);
  }
  const succeeded = phase6a.source.executionStatus === 'succeeded'
    && phase6a.isExecutedTrade && phase6a.quarantines.length === 0;
  const failed = phase6a.source.executionStatus === 'failed'
    && !phase6a.isExecutedTrade
    && phase6a.quarantines.length === 1
    && phase6a.quarantines[0]?.reason === 'failed_transaction';
  if (!succeeded && !failed) {
    return quarantine(['PHASE6A_INVALID'], null, phase6a.canonicalHash);
  }
  const eventBinding = phase6aEventBinding(phase6a, phase6a.events[0]!);
  if (!eventBinding) return quarantine(['INVALID_INPUT_SCHEMA'], null, phase6a.canonicalHash);
  return evaluatePumpSilverStateCore(eventBinding, evidenceValue);
}

export function evaluatePumpSilverStateFixture(
  eventBindingValue: unknown,
  evidenceValue: unknown,
): PumpSilverStateResult {
  return evaluatePumpSilverStateCore(eventBindingValue, evidenceValue);
}

function evaluatePumpSilverStateCore(
  eventBindingValue: unknown,
  evidenceValue: unknown,
): PumpSilverStateResult {
  if (!validEventBinding(eventBindingValue)) return quarantine(['INVALID_INPUT_SCHEMA']);
  const eventBinding = eventBindingValue;
  const eventBindingSha256 = domainHash(EVENT_BINDING_HASH_DOMAIN, eventBinding);
  if (!GOLDEN_FIXTURE_BINDING_HASHES.has(eventBindingSha256)) {
    return quarantine(['PHASE6A_INVALID']);
  }
  const phase6a = { canonicalHash: eventBinding.sourcePhase6aSha256 };
  const evidence = objectValue(evidenceValue);
  if (!evidence) return quarantine(['INVALID_INPUT_SCHEMA'], eventBinding.eventKey, phase6a.canonicalHash, eventBindingSha256);
  if (Object.hasOwn(evidence, 'observability')) {
    return quarantine(['OBSERVABILITY_CANONICAL_INFLUENCE_FORBIDDEN'], eventBinding.eventKey, phase6a.canonicalHash, eventBindingSha256);
  }
  if (!hasExactOwnKeys(evidence, EVIDENCE_KEYS)
    || evidence.schemaVersion !== 'PUMP_SILVER_STATE_EVIDENCE_1'
    || evidence.eventKey !== eventBinding.eventKey) {
    return quarantine(['INVALID_INPUT_SCHEMA'], eventBinding.eventKey, phase6a.canonicalHash, eventBindingSha256);
  }
  const eventKey = eventBinding.eventKey;

  const registry = objectValue(evidence.registry);
  if (!registry || !hasExactOwnKeys(registry, REGISTRY_KEYS)) {
    return quarantine(['INVALID_INPUT_SCHEMA'], eventKey, phase6a.canonicalHash, eventBindingSha256);
  }
  if (registry.researchReady === true) {
    return quarantine(['RESEARCH_READY_FORBIDDEN'], eventKey, phase6a.canonicalHash, eventBindingSha256);
  }
  if (registry.approved === true) {
    return quarantine(['SELF_APPROVED_REGISTRY'], eventKey, phase6a.canonicalHash, eventBindingSha256);
  }
  if (registry.realActivationSlotRange !== null) {
    return quarantine(['REAL_ACTIVATION_RANGE_FORBIDDEN'], eventKey, phase6a.canonicalHash, eventBindingSha256);
  }
  const registryValid = registry.schemaVersion === 'PUMP_SILVER_STATE_FIXTURE_REGISTRY_1'
    && registry.approved === false
    && registry.researchReady === false
    && registry.evidenceClass === 'SYNTHETIC_TEST_ONLY'
    && registry.realActivationSlotRange === null
    && registry.activationSlotEvidence === 'NONE_SYNTHETIC_FIXTURE_ONLY'
    && registry.officialDocsCommit === PUMP_DOCS_COMMIT
    && registry.pumpIdlSha256 === PUMP_IDL_SHA256
    && registry.pumpProgramId === PUMP_PROGRAM_ID
    && registry.bondingCurveLayout === 'PUMP_IDL_BONDING_CURVE_9C82F61_1'
    && registry.bondingCurveDiscriminatorHex === CURVE_DISCRIMINATOR
    && registry.legacyTokenProgramId === TOKEN_PROGRAM
    && registry.token2022ProgramId === TOKEN_2022_PROGRAM
    && isExactArray(registry.supportedToken2022Extensions, 0)
    && registry.supportedToken2022Extensions.length === 0
    && registry.fixtureBindingSha256 === eventBindingSha256;
  if (!registryValid) return quarantine(['INVALID_REGISTRY'], eventKey, phase6a.canonicalHash, eventBindingSha256);

  const reject = (reasons: Iterable<PumpSilverStateQuarantineReason>) => quarantine(
    reasons, eventKey, phase6a.canonicalHash, eventBindingSha256,
  );
  const expectedRoles = eventBinding.quoteBondingCurveTokenAccount === null
    ? ['bonding_curve', 'mint', 'base_bonding_curve_token_account'] as const
    : ['bonding_curve', 'mint', 'base_bonding_curve_token_account', 'quote_bonding_curve_token_account'] as const;
  if (utilTypes.isProxy(evidence.snapshots) || !Array.isArray(evidence.snapshots)) return reject(['INVALID_INPUT_SCHEMA']);
  if (evidence.snapshots.length < expectedRoles.length * 2) return reject(['MISSING_RAW_ACCOUNT_BYTES']);
  if (evidence.snapshots.length > expectedRoles.length * 2) return reject(['AMBIGUOUS_ACCOUNT_WRITES']);
  if (!isExactArray(evidence.snapshots, expectedRoles.length * 2)) return reject(['INVALID_INPUT_SCHEMA']);
  const snapshots = evidence.snapshots;
  type BoundaryState = {
    curve?: DecodedBondingCurve;
    mint?: DecodedMint;
    baseToken?: DecodedTokenAccount;
    quoteToken?: DecodedTokenAccount;
    bondingCurveLamports?: bigint;
  };
  const decoded = new Map<string, BoundaryState>();
  const identities = new Set<string>();
  const beforeOrdinals: bigint[] = [];
  const afterOrdinals: bigint[] = [];
  const rawStateByRole = new Map<string, { before?: string; after?: string }>();
  const lamportsByRole = new Map<string, { before?: bigint; after?: bigint }>();
  for (const snapshotValue of snapshots) {
    const snapshot = objectValue(snapshotValue);
    if (!snapshot || !hasExactOwnKeys(snapshot, SNAPSHOT_KEYS)) return reject(['INVALID_INPUT_SCHEMA']);
    if (snapshot.schemaVersion !== 'PUMP_SILVER_ACCOUNT_SNAPSHOT_1'
      || snapshot.source !== 'SYNTHETIC_EXACT_BYTES'
      || snapshot.evidenceClass !== 'INSTRUCTION_EXACT_SYNTHETIC'
      || snapshot.signature !== eventBinding.signature
      || snapshot.slot !== eventBinding.slot
      || snapshot.transactionIndex !== eventBinding.transactionIndex
      || snapshot.parentInstructionLocation !== eventBinding.parentInstructionLocation
      || snapshot.parentInstructionIndex !== eventBinding.parentInstructionIndex
      || snapshot.parentStackHeight !== eventBinding.parentStackHeight
      || snapshot.eventInstructionLocation !== eventBinding.eventInstructionLocation
      || snapshot.eventParentInstructionIndex !== eventBinding.eventParentInstructionIndex
      || snapshot.eventInstructionIndex !== eventBinding.eventInstructionIndex
      || snapshot.eventStackHeight !== eventBinding.eventStackHeight
      || snapshot.eventKey !== eventKey) return reject(['INVALID_SNAPSHOT_COORDINATES']);
    if (snapshot.transactionWideBalanceOnly !== false) return reject(['TRANSACTION_WIDE_BALANCE_ONLY']);
    if (snapshot.stateAuthority !== 'RAW_ACCOUNT_STATE') return reject(['EVENT_FIELDS_AS_STATE_AUTHORITY']);
    const role = snapshot.accountRole;
    const boundary = snapshot.boundary;
    if (typeof role !== 'string' || !(expectedRoles as readonly string[]).includes(role)
      || (boundary !== 'parent_instruction_pre' && boundary !== 'parent_instruction_post')) {
      return reject(['INVALID_INPUT_SCHEMA']);
    }
    const identity = `${boundary}:${role}`;
    if (identities.has(identity)) return reject(['DUPLICATE_SNAPSHOT']);
    identities.add(identity);
    const bytes = bytesFromHex(snapshot.dataHex);
    if (!bytes) return reject(['MISSING_RAW_ACCOUNT_BYTES']);
    if (snapshot.dataSha256 !== sha256Bytes(bytes)) return reject(['RAW_ACCOUNT_HASH_MISMATCH']);
    const expectedPubkey = role === 'bonding_curve' ? eventBinding.bondingCurve
      : role === 'mint' ? eventBinding.mint
        : role === 'base_bonding_curve_token_account' ? eventBinding.baseBondingCurveTokenAccount
          : eventBinding.quoteBondingCurveTokenAccount;
    const expectedOwner = role === 'bonding_curve' ? PUMP_PROGRAM_ID
      : role === 'quote_bonding_curve_token_account' ? eventBinding.quoteTokenProgramId
        : eventBinding.tokenProgramId;
    if (snapshot.accountPubkey !== expectedPubkey) return reject(['ACCOUNT_IDENTITY_MISMATCH']);
    if (snapshot.ownerProgramId !== expectedOwner) return reject(['WRONG_ACCOUNT_OWNER']);
    if (typeof snapshot.lamports !== 'string' || typeof snapshot.executable !== 'boolean') {
      return reject(['INVALID_INPUT_SCHEMA']);
    }
    if (!canonicalUnsigned(snapshot.writeOrdinal, U64_MAX)
      || !canonicalUnsigned(snapshot.lamports, U64_MAX)) return reject(['UNSAFE_INTEGER']);
    if (snapshot.executable) return reject(['UNKNOWN_ACCOUNT_LAYOUT']);
    const roleState = rawStateByRole.get(role) ?? {};
    const canonicalState = `${snapshot.ownerProgramId}:${snapshot.lamports}:0:${bytes.toString('hex')}`;
    if (boundary === 'parent_instruction_pre') roleState.before = canonicalState;
    else roleState.after = canonicalState;
    rawStateByRole.set(role, roleState);
    const roleLamports = lamportsByRole.get(role) ?? {};
    if (boundary === 'parent_instruction_pre') roleLamports.before = BigInt(snapshot.lamports);
    else roleLamports.after = BigInt(snapshot.lamports);
    lamportsByRole.set(role, roleLamports);
    if (!isExactArray(snapshot.token2022Extensions, 0) || snapshot.token2022Extensions.length !== 0) {
      return reject([expectedOwner === TOKEN_2022_PROGRAM ? 'UNSUPPORTED_TOKEN_2022_EXTENSION' : 'UNKNOWN_ACCOUNT_LAYOUT']);
    }
    (boundary === 'parent_instruction_pre' ? beforeOrdinals : afterOrdinals).push(BigInt(snapshot.writeOrdinal));
    const boundaryState = decoded.get(boundary) ?? {};
    if (role === 'bonding_curve') {
      const curveState = decodeBondingCurve(bytes);
      if (!curveState) return reject(['UNKNOWN_ACCOUNT_LAYOUT']);
      boundaryState.curve = curveState;
      boundaryState.bondingCurveLamports = BigInt(snapshot.lamports);
    } else if (role === 'mint') {
      const mintState = decodeMint(bytes, expectedOwner as string, snapshot.token2022Extensions);
      if (!mintState) return reject([
        expectedOwner === TOKEN_2022_PROGRAM ? 'UNSUPPORTED_TOKEN_2022_EXTENSION' : 'UNKNOWN_ACCOUNT_LAYOUT',
      ]);
      boundaryState.mint = mintState;
    } else {
      const tokenState = decodeTokenAccount(bytes, expectedOwner as string, snapshot.token2022Extensions);
      if (!tokenState) return reject([
        expectedOwner === TOKEN_2022_PROGRAM ? 'UNSUPPORTED_TOKEN_2022_EXTENSION' : 'UNKNOWN_ACCOUNT_LAYOUT',
      ]);
      if (role === 'base_bonding_curve_token_account') boundaryState.baseToken = tokenState;
      else boundaryState.quoteToken = tokenState;
    }
    decoded.set(boundary, boundaryState);
  }

  if (beforeOrdinals.length === 0 || afterOrdinals.length === 0
    || beforeOrdinals.some((beforeOrdinal) => afterOrdinals.some((afterOrdinal) => beforeOrdinal >= afterOrdinal))) {
    return reject(['INVALID_SNAPSHOT_ORDER']);
  }

  const before = decoded.get('parent_instruction_pre');
  const after = decoded.get('parent_instruction_post');
  if (!before?.curve || !before.mint || !before.baseToken || !after?.curve || !after.mint || !after.baseToken
    || (eventBinding.quoteBondingCurveTokenAccount !== null && (!before.quoteToken || !after.quoteToken))) {
    return reject(['MISSING_RAW_ACCOUNT_BYTES']);
  }
  const provenance = objectValue(evidence.provenance);
  const coverage = objectValue(evidence.coverage);
  const pilotBudget = objectValue(evidence.pilotBudget);
  if (!provenance || !hasExactOwnKeys(provenance, PROVENANCE_KEYS)
    || provenance.schemaVersion !== 'PUMP_SILVER_STATE_PROVENANCE_1'
    || provenance.evidenceClass !== 'SYNTHETIC_TEST_ONLY'
    || provenance.epochCid !== GOLDEN_EPOCH_CID
    || provenance.carSha256 !== GOLDEN_CAR_SHA256
    || provenance.carFileSizeBytes !== GOLDEN_CAR_SIZE
    || provenance.parserGitSha !== GOLDEN_PARSER_GIT_SHA
    || provenance.reducerGitSha !== GOLDEN_REDUCER_GIT_SHA
    || provenance.adapterGitSha !== GOLDEN_ADAPTER_GIT_SHA
    || provenance.sourceSha256 !== phase6a.canonicalHash
    || provenance.eventBindingSha256 !== eventBindingSha256
    || typeof provenance.outputSha256 !== 'string' || !HASH_HEX.test(provenance.outputSha256)
    || !canonicalUnsigned(provenance.cumulativeSourceBytes, U128_MAX)) {
    return reject(['INVALID_CAR_PROVENANCE']);
  }
  const slotRange = objectValue(provenance.slotRange);
  if (provenance.slotInventorySha256 !== GOLDEN_INVENTORY_SHA256
    || provenance.slotInventorySizeBytes !== GOLDEN_INVENTORY_SIZE
    || provenance.slotInventoryEntryCount !== GOLDEN_INVENTORY_ENTRIES
    || !slotRange || !hasExactOwnKeys(slotRange, SLOT_RANGE_KEYS)
    || slotRange.startInclusive !== GOLDEN_SLOT_START
    || slotRange.endExclusive !== GOLDEN_SLOT_END
    || BigInt(eventBinding.slot) < BigInt(slotRange.startInclusive)
    || BigInt(eventBinding.slot) >= BigInt(slotRange.endExclusive)) {
    return reject(['INVALID_SLOT_INVENTORY']);
  }
  if (BigInt(provenance.cumulativeSourceBytes as string)
    !== BigInt(provenance.carFileSizeBytes as string) + BigInt(provenance.slotInventorySizeBytes as string)) {
    return reject(['INVALID_CAR_PROVENANCE']);
  }
  if (provenance.outputSha256 !== domainHash(SNAPSHOT_HASH_DOMAIN, snapshots)) {
    return reject(['RAW_ACCOUNT_HASH_MISMATCH']);
  }
  if (!coverage || !hasExactOwnKeys(coverage, COVERAGE_KEYS)
    || !closureValid(coverage.expectedSlots, coverage.observedSlots, coverage.skippedSlots, coverage.quarantinedSlots)
    || !closureValid(coverage.expectedCallbacks, coverage.observedCallbacks, coverage.skippedCallbacks, coverage.quarantinedCallbacks)) {
    return reject(['INCOMPLETE_COVERAGE']);
  }
  if (!isExactArray(coverage.quarantineByReason, 32) || coverage.quarantineByReason.length !== 0) {
    return reject(['QUARANTINE_ACCOUNTING_MISMATCH']);
  }
  if (coverage.expectedSlots !== '1' || coverage.observedSlots !== '1'
    || coverage.skippedSlots !== '0' || coverage.quarantinedSlots !== '0'
    || coverage.expectedCallbacks !== '2' || coverage.observedCallbacks !== '2'
    || coverage.skippedCallbacks !== '0' || coverage.quarantinedCallbacks !== '0') {
    return reject(['INCOMPLETE_COVERAGE']);
  }
  if (!pilotBudget || !hasExactOwnKeys(pilotBudget, BUDGET_KEYS)
    || Object.entries(GOLDEN_PILOT_BUDGET).some(([key, value]) => pilotBudget[key] !== value)) {
    return reject(['UNSAFE_INTEGER']);
  }
  if (BigInt(coverage.expectedSlots as string) > BigInt(pilotBudget.maxSlots as string)
    || BigInt(coverage.expectedCallbacks as string) > BigInt(pilotBudget.maxCallbacks as string)) {
    return reject(['INCOMPLETE_COVERAGE']);
  }
  const rerunHash = domainHash(RERUN_HASH_DOMAIN, {
    registry,
    sourceSha256: provenance.sourceSha256,
    eventBindingSha256: provenance.eventBindingSha256,
    outputSha256: provenance.outputSha256,
    coverage,
    pilotBudget,
  });
  if (provenance.deterministicRerunHash !== rerunHash) return reject(['RERUN_HASH_MISMATCH']);

  if (eventBinding.executionStatus === 'failed'
    && [...rawStateByRole.values()].some((roleState) => roleState.before !== roleState.after)) {
    return reject(['FAILED_TRANSACTION_ROLLBACK_MISMATCH']);
  }
  if (eventBinding.executionStatus !== 'failed'
    && [...lamportsByRole.entries()].some(([role, roleLamports]) => {
      const legacyNativeCurve = role === 'bonding_curve' && eventBinding.quoteBondingCurveTokenAccount === null;
      return !legacyNativeCurve && (roleLamports.before === undefined
        || roleLamports.after === undefined || roleLamports.before !== roleLamports.after);
    })) {
    return reject(['ACCOUNT_LAMPORTS_MISMATCH']);
  }
  if (deriveBondingCurve(eventBinding.mint) !== eventBinding.bondingCurve
    || before.curve.quoteMint !== eventBinding.quoteMint || after.curve.quoteMint !== eventBinding.quoteMint) {
    return reject(['ACCOUNT_IDENTITY_MISMATCH']);
  }
  if (before.mint.supply !== after.mint.supply || before.mint.decimals !== after.mint.decimals
    || before.mint.mintAuthority !== after.mint.mintAuthority || before.mint.freezeAuthority !== after.mint.freezeAuthority
    || before.curve.tokenTotalSupply !== after.curve.tokenTotalSupply
    || before.curve.tokenTotalSupply !== before.mint.supply
    || after.curve.tokenTotalSupply !== after.mint.supply) {
    return reject(['SUPPLY_OR_DECIMAL_MISMATCH']);
  }
  if (eventBinding.quoteBondingCurveTokenAccount === null) {
    const beforeLamports = before.bondingCurveLamports;
    const afterLamports = after.bondingCurveLamports;
    const beforeQuote = BigInt(before.curve.realQuoteReserves);
    const afterQuote = BigInt(after.curve.realQuoteReserves);
    if (beforeLamports === undefined || afterLamports === undefined
      || beforeLamports < beforeQuote || afterLamports < afterQuote
      || beforeLamports - beforeQuote !== SYNTHETIC_ACCOUNT_RENT_RESERVE
      || afterLamports - afterQuote !== SYNTHETIC_ACCOUNT_RENT_RESERVE) {
      return reject(['RESERVE_MISMATCH']);
    }
  }
  if (!before.mint.initialized || !after.mint.initialized
    || before.mint.decimals !== eventBinding.tokenDecimals || after.mint.decimals !== eventBinding.tokenDecimals) {
    return reject(['MINT_STATE_MISMATCH']);
  }
  if (before.curve.creator !== eventBinding.creator || after.curve.creator !== eventBinding.creator
    || before.curve.isMayhemMode !== eventBinding.mayhemMode || after.curve.isMayhemMode !== eventBinding.mayhemMode
    || before.curve.isCashbackCoin !== eventBinding.isCashbackCoin || after.curve.isCashbackCoin !== eventBinding.isCashbackCoin
    || before.curve.complete
    || (after.curve.complete && after.curve.realTokenReserves !== '0')) {
    return reject(['CURVE_STATE_MISMATCH']);
  }
  for (const curve of [before.curve, after.curve]) {
    if (BigInt(curve.realTokenReserves) > BigInt(curve.virtualTokenReserves)
      || BigInt(curve.realTokenReserves) > BigInt(curve.tokenTotalSupply)
      || BigInt(curve.realQuoteReserves) > BigInt(curve.virtualQuoteReserves)) return reject(['RESERVE_MISMATCH']);
  }
  const validCurveTokenAccount = (
    account: DecodedTokenAccount, mint: string, amount: string, tokenProgram: string,
  ) => account.tokenProgramId === tokenProgram
    && account.mint === mint
    && account.owner === eventBinding.bondingCurve
    && account.amount === amount
    && account.state === 'initialized'
    && account.delegate === null
    && account.delegatedAmount === '0'
    && account.closeAuthority === null
    && account.isNativeReserve === null;
  if (!validCurveTokenAccount(before.baseToken, eventBinding.mint, before.curve.realTokenReserves, eventBinding.tokenProgramId)
    || !validCurveTokenAccount(after.baseToken, eventBinding.mint, after.curve.realTokenReserves, eventBinding.tokenProgramId)) {
    return reject(['RESERVE_MISMATCH']);
  }
  if (eventBinding.quoteBondingCurveTokenAccount !== null
    && (!before.quoteToken || !after.quoteToken
      || !validCurveTokenAccount(before.quoteToken, eventBinding.quoteMint, before.curve.realQuoteReserves, eventBinding.quoteTokenProgramId as string)
      || !validCurveTokenAccount(after.quoteToken, eventBinding.quoteMint, after.curve.realQuoteReserves, eventBinding.quoteTokenProgramId as string))) {
    return reject(['RESERVE_MISMATCH']);
  }
  if (eventBinding.executionStatus !== 'failed') {
    const tokenAmount = BigInt(eventBinding.tokenAmount);
    const quoteAmount = BigInt(eventBinding.quoteAmount);
    const isBuy = eventBinding.kind === 'buy';
    const curveTransitionValid = isBuy
      ? BigInt(before.curve.virtualTokenReserves) - tokenAmount === BigInt(after.curve.virtualTokenReserves)
        && BigInt(before.curve.realTokenReserves) - tokenAmount === BigInt(after.curve.realTokenReserves)
        && BigInt(before.curve.virtualQuoteReserves) + quoteAmount === BigInt(after.curve.virtualQuoteReserves)
        && BigInt(before.curve.realQuoteReserves) + quoteAmount === BigInt(after.curve.realQuoteReserves)
      : BigInt(before.curve.virtualTokenReserves) + tokenAmount === BigInt(after.curve.virtualTokenReserves)
        && BigInt(before.curve.realTokenReserves) + tokenAmount === BigInt(after.curve.realTokenReserves)
        && BigInt(before.curve.virtualQuoteReserves) - quoteAmount === BigInt(after.curve.virtualQuoteReserves)
        && BigInt(before.curve.realQuoteReserves) - quoteAmount === BigInt(after.curve.realQuoteReserves);
    if (!curveTransitionValid) return reject(['RESERVE_MISMATCH']);
    if (after.curve.virtualTokenReserves !== eventBinding.virtualTokenReserves
      || after.curve.virtualQuoteReserves !== eventBinding.virtualQuoteReserves
      || after.curve.realTokenReserves !== eventBinding.realTokenReserves
      || after.curve.realQuoteReserves !== eventBinding.realQuoteReserves) {
      return reject(['EVENT_STATE_CONFLICT']);
    }
  }

  if (eventBinding.executionStatus === 'failed') return reject(['FAILED_TRANSACTION']);

  const state = {
    before: {
      bondingCurve: before.curve,
      mint: before.mint,
      baseBondingCurveTokenAccount: before.baseToken,
      quoteBondingCurveTokenAccount: before.quoteToken ?? null,
    },
    after: {
      bondingCurve: after.curve,
      mint: after.mint,
      baseBondingCurveTokenAccount: after.baseToken,
      quoteBondingCurveTokenAccount: after.quoteToken ?? null,
    },
  };
  const unsigned = {
    schemaVersion: 'PUMP_SILVER_STATE_CONTRACT_1' as const,
    status: 'FIXTURE_VALID' as const,
    approved: false as const,
    researchReady: false as const,
    pilotEligible: false as const,
    eventKey,
    sourcePhase6aSha256: phase6a.canonicalHash,
    eventBindingSha256,
    evidenceClass: 'SYNTHETIC_TEST_ONLY' as const,
    state,
    provenance,
    coverage,
    quarantineReasons: [] as PumpSilverStateQuarantineReason[],
  };
  return immutableSnapshot({ ...unsigned, canonicalHash: domainHash(STATE_HASH_DOMAIN, unsigned) });
}

export function validatePumpSilverObservabilitySignal(value: unknown): boolean {
  const signal = objectValue(value);
  if (!signal || !hasExactOwnKeys(signal, ['metric', 'labels'])
    || typeof signal.metric !== 'string') return false;
  if (!(PUMP_SILVER_STATE_OBSERVABILITY_CONTRACT.metricsRequiredLater as readonly string[]).includes(signal.metric)) return false;
  const labels = objectValue(signal.labels);
  if (!labels) return false;
  const labelKeys = Object.getOwnPropertyNames(labels);
  if (!hasExactOwnKeys(labels, labelKeys)) return false;
  for (const key of labelKeys) {
    if ((PUMP_SILVER_STATE_OBSERVABILITY_CONTRACT.forbiddenLabels as readonly string[]).includes(key)) return false;
    const values = PUMP_SILVER_STATE_OBSERVABILITY_CONTRACT.allowedLabels as Record<string, readonly string[]>;
    if (!Object.hasOwn(values, key) || typeof labels[key] !== 'string' || !values[key]!.includes(labels[key] as string)) return false;
  }
  return true;
}
