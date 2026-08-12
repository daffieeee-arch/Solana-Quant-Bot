#!/usr/bin/env node
/**
 * Collect pump.fun launch mints from the last `--hours` (default 48) by scanning
 * recent signatures of the pump program, decoding each txn's token balances to
 * extract the newly-created mint (robust against versioned instruction layouts).
 * Output: one mint per line → feed to old-faithful-backtest.mjs --mints=FILE.
 *
 * Usage: node scripts/collect-launch-mints.mjs [--hours 48] [--limit 120]
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECRETS = path.join(__dirname, '..', 'secrets.txt');

function loadToken() {
  const raw = fs.readFileSync(SECRETS, 'utf8');
  const line = raw.split('\n').find((l) => /value x token/i.test(l));
  const m = line?.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/);
  if (!m) throw new Error('Geen Triton x-token gevonden in secrets.txt');
  return m[1];
}

const PUMP_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const WSOL = 'So11111111111111111111111111111111111111112';
const hours = Number(process.argv.find((a) => a.startsWith('--hours='))?.split('=')[1] || 48);
const limit = Number(process.argv.find((a) => a.startsWith('--limit='))?.split('=')[1] || 120);
const token = loadToken();
const base = `https://johnb-mainnet-2781.mainnet.rpcpool.com/${token}`;

let calls = 0;
async function rpc(method, params) {
  calls += 1;
  const res = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: calls, method, params }), signal: AbortSignal.timeout(20_000) });
  const d = await res.json();
  if (d.error) throw new Error(`RPC ${method}: ${d.error.code} ${d.error.message}`);
  return d.result;
}

/** Haal de nieuw-lanceerde mint uit een create-txn via pre/postTokenBalances. */
function mintFromCreate(tx) {
  const log = tx?.meta?.logMessages ?? [];
  if (!log.some((l) => /Program log: (Instruction: )?Create/.test(l))) return undefined;
  const seen = new Set();
  for (const b of [...(tx?.meta?.postTokenBalances ?? []), ...(tx?.meta?.preTokenBalances ?? [])]) {
    const mint = String(b?.mint ?? '');
    if (mint && mint !== WSOL && !seen.has(mint)) seen.add(mint);
  }
  // prefer the shortest mint (pump.fun tokens: 6..9 chars + 'pump') — heuristic:
  // the new token is usually the one NOT a known stable/wrapper; take the last-seen
  // distinct mint that isn't a known system token.
  const known = new Set([
    'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', // USDC
    'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', // USDT
    'So11111111111111111111111111111111111111112', // WSOL / SOL
    'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So', // mSOL
    'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', // BONK
  ]);
  for (const mint of seen) if (!known.has(mint)) return mint;
  return undefined;
}

async function main() {
  const until = Math.floor(Date.now() / 1000) - hours * 3600;
  console.log(`[collector] pump-launches laatste ${hours}u (limit ${limit}) …`);
  // Paginatie met `before`: 5 pagina's × 1000 sigs = tot ~5000 txns over 48u,
  // zodat we veel meer unieke launch-mints vinden dan de eerste 1000 recente.
  const mints = new Map();
  let scanned = 0;
  let before;
  for (let page = 0; page < 5 && mints.size < limit; page += 1) {
    const params = [{ limit: 1000 }];
    if (before) params[0].before = before;
    let sigs;
    try { sigs = await rpc('getSignaturesForAddress', [PUMP_PROGRAM, params[0]]); }
    catch { break; }
    if (!Array.isArray(sigs) || sigs.length === 0) break;
    console.log(`[collector] pagina ${page + 1}: ${sigs.length} sigs (totaal mints ${mints.size})`);
    for (const sig of sigs) {
      if (mints.size >= limit) break;
      if (typeof sig?.signature !== 'string') continue;
      if (sig.blockTime && sig.blockTime < until) { before = sig.signature; continue; }
      if (sig.err) continue;
      scanned += 1;
      let tx;
      try { tx = await rpc('getTransaction', [sig.signature, { encoding: 'json', maxSupportedTransactionVersion: 0 }]); }
      catch { continue; }
      const mint = mintFromCreate(tx);
      if (mint) {
        mints.set(mint, { sig: sig.signature, blockTime: sig.blockTime });
        if (mints.size % 10 === 0) console.log(`[collector] ${mints.size} mints … (calls ${calls})`);
      }
    }
    before = before ?? sigs[sigs.length - 1]?.signature;
  }
  const out = path.join(__dirname, '..', `launch-mints-${hours}h.txt`);
  fs.writeFileSync(out, Array.from(mints.keys()).join('\n') + '\n');
  console.log(`[collector] ${mints.size} unieke launch-mints → ${out} (calls ${calls})`);
}

main().catch((e) => { console.error('[collector] FOUT:', e.message); process.exit(1); });