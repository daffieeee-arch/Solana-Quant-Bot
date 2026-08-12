#!/usr/bin/env node
/**
 * Multi-DEX launch collector voor backtesting.
 * Vindt verse pool/token-launches over MEERDERE DEX-programma's (niet alleen
 * pump.fun): Raydium AMMv4/CPMM/CLMM, Meteora AMM, Orca Whirlpool, PumpSwaps,
 * Moonshot, Jupiter. Per programma: recente signatures → filter op
 * initialize/create-style txns → mint-extractie uit token-balances.
 *
 * Zuinigheid: alleen de benodigde signatures + init-txns ophalen; mt per
 * mint gecached door de backtest zelf.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECRETS = path.join(__dirname, '..', 'secrets.txt');
const raw = fs.readFileSync(SECRETS, 'utf8');
const token = raw.split('\n').find((l) => /value x token/i.test(l)).match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/)[1];
const base = `https://johnb-mainnet-2781.mainnet.rpcpool.com/${token}`;
let calls = 0;
async function rpc(method, params) {
  calls += 1;
  const res = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: calls, method, params }) });
  const d = await res.json();
  if (d.error) throw new Error(`RPC ${method}: ${d.error.code} ${d.error.message}`);
  return d.result;
}

// Programma's + init-logs die op een nieuwe pool/token wijzen.
const PROGRAMS = [
  { key: 'raydiumAmmv4', id: '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8', initLog: /Initialize|createPool|CreatePool/ },
  { key: 'raydiumCpmm', id: 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C', initLog: /Initialize/ },
  { key: 'raydiumClmm', id: 'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK', initLog: /Initialize|createPool/ },
  { key: 'meteoraAmm', id: 'cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG', initLog: /create|Initialize/ },
  { key: 'orcaWhirlpool', id: 'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc', initLog: /initialize|openPosition/ },
  { key: 'pumpSwaps', id: 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA', initLog: /create_pool|CreatePool/ },
  { key: 'moonshot', id: 'MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG', initLog: /create|Initialize/ },
];

const hours = Number(process.argv.find((a) => a.startsWith('--hours='))?.split('=')[1] ?? 48);
const perProgram = Number(process.argv.find((a) => a.startsWith('--per='))?.split('=')[1] ?? 40);
const until = Math.floor(Date.now() / 1000) - hours * 3600;

/** Haal de gemunte token-mints uit een txn (pre/postTokenBalances van de gecreëerde pool). */
function mintsFromTxn(tx) {
  const mints = new Set();
  for (const b of [...(tx?.meta?.preTokenBalances ?? []), ...(tx?.meta?.postTokenBalances ?? [])]) {
    if (b?.mint && b.mint !== 'So11111111111111111111111111111111111111112') mints.add(b.mint);
  }
  return [...mints];
}

// Verse-launch filter: een mint telt ALLEEN als het de ALLEREERSTE maal is dat we
// hem zien én hij nog jong is (blockTime dicht bij nu). Hiermee filteren we:
//  - her-geïnit buffer/configuraties van bestaande pools (Meteora 'init' is vaak
//    geen verse launch)
//  - volwassen coins die toevallig een init-log hebben
// Zuinigheid: onthoud eerst-geziene blockTime per mint; alleen verse (≤
// MR: MAX_AGE_S for mint) worden teruggegeven.
const seenMints = new Map(); // mint -> earliest blockTime
// 0 = geen leeftijdslimiet (versheid via seenMints-dedup: eerste keer gezien in
// dit collectievenster is de launch). Voor lange vensters (72u+) zet je dit op 0;
// voor verse-only (40-60 min) op een kleine waarde.
const MAX_AGE_S = Number(process.argv.find((a) => a.startsWith('--maxage='))?.split('=')[1] ?? 0); // 0 = uit

/** Geeft verse mints uit deze txn (eerste maal gezien + jong genoeg). */
function verseMintsFromTxn(tx, blockTime) {
  const out = [];
  for (const m of mintsFromTxn(tx)) {
    if (seenMints.has(m)) continue; // al eerder gezien → geen verse launch
    seenMints.set(m, blockTime ?? Date.now() / 1000);
    if (MAX_AGE_S > 0) {
      const age = Date.now() / 1000 - (blockTime ?? Date.now() / 1000);
      if (age > MAX_AGE_S) continue;
    }
    out.push(m);
  }
  return out;
}

async function main() {
  const found = new Map(); // mint -> {program, sig}
  console.log(`[collector] multi-DEX verse-launches laatste ${hours}u (limit ${perProgram}/programma, maxage ${MAX_AGE_S}s)`);
  for (const prog of PROGRAMS) {
    let scanned = 0, inits = 0;
    // Paginateer getSignaturesForAddress met 'before' tot de venstergrens (until).
    // Voor verse-launches zijn alleen de recentste sigs nodig per pagina.
    let before = undefined;
    let reachedUntil = false;
    let lastCount = 0;
    while (!reachedUntil && scanned < 3000) {
      let sigs;
      try {
        sigs = await rpc('getSignaturesForAddress', [prog.id, before ? { limit: 500, before } : { limit: 500 }]);
      } catch { console.log(`[collector] ${prog.key}: RPC-fout, skip`); break; }
      if (!sigs?.length || sigs.length === lastCount) break;
      lastCount = sigs.length;
      let anyInWindow = false;
      for (const sig of sigs.slice(0, 500)) {
        if (found.size >= perProgram * 3 && scanned > 200) break;
        if (typeof sig?.signature !== 'string' || sig.err) continue;
        if (sig.blockTime && sig.blockTime < until) { reachedUntil = true; break; }
        anyInWindow = true;
        scanned += 1;
        let tx;
        try { tx = await rpc('getTransaction', [sig.signature, { encoding: 'json', maxSupportedTransactionVersion: 0 }]); }
        catch { continue; }
        const logs = tx?.meta?.logMessages ?? [];
        const isInit = logs.some((l) => prog.initLog.test(l));
        if (!isInit) continue;
        inits += 1;
        const mints = verseMintsFromTxn(tx, tx?.blockTime ?? sig.blockTime);
        for (const m of mints) {
          if (!found.has(m)) found.set(m, { program: prog.key, sig: sig.signature });
        }
        if (inits % 10 === 0) console.log(`[collector] ${prog.key}: ${inits} init-txns, ${mints.length > 0 ? 'mints+' : ''} (calls ${calls})`);
      } // end for sigs
      if (!anyInWindow) break; // pagina had geen sigs in venster → klaar
      // pagineer verder terug: 'before' = oudste signature van deze pagina
      const oldest = sigs[sigs.length - 1]?.signature;
      if (!oldest) break;
      before = oldest;
    } // end while
    console.log(`[collector] ${prog.key}: ${scanned} sigs gescand, ${inits} init-txns → totaal mints ${found.size}`);
  }
  const out = path.join(__dirname, '..', `launch-mints-multidex-${hours}h.txt`);
  fs.writeFileSync(out, Array.from(found.keys()).join('\n') + '\n');
  console.log(`[collector] ${found.size} unieke multi-DEX launch-mints → ${out} (calls ${calls})`);
}
main().catch((e) => { console.error('[collector] FOUT:', e.message); process.exit(1); });