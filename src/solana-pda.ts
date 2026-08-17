import { createHash } from 'node:crypto';
import bs58 from 'bs58';

const FIELD_PRIME = (1n << 255n) - 19n;
const EDWARDS_D = mod(-121665n * invert(121666n));
const SQRT_MINUS_ONE = powMod(2n, (FIELD_PRIME - 1n) / 4n);
const PDA_MARKER = Buffer.from('ProgramDerivedAddress', 'utf8');
const CANONICAL_PUBLIC_KEY_TEXT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

function mod(value: bigint): bigint {
  const reduced = value % FIELD_PRIME;
  return reduced >= 0n ? reduced : reduced + FIELD_PRIME;
}

function powMod(base: bigint, exponent: bigint): bigint {
  let result = 1n;
  let factor = mod(base);
  let power = exponent;
  while (power > 0n) {
    if ((power & 1n) === 1n) result = mod(result * factor);
    factor = mod(factor * factor);
    power >>= 1n;
  }
  return result;
}

function invert(value: bigint): bigint {
  if (mod(value) === 0n) throw new Error('cannot invert zero');
  return powMod(value, FIELD_PRIME - 2n);
}

function littleEndianInteger(bytes: Uint8Array): bigint {
  let value = 0n;
  for (let index = bytes.length - 1; index >= 0; index -= 1) value = (value << 8n) | BigInt(bytes[index]);
  return value;
}

/** True only when the 32-byte compressed value decodes to an Ed25519 point. */
export function isEd25519Point(bytes: Uint8Array): boolean {
  if (bytes.length !== 32) return false;
  const encoded = Uint8Array.from(bytes);
  const sign = encoded[31] >>> 7;
  encoded[31] &= 0x7f;
  const y = littleEndianInteger(encoded);
  if (y >= FIELD_PRIME) return false;
  const ySquared = mod(y * y);
  const numerator = mod(ySquared - 1n);
  const denominator = mod(EDWARDS_D * ySquared + 1n);
  if (denominator === 0n) return false;
  const xSquared = mod(numerator * invert(denominator));
  let x = powMod(xSquared, (FIELD_PRIME + 3n) / 8n);
  if (mod(x * x - xSquared) !== 0n) x = mod(x * SQRT_MINUS_ONE);
  if (mod(x * x - xSquared) !== 0n) return false;
  return !(x === 0n && sign === 1);
}

export function decodePublicKey(value: string): Buffer {
  if (typeof value !== 'string' || !CANONICAL_PUBLIC_KEY_TEXT.test(value)) {
    throw new Error('invalid public key text');
  }
  const decoded = Buffer.from(bs58.decode(value));
  if (decoded.length !== 32 || bs58.encode(decoded) !== value) throw new Error('invalid canonical public key');
  return decoded;
}

export function encodePublicKey(bytes: Uint8Array): string {
  if (bytes.length !== 32) throw new Error('public key must contain 32 bytes');
  return bs58.encode(bytes);
}

function createProgramAddress(seeds: readonly Uint8Array[], programId: Uint8Array): Buffer | undefined {
  if (seeds.length > 16 || seeds.some((seed) => seed.length > 32) || programId.length !== 32) return undefined;
  const address = createHash('sha256')
    .update(Buffer.concat([...seeds.map((seed) => Buffer.from(seed)), Buffer.from(programId), PDA_MARKER]))
    .digest();
  return isEd25519Point(address) ? undefined : address;
}

export function findProgramAddress(
  seeds: readonly Uint8Array[],
  programId: Uint8Array,
): { address: Buffer; bump: number } | undefined {
  for (let bump = 255; bump >= 0; bump -= 1) {
    const address = createProgramAddress([...seeds, Uint8Array.of(bump)], programId);
    if (address) return { address, bump };
  }
  return undefined;
}
