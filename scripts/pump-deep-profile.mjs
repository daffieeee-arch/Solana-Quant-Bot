#!/usr/bin/env node
/**
 * Diep-profiel van één pump-mint: volledige tijdlijn (buys/sells per 5 min),
 * echte prijs uit txn-meta, momentum vóór elk signaal, volumedrukte, en de
 * forward-return per signaalmoment — om te zien WAT een winnende coin
 * onderscheidt van een verliezer vóór entry.
 *
 * Usage: node scripts/pump-deep-profile.mjs <mint>
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECRETS = path.join(__dirname, '..', 'secrets.txt');
const WSOL = 'So11111111111111111111111111111111111111112';

function loadToken() {
  const raw = fs.readFileSync(SECRETS, 'utf8');
  const line = raw.split('\n').find((l) => /value x token/i.test(l));
  const m = line?.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/);
  if (!m) throw new Error('Geen x-token');
  return m[1];
}

const mint = process.argv[2];
if (!mint) { console.error('usage: node pump-deep-profile.mjs <mint>'); process.exit(1); }
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
function parsePumpTxn(tx) {
  const log = tx?.meta?.logMessages ?? [];
  const isBuy = log.some((l) => l.includes('Program log: Instruction: Buy'));
  const isSell = log.some((l) => l.includes('Program log: Instruction: Sell'));
  if (!isBuy && !isSell) return undefined;
  return { kind: isBuy ? 'buy' : 'sell', t: tx?.blockTime };
}
function txnPriceSol(tx) {
  const meta = tx?.meta; if (!meta) return undefined;
  const byOwner = new Map();
  for (const b of meta.preTokenBalances ?? []) { const o = String(b.owner ?? ''); const m = String(b.mint ?? ''); if (m === WSOL) continue; const c = byOwner.get(o) ?? { mint: m, pre: undefined, post: undefined }; c.pre = b.uiTokenAmount?.uiAmount ?? c.pre; byOwner.set(o, c); }
  for (const b of meta.postTokenBalances ?? []) { const o = String(b.owner ?? ''); const m = String(b.mint ?? ''); if (m === WSOL) continue; const c = byOwner.get(o) ?? { mint: m, pre: undefined, post: undefined }; c.post = b.uiTokenAmount?.uiAmount ?? c.post; byOwner.set(o, c); }
  const preBal = meta.preBalances ?? []; const postBal = meta.postBalances ?? [];
  let solDelta = 0;
  for (let i = 1; i < Math.min(preBal.length, postBal.length); i += 1) solDelta += (postBal[i] ?? 0) - (preBal[i] ?? 0);
  const solSwapped = Math.abs(solDelta) / 1e9;
  let best; for (const { mint: m, pre: p, post: q } of byOwner.values()) { if (p === undefined || q === undefined) continue; const dt = Math.abs(q - p); if (!best || dt > best.dt) best = { mint: m, dt }; }
  if (!best || best.dt <= 0 || solSwapped <= 0) return undefined;
  return { priceSol: Number((solSwapped / best.dt).toFixed(12)), solUsd: solSwapped, tokenDelta: best.dt };
}

async function main() {
  console.log(`[profile] ${mint}`);
  const sigs = await rpc('getSignaturesForAddress', [mint, { limit: 1000 }]);
  console.log(`[profile] ${sigs.length} signatures`);
  const trades = [];
  for (const sig of sigs.slice(0, 300)) {
    if (typeof sig?.signature !== 'string' || sig.err) continue;
    const tx = await rpc('getTransaction', [sig.signature, { encoding: 'json', maxSupportedTransactionVersion: 0 }]);
    const p = parsePumpTxn(tx);
    if (!p) continue;
    const pr = txnPriceSol(tx);
    trades.push({ t: p.t, kind: p.kind, price: pr?.priceSol, usdVol: pr?.solUsd });
  }
  trades.sort((a, b) => (a.t ?? 0) - (b.t ?? 0));
  console.log(`[profile] ${trades.length} pump-trades (buys ${trades.filter((t) => t.kind === 'buy').length}, sells ${trades.filter((t) => t.kind === 'sell').length}) · calls ${calls}`);
  if (trades.length < 10) { console.log('[profile] te weinig trades'); return; }

  // 5-min bins over de volledige reeks
  const start = trades[0].t ?? Math.floor(Date.now() / 1000);
  const bins = [];
  for (let m = 0; m < 96 * 12; m += 1) { // max 96u
    const from = start + m * 300, to = from + 300;
    const slice = trades.filter((t) => t.t >= from && t.t < to);
    const last = slice.filter((t) => t.price !== undefined).pop();
    bins.push({ minute: m, buys: slice.filter((t) => t.kind === 'buy').length, sells: slice.filter((t) => t.kind === 'sell').length, price: last?.price, usdVol: slice.reduce((a, t) => a + (t.usdVol ?? 0), 0) });
    if (slice.length === 0 && m > 20 && !last) { /* meerdere lege bins = einde handel */ }
    if (to > Date.now() / 1000 + 60 && m > 20) break;
  }
  const active = bins.filter((b) => b.buys + b.sells > 0);
  console.log(`[profile] actieve 5-min-bins: ${active.length} over ${(bins.length * 5 / 60).toFixed(0)}u`);

  // Signaal-evaluatie + wat onderscheidt: momentum vóór signaal + volumedruk
  console.log('\n=== SIGNALEN (versoepeld: 20 buys/5m, druk≥0.5) ===');
  let signals = 0, wins = 0;
  for (let i = 0; i < bins.length; i += 1) {
    const b = bins[i];
    if (!b.price) continue;
    const win5 = bins.slice(Math.max(0, i - 4), i + 1);
    const buys = win5.reduce((a, x) => a + x.buys, 0);
    const sells = win5.reduce((a, x) => a + x.sells, 0);
    const pressure = buys + sells > 0 ? buys / (buys + sells) : 0;
    if (buys < 20 || pressure < 0.5) continue;
    const priceNow = b.price;
    const price5ago = bins[Math.max(0, i - 5)]?.price ?? priceNow;
    const price15ago = bins[Math.max(0, i - 15)]?.price ?? priceNow;
    const mom5 = ((priceNow - price5ago) / price5ago) * 100;
    const mom15 = ((priceNow - price15ago) / price15ago) * 100;
    const vol5 = win5.reduce((a, x) => a + (x.usdVol ?? 0), 0);
    const future = bins[i + 3]; // +15 min
    const fwd15 = future?.price ? ((future.price - priceNow) / priceNow) * 100 : 0;
    const won = fwd15 >= 20;
    signals += 1; if (won) wins += 1;
    console.log(`  min${(i * 5).toString().padStart(4)}m buy=${buys} sell=${sells} druk=${pressure.toFixed(2)} mom5=${mom5.toFixed(1)}% mom15=${mom15.toFixed(1)}% vol5=$${(vol5 / 1e3).toFixed(0)}k → fwd15=${fwd15.toFixed(1)}% ${won ? '✅WIN' : '❌'}`);
  }
  console.log(`\n[profile] signalen=${signals} wins(+20%/15m)=${wins} win-rate=${signals ? ((wins / signals) * 100).toFixed(1) : 0}% · calls ${calls}`);
}
main().catch((e) => { console.error('[profile] FOUT:', e.message); process.exit(1); });