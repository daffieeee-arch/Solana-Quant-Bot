import { createHash } from 'node:crypto';
import { PublicKey } from '@solana/web3.js';

/**
 * Structurele Pump.fun swap-parser (OFFLINE).
 * Vervangt log-string/account-count afhankelijke detectie door:
 *  - officiële instruction discriminators: SHA256("global:<name>")[0:8]
 *  - bonding-curve PDA via officiële @solana/web3.js PublicKey.findProgramAddressSync
 *  - correcte accountlayout per variant (mint+1-of-derived).
 */

export const PUMP_PROGRAM_ID = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
export const PUMP_BONDING_CURVE_SEED = 'bonding-curve';
export const WSOL = 'So11111111111111111111111111111111111111112';

// Officiële primitives (delegatie naar @solana/web3.js — géén eigen base58/bump-scan).
export function base58Decode(s: string): Buffer {
  return Buffer.from(Array.from(new PublicKey(s).toBytes()));
}
export function base58Encode(buf: Buffer): string {
  return new PublicKey(buf).toBase58();
}

const discOf = (name: string) => createHash('sha256').update('global:' + name, 'utf8').digest().subarray(0, 8).toString('hex');

// Officiële discriminators (pump-fun/pump-public-docs idl/pump.json, SHA256("global:<name>")[0:8]).
// BELANGRIJK: v2-varianten gebruiken UNDERSCORE in de anchor-instructie-naam; de
// hyphen-variant bestaat niet en levert andere (onjuiste) bytes op.
export const PUMP_DISCRIMINATORS = {
  buy: discOf('buy'),
  sell: discOf('sell'),
  buyV2: discOf('buy_v2'),
  sellV2: discOf('sell_v2'),
  buyExactQuoteInV2: discOf('buy_exact_quote_in_v2'),
  buyExactSolIn: discOf('buy_exact_sol_in'),
} as const;

/** Solana PDA (findProgramAddress) — officiële @solana/web3.js primitive. */
export function deriveBondingCurve(mint: string): string {
  if (!mint) return '';
  try {
    const mintPk = new PublicKey(mint);
    const [pda] = PublicKey.findProgramAddressSync([Buffer.from(PUMP_BONDING_CURVE_SEED, 'utf8'), mintPk.toBuffer()], new PublicKey(PUMP_PROGRAM_ID));
    return pda.toBase58();
  } catch {
    return ''; // invalid/malformed mint → fail-closed (geen curve)
  }
}

export type PumpSwapResult = { mint: string; curve: string; kind: 'buy' | 'sell' } | undefined;

export function parsePumpSwap(payload: any): PumpSwapResult {
  if (!payload?.transaction) return undefined;
  const outer = payload.transaction;
  const inner = outer?.transaction ?? outer;
  const keys: (string | undefined)[] = ((inner?.transaction?.message?.accountKeys ?? []) as unknown[]).map((k: any) => {
    if (typeof k === 'string') return k;
    if (k && typeof k === 'object' && typeof k.toBase58 === 'function') return k.toBase58();
    try { return base58Encode(Buffer.from(Array.from(k as ArrayLike<number>))); } catch { return undefined; }
  });
  for (const group of inner?.meta?.innerInstructions ?? []) {
    for (const ix of group?.instructions ?? []) {
      const prog = keys[ix?.programIdIndex];
      if (prog !== PUMP_PROGRAM_ID) continue;
      const data = ix?.data;
      let discHex = '';
      if (Buffer.isBuffer(data)) discHex = data.subarray(0, 8).toString('hex');
      else if (Array.isArray(data) && data.length >= 8) discHex = Array.from(data as number[]).slice(0, 8).map((b) => b.toString(16).padStart(2, '0')).join('');
      else if (data && typeof data === 'object' && typeof (data as any).slice === 'function') {
        try { discHex = Array.from((data as number[]).slice(0, 8)).map((b) => b.toString(16).padStart(2, '0')).join(''); } catch { continue; }
      }
      if (!discHex) continue;
      let kind: 'buy' | 'sell' | undefined;
      if (discHex === PUMP_DISCRIMINATORS.buy || discHex === PUMP_DISCRIMINATORS.buyV2 || discHex === PUMP_DISCRIMINATORS.buyExactQuoteInV2 || discHex === PUMP_DISCRIMINATORS.buyExactSolIn) kind = 'buy';
      else if (discHex === PUMP_DISCRIMINATORS.sell || discHex === PUMP_DISCRIMINATORS.sellV2) kind = 'sell';
      if (!kind) continue;
      const rawIdx = ix?.accounts;
      const idxs = Array.from(Buffer.isBuffer(rawIdx) ? rawIdx : Uint8Array.from(rawIdx ?? []));
      const accs = idxs.map((i) => keys[i]).filter((a): a is string => !!a && a.length >= 32);
      if (accs.length < 2) continue;
      // STRUCTURELE mint + curve: probeer voor elk account of zijn derived bonding-curve
      // PDA in de account-set voorkomt. Het account wiens PDA aanwezig is = mint;
      // de overeenkomende derived = canonical bonding curve. Geen willekeurige
      // accountoffset/fee-wallet/fund-suffix-gok als primaire regel.
      let mint: string | undefined;
      let curve: string | undefined;
      for (const a of accs) {
        if (a === PUMP_PROGRAM_ID || a === WSOL || a === 'CebN5WGQ4jvEPvsVU4EoHEpgzq1VV2fskvCwf8gCDbZ' || a === 'CebN5WGQ4jvEPvsVsbQh5vp8KdnDLq236Gfto2vXzB1W') continue;
        try {
          const d = deriveBondingCurve(a);
          if (d && accs.includes(d) && d !== a) { mint = a; curve = d; break; }
        } catch { /* ignore */ }
      }
      if (!mint || !curve) continue;
      return { mint, curve, kind };
    }
  }
  return undefined;
}

/** Officiële PDA-validatie: derived (exact) == geparsde curve? */
export function validateBondingCurve(mint: string, curve: string): boolean {
  if (!mint || !curve) return false;
  const derived = deriveBondingCurve(mint);
  if (!derived) return false;
  return curve === derived;
}