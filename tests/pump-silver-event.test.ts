import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import bs58 from 'bs58';
import {
  PUMP_CREATE_EVENT_DISCRIMINATOR,
  PUMP_TRADE_EVENT_DISCRIMINATOR,
  decodePumpSilverEventData,
} from '../src/research/pump-silver-event.js';
import { WSOL, base58Encode } from '../src/pump-address.js';

const EVENT_CPI_TAG = 'e445a52e51cb9a1d';
const mint = base58Encode(Buffer.alloc(32, 31));
const user = base58Encode(Buffer.alloc(32, 32));
const feeRecipient = base58Encode(Buffer.alloc(32, 33));
const creator = base58Encode(Buffer.alloc(32, 34));
const curve = base58Encode(Buffer.alloc(32, 35));
const tokenProgram = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const goldenVectors = JSON.parse(readFileSync(
  new URL('./fixtures/pump-silver/event-vectors.json', import.meta.url),
  'utf8',
)) as {
  vectors: Array<{ name: string; dataHex: string; expected: unknown }>;
};

function u16(value: number): Buffer {
  const bytes = Buffer.alloc(2);
  bytes.writeUInt16LE(value);
  return bytes;
}

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

function i64(value: bigint): Buffer {
  const bytes = Buffer.alloc(8);
  bytes.writeBigInt64LE(value);
  return bytes;
}

function bool(value: boolean): Buffer {
  return Buffer.from([value ? 1 : 0]);
}

function pubkey(value: string): Buffer {
  return Buffer.from(bs58.decode(value));
}

function text(value: string): Buffer {
  const bytes = Buffer.from(value, 'utf8');
  return Buffer.concat([u32(bytes.length), bytes]);
}

function tradeEventHex(overrides: Partial<{
  discriminator: string;
  isBuy: boolean;
  ixName: string;
  quoteAmount: bigint;
  virtualQuoteReserves: bigint;
  realQuoteReserves: bigint;
  fee: bigint;
  feeBasisPoints: bigint;
  creatorFee: bigint;
  creatorFeeBasisPoints: bigint;
}> = {}): string {
  const feeBasisPoints = overrides.feeBasisPoints ?? 100n;
  const creatorFeeBasisPoints = overrides.creatorFeeBasisPoints ?? 50n;
  const parts = [
    Buffer.from(EVENT_CPI_TAG, 'hex'),
    Buffer.from(overrides.discriminator ?? PUMP_TRADE_EVENT_DISCRIMINATOR, 'hex'),
    pubkey(mint),
    u64(1_000_000n),
    u64(2_000_000n),
    bool(overrides.isBuy ?? true),
    pubkey(user),
    i64(1_725_000_000n),
    u64(31_000_000_000n),
    u64(1_071_000_000_000_000n),
    u64(1_000_000_000n),
    u64(791_100_000_000_000n),
    pubkey(feeRecipient),
    u64(feeBasisPoints),
    u64(overrides.fee ?? 10_000n),
    pubkey(creator),
    u64(creatorFeeBasisPoints),
    u64(overrides.creatorFee ?? 5_000n),
    bool(true),
    u64(9n),
    u64(7n),
    u64(1_000_000n),
    i64(1_725_000_001n),
    text(overrides.ixName ?? 'buy'),
    bool(false),
    u64(0n),
    u64(0n),
    u64(0n),
    u64(0n),
    u32(1),
    pubkey(creator),
    u16(10_000),
    pubkey(WSOL),
    u64(overrides.quoteAmount ?? 1_000_000n),
    u64(overrides.virtualQuoteReserves ?? 31_000_000_000n),
    u64(overrides.realQuoteReserves ?? 1_000_000_000n),
  ];
  return Buffer.concat(parts).toString('hex');
}

function createEventHex(): string {
  return Buffer.concat([
    Buffer.from(EVENT_CPI_TAG, 'hex'),
    Buffer.from(PUMP_CREATE_EVENT_DISCRIMINATOR, 'hex'),
    text('Fixture Coin'),
    text('FIX'),
    text('https://example.invalid/fixture.json'),
    pubkey(mint),
    pubkey(curve),
    pubkey(user),
    pubkey(creator),
    i64(1_725_000_000n),
    u64(1_073_000_000_000_000n),
    u64(30_000_000_000n),
    u64(793_100_000_000_000n),
    u64(1_000_000_000_000_000n),
    pubkey(tokenProgram),
    bool(false),
    bool(false),
    pubkey(WSOL),
    u64(30_000_000_000n),
  ]).toString('hex');
}

describe('Pump Silver canonical event wire decoder', () => {
  it.each(goldenVectors.vectors)('matches the shared Rust/TypeScript vector $name', (vector) => {
    expect(decodePumpSilverEventData(vector.dataHex)).toEqual(vector.expected);
  });

  it('decodes the complete official TradeEvent schema without floating-point loss', () => {
    expect(decodePumpSilverEventData(tradeEventHex())).toEqual({
      eventType: 'trade',
      mint,
      solAmount: '1000000',
      tokenAmount: '2000000',
      isBuy: true,
      user,
      timestamp: '1725000000',
      virtualSolReserves: '31000000000',
      virtualTokenReserves: '1071000000000000',
      realSolReserves: '1000000000',
      realTokenReserves: '791100000000000',
      feeRecipient,
      feeBasisPoints: '100',
      fee: '10000',
      creator,
      creatorFeeBasisPoints: '50',
      creatorFee: '5000',
      trackVolume: true,
      totalUnclaimedTokens: '9',
      totalClaimedTokens: '7',
      currentSolVolume: '1000000',
      lastUpdateTimestamp: '1725000001',
      ixName: 'buy',
      mayhemMode: false,
      cashbackFeeBasisPoints: '0',
      cashback: '0',
      buybackFeeBasisPoints: '0',
      buybackFee: '0',
      shareholders: [{ address: creator, shareBps: 10_000 }],
      quoteMint: WSOL,
      quoteAmount: '1000000',
      virtualQuoteReserves: '31000000000',
      realQuoteReserves: '1000000000',
    });
  });

  it('decodes the complete official CreateEvent schema', () => {
    expect(decodePumpSilverEventData(createEventHex())).toEqual({
      eventType: 'create',
      name: 'Fixture Coin',
      symbol: 'FIX',
      uri: 'https://example.invalid/fixture.json',
      mint,
      bondingCurve: curve,
      user,
      creator,
      timestamp: '1725000000',
      virtualTokenReserves: '1073000000000000',
      virtualSolReserves: '30000000000',
      realTokenReserves: '793100000000000',
      tokenTotalSupply: '1000000000000000',
      tokenProgram,
      isMayhemMode: false,
      isCashbackEnabled: false,
      quoteMint: WSOL,
      virtualQuoteReserves: '30000000000',
    });
  });

  it.each([
    ['wrong CPI event tag', `00${tradeEventHex().slice(2)}`, /invalid_event_cpi_tag/],
    ['unknown event discriminator', tradeEventHex({ discriminator: 'deadbeefcafebabe' }), /unknown_event_discriminator/],
    ['invalid bool', tradeEventHex().replace(/01(?=[0-9a-f]+$)/, '02'), /invalid_event_payload|invalid_bool/],
    ['trailing bytes', `${tradeEventHex()}00`, /trailing_event_bytes/],
  ] as const)('rejects %s fail-closed', (_label, eventHex, error) => {
    expect(() => decodePumpSilverEventData(eventHex)).toThrow(error);
  });

  it('normalizes a truncated bounded text field to invalid_event_text', () => {
    const valid = goldenVectors.vectors[0]!.dataHex;
    const truncatedText = valid.replace('03000000627579', '00040000627579');
    expect(truncatedText).not.toBe(valid);
    expect(() => decodePumpSilverEventData(truncatedText)).toThrow('invalid_event_text');
  });

  it('checks duplicate shareholders before reading a truncated shareBps', () => {
    const valid = Buffer.from(tradeEventHex(), 'hex');
    const shareholderTailBytes = 4 + 32 + 2 + 32 + 8 + 8 + 8;
    const shareholderOffset = valid.length - shareholderTailBytes;
    const firstShareholder = valid.subarray(shareholderOffset + 4, shareholderOffset + 4 + 34);
    const duplicateAddress = firstShareholder.subarray(0, 32);
    const malformed = Buffer.concat([
      valid.subarray(0, shareholderOffset),
      u32(2),
      firstShareholder,
      duplicateAddress,
    ]).toString('hex');
    expect(() => decodePumpSilverEventData(malformed)).toThrow('duplicate_shareholder');
  });
});

export const pumpSilverEventTestVectors = {
  tradeEventHex,
  createEventHex,
};
