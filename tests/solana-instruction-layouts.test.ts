import { describe, expect, it } from 'vitest';
import {
  validateSolanaInstructionData,
  type SolanaInstructionLayout,
} from '../src/providers/solana-instruction-layouts.js';

const BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

function encodeBase58(data: Uint8Array): string {
  if (data.length === 0) return '';
  const digits = [0];
  for (const byte of data) {
    let carry = byte;
    for (let index = 0; index < digits.length; index += 1) {
      carry += digits[index]! << 8;
      digits[index] = carry % 58;
      carry = Math.floor(carry / 58);
    }
    while (carry > 0) {
      digits.push(carry % 58);
      carry = Math.floor(carry / 58);
    }
  }
  let output = '';
  for (const byte of data) {
    if (byte !== 0) break;
    output += '1';
  }
  for (let index = digits.length - 1; index >= 0; index -= 1) output += BASE58[digits[index]!]!;
  return output;
}

function concat(...parts: Array<Uint8Array | number[]>): Uint8Array {
  const arrays = parts.map((part) => Uint8Array.from(part));
  const output = new Uint8Array(arrays.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of arrays) {
    output.set(part, offset);
    offset += part.length;
  }
  return output;
}

function hex(value: string): Uint8Array {
  return Uint8Array.from(value.match(/../g)!.map((byte) => Number.parseInt(byte, 16)));
}

function u16(value: number): Uint8Array {
  return Uint8Array.of(value & 0xff, value >>> 8 & 0xff);
}

function u32(value: number): Uint8Array {
  return Uint8Array.of(value & 0xff, value >>> 8 & 0xff, value >>> 16 & 0xff, value >>> 24 & 0xff);
}

function u64(value: bigint): Uint8Array {
  return Uint8Array.from({ length: 8 }, (_, index) => Number(value >> BigInt(index * 8) & 0xffn));
}

function string(value: string): Uint8Array {
  const bytes = new TextEncoder().encode(value);
  return concat(u32(bytes.length), bytes);
}

const z = (length: number) => new Uint8Array(length);

const customMeteora = (discriminator: string, optionTag: 0 | 1, activationTag = 0): Uint8Array => concat(
  hex(discriminator), z(4), u16(1), u16(1),
  [activationTag, 0, optionTag], optionTag === 1 ? u64(0n) : [], [0, 0, 0, 0], z(60),
);

const fixtures: Record<SolanaInstructionLayout, Uint8Array> = {
  pumpFunCreate: concat(hex('181ec828051c0777'), string('A'), string('B'), string('C'), z(32)),
  pumpFunCreateV2: concat(hex('d6904cec5f8b31b4'), string('A'), string('B'), string('C'), z(32), [0]),
  pumpSwapCreatePool: concat(hex('e992d18ecf6840bc'), u16(0), u64(1n), u64(1n), z(32), [0]),
  raydiumCpmmInitialize: concat(hex('afaf6d1f0d989bed'), u64(1n), u64(1n), u64(0n)),
  raydiumCpmmInitializeWithPermission: concat(hex('3f37fe4131b25979'), u64(1n), u64(1n), u64(0n), [0]),
  meteoraInitializeLbPair: concat(hex('2d9aedd2dd0fa65c'), z(4), u16(1)),
  meteoraInitializePermissionLbPair: concat(hex('6c66d555fb033515'), z(4), u16(1), u16(1), [0, 0], u16(0), [0, 0]),
  meteoraInitializeCustomizablePermissionlessLbPair: customMeteora('2e2729876fb7c840', 0),
  meteoraInitializeLbPair2: concat(hex('493b2478ed536cc6'), z(4), z(96)),
  meteoraInitializeCustomizablePermissionlessLbPair2: customMeteora('f349817e3313f16b', 0),
  moonitTokenMint: concat(
    hex('032ca4b87b0df5b3'), string('A'), string('B'), string('C'),
    [9, 0], u64(1_000_000_000n), [1, 0], u16(0), u64(0n),
  ),
};

const optionalPumpTail = new Set<SolanaInstructionLayout>(['pumpFunCreateV2', 'pumpSwapCreatePool']);

describe('official Solana pool instruction layouts', () => {
  it.each(Object.entries(fixtures) as Array<[SolanaInstructionLayout, Uint8Array]>)('%s consumes the complete official layout', (layout, data) => {
    expect(validateSolanaInstructionData(layout, encodeBase58(data))).toBe(true);
    expect(validateSolanaInstructionData(layout, encodeBase58(data.slice(0, 8)))).toBe(false);
    expect(validateSolanaInstructionData(layout, encodeBase58(data.slice(0, -1)))).toBe(false);

    if (optionalPumpTail.has(layout)) {
      expect(validateSolanaInstructionData(layout, encodeBase58(concat(data, [0])))).toBe(true);
      expect(validateSolanaInstructionData(layout, encodeBase58(concat(data, [1])))).toBe(true);
      expect(validateSolanaInstructionData(layout, encodeBase58(concat(data, [0, 0])))).toBe(false);
    } else {
      expect(validateSolanaInstructionData(layout, encodeBase58(concat(data, [0])))).toBe(false);
    }
  });

  it('rejects non-canonical Pump bool bytes and Raydium enum variants', () => {
    const pumpV2 = fixtures.pumpFunCreateV2.slice();
    pumpV2[pumpV2.length - 1] = 2;
    expect(validateSolanaInstructionData('pumpFunCreateV2', encodeBase58(pumpV2))).toBe(false);

    const pumpSwap = fixtures.pumpSwapCreatePool.slice();
    pumpSwap[pumpSwap.length - 1] = 2;
    expect(validateSolanaInstructionData('pumpSwapCreatePool', encodeBase58(pumpSwap))).toBe(false);

    const permission = fixtures.raydiumCpmmInitializeWithPermission.slice();
    permission[32] = 3;
    expect(validateSolanaInstructionData('raydiumCpmmInitializeWithPermission', encodeBase58(permission))).toBe(false);
  });

  it('binds Meteora customizable length to the Option<u64> tag and validates bools', () => {
    const none = fixtures.meteoraInitializeCustomizablePermissionlessLbPair;
    const some = customMeteora('2e2729876fb7c840', 1);
    expect(validateSolanaInstructionData('meteoraInitializeCustomizablePermissionlessLbPair', encodeBase58(none))).toBe(true);
    expect(validateSolanaInstructionData('meteoraInitializeCustomizablePermissionlessLbPair', encodeBase58(some))).toBe(true);

    const invalidOption = none.slice();
    invalidOption[18] = 2;
    expect(validateSolanaInstructionData('meteoraInitializeCustomizablePermissionlessLbPair', encodeBase58(invalidOption))).toBe(false);

    const invalidBool = none.slice();
    invalidBool[17] = 2;
    expect(validateSolanaInstructionData('meteoraInitializeCustomizablePermissionlessLbPair', encodeBase58(invalidBool))).toBe(false);
  });

  it.each([
    ['meteoraInitializeCustomizablePermissionlessLbPair', '2e2729876fb7c840'],
    ['meteoraInitializeCustomizablePermissionlessLbPair2', 'f349817e3313f16b'],
  ] as const)('%s accepts only official ActivationType enum tags', (layout, discriminator) => {
    expect(validateSolanaInstructionData(layout, encodeBase58(customMeteora(discriminator, 0, 0)))).toBe(true);
    expect(validateSolanaInstructionData(layout, encodeBase58(customMeteora(discriminator, 0, 1)))).toBe(true);
    expect(validateSolanaInstructionData(layout, encodeBase58(customMeteora(discriminator, 0, 2)))).toBe(false);
    expect(validateSolanaInstructionData(layout, encodeBase58(customMeteora(discriminator, 0, 255)))).toBe(false);
  });

  it('accepts only official Meteora permissioned enum tags', () => {
    const valid = fixtures.meteoraInitializePermissionLbPair.slice();
    valid[17] = 1;
    valid[20] = 1;
    valid[21] = 1;
    expect(validateSolanaInstructionData('meteoraInitializePermissionLbPair', encodeBase58(valid))).toBe(true);

    for (const offset of [17, 20, 21]) {
      const invalid = fixtures.meteoraInitializePermissionLbPair.slice();
      invalid[offset] = 2;
      expect(validateSolanaInstructionData('meteoraInitializePermissionLbPair', encodeBase58(invalid))).toBe(false);
    }
  });

  it('consumes Meteora initialize_lb_pair2 as exactly active_id i32 plus an unrestricted fixed [u8; 96] padding array', () => {
    const activeIdNegativeOne = Uint8Array.of(0xff, 0xff, 0xff, 0xff);
    const nonZeroPadding = Uint8Array.from({ length: 96 }, (_, index) => index * 37 + 11 & 0xff);
    const payload = concat(hex('493b2478ed536cc6'), activeIdNegativeOne, nonZeroPadding);

    expect(payload).toHaveLength(108);
    expect(validateSolanaInstructionData('meteoraInitializeLbPair2', encodeBase58(payload))).toBe(true);
    for (let length = 0; length < payload.length; length += 1) {
      expect(validateSolanaInstructionData('meteoraInitializeLbPair2', encodeBase58(payload.slice(0, length)))).toBe(false);
    }
    expect(validateSolanaInstructionData('meteoraInitializeLbPair2', encodeBase58(concat(payload, [0])))).toBe(false);
  });

  it.each([
    ['meteoraInitializeCustomizablePermissionlessLbPair', '2e2729876fb7c840'],
    ['meteoraInitializeCustomizablePermissionlessLbPair2', 'f349817e3313f16b'],
  ] as const)('%s accepts only official ConcreteFunctionType and CollectFeeMode tags', (layout, discriminator) => {
    const valid = customMeteora(discriminator, 0);
    valid[21] = 1;
    valid[22] = 1;
    expect(validateSolanaInstructionData(layout, encodeBase58(valid))).toBe(true);

    for (const offset of [21, 22]) {
      const invalid = customMeteora(discriminator, 0);
      invalid[offset] = 2;
      expect(validateSolanaInstructionData(layout, encodeBase58(invalid))).toBe(false);
    }
  });

  it('enforces the documented Moonit enum domains plus the approved observed MigrationTarget tag 1', () => {
    for (const curveType of [0, 1, 2, 3, 4]) {
      const valid = fixtures.moonitTokenMint.slice();
      valid[33] = curveType;
      expect(validateSolanaInstructionData('moonitTokenMint', encodeBase58(valid))).toBe(true);
    }

    for (const migrationTarget of [0, 1]) {
      const valid = fixtures.moonitTokenMint.slice();
      valid[34] = migrationTarget;
      expect(validateSolanaInstructionData('moonitTokenMint', encodeBase58(valid))).toBe(true);
    }

    for (const [offset, invalidTag] of [[24, 1], [33, 5], [34, 2]] as const) {
      const invalid = fixtures.moonitTokenMint.slice();
      invalid[offset] = invalidTag;
      expect(validateSolanaInstructionData('moonitTokenMint', encodeBase58(invalid))).toBe(false);
    }
  });

  it('enforces Moonit UTF-8 byte caps and rejects invalid UTF-8 without allocating from hostile lengths', () => {
    const valid = fixtures.moonitTokenMint;
    expect(validateSolanaInstructionData('moonitTokenMint', encodeBase58(valid))).toBe(true);

    const oversizedName = concat(
      hex('032ca4b87b0df5b3'), string('A'.repeat(33)), string('B'), string('C'),
      [9, 0], u64(1n), [1, 0], u16(0), u64(0n),
    );
    expect(validateSolanaInstructionData('moonitTokenMint', encodeBase58(oversizedName))).toBe(false);

    const hostileLength = concat(hex('032ca4b87b0df5b3'), [0xff, 0xff, 0xff, 0xff]);
    expect(validateSolanaInstructionData('moonitTokenMint', encodeBase58(hostileLength))).toBe(false);

    const invalidUtf8 = concat(
      hex('032ca4b87b0df5b3'), u32(1), [0xff], string('B'), string('C'),
      [9, 0], u64(1n), [1, 0], u16(0), u64(0n),
    );
    expect(validateSolanaInstructionData('moonitTokenMint', encodeBase58(invalidUtf8))).toBe(false);
  });
});
