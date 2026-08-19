import { createHash } from 'node:crypto';
import bs58 from 'bs58';
import { PUMP_PROGRAM_ID, WSOL } from '../../../src/pump-address.js';
import { evaluatePumpSilverTransaction } from '../../../src/research/pump-silver-contract.js';
import type { PumpV2BronzeTransaction } from '../../../src/research/pump-v2-bronze.js';
import {
  TOKEN_PROGRAM,
  TOKEN_2022_PROGRAM,
  bronzeFixture,
  creator,
  curve,
  mint,
  mintAuthority,
} from './fixture.js';

const CURVE_DISCRIMINATOR = '17b7f83760d8ac60';
const IDL_SHA256 = 'b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49';
const OFFICIAL_DOCS_COMMIT = '9c82f61cb711b044a17f770ab8ce9f9bdf78f333';
const SYNTHETIC_CID = 'bafkreiacv4rclo6minmaq75znwrqj5oxg7l5cim3ami4snfdptltigowka';
const HASH_64 = {
  car: 'ec5ce3b235ac4e5774656598a2e548e11873c9603fcc324f7f4ca1d0077bb326',
  inventory: '1e188b728002793e3b64d6a7072c370d4c0f90c657ce10140f276a908b35b5b8',
};
const EVENT_BINDING_HASH_DOMAIN = 'PUMP_SILVER_STATE_EVENT_BINDING_HASH_1';

type TradeVariant = 'buy' | 'sell' | 'buy_v2' | 'sell_v2' | 'buy_exact_sol_in' | 'buy_exact_quote_in_v2';
type AccountRole = 'bonding_curve' | 'mint' | 'base_bonding_curve_token_account' | 'quote_bonding_curve_token_account';
type Boundary = 'parent_instruction_pre' | 'parent_instruction_post';

const SYNTHETIC_ACCOUNT_RENT_RESERVE = 1_000_000n;
const FIXED_ACCOUNT_LAMPORTS: Record<AccountRole, bigint> = {
  bonding_curve: 2_000_000n,
  mint: 1_500_000n,
  base_bonding_curve_token_account: 2_100_000n,
  quote_bonding_curve_token_account: 2_100_000n,
};

function u32(value: number): Buffer {
  const bytes = Buffer.alloc(4);
  bytes.writeUInt32LE(value);
  return bytes;
}

function u64(value: bigint): Buffer {
  const bytes = Buffer.alloc(8);
  bytes.writeBigUInt64LE(value);
  return bytes;
}

function publicKey(value: string): Buffer {
  return Buffer.from(bs58.decode(value));
}

function sha256Bytes(value: Buffer | string): string {
  return createHash('sha256').update(value).digest('hex');
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number') return String(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
}

function domainHash(domain: string, value: unknown): string {
  return sha256Bytes(`${domain}\0${canonicalJson(value)}`);
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

function bondingCurveBytes(values: {
  virtualTokenReserves: bigint;
  virtualQuoteReserves: bigint;
  realTokenReserves: bigint;
  realQuoteReserves: bigint;
}): Buffer {
  return Buffer.concat([
    Buffer.from(CURVE_DISCRIMINATOR, 'hex'),
    u64(values.virtualTokenReserves),
    u64(values.virtualQuoteReserves),
    u64(values.realTokenReserves),
    u64(values.realQuoteReserves),
    u64(1_000_000_000_000_000n),
    Buffer.from([0]),
    publicKey(creator),
    Buffer.from([0, 0]),
    publicKey(WSOL),
  ]);
}

function mintBytes(tokenProgramId = TOKEN_PROGRAM): Buffer {
  if (tokenProgramId !== TOKEN_PROGRAM && tokenProgramId !== TOKEN_2022_PROGRAM) throw new Error('unsupported fixture token program');
  return Buffer.concat([
    u32(1), publicKey(mintAuthority),
    u64(1_000_000_000_000_000n),
    Buffer.from([6, 1]),
    u32(0), Buffer.alloc(32),
  ]);
}

function tokenAccountBytes(tokenMint: string, owner: string, amount: bigint): Buffer {
  return Buffer.concat([
    publicKey(tokenMint),
    publicKey(owner),
    u64(amount),
    u32(0), Buffer.alloc(32),
    Buffer.from([1]),
    u32(0), Buffer.alloc(8),
    u64(0n),
    u32(0), Buffer.alloc(32),
  ]);
}

function snapshot(
  eventBinding: ReturnType<typeof validStateEventBinding>,
  role: AccountRole,
  boundary: Boundary,
  accountPubkey: string,
  ownerProgramId: string,
  bytes: Buffer,
  writeOrdinal: string,
) {
  const lamports = role === 'bonding_curve' && eventBinding.quoteBondingCurveTokenAccount === null
    ? bytes.readBigUInt64LE(32) + SYNTHETIC_ACCOUNT_RENT_RESERVE
    : FIXED_ACCOUNT_LAMPORTS[role];
  return {
    schemaVersion: 'PUMP_SILVER_ACCOUNT_SNAPSHOT_1',
    accountRole: role,
    boundary,
    source: 'SYNTHETIC_EXACT_BYTES',
    evidenceClass: 'INSTRUCTION_EXACT_SYNTHETIC',
    signature: eventBinding.signature,
    slot: eventBinding.slot,
    transactionIndex: eventBinding.transactionIndex,
    parentInstructionLocation: eventBinding.parentInstructionLocation,
    parentInstructionIndex: eventBinding.parentInstructionIndex,
    parentStackHeight: eventBinding.parentStackHeight,
    eventInstructionLocation: eventBinding.eventInstructionLocation,
    eventParentInstructionIndex: eventBinding.eventParentInstructionIndex,
    eventInstructionIndex: eventBinding.eventInstructionIndex,
    eventStackHeight: eventBinding.eventStackHeight,
    eventKey: eventBinding.eventKey,
    accountPubkey,
    ownerProgramId,
    lamports: lamports.toString(),
    executable: false,
    dataHex: bytes.toString('hex'),
    dataSha256: sha256Bytes(bytes),
    writeOrdinal,
    transactionWideBalanceOnly: false,
    stateAuthority: 'RAW_ACCOUNT_STATE',
    token2022Extensions: [],
  };
}

export function rebindStateEvidenceHashes<T extends Record<string, any>>(evidence: T): T {
  for (const snapshotValue of evidence.snapshots ?? []) {
    const snapshotRecord = snapshotValue as Record<string, unknown>;
    if (typeof snapshotRecord.dataHex === 'string' && /^[0-9a-f]*$/.test(snapshotRecord.dataHex)) {
      snapshotRecord.dataSha256 = sha256Bytes(Buffer.from(snapshotRecord.dataHex, 'hex'));
    }
  }
  evidence.provenance.outputSha256 = domainHash('PUMP_SILVER_STATE_SNAPSHOT_SET_1', evidence.snapshots);
  evidence.provenance.deterministicRerunHash = domainHash('PUMP_SILVER_STATE_RERUN_1', {
    registry: evidence.registry,
    sourceSha256: evidence.provenance.sourceSha256,
    eventBindingSha256: evidence.provenance.eventBindingSha256,
    outputSha256: evidence.provenance.outputSha256,
    coverage: evidence.coverage,
    pilotBudget: evidence.pilotBudget,
  });
  return evidence;
}

export function stateEventBindingFromBronze(bronze: PumpV2BronzeTransaction) {
  const phase6a = evaluatePumpSilverTransaction(bronze);
  const event = phase6a.events[0]!;
  const succeeded = phase6a.source.executionStatus === 'succeeded'
    && phase6a.isExecutedTrade && phase6a.quarantines.length === 0;
  const failed = phase6a.source.executionStatus === 'failed'
    && !phase6a.isExecutedTrade
    && phase6a.quarantines.length === 1
    && phase6a.quarantines[0]?.reason === 'failed_transaction';
  if ((!succeeded && !failed) || phase6a.events.length !== 1 || event.event.eventType !== 'trade') {
    throw new Error('expected authenticated trade fixture');
  }
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
    quoteMint: event.event.quoteMint,
    signature: phase6a.source.signature,
    slot: String(phase6a.source.slot),
    transactionIndex: phase6a.source.transactionIndex,
    executionStatus: phase6a.source.executionStatus,
    parentInstructionLocation: event.parentCoordinate.instructionLocation,
    parentInstructionIndex: event.parentCoordinate.instructionIndex,
    parentStackHeight: event.parentCoordinate.stackHeight ?? null,
    eventInstructionLocation: event.eventCoordinate.instructionLocation,
    eventParentInstructionIndex: event.eventCoordinate.parentInstructionIndex ?? null,
    eventInstructionIndex: event.eventCoordinate.instructionIndex,
    eventStackHeight: event.eventCoordinate.stackHeight ?? null,
    sourcePhase6aSha256: phase6a.canonicalHash,
    creator: event.event.creator,
    mayhemMode: event.event.mayhemMode,
    isCashbackCoin: false as const,
    tokenDecimals: event.tokenDecimals,
    quoteDecimals: event.quoteDecimals,
    tokenAmount: event.event.tokenAmount,
    quoteAmount: event.event.quoteAmount,
    virtualTokenReserves: event.event.virtualTokenReserves,
    virtualQuoteReserves: event.event.virtualQuoteReserves,
    realTokenReserves: event.event.realTokenReserves,
    realQuoteReserves: event.event.realQuoteReserves,
  };
  return { ...normalized, eventKey: canonicalEventKey(normalized) };
}

export function validStateEventBinding(variant: TradeVariant = 'buy') {
  const ixName = variant === 'sell' || variant === 'sell_v2' ? 'sell'
    : variant === 'buy_exact_sol_in' ? 'buy_exact_sol_in' : 'buy';
  return stateEventBindingFromBronze(bronzeFixture(variant, { eventOverrides: { ixName } }));
}

export function validBuyStateEventBinding() {
  return validStateEventBinding('buy');
}

export function validToken2022StateEventBinding() {
  return stateEventBindingFromBronze(bronzeFixture('buy_v2', {
    baseTokenProgram: TOKEN_2022_PROGRAM,
    eventOverrides: { ixName: 'buy' },
  }));
}

export function validStateEvidence(eventBinding = validStateEventBinding('buy')) {
  const isBuy = eventBinding.kind === 'buy';
  const beforeValues = isBuy ? {
    virtualTokenReserves: BigInt(eventBinding.virtualTokenReserves) + BigInt(eventBinding.tokenAmount),
    virtualQuoteReserves: BigInt(eventBinding.virtualQuoteReserves) - BigInt(eventBinding.quoteAmount),
    realTokenReserves: BigInt(eventBinding.realTokenReserves) + BigInt(eventBinding.tokenAmount),
    realQuoteReserves: BigInt(eventBinding.realQuoteReserves) - BigInt(eventBinding.quoteAmount),
  } : {
    virtualTokenReserves: BigInt(eventBinding.virtualTokenReserves) - BigInt(eventBinding.tokenAmount),
    virtualQuoteReserves: BigInt(eventBinding.virtualQuoteReserves) + BigInt(eventBinding.quoteAmount),
    realTokenReserves: BigInt(eventBinding.realTokenReserves) - BigInt(eventBinding.tokenAmount),
    realQuoteReserves: BigInt(eventBinding.realQuoteReserves) + BigInt(eventBinding.quoteAmount),
  };
  const afterValues = {
    virtualTokenReserves: BigInt(eventBinding.virtualTokenReserves),
    virtualQuoteReserves: BigInt(eventBinding.virtualQuoteReserves),
    realTokenReserves: BigInt(eventBinding.realTokenReserves),
    realQuoteReserves: BigInt(eventBinding.realQuoteReserves),
  };
  const beforeCurve = bondingCurveBytes(beforeValues);
  const afterCurve = bondingCurveBytes(afterValues);
  const mintData = mintBytes(eventBinding.tokenProgramId);
  const beforeBaseToken = tokenAccountBytes(eventBinding.mint, curve, beforeValues.realTokenReserves);
  const afterBaseToken = tokenAccountBytes(eventBinding.mint, curve, afterValues.realTokenReserves);
  const snapshots = [
    snapshot(eventBinding, 'bonding_curve', 'parent_instruction_pre', curve, PUMP_PROGRAM_ID, beforeCurve, '1'),
    snapshot(eventBinding, 'mint', 'parent_instruction_pre', mint, eventBinding.tokenProgramId, mintData, '1'),
    snapshot(eventBinding, 'bonding_curve', 'parent_instruction_post', curve, PUMP_PROGRAM_ID, afterCurve, '2'),
    snapshot(eventBinding, 'mint', 'parent_instruction_post', mint, eventBinding.tokenProgramId, mintData, '2'),
    snapshot(eventBinding, 'base_bonding_curve_token_account', 'parent_instruction_pre',
      eventBinding.baseBondingCurveTokenAccount, eventBinding.tokenProgramId, beforeBaseToken, '1'),
    snapshot(eventBinding, 'base_bonding_curve_token_account', 'parent_instruction_post',
      eventBinding.baseBondingCurveTokenAccount, eventBinding.tokenProgramId, afterBaseToken, '2'),
  ];
  if (eventBinding.quoteBondingCurveTokenAccount !== null && eventBinding.quoteTokenProgramId !== null) {
    snapshots.push(
      snapshot(eventBinding, 'quote_bonding_curve_token_account', 'parent_instruction_pre',
        eventBinding.quoteBondingCurveTokenAccount, eventBinding.quoteTokenProgramId,
        tokenAccountBytes(eventBinding.quoteMint, curve, beforeValues.realQuoteReserves), '1'),
      snapshot(eventBinding, 'quote_bonding_curve_token_account', 'parent_instruction_post',
        eventBinding.quoteBondingCurveTokenAccount, eventBinding.quoteTokenProgramId,
        tokenAccountBytes(eventBinding.quoteMint, curve, afterValues.realQuoteReserves), '2'),
    );
  }
  const eventBindingSha256 = domainHash(EVENT_BINDING_HASH_DOMAIN, eventBinding);
  const registry = {
    schemaVersion: 'PUMP_SILVER_STATE_FIXTURE_REGISTRY_1',
    approved: false,
    researchReady: false,
    evidenceClass: 'SYNTHETIC_TEST_ONLY',
    realActivationSlotRange: null,
    activationSlotEvidence: 'NONE_SYNTHETIC_FIXTURE_ONLY',
    officialDocsCommit: OFFICIAL_DOCS_COMMIT,
    pumpIdlSha256: IDL_SHA256,
    pumpProgramId: PUMP_PROGRAM_ID,
    bondingCurveLayout: 'PUMP_IDL_BONDING_CURVE_9C82F61_1',
    bondingCurveDiscriminatorHex: CURVE_DISCRIMINATOR,
    legacyTokenProgramId: TOKEN_PROGRAM,
    token2022ProgramId: TOKEN_2022_PROGRAM,
    supportedToken2022Extensions: [],
    fixtureBindingSha256: eventBindingSha256,
  };
  const coverage = {
    expectedSlots: '1', observedSlots: '1', skippedSlots: '0', quarantinedSlots: '0',
    expectedCallbacks: '2', observedCallbacks: '2', skippedCallbacks: '0', quarantinedCallbacks: '0',
    quarantineByReason: [],
  };
  const pilotBudget = {
    maxBytesRead: '1048576', maxBytesWritten: '1048576', maxSlots: '1', maxCallbacks: '16',
    maxRuntimeMillis: '60000', maxStorageBytes: '2097152',
  };
  const outputSha256 = domainHash('PUMP_SILVER_STATE_SNAPSHOT_SET_1', snapshots);
  const provenanceBase = {
    schemaVersion: 'PUMP_SILVER_STATE_PROVENANCE_1',
    evidenceClass: 'SYNTHETIC_TEST_ONLY',
    epochCid: SYNTHETIC_CID,
    carSha256: HASH_64.car,
    carFileSizeBytes: '4096',
    slotInventorySha256: HASH_64.inventory,
    slotInventorySizeBytes: '512',
    slotInventoryEntryCount: '1',
    slotRange: { startInclusive: '361000001', endExclusive: '361000002' },
    parserGitSha: 'a'.repeat(40), reducerGitSha: 'b'.repeat(40), adapterGitSha: 'c'.repeat(40),
    sourceSha256: eventBinding.sourcePhase6aSha256,
    eventBindingSha256,
    outputSha256,
    cumulativeSourceBytes: '4608',
  };
  const deterministicRerunHash = domainHash('PUMP_SILVER_STATE_RERUN_1', {
    registry,
    sourceSha256: provenanceBase.sourceSha256,
    eventBindingSha256,
    outputSha256,
    coverage,
    pilotBudget,
  });
  return {
    schemaVersion: 'PUMP_SILVER_STATE_EVIDENCE_1',
    eventKey: eventBinding.eventKey,
    registry,
    snapshots,
    provenance: { ...provenanceBase, deterministicRerunHash },
    coverage,
    pilotBudget,
  };
}

export function validBuyStateEvidence() {
  return validStateEvidence(validBuyStateEventBinding());
}
