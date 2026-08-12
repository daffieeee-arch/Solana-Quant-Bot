/**
 * Solana findProgramAddress — officiële @solana/web3.js implementatie.
 * Gebruikt door generic-mint curve-resolutie. De pump.fun bonding-curve PDA (v1)
 * is een CONSTANTE PDA met seed 'global': findProgramAddress([b'global'], PUMPFUN)
 * = 4wTV1Ymi… (geverifieerd op mainnet). Alle v1-curves delen hetzelfde account;
 * de per-mint reserves staan in de account-data (decodePumpCurve).
 */
import { PublicKey } from '@solana/web3.js';

const PUMPFUN_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';

/** bytes → base58 (voor compat met scripts die deze helper gebruikten). */
export function bytesToBase58(bytes: Uint8Array): string {
  return new PublicKey(bytes).toBase58();
}

/** base58 decode → bytes (compat). */
export function base58ToBytes(input: string): Uint8Array {
  return new PublicKey(input).toBytes();
}

/** Officiële findProgramAddress (bump 255..0). */
export function findProgramAddress(seeds: Uint8Array[], programId: string): { address: string; bump: number } | null {
  const [address, bump] = PublicKey.findProgramAddressSync(
    seeds.map((s) => Buffer.from(s)),
    new PublicKey(programId),
  );
  return { address: address.toBase58(), bump };
}

/**
 * Pump.fun bonding-curve PDA (v1): constante PDA met seed 'global' —
 * findProgramAddress([b'global'], PUMPFUN) = 4wTV1Ymi…. Gebruikt voor de
 * generic multi-DEX lane waar de txn geen pump-CPI-curve meelevert.
 */
export function pumpBondingCurve(_mint: string): string | undefined {
  const pda = findProgramAddress([Buffer.from('global')], PUMPFUN_PROGRAM);
  return pda?.address;
}