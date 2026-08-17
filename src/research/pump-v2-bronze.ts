import bs58 from 'bs58';
import { PUMP_PROGRAM_ID, deriveBondingCurve } from '../pump-address.js';
import { PUMP_DISCRIMINATORS } from '../pump-parser.js';

export type PumpV2BronzeSource = {
  slot: number;
  transactionIndex: number;
  signature: string;
  blockTime: string;
  err: unknown;
  feeLamports: string;
  logMessages: string[];
  staticAccountKeys: string[];
  loadedAddresses: { writable: string[]; readonly: string[] };
  preBalancesLamports: string[];
  postBalancesLamports: string[];
  topLevelInstructions: unknown[];
  innerInstructionGroups: unknown[];
  preTokenBalances: unknown[];
  postTokenBalances: unknown[];
};

export type PumpV2LocatedInstruction = {
  instructionLocation: 'top_level' | 'inner';
  parentInstructionIndex?: number;
  instructionIndex: number;
  stackHeight?: number;
  programIdIndex: number;
  programId: string;
  accountIndices: number[];
  accounts: string[];
  dataHex: string;
};

export type PumpV2Candidate = {
  eventKey: string;
  instructionLocation: 'top_level' | 'inner';
  parentInstructionIndex?: number;
  instructionIndex: number;
  discriminatorHex: string;
  discriminatorSource: 'official_idl' | 'observed_runtime' | null;
  parserStatus: 'known_discriminator' | 'quarantined';
  variant: string | null;
  kind: 'buy' | 'sell' | null;
  mint: string | null;
  curve: string | null;
  executionStatus: 'succeeded' | 'failed';
};

export type PumpV2Quarantine = {
  eventKey: string;
  reason: 'unknown_pump_discriminator' | 'unresolved_pump_identity';
  discriminatorHex: string;
};

export type PumpV2TokenBalance = {
  accountIndex: number;
  mint: string;
  owner: string;
  programId: string;
  decimals: number;
  amount: string;
};

export type PumpV2BronzeTransaction = {
  schemaVersion: 'PUMP_V2_BRONZE_TRANSACTION_1';
  slot: number;
  transactionIndex: number;
  signature: string;
  blockTime: string;
  executionStatus: 'succeeded' | 'failed';
  feeLamports: string;
  logMessages: string[];
  accountKeys: string[];
  preBalancesLamports: string[];
  postBalancesLamports: string[];
  instructions: PumpV2LocatedInstruction[];
  pumpCandidates: PumpV2Candidate[];
  quarantines: PumpV2Quarantine[];
  preTokenBalances: PumpV2TokenBalance[];
  postTokenBalances: PumpV2TokenBalance[];
};

const CANONICAL_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const UNSIGNED_INTEGER = /^(0|[1-9]\d*)$/;
const CANONICAL_HEX = /^(?:[0-9a-f]{2})*$/;
const BASE58_TEXT = /^[1-9A-HJ-NP-Za-km-z]+$/;
const MAX_RESOLVED_ACCOUNT_KEYS = 256;
const MAX_SIGNATURE_TEXT = 88;
const MIN_SIGNATURE_TEXT = 64;
const MAX_PUBLIC_KEY_TEXT = 44;
const MIN_PUBLIC_KEY_TEXT = 32;
const MAX_INSTRUCTION_DATA_BYTES = 1_232;
const MAX_TOP_LEVEL_INSTRUCTIONS = 256;
const MAX_INNER_INSTRUCTIONS = 4_096;
const MAX_INSTRUCTION_ACCOUNT_REFERENCES = 4_096;
const MAX_SOLANA_INSTRUCTION_STACK_HEIGHT = 9;
const MAX_LOG_MESSAGES = 1_024;
const MAX_LOG_BYTES = 10_000;
const KNOWN_TRADES = new Map<string, {
  variant: string;
  kind: 'buy' | 'sell';
  discriminatorSource: 'official_idl' | 'observed_runtime';
}>([
  [PUMP_DISCRIMINATORS.buy, { variant: 'buy', kind: 'buy', discriminatorSource: 'official_idl' }],
  [PUMP_DISCRIMINATORS.sell, { variant: 'sell', kind: 'sell', discriminatorSource: 'official_idl' }],
  [PUMP_DISCRIMINATORS.buyV2, { variant: 'buy_v2', kind: 'buy', discriminatorSource: 'official_idl' }],
  [PUMP_DISCRIMINATORS.sellV2, { variant: 'sell_v2', kind: 'sell', discriminatorSource: 'official_idl' }],
  [PUMP_DISCRIMINATORS.buyExactQuoteInV2, { variant: 'buy_exact_quote_in_v2', kind: 'buy', discriminatorSource: 'official_idl' }],
  [PUMP_DISCRIMINATORS.buyExactSolIn, { variant: 'buy_exact_sol_in', kind: 'buy', discriminatorSource: 'official_idl' }],
  [PUMP_DISCRIMINATORS.liveBuy, { variant: 'live_buy_dispatcher', kind: 'buy', discriminatorSource: 'observed_runtime' }],
  [PUMP_DISCRIMINATORS.liveBuyV2, { variant: 'live_buy_v2_dispatcher', kind: 'buy', discriminatorSource: 'observed_runtime' }],
  [PUMP_DISCRIMINATORS.liveBuyExactSolIn, { variant: 'live_buy_exact_sol_in_dispatcher', kind: 'buy', discriminatorSource: 'observed_runtime' }],
  [PUMP_DISCRIMINATORS.liveSell, { variant: 'live_sell_dispatcher', kind: 'sell', discriminatorSource: 'observed_runtime' }],
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function denseArray(value: unknown, maxLength: number): value is unknown[] {
  if (!Array.isArray(value) || value.length > maxLength) return false;
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(value, index)) return false;
  }
  return true;
}

function stringArray(value: unknown, maxLength: number): string[] | undefined {
  return denseArray(value, maxLength)
    && value.every((entry) => typeof entry === 'string')
    ? value
    : undefined;
}

function validSignature(value: unknown): value is string {
  if (typeof value !== 'string'
    || value.length < MIN_SIGNATURE_TEXT
    || value.length > MAX_SIGNATURE_TEXT
    || !BASE58_TEXT.test(value)) return false;
  try {
    return bs58.decode(value).length === 64;
  } catch {
    return false;
  }
}

function canonicalUtc(value: unknown): value is string {
  if (typeof value !== 'string' || !CANONICAL_UTC.test(value)) return false;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString() === value;
}

function validExecutionError(value: unknown): boolean {
  return value === null
    || (typeof value === 'string' && value.length > 0)
    || isRecord(value);
}

function locatedInstruction(
  value: unknown,
  accountKeys: string[],
  staticAccountCount: number,
  location: 'top_level' | 'inner',
  instructionIndex: number,
  parentInstructionIndex?: number,
): PumpV2LocatedInstruction {
  if (!isRecord(value)
    || !Number.isSafeInteger(value.programIdIndex)
    || (value.programIdIndex as number) < 0
    || (value.programIdIndex as number) >= accountKeys.length
    || !denseArray(value.accountIndices, MAX_RESOLVED_ACCOUNT_KEYS)
    || !value.accountIndices.every((index) => Number.isSafeInteger(index)
      && (index as number) >= 0
      && (index as number) < accountKeys.length)
    || typeof value.dataHex !== 'string'
    || value.dataHex.length > MAX_INSTRUCTION_DATA_BYTES * 2
    || !CANONICAL_HEX.test(value.dataHex)) {
    throw new Error('invalid_instruction');
  }
  const stackHeight = value.stackHeight;
  if (stackHeight !== undefined
    && (typeof stackHeight !== 'number'
      || !Number.isSafeInteger(stackHeight)
      || (location === 'top_level' && stackHeight !== 1)
      || (location === 'inner'
        && (stackHeight < 2 || stackHeight > MAX_SOLANA_INSTRUCTION_STACK_HEIGHT)))) {
    throw new Error('invalid_stack_height');
  }
  const programIdIndex = value.programIdIndex as number;
  if (location === 'top_level' && programIdIndex >= staticAccountCount) {
    throw new Error('top_level_program_must_be_static');
  }
  const accountIndices = [...value.accountIndices] as number[];
  return {
    instructionLocation: location,
    ...(parentInstructionIndex === undefined ? {} : { parentInstructionIndex }),
    instructionIndex,
    ...(value.stackHeight === undefined ? {} : { stackHeight: value.stackHeight as number }),
    programIdIndex,
    programId: accountKeys[programIdIndex]!,
    accountIndices,
    accounts: accountIndices.map((index) => accountKeys[index]!),
    dataHex: value.dataHex,
  };
}

function locateInstructions(
  source: Record<string, unknown>,
  accountKeys: string[],
  staticAccountCount: number,
): PumpV2LocatedInstruction[] {
  const topLevel = source.topLevelInstructions as unknown[];
  const groups = source.innerInstructionGroups as unknown[];
  if (!denseArray(topLevel, MAX_TOP_LEVEL_INSTRUCTIONS)
    || !denseArray(groups, MAX_TOP_LEVEL_INSTRUCTIONS)) {
    throw new Error('too_many_instructions');
  }
  const byParent = new Map<number, unknown[]>();
  let innerInstructionCount = 0;
  for (const group of groups) {
    if (!isRecord(group)
      || !Number.isSafeInteger(group.parentInstructionIndex)
      || (group.parentInstructionIndex as number) < 0
      || (group.parentInstructionIndex as number) >= topLevel.length
      || !denseArray(group.instructions, MAX_INNER_INSTRUCTIONS)) {
      throw new Error('invalid_inner_instruction_group');
    }
    innerInstructionCount += group.instructions.length;
    if (innerInstructionCount > MAX_INNER_INSTRUCTIONS) throw new Error('too_many_instructions');
    const parent = group.parentInstructionIndex as number;
    if (byParent.has(parent)) throw new Error('duplicate_inner_instruction_group');
    byParent.set(parent, group.instructions);
  }

  const located: PumpV2LocatedInstruction[] = [];
  let accountReferenceCount = 0;
  for (let parent = 0; parent < topLevel.length; parent += 1) {
    const topLevelInstruction = locatedInstruction(
      topLevel[parent], accountKeys, staticAccountCount, 'top_level', parent,
    );
    accountReferenceCount += topLevelInstruction.accountIndices.length;
    if (accountReferenceCount > MAX_INSTRUCTION_ACCOUNT_REFERENCES) {
      throw new Error('too_many_instruction_account_references');
    }
    located.push(topLevelInstruction);
    for (const [instructionIndex, instruction] of (byParent.get(parent) ?? []).entries()) {
      const innerInstruction = locatedInstruction(
        instruction, accountKeys, staticAccountCount, 'inner', instructionIndex, parent,
      );
      accountReferenceCount += innerInstruction.accountIndices.length;
      if (accountReferenceCount > MAX_INSTRUCTION_ACCOUNT_REFERENCES) {
        throw new Error('too_many_instruction_account_references');
      }
      located.push(innerInstruction);
    }
  }
  return located;
}

function uniquePumpIdentity(
  accounts: string[],
  pdaCache: Map<string, string | null>,
): { mint: string; curve: string } | undefined {
  const accountSet = new Set(accounts);
  const pairs = new Map<string, { mint: string; curve: string }>();
  for (const mint of accountSet) {
    if (!pdaCache.has(mint)) pdaCache.set(mint, deriveBondingCurve(mint) ?? null);
    const curve = pdaCache.get(mint);
    if (curve && curve !== mint && accountSet.has(curve)) pairs.set(`${mint}:${curve}`, { mint, curve });
  }
  return pairs.size === 1 ? [...pairs.values()][0] : undefined;
}

function capturePumpCandidates(
  source: Record<string, unknown>,
  instructions: PumpV2LocatedInstruction[],
): { pumpCandidates: PumpV2Candidate[]; quarantines: PumpV2Quarantine[] } {
  const executionStatus = source.err === null ? 'succeeded' as const : 'failed' as const;
  const pumpCandidates: PumpV2Candidate[] = [];
  const quarantines: PumpV2Quarantine[] = [];
  const pdaCache = new Map<string, string | null>();
  for (const instruction of instructions) {
    if (instruction.programId !== PUMP_PROGRAM_ID) continue;
    const discriminatorHex = instruction.dataHex.slice(0, 16);
    const known = KNOWN_TRADES.get(discriminatorHex);
    const identity = uniquePumpIdentity(instruction.accounts, pdaCache);
    const eventKey = [
      source.slot,
      source.transactionIndex,
      source.signature,
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
      ...(instruction.parentInstructionIndex === undefined ? {} : { parentInstructionIndex: instruction.parentInstructionIndex }),
      instructionIndex: instruction.instructionIndex,
      discriminatorHex,
      discriminatorSource: known?.discriminatorSource ?? null,
      parserStatus: reason === undefined ? 'known_discriminator' : 'quarantined',
      variant: known?.variant ?? null,
      kind: known?.kind ?? null,
      mint: identity?.mint ?? null,
      curve: identity?.curve ?? null,
      executionStatus,
    });
    if (reason) quarantines.push({ eventKey, reason, discriminatorHex });
  }
  return { pumpCandidates, quarantines };
}

function validPublicKey(value: unknown): value is string {
  if (typeof value !== 'string'
    || value.length < MIN_PUBLIC_KEY_TEXT
    || value.length > MAX_PUBLIC_KEY_TEXT
    || !BASE58_TEXT.test(value)) return false;
  try {
    return bs58.decode(value).length === 32;
  } catch {
    return false;
  }
}

const MAX_U64 = (1n << 64n) - 1n;

function validU64(value: unknown): value is string {
  return typeof value === 'string'
    && value.length <= 20
    && UNSIGNED_INTEGER.test(value)
    && BigInt(value) <= MAX_U64;
}

function orderedTokenBalances(values: unknown[], accountCount: number): PumpV2TokenBalance[] {
  const seen = new Set<number>();
  const balances: PumpV2TokenBalance[] = [];
  for (const value of values) {
    if (!isRecord(value)
      || !Number.isSafeInteger(value.accountIndex)
      || (value.accountIndex as number) < 0
      || (value.accountIndex as number) >= accountCount
      || !validPublicKey(value.mint)
      || !validPublicKey(value.owner)
      || !validPublicKey(value.programId)
      || !Number.isSafeInteger(value.decimals)
      || (value.decimals as number) < 0
      || (value.decimals as number) > 255
      || !validU64(value.amount)) {
      throw new Error('invalid_token_balance');
    }
    const accountIndex = value.accountIndex as number;
    if (seen.has(accountIndex)) throw new Error('duplicate_token_balance_account_index');
    seen.add(accountIndex);
    balances.push({
      accountIndex,
      mint: value.mint,
      owner: value.owner,
      programId: value.programId,
      decimals: value.decimals as number,
      amount: value.amount,
    });
  }
  return balances.sort((left, right) => left.accountIndex - right.accountIndex);
}


export function capturePumpV2BronzeTransaction(source: unknown): PumpV2BronzeTransaction {
  if (!isRecord(source)) throw new Error('invalid_source_shape');
  if (!Number.isSafeInteger(source.slot) || (source.slot as number) < 0) throw new Error('invalid_slot');
  if (!Number.isSafeInteger(source.transactionIndex) || (source.transactionIndex as number) < 0) {
    throw new Error('invalid_transaction_index');
  }
  if (!validSignature(source.signature)) throw new Error('invalid_signature');
  if (!canonicalUtc(source.blockTime)) throw new Error('invalid_block_time');
  if (!Object.prototype.hasOwnProperty.call(source, 'err') || source.err === undefined) {
    throw new Error('missing_execution_state');
  }
  if (!validExecutionError(source.err)) throw new Error('invalid_execution_error');
  if (!validU64(source.feeLamports)) {
    throw new Error('invalid_fee_lamports');
  }
  const logMessages = stringArray(source.logMessages, MAX_LOG_MESSAGES);
  if (!logMessages || logMessages.some((message) => message.length > MAX_LOG_BYTES)) {
    throw new Error('invalid_log_messages');
  }
  let logBytes = 0;
  for (const message of logMessages) {
    logBytes += Buffer.byteLength(message, 'utf8');
    if (logBytes > MAX_LOG_BYTES) throw new Error('invalid_log_messages');
  }

  const staticAccountKeys = stringArray(source.staticAccountKeys, MAX_RESOLVED_ACCOUNT_KEYS);
  const loaded = isRecord(source.loadedAddresses) ? source.loadedAddresses : undefined;
  const loadedWritable = stringArray(loaded?.writable, MAX_RESOLVED_ACCOUNT_KEYS);
  const loadedReadonly = stringArray(loaded?.readonly, MAX_RESOLVED_ACCOUNT_KEYS);
  if (!staticAccountKeys || !loadedWritable || !loadedReadonly) {
    throw new Error('invalid_source_shape');
  }
  if (staticAccountKeys.length === 0) throw new Error('empty_static_account_keys');
  const accountKeys = [...staticAccountKeys, ...loadedWritable, ...loadedReadonly];
  if (accountKeys.length > MAX_RESOLVED_ACCOUNT_KEYS) throw new Error('too_many_account_keys');
  if (!accountKeys.every(validPublicKey)) throw new Error('invalid_account_key');
  if (new Set(accountKeys).size !== accountKeys.length) throw new Error('duplicate_account_key');
  const preBalancesLamports = stringArray(source.preBalancesLamports, MAX_RESOLVED_ACCOUNT_KEYS);
  const postBalancesLamports = stringArray(source.postBalancesLamports, MAX_RESOLVED_ACCOUNT_KEYS);
  if (!preBalancesLamports || !postBalancesLamports) throw new Error('invalid_source_shape');
  if (preBalancesLamports.length !== accountKeys.length || postBalancesLamports.length !== accountKeys.length) {
    throw new Error('native_balance_length_mismatch');
  }
  if (![...preBalancesLamports, ...postBalancesLamports].every(validU64)) {
    throw new Error('invalid_lamport_amount');
  }
  if (!Array.isArray(source.topLevelInstructions)
    || !Array.isArray(source.innerInstructionGroups)
    || !denseArray(source.preTokenBalances, MAX_RESOLVED_ACCOUNT_KEYS)
    || !denseArray(source.postTokenBalances, MAX_RESOLVED_ACCOUNT_KEYS)) {
    throw new Error('invalid_source_shape');
  }
  const instructions = locateInstructions(source, accountKeys, staticAccountKeys.length);
  const { pumpCandidates, quarantines } = capturePumpCandidates(source, instructions);
  const preTokenBalances = orderedTokenBalances(source.preTokenBalances, accountKeys.length);
  const postTokenBalances = orderedTokenBalances(source.postTokenBalances, accountKeys.length);

  return {
    schemaVersion: 'PUMP_V2_BRONZE_TRANSACTION_1',
    slot: source.slot as number,
    transactionIndex: source.transactionIndex as number,
    signature: source.signature,
    blockTime: source.blockTime,
    executionStatus: source.err === null ? 'succeeded' : 'failed',
    feeLamports: source.feeLamports,
    logMessages: [...logMessages],
    accountKeys,
    preBalancesLamports: [...preBalancesLamports],
    postBalancesLamports: [...postBalancesLamports],
    instructions,
    pumpCandidates,
    quarantines,
    preTokenBalances,
    postTokenBalances,
  };
}
