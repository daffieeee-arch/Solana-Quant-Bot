import { decodePublicKey, encodePublicKey, findProgramAddress } from './solana-pda.js';

export const PUMP_PROGRAM_ID = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
export const PUMP_BONDING_CURVE_SEED = 'bonding-curve';
export const WSOL = 'So11111111111111111111111111111111111111112';

export function base58Decode(value: string): Buffer {
  return decodePublicKey(value);
}

export function base58Encode(bytes: Buffer): string {
  return encodePublicKey(bytes);
}

/** Pure deterministic Solana PDA derivation; no RPC or transport dependency. */
export function deriveBondingCurve(mint: string): string {
  if (!mint) return '';
  try {
    const derived = findProgramAddress(
      [Buffer.from(PUMP_BONDING_CURVE_SEED, 'utf8'), decodePublicKey(mint)],
      decodePublicKey(PUMP_PROGRAM_ID),
    );
    return derived ? encodePublicKey(derived.address) : '';
  } catch {
    return '';
  }
}

export function validateBondingCurve(mint: string, curve: string): boolean {
  return mint.length > 0 && curve.length > 0 && deriveBondingCurve(mint) === curve;
}
