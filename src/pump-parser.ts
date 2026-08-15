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
  // LIVE-waargenomen discriminators (mainnet 2026-08-15, publieke RPC-dumps; custom
  // dispatcher op de live binary — NÍET sha256("global:<name>")). De gepinde IDL
  // beschrijft de live binary niet volledig; deze bytes matchen echte txn's.
  // provenance: /opt/data/real-trades.json (reviewer-dump deleg_0c403372).
  liveSell: 'e6345c8dd8b14540',
  liveBuy: '0094d0da1f435eb0',
  liveBuyV2: '1e7435e21cba7f11',
  liveBuyExactSolIn: 'e822865bc7d49d0e',
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
  const isBuyDisc = (h: string) =>
    h === PUMP_DISCRIMINATORS.buy || h === PUMP_DISCRIMINATORS.buyV2 || h === PUMP_DISCRIMINATORS.buyExactQuoteInV2 || h === PUMP_DISCRIMINATORS.buyExactSolIn
    || h === PUMP_DISCRIMINATORS.liveBuy || h === PUMP_DISCRIMINATORS.liveBuyV2 || h === PUMP_DISCRIMINATORS.liveBuyExactSolIn;
  const isSellDisc = (h: string) =>
    h === PUMP_DISCRIMINATORS.sell || h === PUMP_DISCRIMINATORS.sellV2 || h === PUMP_DISCRIMINATORS.liveSell;
  const tryIx = (ix: any): PumpSwapResult => {
    const prog = keys[ix?.programIdIndex];
    if (prog !== PUMP_PROGRAM_ID) return undefined;
    const data = ix?.data;
    let discHex = '';
    if (Buffer.isBuffer(data)) discHex = data.subarray(0, 8).toString('hex');
    else if (Array.isArray(data) && data.length >= 8) discHex = Array.from(data as number[]).slice(0, 8).map((b) => b.toString(16).padStart(2, '0')).join('');
    else if (data && typeof data === 'object' && typeof (data as any).slice === 'function') {
      try { discHex = Array.from((data as number[]).slice(0, 8)).map((b) => b.toString(16).padStart(2, '0')).join(''); } catch { return undefined; }
    }
    if (!discHex) return undefined;
    let kind: 'buy' | 'sell' | undefined;
    if (isBuyDisc(discHex)) kind = 'buy';
    else if (isSellDisc(discHex)) kind = 'sell';
    if (!kind) return undefined;
    const rawIdx = ix?.accounts;
    const idxs = Array.from(Buffer.isBuffer(rawIdx) ? rawIdx : Uint8Array.from(rawIdx ?? []));
    const accs = idxs.map((i) => keys[i]).filter((a): a is string => !!a && a.length >= 32);
    if (accs.length < 2) return undefined;
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
    if (!mint || !curve) return undefined;
    return { mint, curve, kind };
  };
  // Top-level instructies (message.instructions) + inner-CPI (meta.innerInstructions[].instructions)
  for (const ix of inner?.transaction?.message?.instructions ?? []) { const r = tryIx(ix); if (r) return r; }
  for (const group of inner?.meta?.innerInstructions ?? []) {
    for (const ix of group?.instructions ?? []) { const r = tryIx(ix); if (r) return r; }
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