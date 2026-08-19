import { base58Encode } from '../pump-address.js';

export const PUMP_EVENT_CPI_TAG = 'e445a52e51cb9a1d';
export const PUMP_CREATE_EVENT_DISCRIMINATOR = '1b72a94ddeeb6376';
export const PUMP_TRADE_EVENT_DISCRIMINATOR = 'bddb7fd34ee661ee';

const CANONICAL_HEX = /^(?:[0-9a-f]{2})+$/;
const MAX_EVENT_BYTES = 4_096;
const MAX_EVENT_TEXT_BYTES = 1_024;
const MAX_SHAREHOLDERS = 128;
const UTF8 = new TextDecoder('utf-8', { fatal: true });

export type PumpSilverShareholder = {
  address: string;
  shareBps: number;
};

export type PumpSilverCreateEvent = {
  eventType: 'create';
  name: string;
  symbol: string;
  uri: string;
  mint: string;
  bondingCurve: string;
  user: string;
  creator: string;
  timestamp: string;
  virtualTokenReserves: string;
  virtualSolReserves: string;
  realTokenReserves: string;
  tokenTotalSupply: string;
  tokenProgram: string;
  isMayhemMode: boolean;
  isCashbackEnabled: boolean;
  quoteMint: string;
  virtualQuoteReserves: string;
};

export type PumpSilverTradeEvent = {
  eventType: 'trade';
  mint: string;
  solAmount: string;
  tokenAmount: string;
  isBuy: boolean;
  user: string;
  timestamp: string;
  virtualSolReserves: string;
  virtualTokenReserves: string;
  realSolReserves: string;
  realTokenReserves: string;
  feeRecipient: string;
  feeBasisPoints: string;
  fee: string;
  creator: string;
  creatorFeeBasisPoints: string;
  creatorFee: string;
  trackVolume: boolean;
  totalUnclaimedTokens: string;
  totalClaimedTokens: string;
  currentSolVolume: string;
  lastUpdateTimestamp: string;
  ixName: string;
  mayhemMode: boolean;
  cashbackFeeBasisPoints: string;
  cashback: string;
  buybackFeeBasisPoints: string;
  buybackFee: string;
  shareholders: PumpSilverShareholder[];
  quoteMint: string;
  quoteAmount: string;
  virtualQuoteReserves: string;
  realQuoteReserves: string;
};

export type PumpSilverEvent = PumpSilverCreateEvent | PumpSilverTradeEvent;

class EventCursor {
  readonly #bytes: Buffer;
  #offset = 0;

  constructor(bytes: Buffer) {
    this.#bytes = bytes;
  }

  remaining(): number {
    return this.#bytes.length - this.#offset;
  }

  bytes(length: number): Buffer {
    if (!Number.isSafeInteger(length) || length < 0 || length > this.remaining()) {
      throw new Error('invalid_event_payload');
    }
    const value = this.#bytes.subarray(this.#offset, this.#offset + length);
    this.#offset += length;
    return value;
  }

  u16(): number {
    return this.bytes(2).readUInt16LE(0);
  }

  u32(): number {
    return this.bytes(4).readUInt32LE(0);
  }

  u64(): string {
    return this.bytes(8).readBigUInt64LE(0).toString();
  }

  i64(): string {
    return this.bytes(8).readBigInt64LE(0).toString();
  }

  bool(): boolean {
    const value = this.bytes(1)[0];
    if (value !== 0 && value !== 1) throw new Error('invalid_bool');
    return value === 1;
  }

  publicKey(): string {
    return base58Encode(this.bytes(32));
  }

  text(): string {
    const length = this.u32();
    if (length > MAX_EVENT_TEXT_BYTES) throw new Error('invalid_event_text');
    try {
      return UTF8.decode(this.bytes(length));
    } catch {
      throw new Error('invalid_event_text');
    }
  }

  assertEnd(): void {
    if (this.remaining() !== 0) throw new Error('trailing_event_bytes');
  }
}

function decodeCreate(cursor: EventCursor): PumpSilverCreateEvent {
  const event: PumpSilverCreateEvent = {
    eventType: 'create',
    name: cursor.text(),
    symbol: cursor.text(),
    uri: cursor.text(),
    mint: cursor.publicKey(),
    bondingCurve: cursor.publicKey(),
    user: cursor.publicKey(),
    creator: cursor.publicKey(),
    timestamp: cursor.i64(),
    virtualTokenReserves: cursor.u64(),
    virtualSolReserves: cursor.u64(),
    realTokenReserves: cursor.u64(),
    tokenTotalSupply: cursor.u64(),
    tokenProgram: cursor.publicKey(),
    isMayhemMode: cursor.bool(),
    isCashbackEnabled: cursor.bool(),
    quoteMint: cursor.publicKey(),
    virtualQuoteReserves: cursor.u64(),
  };
  cursor.assertEnd();
  return event;
}

function decodeTrade(cursor: EventCursor): PumpSilverTradeEvent {
  const eventWithoutShareholders = {
    eventType: 'trade' as const,
    mint: cursor.publicKey(),
    solAmount: cursor.u64(),
    tokenAmount: cursor.u64(),
    isBuy: cursor.bool(),
    user: cursor.publicKey(),
    timestamp: cursor.i64(),
    virtualSolReserves: cursor.u64(),
    virtualTokenReserves: cursor.u64(),
    realSolReserves: cursor.u64(),
    realTokenReserves: cursor.u64(),
    feeRecipient: cursor.publicKey(),
    feeBasisPoints: cursor.u64(),
    fee: cursor.u64(),
    creator: cursor.publicKey(),
    creatorFeeBasisPoints: cursor.u64(),
    creatorFee: cursor.u64(),
    trackVolume: cursor.bool(),
    totalUnclaimedTokens: cursor.u64(),
    totalClaimedTokens: cursor.u64(),
    currentSolVolume: cursor.u64(),
    lastUpdateTimestamp: cursor.i64(),
    ixName: cursor.text(),
    mayhemMode: cursor.bool(),
    cashbackFeeBasisPoints: cursor.u64(),
    cashback: cursor.u64(),
    buybackFeeBasisPoints: cursor.u64(),
    buybackFee: cursor.u64(),
  };
  const shareholderCount = cursor.u32();
  if (shareholderCount > MAX_SHAREHOLDERS) throw new Error('too_many_shareholders');
  const shareholders: PumpSilverShareholder[] = [];
  const seen = new Set<string>();
  for (let index = 0; index < shareholderCount; index += 1) {
    const address = cursor.publicKey();
    if (seen.has(address)) throw new Error('duplicate_shareholder');
    const shareBps = cursor.u16();
    seen.add(address);
    shareholders.push({ address, shareBps });
  }
  const event: PumpSilverTradeEvent = {
    ...eventWithoutShareholders,
    shareholders,
    quoteMint: cursor.publicKey(),
    quoteAmount: cursor.u64(),
    virtualQuoteReserves: cursor.u64(),
    realQuoteReserves: cursor.u64(),
  };
  cursor.assertEnd();
  return event;
}

export function decodePumpSilverEventData(dataHex: unknown): PumpSilverEvent {
  if (typeof dataHex !== 'string'
    || dataHex.length > MAX_EVENT_BYTES * 2
    || !CANONICAL_HEX.test(dataHex)) {
    throw new Error('invalid_event_hex');
  }
  const bytes = Buffer.from(dataHex, 'hex');
  const cursor = new EventCursor(bytes);
  if (cursor.bytes(8).toString('hex') !== PUMP_EVENT_CPI_TAG) {
    throw new Error('invalid_event_cpi_tag');
  }
  const discriminator = cursor.bytes(8).toString('hex');
  if (discriminator === PUMP_CREATE_EVENT_DISCRIMINATOR) return decodeCreate(cursor);
  if (discriminator === PUMP_TRADE_EVENT_DISCRIMINATOR) return decodeTrade(cursor);
  throw new Error('unknown_event_discriminator');
}
