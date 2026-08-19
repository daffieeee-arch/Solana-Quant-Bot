import bs58 from 'bs58';
import { PUMP_PROGRAM_ID, WSOL, base58Encode, deriveBondingCurve } from '../../../src/pump-address.js';
import { findProgramAddress, decodePublicKey, encodePublicKey } from '../../../src/solana-pda.js';
import {
  capturePumpV2BronzeTransaction,
  type PumpV2BronzeTransaction,
  type PumpV2LocatedInstruction,
  type PumpV2TokenBalance,
} from '../../../src/research/pump-v2-bronze.js';
import { PUMP_CREATE_EVENT_DISCRIMINATOR, PUMP_EVENT_CPI_TAG, PUMP_TRADE_EVENT_DISCRIMINATOR } from '../../../src/research/pump-silver-event.js';

export type SupportedFixtureVariant =
  | 'create' | 'create_v2'
  | 'buy' | 'sell' | 'buy_v2' | 'sell_v2'
  | 'buy_exact_sol_in' | 'buy_exact_quote_in_v2';

export const DISCRIMINATORS: Record<SupportedFixtureVariant, string> = {
  create: '181ec828051c0777',
  create_v2: 'd6904cec5f8b31b4',
  buy: '66063d1201daebea',
  sell: '33e685a4017f83ad',
  buy_v2: 'b817ee6167c5d33d',
  sell_v2: '5df6823ce7e940b2',
  buy_exact_sol_in: '38fc74089edfcd5f',
  buy_exact_quote_in_v2: 'c2ab1c46684d5b2f',
};

export const TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
export const TOKEN_2022_PROGRAM = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';
export const ASSOCIATED_TOKEN_PROGRAM = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL';
export const SYSTEM_PROGRAM = '11111111111111111111111111111111';
export const RENT = 'SysvarRent111111111111111111111111111111111';
export const FEE_PROGRAM = 'pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ';
export const METADATA_PROGRAM = 'metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s';
export const MAYHEM_PROGRAM = 'MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e';

export const key = (seed: number): string => base58Encode(Buffer.alloc(32, seed));
export const user = key(32);
export const feeRecipient = key(33);
export const creator = key(34);
export const mint = key(31);
export const curve = deriveBondingCurve(mint);

function programPda(programId: string, seeds: Buffer[]): string {
  const derived = findProgramAddress(seeds, decodePublicKey(programId));
  if (!derived) throw new Error('fixture PDA derivation failed');
  return encodePublicKey(derived.address);
}

function pda(seed: string, address?: string): string {
  return programPda(PUMP_PROGRAM_ID, [Buffer.from(seed, 'utf8'), ...(address ? [decodePublicKey(address)] : [])]);
}

function ata(owner: string, tokenProgram: string, tokenMint: string): string {
  return programPda(ASSOCIATED_TOKEN_PROGRAM, [
    decodePublicKey(owner), decodePublicKey(tokenProgram), decodePublicKey(tokenMint),
  ]);
}

export const creatorVault = pda('creator-vault', creator);
export const eventAuthority = pda('__event_authority');
export const global = pda('global');
export const mintAuthority = pda('mint-authority');
export const associatedCurve = ata(curve, TOKEN_PROGRAM, mint);
export const associatedUser = ata(user, TOKEN_PROGRAM, mint);
export const globalVolumeAccumulator = pda('global_volume_accumulator');
export const userVolumeAccumulator = pda('user_volume_accumulator', user);
export const feeConfig = programPda(FEE_PROGRAM, [Buffer.from('fee_config'), decodePublicKey(PUMP_PROGRAM_ID)]);
export const metadata = programPda(METADATA_PROGRAM, [
  Buffer.from('metadata'), decodePublicKey(METADATA_PROGRAM), decodePublicKey(mint),
]);
export const mayhemGlobalParams = programPda(MAYHEM_PROGRAM, [Buffer.from('global-params')]);
export const mayhemSolVault = programPda(MAYHEM_PROGRAM, [Buffer.from('sol-vault')]);
export const mayhemState = programPda(MAYHEM_PROGRAM, [Buffer.from('mayhem-state'), decodePublicKey(mint)]);

function u16(value: number): Buffer { const b = Buffer.alloc(2); b.writeUInt16LE(value); return b; }
function u32(value: number): Buffer { const b = Buffer.alloc(4); b.writeUInt32LE(value); return b; }
function u64(value: bigint): Buffer { const b = Buffer.alloc(8); b.writeBigUInt64LE(value); return b; }
function i64(value: bigint): Buffer { const b = Buffer.alloc(8); b.writeBigInt64LE(value); return b; }
function bool(value: boolean): Buffer { return Buffer.from([value ? 1 : 0]); }
function pubkey(value: string): Buffer { return Buffer.from(bs58.decode(value)); }
function text(value: string): Buffer { const b = Buffer.from(value, 'utf8'); return Buffer.concat([u32(b.length), b]); }

export function tradeEventHex(variant: SupportedFixtureVariant, overrides: Record<string, unknown> = {}): string {
  const isBuy = variant !== 'sell' && variant !== 'sell_v2';
  const solAmount = BigInt(String(overrides.solAmount ?? '1000000'));
  const tokenAmount = BigInt(String(overrides.tokenAmount ?? '2000000'));
  const realSolReserves = BigInt(String(overrides.realSolReserves ?? (isBuy ? '1001000000' : '999000000')));
  const realTokenReserves = BigInt(String(overrides.realTokenReserves ?? (isBuy ? '98000000' : '102000000')));
  const feeBps = BigInt(String(overrides.feeBasisPoints ?? '100'));
  const creatorFeeBps = BigInt(String(overrides.creatorFeeBasisPoints ?? '50'));
  const cashbackBps = BigInt(String(overrides.cashbackFeeBasisPoints ?? '0'));
  const buybackBps = BigInt(String(overrides.buybackFeeBasisPoints ?? '0'));
  const ceilFee = (bps: bigint) => (solAmount * bps + 9_999n) / 10_000n;
  const shareholders = (overrides.shareholders as Array<{ address: string; shareBps: number }> | undefined)
    ?? [{ address: creator, shareBps: 10_000 }];
  return Buffer.concat([
    Buffer.from(PUMP_EVENT_CPI_TAG, 'hex'),
    Buffer.from(String(overrides.eventDiscriminator ?? PUMP_TRADE_EVENT_DISCRIMINATOR), 'hex'),
    pubkey(String(overrides.mint ?? mint)),
    u64(solAmount), u64(tokenAmount), bool(Boolean(overrides.isBuy ?? isBuy)),
    pubkey(String(overrides.user ?? user)), i64(1_725_000_000n),
    u64(BigInt(String(overrides.virtualSolReserves ?? '31000000000'))),
    u64(BigInt(String(overrides.virtualTokenReserves ?? '900000000000000'))),
    u64(realSolReserves),
    u64(realTokenReserves),
    pubkey(String(overrides.feeRecipient ?? feeRecipient)),
    u64(feeBps), u64(BigInt(String(overrides.fee ?? ceilFee(feeBps)))),
    pubkey(String(overrides.creator ?? creator)),
    u64(creatorFeeBps), u64(BigInt(String(overrides.creatorFee ?? ceilFee(creatorFeeBps)))),
    bool(true), u64(9n), u64(7n), u64(solAmount), i64(1_725_000_001n),
    text(String(overrides.ixName ?? variant)), bool(false),
    u64(cashbackBps), u64(BigInt(String(overrides.cashback ?? ceilFee(cashbackBps)))),
    u64(buybackBps), u64(BigInt(String(overrides.buybackFee ?? ceilFee(buybackBps)))),
    u32(shareholders.length),
    ...shareholders.flatMap((shareholder) => [pubkey(shareholder.address), u16(shareholder.shareBps)]),
    pubkey(String(overrides.quoteMint ?? WSOL)), u64(BigInt(String(overrides.quoteAmount ?? solAmount))),
    u64(BigInt(String(overrides.virtualQuoteReserves ?? overrides.virtualSolReserves ?? '31000000000'))),
    u64(BigInt(String(overrides.realQuoteReserves ?? realSolReserves))),
  ]).toString('hex');
}

export function createEventHex(overrides: Record<string, unknown> = {}): string {
  return Buffer.concat([
    Buffer.from(PUMP_EVENT_CPI_TAG, 'hex'), Buffer.from(PUMP_CREATE_EVENT_DISCRIMINATOR, 'hex'),
    text(String(overrides.name ?? 'Fixture Coin')), text(String(overrides.symbol ?? 'FIX')),
    text(String(overrides.uri ?? 'https://example.invalid/fixture.json')),
    pubkey(String(overrides.mint ?? mint)), pubkey(String(overrides.bondingCurve ?? curve)),
    pubkey(String(overrides.user ?? user)), pubkey(String(overrides.creator ?? creator)),
    i64(1_725_000_000n),
    u64(BigInt(String(overrides.virtualTokenReserves ?? '1073000000000000'))),
    u64(BigInt(String(overrides.virtualSolReserves ?? '30000000000'))),
    u64(BigInt(String(overrides.realTokenReserves ?? '793100000000000'))),
    u64(BigInt(String(overrides.tokenTotalSupply ?? '1000000000000000'))),
    pubkey(String(overrides.tokenProgram ?? TOKEN_PROGRAM)), bool(Boolean(overrides.isMayhemMode ?? false)),
    bool(Boolean(overrides.isCashbackEnabled ?? false)), pubkey(WSOL), u64(30_000_000_000n),
  ]).toString('hex');
}

function createInstructionHex(variant: 'create' | 'create_v2'): string {
  return Buffer.concat([
    Buffer.from(DISCRIMINATORS[variant], 'hex'), text('Fixture Coin'), text('FIX'),
    text('https://example.invalid/fixture.json'), pubkey(creator),
    ...(variant === 'create_v2' ? [bool(false), Buffer.from([0])] : []),
  ]).toString('hex');
}

function tradeAccounts(variant: Exclude<SupportedFixtureVariant, 'create' | 'create_v2'>): string[] {
  if (variant === 'buy_v2' || variant === 'sell_v2' || variant === 'buy_exact_quote_in_v2') {
    const buybackFeeRecipient = key(42);
    const quoteFeeAta = ata(feeRecipient, TOKEN_PROGRAM, WSOL);
    const quoteBuybackAta = ata(buybackFeeRecipient, TOKEN_PROGRAM, WSOL);
    const quoteCurveAta = ata(curve, TOKEN_PROGRAM, WSOL);
    const quoteUserAta = ata(user, TOKEN_PROGRAM, WSOL);
    const associatedCreatorVault = ata(creatorVault, TOKEN_PROGRAM, WSOL);
    const sharingConfig = programPda(FEE_PROGRAM, [Buffer.from('sharing-config'), decodePublicKey(mint)]);
    const associatedUserVolume = ata(userVolumeAccumulator, TOKEN_PROGRAM, WSOL);
    const base = [
      global, mint, WSOL, TOKEN_PROGRAM, TOKEN_PROGRAM, ASSOCIATED_TOKEN_PROGRAM,
      feeRecipient, quoteFeeAta, buybackFeeRecipient, quoteBuybackAta, curve, associatedCurve, quoteCurveAta, user,
      associatedUser, quoteUserAta, creatorVault, associatedCreatorVault, sharingConfig, globalVolumeAccumulator,
      userVolumeAccumulator, associatedUserVolume, feeConfig, FEE_PROGRAM, SYSTEM_PROGRAM, eventAuthority, PUMP_PROGRAM_ID,
    ];
    if (variant === 'sell_v2') base.splice(19, 1);
    return base;
  }
  const buyLike = [global, feeRecipient, mint, curve, associatedCurve, associatedUser, user,
    SYSTEM_PROGRAM, TOKEN_PROGRAM, creatorVault, eventAuthority, PUMP_PROGRAM_ID,
    globalVolumeAccumulator, userVolumeAccumulator, feeConfig, FEE_PROGRAM];
  if (variant === 'sell') {
    return [global, feeRecipient, mint, curve, associatedCurve, associatedUser, user,
      SYSTEM_PROGRAM, creatorVault, TOKEN_PROGRAM, eventAuthority, PUMP_PROGRAM_ID, feeConfig, FEE_PROGRAM];
  }
  return buyLike;
}

function createAccounts(variant: 'create' | 'create_v2'): string[] {
  if (variant === 'create') return [mint, mintAuthority, curve, associatedCurve, global, METADATA_PROGRAM, metadata, user,
    SYSTEM_PROGRAM, TOKEN_PROGRAM, ASSOCIATED_TOKEN_PROGRAM, RENT, eventAuthority, PUMP_PROGRAM_ID];
  const token2022Curve = ata(curve, TOKEN_2022_PROGRAM, mint);
  return [mint, mintAuthority, curve, token2022Curve, global, user, SYSTEM_PROGRAM, TOKEN_2022_PROGRAM,
    ASSOCIATED_TOKEN_PROGRAM, MAYHEM_PROGRAM, mayhemGlobalParams, mayhemSolVault, mayhemState, key(67),
    eventAuthority, PUMP_PROGRAM_ID];
}

export function bronzeFixture(
  variant: SupportedFixtureVariant = 'buy',
  options: { innerParent?: boolean; failed?: boolean; eventOverrides?: Record<string, unknown> } = {},
): PumpV2BronzeTransaction {
  const isCreate = variant === 'create' || variant === 'create_v2';
  const accounts = isCreate ? createAccounts(variant) : tradeAccounts(variant);
  const allKeys = Array.from(new Set([key(1), ...accounts]));
  const index = (address: string) => allKeys.indexOf(address);
  const parent: PumpV2LocatedInstruction = {
    instructionLocation: options.innerParent ? 'inner' : 'top_level',
    ...(options.innerParent ? { parentInstructionIndex: 0, stackHeight: 2 } : {}),
    instructionIndex: 0,
    programIdIndex: index(PUMP_PROGRAM_ID), programId: PUMP_PROGRAM_ID,
    accountIndices: accounts.map(index), accounts,
    dataHex: isCreate
      ? createInstructionHex(variant)
      : `${DISCRIMINATORS[variant]}${'00'.repeat(16)}${variant === 'buy' || variant === 'buy_exact_sol_in' ? '00' : ''}`,
  };
  const event: PumpV2LocatedInstruction = {
    instructionLocation: 'inner', parentInstructionIndex: options.innerParent ? 0 : 0,
    instructionIndex: options.innerParent ? 1 : 0, stackHeight: options.innerParent ? 3 : 2,
    programIdIndex: index(PUMP_PROGRAM_ID), programId: PUMP_PROGRAM_ID,
    accountIndices: [index(eventAuthority)], accounts: [eventAuthority],
    dataHex: isCreate
      ? createEventHex({
        ...options.eventOverrides,
        tokenProgram: options.eventOverrides?.tokenProgram ?? (variant === 'create_v2' ? TOKEN_2022_PROGRAM : TOKEN_PROGRAM),
      })
      : tradeEventHex(variant, options.eventOverrides),
  };
  const instructions: PumpV2LocatedInstruction[] = options.innerParent
    ? [{ instructionLocation: 'top_level', instructionIndex: 0, programIdIndex: 0, programId: allKeys[0]!, accountIndices: [], accounts: [], dataHex: '' }, parent, event]
    : [parent, event];
  const preTokenBalances: PumpV2TokenBalance[] = [];
  const postTokenBalances: PumpV2TokenBalance[] = [];
  const preBalancesLamports = allKeys.map(() => '1000000000');
  const postBalancesLamports = [...preBalancesLamports];
  postBalancesLamports[0] = '999995000';
  if (!isCreate) {
    const tokenAmount = BigInt(String(options.eventOverrides?.tokenAmount ?? '2000000'));
    const solAmount = BigInt(String(options.eventOverrides?.solAmount ?? '1000000'));
    const feeBps = BigInt(String(options.eventOverrides?.feeBasisPoints ?? '100'));
    const creatorFeeBps = BigInt(String(options.eventOverrides?.creatorFeeBasisPoints ?? '50'));
    const buybackFeeBps = BigInt(String(options.eventOverrides?.buybackFeeBasisPoints ?? '0'));
    const cashbackFeeBps = BigInt(String(options.eventOverrides?.cashbackFeeBasisPoints ?? '0'));
    const ceilFee = (bps: bigint) => (solAmount * bps + 9_999n) / 10_000n;
    const protocolFee = BigInt(String(options.eventOverrides?.fee ?? ceilFee(feeBps)));
    const creatorFee = BigInt(String(options.eventOverrides?.creatorFee ?? ceilFee(creatorFeeBps)));
    const buybackFee = BigInt(String(options.eventOverrides?.buybackFee ?? ceilFee(buybackFeeBps)));
    const cashback = BigInt(String(options.eventOverrides?.cashback ?? ceilFee(cashbackFeeBps)));
    const buy = variant !== 'sell' && variant !== 'sell_v2';
    const userIndex = index(associatedUser);
    const curveIndex = index(associatedCurve);
    const beforeUser = 10_000_000n;
    const beforeCurve = 100_000_000n;
    const balance = (accountIndex: number, owner: string, amount: bigint): PumpV2TokenBalance => ({
      accountIndex, mint, owner, programId: TOKEN_PROGRAM, decimals: 6, amount: amount.toString(),
    });
    preTokenBalances.push(balance(userIndex, user, beforeUser), balance(curveIndex, curve, beforeCurve));
    postTokenBalances.push(
      balance(userIndex, user, options.failed ? beforeUser : beforeUser + (buy ? tokenAmount : -tokenAmount)),
      balance(curveIndex, curve, options.failed ? beforeCurve : beforeCurve + (buy ? -tokenAmount : tokenAmount)),
    );
    if (variant.endsWith('_v2')) {
      const roles = tradeAccounts(variant);
      const quoteUserAddress = roles[15]!;
      const quoteCurveAddress = roles[12]!;
      const quoteFeeAddress = roles[7]!;
      const quoteBuybackAddress = roles[9]!;
      const associatedCreatorAddress = roles[17]!;
      const quoteBalance = (address: string, owner: string, amount: bigint): PumpV2TokenBalance => ({
        accountIndex: index(address), mint: WSOL, owner, programId: TOKEN_PROGRAM, decimals: 9, amount: amount.toString(),
      });
      const beforeQuoteUser = 2_000_000_000n;
      const beforeQuoteCurve = 1_000_000_000n;
      const attemptedUser = beforeQuoteUser + (buy
        ? -(solAmount + protocolFee + creatorFee + buybackFee - cashback)
        : solAmount - protocolFee - creatorFee - buybackFee + cashback);
      preTokenBalances.push(
        quoteBalance(quoteUserAddress, user, beforeQuoteUser),
        quoteBalance(quoteCurveAddress, curve, beforeQuoteCurve),
        quoteBalance(quoteFeeAddress, feeRecipient, 0n),
        quoteBalance(quoteBuybackAddress, key(42), 0n),
        quoteBalance(associatedCreatorAddress, creatorVault, 0n),
      );
      postTokenBalances.push(
        quoteBalance(quoteUserAddress, user, options.failed ? beforeQuoteUser : attemptedUser),
        quoteBalance(quoteCurveAddress, curve, options.failed ? beforeQuoteCurve : beforeQuoteCurve + (buy ? solAmount : -solAmount)),
        quoteBalance(quoteFeeAddress, feeRecipient, options.failed ? 0n : protocolFee),
        quoteBalance(quoteBuybackAddress, key(42), options.failed ? 0n : buybackFee),
        quoteBalance(associatedCreatorAddress, creatorVault, options.failed ? 0n : creatorFee),
      );
    } else if (!options.failed) {
      const delta = (address: string, amount: bigint) => {
        const accountIndex = index(address);
        postBalancesLamports[accountIndex] = (BigInt(preBalancesLamports[accountIndex]!) + amount).toString();
      };
      delta(curve, buy ? solAmount : -solAmount);
      delta(feeRecipient, protocolFee);
      delta(creatorVault, creatorFee);
      delta(user, buy
        ? -(solAmount + protocolFee + creatorFee - cashback)
        : solAmount - protocolFee - creatorFee + cashback);
    }
  }
  return {
    schemaVersion: 'PUMP_V2_BRONZE_TRANSACTION_1', slot: 361_000_001, transactionIndex: 7,
    signature: bs58.encode(Buffer.alloc(64, 7)), blockTime: '2026-08-17T00:00:00.000Z',
    executionStatus: options.failed ? 'failed' : 'succeeded', feeLamports: '5000', logMessages: [],
    accountKeys: allKeys, preBalancesLamports,
    postBalancesLamports, instructions,
    pumpCandidates: [], quarantines: [],
    preTokenBalances: preTokenBalances.sort((left, right) => left.accountIndex - right.accountIndex),
    postTokenBalances: postTokenBalances.sort((left, right) => left.accountIndex - right.accountIndex),
  };
}

/** Builds the same deterministic buy through the real Bronze v0 loaded-address resolution boundary. */
export function bronzeV0LoadedFixture(): PumpV2BronzeTransaction {
  const normalized = bronzeFixture('buy');
  const staticAccountKeys = [key(1), PUMP_PROGRAM_ID];
  const loadedKeys = normalized.accountKeys.filter((address) => !staticAccountKeys.includes(address));
  const resolved = [...staticAccountKeys, ...loadedKeys];
  const resolvedIndex = (address: string) => resolved.indexOf(address);
  const parent = normalized.instructions[0]!;
  const event = normalized.instructions[1]!;
  const remapInstruction = (instruction: PumpV2LocatedInstruction) => ({
    programIdIndex: resolvedIndex(instruction.programId),
    accountIndices: instruction.accounts.map(resolvedIndex),
    dataHex: instruction.dataHex,
    ...(instruction.stackHeight === undefined ? {} : { stackHeight: instruction.stackHeight }),
  });
  const remapBalance = (balance: PumpV2TokenBalance) => ({
    ...balance,
    accountIndex: resolvedIndex(normalized.accountKeys[balance.accountIndex]!),
  });
  return capturePumpV2BronzeTransaction({
    slot: normalized.slot,
    transactionIndex: normalized.transactionIndex,
    signature: normalized.signature,
    blockTime: normalized.blockTime,
    err: null,
    feeLamports: normalized.feeLamports,
    logMessages: normalized.logMessages,
    staticAccountKeys,
    loadedAddresses: { writable: loadedKeys, readonly: [] },
    preBalancesLamports: resolved.map((address) => normalized.preBalancesLamports[normalized.accountKeys.indexOf(address)]!),
    postBalancesLamports: resolved.map((address) => normalized.postBalancesLamports[normalized.accountKeys.indexOf(address)]!),
    topLevelInstructions: [remapInstruction(parent)],
    innerInstructionGroups: [{ parentInstructionIndex: 0, instructions: [remapInstruction(event)] }],
    preTokenBalances: normalized.preTokenBalances.map(remapBalance),
    postTokenBalances: normalized.postTokenBalances.map(remapBalance),
  });
}
