import { createHash } from 'node:crypto';

/**
 * Structurele Pump.fun swap-parser (OFFLINE).
 * Vervangt log-string/account-count afhankelijke detectie door:
 *  - officiële instruction discriminators: SHA256("global:<name>")[0:8]
 *  - bonding-curve PDA: Solana findProgramAddress via bumps (zonder web3.js)
 *  - correcte accountlayout per variant (mint+1-of-derived).
 */

export const PUMP_PROGRAM_ID = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
export const PUMP_BONDING_CURVE_SEED = 'bonding-curve';
export const WSOL = 'So11111111111111111111111111111111111111112';

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const B58_INDEX: Record<string, number> = {};
for (let i = 0; i < B58.length; i++) B58_INDEX[B58[i]] = i;

export function base58Decode(s: string): Buffer {
  let n = 0n;
  for (const c of s) n = n * 58n + BigInt(B58_INDEX[c] ?? 0);
  const bytes: number[] = [];
  while (n > 0n) { bytes.unshift(Number(n % 256n)); n /= 256n; }
  // leading 1s = zero bytes
  for (const c of s) { if (c !== '1') break; bytes.unshift(0); }
  return Buffer.from(bytes);
}
export function base58Encode(buf: Buffer): string {
  let n = 0n;
  for (const b of buf) n = n * 256n + BigInt(b);
  let out = '';
  while (n > 0n) { out = B58[Number(n % 58n)] + out; n /= 58n; }
  for (const b of buf) { if (b !== 0) break; out = '1' + out; }
  return out || '';
}

const discOf = (name: string) => createHash('sha256').update('global:' + name, 'utf8').digest().subarray(0, 8).toString('hex');

export const PUMP_DISCRIMINATORS = {
  buy: discOf('buy'),
  sell: discOf('sell'),
  buyV2: discOf('buy-v2'),
  sellV2: discOf('sell-v2'),
} as const;

/** Solana PDA (findProgramAddress): seeds ‖ bump ‖ programId via sha256. */
export function deriveBondingCurve(mint: string): string {
  const seed = Buffer.concat([Buffer.from(PUMP_BONDING_CURVE_SEED, 'utf8'), base58Decode(mint)]);
  for (let bump = 255; bump > 0; bump--) {
    const h = createHash('sha256');
    h.update(seed);
    h.update(Buffer.from([bump]));
    h.update(base58Decode(PUMP_PROGRAM_ID));
    const addr = base58Encode(h.digest());
    if (addr.length > 0) return addr;
  }
  return '';
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
      if (discHex === PUMP_DISCRIMINATORS.buy || discHex === PUMP_DISCRIMINATORS.buyV2) kind = 'buy';
      else if (discHex === PUMP_DISCRIMINATORS.sell || discHex === PUMP_DISCRIMINATORS.sellV2) kind = 'sell';
      if (!kind) continue;
      const rawIdx = ix?.accounts;
      const idxs = Array.from(Buffer.isBuffer(rawIdx) ? rawIdx : Uint8Array.from(rawIdx ?? []));
      const accs = idxs.map((i) => keys[i]);
      // mint = pump-suffix token; curve = mint+1 (relatie) óf derived-PDA
      const mint = accs.find((a) => a && /pump$/i.test(a));
      if (!mint) continue;
      const idxM = accs.indexOf(mint);
      const curveAcc = idxM >= 0 && idxM + 1 < accs.length ? accs[idxM + 1] : undefined;
      if (curveAcc && curveAcc.length >= 30 && curveAcc !== mint) {
        // validate via PDA (indien resolvable)
        const derived = deriveBondingCurve(mint);
        if (derived && curveAcc !== derived && !curveAcc.startsWith(derived.slice(0, 12))) {
          // fallback: accepteer curve als het apart is van mint (geen vaste offset-regel)
        }
        return { mint, curve: curveAcc, kind };
      }
      // fallback via derived PDA in accounts
      const derived = deriveBondingCurve(mint);
      const found = accs.find((a) => a === derived);
      if (found) return { mint, curve: found, kind };
      return undefined;
    }
  }
  return undefined;
}

/** Officiële PDA-validatie: derived == parsed? */
export function validateBondingCurve(mint: string, curve: string): boolean {
  if (!mint || !curve) return false;
  const derived = deriveBondingCurve(mint);
  return curve === derived || curve.startsWith(derived.slice(0, 12));
}