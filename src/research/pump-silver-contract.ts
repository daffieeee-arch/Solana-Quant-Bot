import { createHash } from 'node:crypto';
import { PUMP_PROGRAM_ID, WSOL, deriveBondingCurve } from '../pump-address.js';
import { decodePublicKey, encodePublicKey, findProgramAddress } from '../solana-pda.js';
import type { PumpV2BronzeTransaction, PumpV2LocatedInstruction, PumpV2TokenBalance } from './pump-v2-bronze.js';
import { capturePumpV2BronzeTransaction } from './pump-v2-bronze.js';
import { PUMP_EVENT_CPI_TAG, decodePumpSilverEventData, type PumpSilverEvent } from './pump-silver-event.js';

const TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const TOKEN_2022_PROGRAM = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';
const ASSOCIATED_TOKEN_PROGRAM = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL';
const SYSTEM_PROGRAM = '11111111111111111111111111111111';
const RENT = 'SysvarRent111111111111111111111111111111111';
const METADATA_PROGRAM = 'metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s';
const MAYHEM_PROGRAM = 'MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e';
const FEE_PROGRAM = 'pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ';
const HASH_DOMAIN = 'PUMP_SILVER_TRANSACTION_CONTRACT_1\u0000';
const UNSIGNED_INTEGER = /^(0|[1-9]\d*)$/;
const SIGNED_INTEGER = /^-?(0|[1-9]\d*)$/;
const GIT_COMMIT_HEX = /^[0-9a-f]{40}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const FIXTURE_REGISTRY_ENTRY_KEYS = new Set([
  'startInclusive',
  'endExclusive',
  'officialDocsCommit',
  'pumpIdlSha256',
]);
const MAX_NORMALIZED_INSTRUCTIONS = 4_352;
const MAX_INSTRUCTION_ACCOUNT_REFERENCES = 4_096;
const MAX_RESOLVED_ACCOUNT_KEYS = 256;
const MAX_LOG_MESSAGES = 1_024;
const MAX_LOG_BYTES = 10_000;
const MAX_INSTRUCTION_DATA_HEX = 1_232 * 2;

export const PUMP_SILVER_FIXTURE_REGISTRY = Object.freeze({
  schemaVersion: 'PUMP_SILVER_FIXTURE_REGISTRY_1' as const,
  registryStatus: 'fixture_only_unapproved' as const,
  fixtureOnly: true as const,
  approved: false as const,
  researchReady: false as const,
  officialDocsCommit: '9c82f61cb711b044a17f770ab8ce9f9bdf78f333',
  pumpIdlPath: 'idl/pump.json',
  pumpIdlSha256: 'b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49',
  programId: PUMP_PROGRAM_ID,
  activationSlotEvidence: 'synthetic_fixture_only_no_real_activation_slot_approval' as const,
  slotRange: Object.freeze({ startInclusive: 361_000_000, endExclusive: 362_000_000 }),
  eventCpiTag: PUMP_EVENT_CPI_TAG,
  tradeEventIxNameDocumentation: 'pinned IDL documents buy|sell|buy_exact_sol_in only',
  ixNameBindings: Object.freeze({
    buy: Object.freeze({ value: 'buy', evidence: 'official_idl' }),
    sell: Object.freeze({ value: 'sell', evidence: 'official_idl' }),
    buy_exact_sol_in: Object.freeze({ value: 'buy_exact_sol_in', evidence: 'official_idl' }),
    buy_v2: Object.freeze({ value: 'buy', evidence: 'fixture_only_synthetic_mapping' }),
    sell_v2: Object.freeze({ value: 'sell', evidence: 'fixture_only_synthetic_mapping' }),
    buy_exact_quote_in_v2: Object.freeze({ value: 'buy', evidence: 'fixture_only_synthetic_mapping' }),
  }),
  variants: Object.freeze({
    create: '181ec828051c0777',
    create_v2: 'd6904cec5f8b31b4',
    buy: '66063d1201daebea',
    sell: '33e685a4017f83ad',
    buy_v2: 'b817ee6167c5d33d',
    sell_v2: '5df6823ce7e940b2',
    buy_exact_sol_in: '38fc74089edfcd5f',
    buy_exact_quote_in_v2: 'c2ab1c46684d5b2f',
  }),
});

export type PumpSilverFixtureRegistryEntry = {
  startInclusive: number;
  endExclusive: number;
  officialDocsCommit: string;
  pumpIdlSha256: string;
};

export const PUMP_SILVER_FIXTURE_REGISTRY_ENTRIES: readonly PumpSilverFixtureRegistryEntry[] = Object.freeze([
  Object.freeze({
    startInclusive: PUMP_SILVER_FIXTURE_REGISTRY.slotRange.startInclusive,
    endExclusive: PUMP_SILVER_FIXTURE_REGISTRY.slotRange.endExclusive,
    officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
    pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
  }),
]);

type Variant = keyof typeof PUMP_SILVER_FIXTURE_REGISTRY.variants;
type TradeVariant = Exclude<Variant, 'create' | 'create_v2'>;
type Kind = 'create' | 'buy' | 'sell';

type VariantConfig = {
  kind: Kind;
  roles: readonly string[];
  fixed: Readonly<Record<string, string>>;
};

const CREATE_ROLES = ['mint', 'mint_authority', 'bonding_curve', 'associated_bonding_curve', 'global',
  'mpl_token_metadata', 'metadata', 'user', 'system_program', 'token_program', 'associated_token_program',
  'rent', 'event_authority', 'program'] as const;
const CREATE_V2_ROLES = ['mint', 'mint_authority', 'bonding_curve', 'associated_bonding_curve', 'global',
  'user', 'system_program', 'token_program', 'associated_token_program', 'mayhem_program_id', 'global_params',
  'sol_vault', 'mayhem_state', 'mayhem_token_vault', 'event_authority', 'program'] as const;
const BUY_ROLES = ['global', 'fee_recipient', 'mint', 'bonding_curve', 'associated_bonding_curve',
  'associated_user', 'user', 'system_program', 'token_program', 'creator_vault', 'event_authority',
  'program', 'global_volume_accumulator', 'user_volume_accumulator', 'fee_config', 'fee_program'] as const;
const SELL_ROLES = ['global', 'fee_recipient', 'mint', 'bonding_curve', 'associated_bonding_curve',
  'associated_user', 'user', 'system_program', 'creator_vault', 'token_program', 'event_authority',
  'program', 'fee_config', 'fee_program'] as const;
const BUY_V2_ROLES = ['global', 'base_mint', 'quote_mint', 'base_token_program', 'quote_token_program',
  'associated_token_program', 'fee_recipient', 'associated_quote_fee_recipient', 'buyback_fee_recipient',
  'associated_quote_buyback_fee_recipient', 'bonding_curve', 'associated_base_bonding_curve',
  'associated_quote_bonding_curve', 'user', 'associated_base_user', 'associated_quote_user', 'creator_vault',
  'associated_creator_vault', 'sharing_config', 'global_volume_accumulator', 'user_volume_accumulator',
  'associated_user_volume_accumulator', 'fee_config', 'fee_program', 'system_program', 'event_authority', 'program'] as const;
const SELL_V2_ROLES = ['global', 'base_mint', 'quote_mint', 'base_token_program', 'quote_token_program',
  'associated_token_program', 'fee_recipient', 'associated_quote_fee_recipient', 'buyback_fee_recipient',
  'associated_quote_buyback_fee_recipient', 'bonding_curve', 'associated_base_bonding_curve',
  'associated_quote_bonding_curve', 'user', 'associated_base_user', 'associated_quote_user', 'creator_vault',
  'associated_creator_vault', 'sharing_config', 'user_volume_accumulator', 'associated_user_volume_accumulator',
  'fee_config', 'fee_program', 'system_program', 'event_authority', 'program'] as const;

const COMMON_LEGACY_FIXED = Object.freeze({ system_program: SYSTEM_PROGRAM, program: PUMP_PROGRAM_ID, fee_program: FEE_PROGRAM });
const COMMON_V2_FIXED = Object.freeze({
  associated_token_program: ASSOCIATED_TOKEN_PROGRAM,
  fee_program: FEE_PROGRAM,
  system_program: SYSTEM_PROGRAM,
  program: PUMP_PROGRAM_ID,
});
const VARIANT_CONFIG: Record<Variant, VariantConfig> = {
  create: {
    kind: 'create', roles: CREATE_ROLES, fixed: Object.freeze({
      mpl_token_metadata: METADATA_PROGRAM, system_program: SYSTEM_PROGRAM, token_program: TOKEN_PROGRAM,
      associated_token_program: ASSOCIATED_TOKEN_PROGRAM, rent: RENT, program: PUMP_PROGRAM_ID,
    }),
  },
  create_v2: {
    kind: 'create', roles: CREATE_V2_ROLES, fixed: Object.freeze({
      system_program: SYSTEM_PROGRAM, token_program: TOKEN_2022_PROGRAM,
      associated_token_program: ASSOCIATED_TOKEN_PROGRAM, mayhem_program_id: MAYHEM_PROGRAM,
      program: PUMP_PROGRAM_ID,
    }),
  },
  buy: { kind: 'buy', roles: BUY_ROLES, fixed: COMMON_LEGACY_FIXED },
  sell: { kind: 'sell', roles: SELL_ROLES, fixed: COMMON_LEGACY_FIXED },
  buy_exact_sol_in: { kind: 'buy', roles: BUY_ROLES, fixed: COMMON_LEGACY_FIXED },
  buy_v2: { kind: 'buy', roles: BUY_V2_ROLES, fixed: COMMON_V2_FIXED },
  sell_v2: { kind: 'sell', roles: SELL_V2_ROLES, fixed: COMMON_V2_FIXED },
  buy_exact_quote_in_v2: { kind: 'buy', roles: BUY_V2_ROLES, fixed: COMMON_V2_FIXED },
};

const DISCRIMINATOR_TO_VARIANT = new Map<string, Variant>(
  Object.entries(PUMP_SILVER_FIXTURE_REGISTRY.variants).map(([variant, discriminator]) => [discriminator, variant as Variant]),
);

export type PumpSilverCoordinate = {
  instructionLocation: 'top_level' | 'inner';
  parentInstructionIndex?: number;
  instructionIndex: number;
  stackHeight?: number;
};

export type PumpSilverQuarantine = {
  reason: string;
  coordinate?: PumpSilverCoordinate;
  discriminatorHex?: string;
};

export type PumpSilverContractEvent = {
  variant: Variant;
  kind: Kind;
  mint: string;
  bondingCurve: string;
  user: string;
  feeRecipient: string | null;
  tokenDecimals: number | null;
  quoteDecimals: number | null;
  roles: Record<string, string>;
  parentCoordinate: PumpSilverCoordinate;
  eventCoordinate: PumpSilverCoordinate;
  event: PumpSilverEvent;
  researchReady: false;
  isExecutedTrade: boolean;
};

export type PumpSilverTransactionContract = {
  schemaVersion: 'PUMP_SILVER_TRANSACTION_CONTRACT_1';
  registry: typeof PUMP_SILVER_FIXTURE_REGISTRY;
  source: {
    schemaVersion: string;
    slot: number | null;
    transactionIndex: number | null;
    signature: string;
    executionStatus: string;
  };
  researchReady: false;
  isExecutedTrade: boolean;
  events: PumpSilverContractEvent[];
  quarantines: PumpSilverQuarantine[];
  canonicalHash: string;
};

function canonicalJson(value: unknown, seen: Set<object>): string {
  if (value === null) return 'null';
  if (typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new Error('non_canonical_number');
    return String(value);
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) throw new Error('cyclic_canonical_value');
    seen.add(value);
    const values: string[] = [];
    for (let index = 0; index < value.length; index += 1) {
      if (!Object.hasOwn(value, index)) throw new Error('sparse_canonical_array');
      values.push(canonicalJson(value[index], seen));
    }
    seen.delete(value);
    return `[${values.join(',')}]`;
  }
  if (typeof value === 'object') {
    if (seen.has(value)) throw new Error('cyclic_canonical_value');
    seen.add(value);
    const pairs = Object.keys(value).sort().map((key) => {
      const entry = (value as Record<string, unknown>)[key];
      if (entry === undefined) throw new Error('undefined_canonical_value');
      return `${JSON.stringify(key)}:${canonicalJson(entry, seen)}`;
    });
    seen.delete(value);
    return `{${pairs.join(',')}}`;
  }
  throw new Error('unsupported_canonical_value');
}

export function canonicalPumpSilverHash(value: unknown): string {
  return createHash('sha256').update(HASH_DOMAIN, 'utf8').update(canonicalJson(value, new Set()), 'utf8').digest('hex');
}

function coordinate(instruction: PumpV2LocatedInstruction): PumpSilverCoordinate {
  return {
    instructionLocation: instruction.instructionLocation,
    ...(instruction.parentInstructionIndex === undefined ? {} : { parentInstructionIndex: instruction.parentInstructionIndex }),
    instructionIndex: instruction.instructionIndex,
    ...(instruction.stackHeight === undefined ? {} : { stackHeight: instruction.stackHeight }),
  };
}

function coordinateKey(instruction: PumpV2LocatedInstruction): string {
  return `${instruction.instructionLocation}:${instruction.parentInstructionIndex ?? '-'}:${instruction.instructionIndex}`;
}

function deriveProgramAddress(programId: string, seeds: Buffer[]): string {
  try {
    const derived = findProgramAddress(seeds, decodePublicKey(programId));
    return derived ? encodePublicKey(derived.address) : '';
  } catch {
    return '';
  }
}

function derivePumpAddress(seed: string, address?: string): string {
  try {
    return deriveProgramAddress(PUMP_PROGRAM_ID, [
      Buffer.from(seed, 'utf8'),
      ...(address ? [decodePublicKey(address)] : []),
    ]);
  } catch {
    return '';
  }
}

function deriveAssociatedTokenAddress(owner: string, tokenProgram: string, mint: string): string {
  try {
    return deriveProgramAddress(ASSOCIATED_TOKEN_PROGRAM, [
      decodePublicKey(owner), decodePublicKey(tokenProgram), decodePublicKey(mint),
    ]);
  } catch {
    return '';
  }
}

function validU64(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 20 && UNSIGNED_INTEGER.test(value)
    && BigInt(value) <= ((1n << 64n) - 1n);
}

function isDenseStringArray(value: unknown): value is string[] {
  if (!Array.isArray(value)) return false;
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.hasOwn(value, index) || typeof value[index] !== 'string') return false;
  }
  return true;
}

function isDenseBoundedArray(value: unknown, maxLength: number): value is unknown[] {
  if (!Array.isArray(value) || value.length > maxLength) return false;
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.hasOwn(value, index)) return false;
  }
  return true;
}

function boundedNormalizedBronzeWork(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const bronze = value as Record<string, unknown>;
  if (!isDenseBoundedArray(bronze.accountKeys, MAX_RESOLVED_ACCOUNT_KEYS)
    || !isDenseBoundedArray(bronze.preBalancesLamports, MAX_RESOLVED_ACCOUNT_KEYS)
    || !isDenseBoundedArray(bronze.postBalancesLamports, MAX_RESOLVED_ACCOUNT_KEYS)
    || !isDenseBoundedArray(bronze.preTokenBalances, MAX_RESOLVED_ACCOUNT_KEYS)
    || !isDenseBoundedArray(bronze.postTokenBalances, MAX_RESOLVED_ACCOUNT_KEYS)
    || !isDenseBoundedArray(bronze.logMessages, MAX_LOG_MESSAGES)
    || !isDenseBoundedArray(bronze.instructions, MAX_NORMALIZED_INSTRUCTIONS)) return false;
  let logCharacters = 0;
  for (const log of bronze.logMessages) {
    if (typeof log !== 'string' || log.length > MAX_LOG_BYTES) return false;
    logCharacters += log.length;
    if (logCharacters > MAX_LOG_BYTES) return false;
  }
  let accountReferences = 0;
  for (const instruction of bronze.instructions) {
    if (typeof instruction !== 'object' || instruction === null || Array.isArray(instruction)) return false;
    const located = instruction as Record<string, unknown>;
    if (!isDenseBoundedArray(located.accountIndices, MAX_RESOLVED_ACCOUNT_KEYS)
      || !isDenseBoundedArray(located.accounts, MAX_RESOLVED_ACCOUNT_KEYS)
      || located.accountIndices.length !== located.accounts.length
      || typeof located.dataHex !== 'string'
      || located.dataHex.length > MAX_INSTRUCTION_DATA_HEX) return false;
    accountReferences += located.accountIndices.length;
    if (accountReferences > MAX_INSTRUCTION_ACCOUNT_REFERENCES) return false;
  }
  return true;
}

function validateInstruction(instruction: unknown, bronze: PumpV2BronzeTransaction): string | undefined {
  if (typeof instruction !== 'object' || instruction === null || Array.isArray(instruction)
    || !isDenseStringArray(bronze?.accountKeys)
    || !Array.isArray((instruction as PumpV2LocatedInstruction).accountIndices)
    || !isDenseStringArray((instruction as PumpV2LocatedInstruction).accounts)
    || typeof (instruction as PumpV2LocatedInstruction).programId !== 'string'
    || typeof (instruction as PumpV2LocatedInstruction).dataHex !== 'string'
    || ((instruction as PumpV2LocatedInstruction).instructionLocation !== 'top_level'
      && (instruction as PumpV2LocatedInstruction).instructionLocation !== 'inner')) {
    return 'invalid_bronze_instruction_structure';
  }
  const located = instruction as PumpV2LocatedInstruction;
  if (!Number.isSafeInteger(located.instructionIndex) || located.instructionIndex < 0
    || !Number.isSafeInteger(located.programIdIndex) || located.programIdIndex < 0
    || located.programIdIndex >= bronze.accountKeys.length
    || bronze.accountKeys[located.programIdIndex] !== located.programId
    || located.accountIndices.length !== located.accounts.length) return 'invalid_bronze_instruction_structure';
  for (let index = 0; index < located.accountIndices.length; index += 1) {
    if (!Object.hasOwn(located.accountIndices, index)) return 'invalid_bronze_instruction_structure';
    const accountIndex = located.accountIndices[index];
    if (!Number.isSafeInteger(accountIndex) || accountIndex < 0 || accountIndex >= bronze.accountKeys.length
      || bronze.accountKeys[accountIndex] !== located.accounts[index]) return 'invalid_bronze_instruction_structure';
  }
  if (located.instructionLocation === 'top_level') {
    if (located.parentInstructionIndex !== undefined || (located.stackHeight !== undefined && located.stackHeight !== 1)) {
      return 'invalid_bronze_instruction_structure';
    }
  } else if (!Number.isSafeInteger(located.parentInstructionIndex)
    || located.parentInstructionIndex! < 0
    || !Number.isSafeInteger(located.stackHeight)
    || located.stackHeight! < 2 || located.stackHeight! > 9) return 'invalid_bronze_instruction_structure';
  return undefined;
}

function validInstructionOrder(instructions: PumpV2LocatedInstruction[]): boolean {
  let expectedTopLevelIndex = 0;
  let currentParentIndex = -1;
  let expectedInnerIndex = 0;
  for (const instruction of instructions) {
    if (instruction.instructionLocation === 'top_level') {
      if (instruction.instructionIndex !== expectedTopLevelIndex) return false;
      currentParentIndex = instruction.instructionIndex;
      expectedTopLevelIndex += 1;
      expectedInnerIndex = 0;
      continue;
    }
    if (currentParentIndex < 0
      || instruction.parentInstructionIndex !== currentParentIndex
      || instruction.instructionIndex !== expectedInnerIndex) return false;
    expectedInnerIndex += 1;
  }
  return true;
}

function denseUnknownArray(value: unknown): value is unknown[] {
  if (!Array.isArray(value)) return false;
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.hasOwn(value, index)) return false;
  }
  return true;
}

function bronzeProducerProjection(bronze: PumpV2BronzeTransaction) {
  return {
    schemaVersion: bronze.schemaVersion,
    slot: bronze.slot,
    transactionIndex: bronze.transactionIndex,
    signature: bronze.signature,
    blockTime: bronze.blockTime,
    executionStatus: bronze.executionStatus,
    feeLamports: bronze.feeLamports,
    logMessages: bronze.logMessages,
    accountKeys: bronze.accountKeys,
    preBalancesLamports: bronze.preBalancesLamports,
    postBalancesLamports: bronze.postBalancesLamports,
    instructions: bronze.instructions,
    preTokenBalances: bronze.preTokenBalances,
    postTokenBalances: bronze.postTokenBalances,
  };
}

function revalidateNormalizedBronze(value: unknown): boolean {
  try {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
    const bronze = value as PumpV2BronzeTransaction;
    if (!denseUnknownArray(bronze.instructions)) return false;
    const topLevelInstructions: unknown[] = [];
    const innerByParent = new Map<number, unknown[]>();
    for (const rawInstruction of bronze.instructions) {
      if (typeof rawInstruction !== 'object' || rawInstruction === null || Array.isArray(rawInstruction)) return false;
      const instruction = rawInstruction as PumpV2LocatedInstruction;
      const sourceInstruction = {
        programIdIndex: instruction.programIdIndex,
        accountIndices: instruction.accountIndices,
        dataHex: instruction.dataHex,
        ...(instruction.stackHeight === undefined ? {} : { stackHeight: instruction.stackHeight }),
      };
      if (instruction.instructionLocation === 'top_level') {
        if (!Number.isSafeInteger(instruction.instructionIndex) || instruction.instructionIndex < 0
          || Object.hasOwn(topLevelInstructions, instruction.instructionIndex)) return false;
        topLevelInstructions[instruction.instructionIndex] = sourceInstruction;
      } else if (instruction.instructionLocation === 'inner') {
        if (!Number.isSafeInteger(instruction.parentInstructionIndex) || instruction.parentInstructionIndex! < 0
          || !Number.isSafeInteger(instruction.instructionIndex) || instruction.instructionIndex < 0) return false;
        const group = innerByParent.get(instruction.parentInstructionIndex!) ?? [];
        if (Object.hasOwn(group, instruction.instructionIndex)) return false;
        group[instruction.instructionIndex] = sourceInstruction;
        innerByParent.set(instruction.parentInstructionIndex!, group);
      } else return false;
    }
    if (!denseUnknownArray(topLevelInstructions) || [...innerByParent.values()].some((group) => !denseUnknownArray(group))) {
      return false;
    }
    const recaptured = capturePumpV2BronzeTransaction({
      slot: bronze.slot,
      transactionIndex: bronze.transactionIndex,
      signature: bronze.signature,
      blockTime: bronze.blockTime,
      err: bronze.executionStatus === 'succeeded' ? null : bronze.executionStatus === 'failed' ? 'failed' : undefined,
      feeLamports: bronze.feeLamports,
      logMessages: bronze.logMessages,
      staticAccountKeys: bronze.accountKeys,
      loadedAddresses: { writable: [], readonly: [] },
      preBalancesLamports: bronze.preBalancesLamports,
      postBalancesLamports: bronze.postBalancesLamports,
      topLevelInstructions,
      innerInstructionGroups: [...innerByParent.entries()]
        .sort(([left], [right]) => left - right)
        .map(([parentInstructionIndex, instructions]) => ({ parentInstructionIndex, instructions })),
      preTokenBalances: bronze.preTokenBalances,
      postTokenBalances: bronze.postTokenBalances,
    });
    return canonicalJson(bronzeProducerProjection(bronze), new Set())
      === canonicalJson(bronzeProducerProjection(recaptured), new Set());
  } catch {
    return false;
  }
}

function balancesForAccount(
  bronze: PumpV2BronzeTransaction,
  accountIndex: number,
): { pre: PumpV2TokenBalance; post: PumpV2TokenBalance } | undefined {
  const pre = bronze.preTokenBalances.filter((balance) => balance.accountIndex === accountIndex);
  const post = bronze.postTokenBalances.filter((balance) => balance.accountIndex === accountIndex);
  return pre.length === 1 && post.length === 1 ? { pre: pre[0]!, post: post[0]! } : undefined;
}

function tradeBalanceDecimals(
  bronze: PumpV2BronzeTransaction,
  roles: Record<string, string>,
  event: Extract<PumpSilverEvent, { eventType: 'trade' }>,
  requireExecutedMovement: boolean,
): number | undefined {
  const associatedUser = roles.associated_user ?? roles.associated_base_user;
  const associatedCurve = roles.associated_bonding_curve ?? roles.associated_base_bonding_curve;
  const tokenProgram = roles.token_program ?? roles.base_token_program;
  const userIndex = bronze.accountKeys.indexOf(associatedUser);
  const curveIndex = bronze.accountKeys.indexOf(associatedCurve);
  const userBalances = balancesForAccount(bronze, userIndex);
  const curveBalances = balancesForAccount(bronze, curveIndex);
  if (!userBalances || !curveBalances) return undefined;
  const all = [userBalances.pre, userBalances.post, curveBalances.pre, curveBalances.post];
  if (all.some((balance) => balance.mint !== event.mint
    || balance.programId !== tokenProgram
    || !validU64(balance.amount)
    || !Number.isSafeInteger(balance.decimals)
    || balance.decimals < 0 || balance.decimals > 255
    || balance.decimals !== all[0]!.decimals)
    || userBalances.pre.owner !== roles.user || userBalances.post.owner !== roles.user
    || curveBalances.pre.owner !== roles.bonding_curve || curveBalances.post.owner !== roles.bonding_curve) return undefined;
  const amount = BigInt(event.tokenAmount);
  const expectedUser = BigInt(userBalances.pre.amount)
    + (requireExecutedMovement ? (event.isBuy ? amount : -amount) : 0n);
  const expectedCurve = BigInt(curveBalances.pre.amount)
    + (requireExecutedMovement ? (event.isBuy ? -amount : amount) : 0n);
  if (expectedUser < 0n || expectedCurve < 0n
    || BigInt(userBalances.post.amount) !== expectedUser
    || BigInt(curveBalances.post.amount) !== expectedCurve) return undefined;
  const mintDecimals = new Set([
    ...bronze.preTokenBalances.filter((balance) => balance.mint === event.mint).map((balance) => balance.decimals),
    ...bronze.postTokenBalances.filter((balance) => balance.mint === event.mint).map((balance) => balance.decimals),
  ]);
  return mintDecimals.size === 1 ? all[0]!.decimals : undefined;
}

function quoteDecimals(bronze: PumpV2BronzeTransaction, roles: Record<string, string>, event: PumpSilverEvent): number | undefined {
  if (event.quoteMint !== WSOL) return undefined;
  if (!roles.quote_mint) return 9;
  if (roles.quote_mint !== WSOL || roles.quote_token_program !== TOKEN_PROGRAM) return undefined;
  const quoteBalances = [...bronze.preTokenBalances, ...bronze.postTokenBalances]
    .filter((balance) => balance.mint === WSOL);
  if (quoteBalances.length < 4 || quoteBalances.some((balance) => balance.decimals !== 9
    || balance.programId !== TOKEN_PROGRAM)) return undefined;
  const userIndex = bronze.accountKeys.indexOf(roles.associated_quote_user);
  const curveIndex = bronze.accountKeys.indexOf(roles.associated_quote_bonding_curve);
  return balancesForAccount(bronze, userIndex) && balancesForAccount(bronze, curveIndex) ? 9 : undefined;
}

function addExpectedDelta(expected: Map<number, bigint>, index: number, delta: bigint): boolean {
  if (index < 0) return false;
  expected.set(index, (expected.get(index) ?? 0n) + delta);
  return true;
}

function failedStateRolledBack(bronze: PumpV2BronzeTransaction): boolean {
  if (bronze.preTokenBalances.length !== bronze.postTokenBalances.length) return false;
  for (let index = 0; index < bronze.preTokenBalances.length; index += 1) {
    if (canonicalJson(bronze.preTokenBalances[index], new Set())
      !== canonicalJson(bronze.postTokenBalances[index], new Set())) return false;
  }
  if (bronze.preBalancesLamports.length !== bronze.postBalancesLamports.length
    || bronze.preBalancesLamports.length === 0) return false;
  const fee = BigInt(bronze.feeLamports);
  for (let index = 0; index < bronze.preBalancesLamports.length; index += 1) {
    const pre = BigInt(bronze.preBalancesLamports[index]!);
    const post = BigInt(bronze.postBalancesLamports[index]!);
    if (post - pre !== (index === 0 ? -fee : 0n)) return false;
  }
  return true;
}

function legacyNativeMovementMatches(
  bronze: PumpV2BronzeTransaction,
  roles: Record<string, string>,
  event: Extract<PumpSilverEvent, { eventType: 'trade' }>,
): boolean {
  if (event.cashback !== '0' || event.buybackFee !== '0') return false;
  const expected = new Map<number, bigint>();
  const solAmount = BigInt(event.solAmount);
  const protocolFee = BigInt(event.fee);
  const creatorFee = BigInt(event.creatorFee);
  if (!addExpectedDelta(expected, 0, -BigInt(bronze.feeLamports))
    || !addExpectedDelta(expected, bronze.accountKeys.indexOf(roles.bonding_curve), event.isBuy ? solAmount : -solAmount)
    || !addExpectedDelta(expected, bronze.accountKeys.indexOf(roles.fee_recipient), protocolFee)
    || !addExpectedDelta(expected, bronze.accountKeys.indexOf(roles.creator_vault), creatorFee)
    || !addExpectedDelta(
      expected,
      bronze.accountKeys.indexOf(roles.user),
      event.isBuy ? -(solAmount + protocolFee + creatorFee) : solAmount - protocolFee - creatorFee,
    )) return false;
  for (let index = 0; index < bronze.accountKeys.length; index += 1) {
    const actual = BigInt(bronze.postBalancesLamports[index]!) - BigInt(bronze.preBalancesLamports[index]!);
    if (actual !== (expected.get(index) ?? 0n)) return false;
  }
  return true;
}

function v2QuoteMovementMatches(
  bronze: PumpV2BronzeTransaction,
  roles: Record<string, string>,
  event: Extract<PumpSilverEvent, { eventType: 'trade' }>,
): boolean {
  if (event.cashback !== '0') return false;
  const roleOwners = [
    ['associated_quote_user', 'user'],
    ['associated_quote_bonding_curve', 'bonding_curve'],
    ['associated_quote_fee_recipient', 'fee_recipient'],
    ['associated_quote_buyback_fee_recipient', 'buyback_fee_recipient'],
    ['associated_creator_vault', 'creator_vault'],
  ] as const;
  for (const [accountRole, ownerRole] of roleOwners) {
    const pair = balancesForAccount(bronze, bronze.accountKeys.indexOf(roles[accountRole]));
    if (!pair || pair.pre.owner !== roles[ownerRole] || pair.post.owner !== roles[ownerRole]
      || pair.pre.mint !== WSOL || pair.post.mint !== WSOL) return false;
  }
  const expected = new Map<number, bigint>();
  const quoteAmount = BigInt(event.quoteAmount);
  const protocolFee = BigInt(event.fee);
  const creatorFee = BigInt(event.creatorFee);
  const buybackFee = BigInt(event.buybackFee);
  if (!addExpectedDelta(expected, bronze.accountKeys.indexOf(roles.associated_quote_bonding_curve), event.isBuy ? quoteAmount : -quoteAmount)
    || !addExpectedDelta(expected, bronze.accountKeys.indexOf(roles.associated_quote_fee_recipient), protocolFee)
    || !addExpectedDelta(expected, bronze.accountKeys.indexOf(roles.associated_quote_buyback_fee_recipient), buybackFee)
    || !addExpectedDelta(expected, bronze.accountKeys.indexOf(roles.associated_creator_vault), creatorFee)
    || !addExpectedDelta(
      expected,
      bronze.accountKeys.indexOf(roles.associated_quote_user),
      event.isBuy
        ? -(quoteAmount + protocolFee + creatorFee + buybackFee)
        : quoteAmount - protocolFee - creatorFee - buybackFee,
    )) return false;
  const accountIndices = new Set([
    ...bronze.preTokenBalances.filter((balance) => balance.mint === WSOL).map((balance) => balance.accountIndex),
    ...bronze.postTokenBalances.filter((balance) => balance.mint === WSOL).map((balance) => balance.accountIndex),
  ]);
  for (const accountIndex of accountIndices) {
    const pair = balancesForAccount(bronze, accountIndex);
    if (!pair) return false;
    const actual = BigInt(pair.post.amount) - BigInt(pair.pre.amount);
    if (actual !== (expected.get(accountIndex) ?? 0n)) return false;
  }
  return true;
}

function reserveBalancesMatch(
  bronze: PumpV2BronzeTransaction,
  roles: Record<string, string>,
  event: Extract<PumpSilverEvent, { eventType: 'trade' }>,
): boolean {
  const baseCurve = roles.associated_bonding_curve ?? roles.associated_base_bonding_curve;
  const basePair = balancesForAccount(bronze, bronze.accountKeys.indexOf(baseCurve));
  if (!basePair || basePair.post.amount !== event.realTokenReserves) return false;
  if (!roles.quote_mint) {
    const curveIndex = bronze.accountKeys.indexOf(roles.bonding_curve);
    return curveIndex >= 0 && bronze.postBalancesLamports[curveIndex] === event.realSolReserves;
  }
  const quotePair = balancesForAccount(
    bronze,
    bronze.accountKeys.indexOf(roles.associated_quote_bonding_curve),
  );
  return Boolean(quotePair && quotePair.post.amount === event.realQuoteReserves);
}

function ceilFee(amount: string, basisPoints: string): bigint {
  return (BigInt(amount) * BigInt(basisPoints) + 9_999n) / 10_000n;
}

function validateTradeEvent(event: Extract<PumpSilverEvent, { eventType: 'trade' }>, variant: TradeVariant): string | undefined {
  const config = VARIANT_CONFIG[variant];
  if (event.isBuy !== (config.kind === 'buy')) return 'event_kind_mismatch';
  const expectedIxName = PUMP_SILVER_FIXTURE_REGISTRY.ixNameBindings[variant].value;
  if (event.ixName !== expectedIxName) return 'event_kind_mismatch';
  const unsigned = [event.solAmount, event.tokenAmount, event.virtualSolReserves, event.virtualTokenReserves,
    event.realSolReserves, event.realTokenReserves, event.feeBasisPoints, event.fee,
    event.creatorFeeBasisPoints, event.creatorFee, event.totalUnclaimedTokens, event.totalClaimedTokens,
    event.currentSolVolume, event.cashbackFeeBasisPoints, event.cashback, event.buybackFeeBasisPoints,
    event.buybackFee, event.quoteAmount, event.virtualQuoteReserves, event.realQuoteReserves];
  if (unsigned.some((value) => !validU64(value))
    || !SIGNED_INTEGER.test(event.timestamp) || !SIGNED_INTEGER.test(event.lastUpdateTimestamp)) return 'invalid_raw_integer';
  if (event.quoteMint !== WSOL || event.quoteAmount !== event.solAmount
    || event.virtualQuoteReserves !== event.virtualSolReserves
    || event.realQuoteReserves !== event.realSolReserves) return 'quote_sol_inconsistency';
  if (BigInt(event.realSolReserves) > BigInt(event.virtualSolReserves)
    || BigInt(event.realTokenReserves) > BigInt(event.virtualTokenReserves)
    || BigInt(event.realQuoteReserves) > BigInt(event.virtualQuoteReserves)) return 'reserve_inconsistency';
  for (const [bps, fee] of [[event.feeBasisPoints, event.fee], [event.creatorFeeBasisPoints, event.creatorFee],
    [event.cashbackFeeBasisPoints, event.cashback], [event.buybackFeeBasisPoints, event.buybackFee]] as const) {
    if (BigInt(bps) > 10_000n || BigInt(fee) !== ceilFee(event.solAmount, bps)) return 'fee_inconsistency';
  }
  const shareholders = new Set(event.shareholders.map((shareholder) => shareholder.address));
  const shareTotal = event.shareholders.reduce((sum, shareholder) => sum + shareholder.shareBps, 0);
  if (event.shareholders.length === 0 || shareholders.size !== event.shareholders.length
    || event.shareholders.some((shareholder) => !Number.isSafeInteger(shareholder.shareBps)
      || shareholder.shareBps <= 0 || shareholder.shareBps > 10_000)
    || shareTotal !== 10_000) return 'invalid_shareholders';
  return undefined;
}

function validTradeInstructionArgs(dataHex: string, variant: TradeVariant): boolean {
  const hasOptionBool = variant === 'buy' || variant === 'buy_exact_sol_in';
  const expectedBytes = hasOptionBool ? 25 : 24;
  if (dataHex.length !== expectedBytes * 2
    || dataHex.slice(0, 16) !== PUMP_SILVER_FIXTURE_REGISTRY.variants[variant]) return false;
  if (!hasOptionBool) return true;
  const boolByte = dataHex.slice(-2);
  return boolByte === '00' || boolByte === '01';
}

class CreateCursor {
  #bytes: Buffer;
  #offset = 0;

  constructor(dataHex: string) { this.#bytes = Buffer.from(dataHex, 'hex'); }

  take(length: number): Buffer {
    if (!Number.isSafeInteger(length) || length < 0 || this.#offset + length > this.#bytes.length) {
      throw new Error('invalid_create_instruction_args');
    }
    const value = this.#bytes.subarray(this.#offset, this.#offset + length);
    this.#offset += length;
    return value;
  }

  text(): string {
    const length = this.take(4).readUInt32LE(0);
    if (length > 1_024) throw new Error('invalid_create_instruction_args');
    const bytes = this.take(length);
    const value = bytes.toString('utf8');
    if (!Buffer.from(value, 'utf8').equals(bytes)) throw new Error('invalid_create_instruction_args');
    return value;
  }

  publicKey(): string { return encodePublicKey(this.take(32)); }
  bool(): boolean { const value = this.take(1)[0]; if (value !== 0 && value !== 1) throw new Error('invalid_create_instruction_args'); return value === 1; }
  remaining(): number { return this.#bytes.length - this.#offset; }
}

function decodeCreateArgs(dataHex: string, variant: 'create' | 'create_v2') {
  const cursor = new CreateCursor(dataHex);
  if (cursor.take(8).toString('hex') !== PUMP_SILVER_FIXTURE_REGISTRY.variants[variant]) {
    throw new Error('wrong_parent_discriminator');
  }
  const args = { name: cursor.text(), symbol: cursor.text(), uri: cursor.text(), creator: cursor.publicKey(),
    isMayhemMode: false, isCashbackEnabled: false };
  if (variant === 'create_v2') {
    args.isMayhemMode = cursor.bool();
    args.isCashbackEnabled = cursor.bool();
  }
  if (cursor.remaining() !== 0) throw new Error('invalid_create_instruction_args');
  return args;
}

function roleMap(accounts: string[], config: VariantConfig): Record<string, string> | undefined {
  if (accounts.length !== config.roles.length) return undefined;
  const roles: Record<string, string> = {};
  for (let index = 0; index < config.roles.length; index += 1) roles[config.roles[index]!] = accounts[index]!;
  return roles;
}

function fixedRolesMatch(roles: Record<string, string>, fixed: Readonly<Record<string, string>>): boolean {
  return Object.entries(fixed).every(([name, address]) => roles[name] === address);
}

function officialPdaRolesMatch(
  roles: Record<string, string>,
  event: PumpSilverEvent,
): boolean {
  const expectRole = (name: string, address: string) => roles[name] === undefined || (address !== '' && roles[name] === address);
  const mint = roles.mint ?? roles.base_mint ?? event.mint;
  const tokenProgram = roles.token_program ?? roles.base_token_program;
  if (!expectRole('global', derivePumpAddress('global'))
    || !expectRole('mint_authority', derivePumpAddress('mint-authority'))
    || !expectRole('bonding_curve', derivePumpAddress('bonding-curve', mint))
    || !expectRole('event_authority', derivePumpAddress('__event_authority'))
    || !expectRole('global_volume_accumulator', derivePumpAddress('global_volume_accumulator'))
    || !expectRole('user_volume_accumulator', derivePumpAddress('user_volume_accumulator', roles.user))) return false;
  if (roles.associated_bonding_curve
    && !expectRole('associated_bonding_curve', deriveAssociatedTokenAddress(roles.bonding_curve, tokenProgram, mint))) return false;
  if (roles.associated_base_bonding_curve
    && !expectRole('associated_base_bonding_curve', deriveAssociatedTokenAddress(
      roles.bonding_curve, roles.base_token_program, mint,
    ))) return false;
  if (roles.metadata && !expectRole('metadata', deriveProgramAddress(roles.mpl_token_metadata, [
    Buffer.from('metadata'), decodePublicKey(roles.mpl_token_metadata), decodePublicKey(mint),
  ]))) return false;
  if (roles.creator_vault && event.eventType === 'trade'
    && !expectRole('creator_vault', derivePumpAddress('creator-vault', event.creator))) return false;
  if (roles.fee_config && !expectRole('fee_config', deriveProgramAddress(roles.fee_program, [
    Buffer.from('fee_config'), decodePublicKey(PUMP_PROGRAM_ID),
  ]))) return false;
  if (roles.associated_quote_fee_recipient
    && !expectRole('associated_quote_fee_recipient', deriveAssociatedTokenAddress(
      roles.fee_recipient, roles.quote_token_program, roles.quote_mint,
    ))) return false;
  if (roles.associated_quote_buyback_fee_recipient
    && !expectRole('associated_quote_buyback_fee_recipient', deriveAssociatedTokenAddress(
      roles.buyback_fee_recipient, roles.quote_token_program, roles.quote_mint,
    ))) return false;
  if (roles.associated_quote_bonding_curve
    && !expectRole('associated_quote_bonding_curve', deriveAssociatedTokenAddress(
      roles.bonding_curve, roles.quote_token_program, roles.quote_mint,
    ))) return false;
  if (roles.associated_quote_user
    && !expectRole('associated_quote_user', deriveAssociatedTokenAddress(
      roles.user, roles.quote_token_program, roles.quote_mint,
    ))) return false;
  if (roles.associated_creator_vault
    && !expectRole('associated_creator_vault', deriveAssociatedTokenAddress(
      roles.creator_vault, roles.quote_token_program, roles.quote_mint,
    ))) return false;
  if (roles.sharing_config && !expectRole('sharing_config', deriveProgramAddress(FEE_PROGRAM, [
    Buffer.from('sharing-config'), decodePublicKey(mint),
  ]))) return false;
  if (roles.associated_user_volume_accumulator
    && !expectRole('associated_user_volume_accumulator', deriveAssociatedTokenAddress(
      roles.user_volume_accumulator, roles.quote_token_program, roles.quote_mint,
    ))) return false;
  if (roles.global_params && !expectRole('global_params', deriveProgramAddress(MAYHEM_PROGRAM, [
    Buffer.from('global-params'),
  ]))) return false;
  if (roles.sol_vault && !expectRole('sol_vault', deriveProgramAddress(MAYHEM_PROGRAM, [
    Buffer.from('sol-vault'),
  ]))) return false;
  if (roles.mayhem_state && !expectRole('mayhem_state', deriveProgramAddress(MAYHEM_PROGRAM, [
    Buffer.from('mayhem-state'), decodePublicKey(mint),
  ]))) return false;
  return true;
}

function pairParent(
  eventInstruction: PumpV2LocatedInstruction,
  parents: PumpV2LocatedInstruction[],
  instructions: PumpV2LocatedInstruction[],
): PumpV2LocatedInstruction | undefined {
  if (eventInstruction.instructionLocation !== 'inner' || eventInstruction.stackHeight === undefined) return undefined;
  const candidates = parents.filter((parent) => {
    if (parent.instructionLocation === 'top_level') {
      return parent.instructionIndex === eventInstruction.parentInstructionIndex && eventInstruction.stackHeight === 2;
    }
    if (parent.parentInstructionIndex !== eventInstruction.parentInstructionIndex
      || parent.instructionIndex >= eventInstruction.instructionIndex
      || parent.stackHeight === undefined
      || parent.stackHeight + 1 !== eventInstruction.stackHeight) return false;
    return !instructions.some((between) => between.instructionLocation === 'inner'
      && between.parentInstructionIndex === parent.parentInstructionIndex
      && between.instructionIndex > parent.instructionIndex
      && between.instructionIndex < eventInstruction.instructionIndex
      && between.stackHeight !== undefined
      && between.stackHeight <= parent.stackHeight!);
  });
  return candidates.length === 1 ? candidates[0] : undefined;
}

function parentBinding(
  bronze: PumpV2BronzeTransaction,
  parent: PumpV2LocatedInstruction,
  event: PumpSilverEvent,
  variant: Variant,
): { event?: PumpSilverContractEvent; reason?: string } {
  const config = VARIANT_CONFIG[variant];
  const roles = roleMap(parent.accounts, config);
  if (!roles) return { reason: 'invalid_account_layout' };
  if (!fixedRolesMatch(roles, config.fixed)
    || !officialPdaRolesMatch(roles, event)
    || roles.program !== PUMP_PROGRAM_ID) return { reason: 'account_role_or_pda_mismatch' };

  if (event.eventType === 'create') {
    if (config.kind !== 'create') return { reason: 'event_kind_mismatch' };
    let args: ReturnType<typeof decodeCreateArgs>;
    try { args = decodeCreateArgs(parent.dataHex, variant as 'create' | 'create_v2'); }
    catch { return { reason: 'invalid_create_instruction_args' }; }
    if (roles.mint !== event.mint || roles.bonding_curve !== event.bondingCurve
      || roles.bonding_curve !== deriveBondingCurve(event.mint) || roles.user !== event.user
      || roles.token_program !== event.tokenProgram || args.name !== event.name || args.symbol !== event.symbol
      || args.uri !== event.uri || args.creator !== event.creator || args.isMayhemMode !== event.isMayhemMode
      || args.isCashbackEnabled !== event.isCashbackEnabled || event.quoteMint !== WSOL
      || event.virtualQuoteReserves !== event.virtualSolReserves) return { reason: 'account_role_or_pda_mismatch' };
    if (![event.timestamp].every((value) => SIGNED_INTEGER.test(value))
      || ![event.virtualTokenReserves, event.virtualSolReserves, event.realTokenReserves,
        event.tokenTotalSupply, event.virtualQuoteReserves].every(validU64)) return { reason: 'invalid_raw_integer' };
    if (BigInt(event.realTokenReserves) > BigInt(event.virtualTokenReserves)
      || BigInt(event.realTokenReserves) > BigInt(event.tokenTotalSupply)) {
      return { reason: 'reserve_inconsistency' };
    }
    return { event: {
      variant, kind: 'create', mint: event.mint, bondingCurve: event.bondingCurve, user: event.user,
      feeRecipient: null, tokenDecimals: null, quoteDecimals: 9, roles,
      parentCoordinate: coordinate(parent), eventCoordinate: { instructionLocation: 'inner', instructionIndex: -1 },
      event, researchReady: false, isExecutedTrade: false,
    } };
  }

  if (config.kind === 'create') return { reason: 'event_kind_mismatch' };
  if (!validTradeInstructionArgs(parent.dataHex, variant as TradeVariant)) {
    return { reason: 'invalid_parent_instruction_args' };
  }
  const semanticReason = validateTradeEvent(event, variant as TradeVariant);
  if (semanticReason) return { reason: semanticReason };
  const mint = roles.mint ?? roles.base_mint;
  const tokenProgram = roles.token_program ?? roles.base_token_program;
  if (mint !== event.mint || roles.bonding_curve !== deriveBondingCurve(event.mint)
    || roles.user !== event.user || roles.fee_recipient !== event.feeRecipient
    || roles.creator_vault !== derivePumpAddress('creator-vault', event.creator)
    || (tokenProgram !== TOKEN_PROGRAM && tokenProgram !== TOKEN_2022_PROGRAM)) {
    return { reason: 'account_role_or_pda_mismatch' };
  }
  const isSucceeded = bronze.executionStatus === 'succeeded';
  if (!isSucceeded && !failedStateRolledBack(bronze)) return { reason: 'failed_state_not_rolled_back' };
  const tokenDecimals = tradeBalanceDecimals(bronze, roles, event, isSucceeded);
  const observedQuoteDecimals = quoteDecimals(bronze, roles, event);
  if (tokenDecimals === undefined || observedQuoteDecimals === undefined) {
    return { reason: 'ambiguous_token_balance_attribution' };
  }
  if (isSucceeded) {
    if (roles.quote_mint) {
      if (!v2QuoteMovementMatches(bronze, roles, event)) return { reason: 'quote_balance_inconsistency' };
    } else if (!legacyNativeMovementMatches(bronze, roles, event)) {
      return { reason: 'native_balance_inconsistency' };
    }
    if (!reserveBalancesMatch(bronze, roles, event)) return { reason: 'reserve_balance_inconsistency' };
  }
  return { event: {
    variant, kind: config.kind, mint: event.mint, bondingCurve: roles.bonding_curve, user: event.user,
    feeRecipient: event.feeRecipient, tokenDecimals, quoteDecimals: observedQuoteDecimals, roles,
    parentCoordinate: coordinate(parent), eventCoordinate: { instructionLocation: 'inner', instructionIndex: -1 },
    event, researchReady: false, isExecutedTrade: bronze.executionStatus === 'succeeded',
  } };
}

function normalizeFixtureRegistryEntry(entry: unknown): PumpSilverFixtureRegistryEntry | null {
  try {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return null;
    const ownPropertyNames = Object.getOwnPropertyNames(entry);
    if (Object.getOwnPropertySymbols(entry).length !== 0
      || ownPropertyNames.length !== FIXTURE_REGISTRY_ENTRY_KEYS.size
      || ownPropertyNames.some((key) => !FIXTURE_REGISTRY_ENTRY_KEYS.has(key))) return null;

    const startDescriptor = Object.getOwnPropertyDescriptor(entry, 'startInclusive');
    const endDescriptor = Object.getOwnPropertyDescriptor(entry, 'endExclusive');
    const commitDescriptor = Object.getOwnPropertyDescriptor(entry, 'officialDocsCommit');
    const idlDescriptor = Object.getOwnPropertyDescriptor(entry, 'pumpIdlSha256');
    if (!startDescriptor || !Object.hasOwn(startDescriptor, 'value')
      || !endDescriptor || !Object.hasOwn(endDescriptor, 'value')
      || !commitDescriptor || !Object.hasOwn(commitDescriptor, 'value')
      || !idlDescriptor || !Object.hasOwn(idlDescriptor, 'value')) return null;

    const candidate = entry as Record<string, unknown>;
    const startInclusive = candidate.startInclusive;
    const endExclusive = candidate.endExclusive;
    const officialDocsCommit = candidate.officialDocsCommit;
    const pumpIdlSha256 = candidate.pumpIdlSha256;
    if (!Object.is(startInclusive, startDescriptor.value)
      || !Object.is(endExclusive, endDescriptor.value)
      || !Object.is(officialDocsCommit, commitDescriptor.value)
      || !Object.is(pumpIdlSha256, idlDescriptor.value)
      || !Number.isSafeInteger(startInclusive)
      || !Number.isSafeInteger(endExclusive)
      || (startInclusive as number) < 0
      || (startInclusive as number) >= (endExclusive as number)
      || typeof officialDocsCommit !== 'string'
      || !GIT_COMMIT_HEX.test(officialDocsCommit)
      || typeof pumpIdlSha256 !== 'string'
      || !SHA256_HEX.test(pumpIdlSha256)) return null;
    return {
      startInclusive: startInclusive as number,
      endExclusive: endExclusive as number,
      officialDocsCommit,
      pumpIdlSha256,
    };
  } catch {
    return null;
  }
}

function registryQuarantineReason(
  slot: number,
  entries: readonly PumpSilverFixtureRegistryEntry[],
): string | undefined {
  try {
    if (!Array.isArray(entries)) return 'invalid_fixture_registry';
    const entryCount = entries.length;
    if (!Number.isSafeInteger(entryCount) || entryCount === 0 || entryCount > 16) return 'invalid_fixture_registry';
    const normalizedEntries: PumpSilverFixtureRegistryEntry[] = [];
    for (let index = 0; index < entryCount; index += 1) {
      if (!Object.hasOwn(entries, index)) return 'invalid_fixture_registry';
      const entry = normalizeFixtureRegistryEntry(entries[index]);
      if (!entry) return 'invalid_fixture_registry';
      normalizedEntries.push(entry);
    }
    if (entries.length !== entryCount) return 'invalid_fixture_registry';
    const ranged = normalizedEntries.filter((entry) => slot >= entry.startInclusive && slot < entry.endExclusive);
    if (ranged.length === 0) return 'registry_slot_out_of_range';
    const pinned = ranged.filter((entry) => entry.officialDocsCommit === PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit
      && entry.pumpIdlSha256 === PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256);
    if (pinned.length === 0) return 'registry_provenance_mismatch';
    if (pinned.some((entry) => entry.startInclusive !== PUMP_SILVER_FIXTURE_REGISTRY.slotRange.startInclusive
      || entry.endExclusive !== PUMP_SILVER_FIXTURE_REGISTRY.slotRange.endExclusive)) return 'registry_entry_mismatch';
    if (pinned.length !== 1 || ranged.length !== 1) return 'ambiguous_registry_match';
    return undefined;
  } catch {
    return 'invalid_fixture_registry';
  }
}

export function evaluatePumpSilverTransaction(
  bronze: PumpV2BronzeTransaction,
  registryEntries: readonly PumpSilverFixtureRegistryEntry[] = PUMP_SILVER_FIXTURE_REGISTRY_ENTRIES,
): PumpSilverTransactionContract {
  const observedSlot = bronze?.slot;
  const observedTransactionIndex = bronze?.transactionIndex;
  const safeCoordinates = Number.isSafeInteger(observedSlot) && observedSlot >= 0
    && Number.isSafeInteger(observedTransactionIndex) && observedTransactionIndex >= 0;
  const observedSchemaVersion = bronze?.schemaVersion;
  const observedSignature = bronze?.signature;
  const observedExecutionStatus = bronze?.executionStatus;
  const source = {
    schemaVersion: observedSchemaVersion === 'PUMP_V2_BRONZE_TRANSACTION_1' ? observedSchemaVersion : '',
    slot: safeCoordinates ? observedSlot : null,
    transactionIndex: safeCoordinates ? observedTransactionIndex : null,
    signature: typeof observedSignature === 'string' && observedSignature.length <= 88 ? observedSignature : '',
    executionStatus: observedExecutionStatus === 'succeeded' || observedExecutionStatus === 'failed'
      ? observedExecutionStatus
      : '',
  };
  const events: PumpSilverContractEvent[] = [];
  const quarantines: PumpSilverQuarantine[] = [];
  if (!safeCoordinates) quarantines.push({ reason: 'unsafe_transaction_coordinates' });
  if (safeCoordinates) {
    const registryReason = registryQuarantineReason(observedSlot, registryEntries);
    if (registryReason) quarantines.push({ reason: registryReason });
  }
  if (source.schemaVersion !== 'PUMP_V2_BRONZE_TRANSACTION_1') quarantines.push({ reason: 'invalid_bronze_schema' });
  const boundedBronzeWork = boundedNormalizedBronzeWork(bronze);
  if (!boundedBronzeWork) quarantines.push({ reason: 'invalid_bronze_instruction_structure' });
  if (!boundedBronzeWork || !revalidateNormalizedBronze(bronze)) quarantines.push({ reason: 'invalid_bronze_source' });

  const rawInstructions = boundedBronzeWork && Array.isArray(bronze?.instructions) ? bronze.instructions : [];
  let denseInstructionVector = true;
  for (let index = 0; index < rawInstructions.length; index += 1) {
    if (!Object.hasOwn(rawInstructions, index)) {
      denseInstructionVector = false;
      break;
    }
  }
  if (!denseInstructionVector) quarantines.push({ reason: 'invalid_bronze_instruction_structure' });
  const instructions = denseInstructionVector ? rawInstructions : [];
  const coordinateCounts = new Map<string, number>();
  let allInstructionsValid = denseInstructionVector;
  for (const instruction of instructions) {
    const reason = validateInstruction(instruction, bronze);
    if (reason) {
      quarantines.push({ reason });
      allInstructionsValid = false;
      continue;
    }
    const key = coordinateKey(instruction);
    coordinateCounts.set(key, (coordinateCounts.get(key) ?? 0) + 1);
  }
  const validatedInstructions = allInstructionsValid ? instructions : [];
  if (allInstructionsValid && !validInstructionOrder(validatedInstructions)) {
    quarantines.push({ reason: 'invalid_bronze_instruction_order' });
  }
  const duplicateKeys = new Set([...coordinateCounts].filter(([, count]) => count > 1).map(([key]) => key));
  for (const key of duplicateKeys) {
    const duplicate = validatedInstructions.find((instruction) => coordinateKey(instruction) === key);
    quarantines.push({ reason: 'duplicate_instruction_coordinate', ...(duplicate ? { coordinate: coordinate(duplicate) } : {}) });
  }

  const pumpNonEvents = validatedInstructions.filter((instruction) => instruction.programId === PUMP_PROGRAM_ID
    && !instruction.dataHex.startsWith(PUMP_EVENT_CPI_TAG));
  const parents = pumpNonEvents.filter((instruction) => DISCRIMINATOR_TO_VARIANT.has(instruction.dataHex.slice(0, 16)));
  for (const instruction of pumpNonEvents) {
    const discriminatorHex = instruction.dataHex.slice(0, 16);
    if (!DISCRIMINATOR_TO_VARIANT.has(discriminatorHex)) {
      quarantines.push({ reason: 'unknown_parent_discriminator', coordinate: coordinate(instruction), discriminatorHex });
    }
  }
  const eventInstructions = validatedInstructions.filter((instruction) => instruction.programId === PUMP_PROGRAM_ID
    && instruction.dataHex.startsWith(PUMP_EVENT_CPI_TAG));
  for (const eventInstruction of eventInstructions) {
    if (eventInstruction.instructionLocation !== 'inner') {
      quarantines.push({ reason: 'event_must_be_inner', coordinate: coordinate(eventInstruction) });
    }
  }
  for (const parent of parents) {
    const hasPairedEvent = eventInstructions.some((eventInstruction) => pairParent(
      eventInstruction, [parent], validatedInstructions,
    ) === parent);
    if (!hasPairedEvent) quarantines.push({ reason: 'missing_event_cpi', coordinate: coordinate(parent) });
  }

  if (safeCoordinates && quarantines.length === 0) {
    for (const eventInstruction of eventInstructions) {
      if (eventInstruction.instructionLocation !== 'inner') {
        quarantines.push({ reason: 'event_must_be_inner', coordinate: coordinate(eventInstruction) });
        continue;
      }
      if (duplicateKeys.has(coordinateKey(eventInstruction))) continue;
      const parent = pairParent(eventInstruction, parents, validatedInstructions);
      if (!parent) {
        quarantines.push({ reason: 'unpaired_event_cpi', coordinate: coordinate(eventInstruction) });
        continue;
      }
      let event: PumpSilverEvent;
      try {
        event = decodePumpSilverEventData(eventInstruction.dataHex);
      } catch (error) {
        quarantines.push({
          reason: error instanceof Error ? error.message : 'invalid_event_data',
          coordinate: coordinate(eventInstruction),
        });
        continue;
      }
      const variant = DISCRIMINATOR_TO_VARIANT.get(parent.dataHex.slice(0, 16))!;
      const outcome = parentBinding(bronze, parent, event, variant);
      if (!outcome.event) {
        quarantines.push({ reason: outcome.reason ?? 'invalid_event_binding', coordinate: coordinate(parent) });
        continue;
      }
      if (eventInstructions.length !== 1 && event.eventType === 'trade') {
        quarantines.push({ reason: 'ambiguous_token_balance_attribution', coordinate: coordinate(parent) });
        continue;
      }
      if (eventInstruction.accounts.length !== 1
        || eventInstruction.accounts[0] !== outcome.event.roles.event_authority) {
        quarantines.push({ reason: 'event_cpi_account_mismatch', coordinate: coordinate(eventInstruction) });
        continue;
      }
      outcome.event.eventCoordinate = coordinate(eventInstruction);
      if (bronze.executionStatus !== 'succeeded') {
        outcome.event.isExecutedTrade = false;
        quarantines.push({ reason: 'failed_transaction', coordinate: coordinate(parent) });
      }
      events.push(outcome.event);
    }
  }

  const unsigned = {
    schemaVersion: 'PUMP_SILVER_TRANSACTION_CONTRACT_1' as const,
    registry: PUMP_SILVER_FIXTURE_REGISTRY,
    source,
    researchReady: false as const,
    isExecutedTrade: events.length === 1 && events[0]!.isExecutedTrade && quarantines.length === 0,
    events,
    quarantines,
  };
  return { ...unsigned, canonicalHash: canonicalPumpSilverHash(unsigned) };
}
