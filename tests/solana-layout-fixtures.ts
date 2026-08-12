const BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

function encodeBase58(data: Uint8Array): string {
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

const z = (length: number) => new Uint8Array(length);
const u16 = (value: number) => Uint8Array.of(value & 0xff, value >>> 8 & 0xff);
const u32 = (value: number) => Uint8Array.of(value & 0xff, value >>> 8 & 0xff, value >>> 16 & 0xff, value >>> 24 & 0xff);
const u64 = (value: bigint) => Uint8Array.from({ length: 8 }, (_, index) => Number(value >> BigInt(index * 8) & 0xffn));
const string = (value: string) => {
  const bytes = new TextEncoder().encode(value);
  return concat(u32(bytes.length), bytes);
};
const customMeteora = (discriminator: string) => concat(
  hex(discriminator), z(4), u16(1), u16(1), [0, 0, 0, 0, 0, 0, 0], z(60),
);

export const VALID_SOLANA_INSTRUCTION_DATA = {
  pumpFunCreate: encodeBase58(concat(hex('181ec828051c0777'), string('A'), string('B'), string('C'), z(32))),
  pumpFunCreateV2: encodeBase58(concat(hex('d6904cec5f8b31b4'), string('A'), string('B'), string('C'), z(32), [0])),
  pumpSwapCreatePool: encodeBase58(concat(hex('e992d18ecf6840bc'), u16(0), u64(1n), u64(1n), z(32), [0])),
  raydiumCpmmInitialize: encodeBase58(concat(hex('afaf6d1f0d989bed'), u64(1n), u64(1n), u64(0n))),
  raydiumCpmmInitializeWithPermission: encodeBase58(concat(hex('3f37fe4131b25979'), u64(1n), u64(1n), u64(0n), [0])),
  meteoraInitializeLbPair: encodeBase58(concat(hex('2d9aedd2dd0fa65c'), z(4), u16(1))),
  meteoraInitializeCustomizablePermissionlessLbPair: encodeBase58(customMeteora('2e2729876fb7c840')),
  meteoraInitializeLbPair2: encodeBase58(concat(
    hex('493b2478ed536cc6'),
    [0xff, 0xff, 0xff, 0xff],
    Uint8Array.from({ length: 96 }, (_, index) => index * 37 + 11 & 0xff),
  )),
  meteoraInitializeCustomizablePermissionlessLbPair2: encodeBase58(customMeteora('f349817e3313f16b')),
  meteoraInitializePermissionLbPair: encodeBase58(concat(hex('6c66d555fb033515'), z(4), u16(1), u16(1), [0, 0], u16(0), [0, 0])),
  moonitTokenMint: encodeBase58(concat(
    hex('032ca4b87b0df5b3'), string('A'), string('B'), string('C'),
    [9, 0], u64(1_000_000_000n), [1, 0], u16(0), u64(0n),
  )),
} as const;
