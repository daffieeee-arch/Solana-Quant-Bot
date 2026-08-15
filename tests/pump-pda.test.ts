import { describe, expect, it } from 'vitest';
import { PublicKey } from '@solana/web3.js';
import { deriveBondingCurve, validateBondingCurve, base58Decode, base58Encode } from '../src/pump-parser.js';
import { createHash } from 'node:crypto';

const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';

/** Echte geldige 44-char base58 pubkeys (geldige 32-byte) voor deterministische fixtures. */
const MINTS = [
  'GKUMirQJm5AzpnknHKZSpn6NRVEcRHDfxr8dWWN1pump',
  'So11111111111111111111111111111111111111112', // WSOL (niet-pump, edgecase)
  'C9cAPKjWG8dsujybrn6LhXnTxAx3Y6Z9HVtrfQ1Cn8Hy',
];

describe('PDA/address primitives vs officiële @solana/web3.js', () => {
  it('deriveBondingCurve matcht officiële findProgramAddressSync (deterministisch)', () => {
    for (const mintStr of MINTS) {
      const mint = new PublicKey(mintStr);
      const [official, bump] = PublicKey.findProgramAddressSync([Buffer.from('bonding-curve'), mint.toBuffer()], new PublicKey(PUMP));
      const own = deriveBondingCurve(mintStr);
      expect(own).toBe(official.toBase58());
      expect(bump).toBeGreaterThan(0);
    }
  });

  it('validateBondingCurve: geldige curve matcht derived; ongeldig + verkeerde seeds faalt', () => {
    const mintStr = MINTS[0];
    const [official] = PublicKey.findProgramAddressSync([Buffer.from('bonding-curve'), new PublicKey(mintStr).toBuffer()], new PublicKey(PUMP));
    expect(validateBondingCurve(mintStr, official.toBase58())).toBe(true);
    expect(validateBondingCurve(mintStr, 'WRONG')).toBe(false);
    // verkeerde program id
    const [otherProgram] = PublicKey.findProgramAddressSync([Buffer.from('bonding-curve'), new PublicKey(mintStr).toBuffer()], new PublicKey('675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'));
    expect(validateBondingCurve(mintStr, otherProgram.toBase58())).toBe(false);
  });

  it('base58 roundtrip + invalid key edgecases (fail-closed via officiële library)', () => {
    for (const m of MINTS) {
      const bytes = base58Decode(m);
      expect(base58Encode(bytes)).toBe(m);
    }
    // Invalid base58/pubkey → officiële library gooit (fail-closed), geen stille corruptie
    expect(() => base58Decode('0OIl')).toThrow();
    // lege input edge
    expect(() => base58Decode('')).toThrow();
  });

  it('100 deterministische fixtures (variërende laatste bytes) matchen officiële PDA', () => {
    for (let i = 0; i < 100; i++) {
      // deterministische mint uit sha256('mint-{i}') met pump-suffix — geldige 32-byte
      const h = createHash('sha256').update(`mint-${i}`).digest();
      const b58 = base58Encode(h).slice(0, 44);
      const mint = new PublicKey(base58Decode(base58Encode(base58Decode(b58)))); // normalizeer naar 32 bytes
      const [official] = PublicKey.findProgramAddressSync([Buffer.from('bonding-curve'), mint.toBuffer()], new PublicKey(PUMP));
      const own = deriveBondingCurve(mint.toBase58());
      expect(own).toBe(official.toBase58());
    }
  });
});