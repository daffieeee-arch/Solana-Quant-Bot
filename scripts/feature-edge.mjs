#!/usr/bin/env node
/**
 * Feature-edge analyse (b) — over de gecachte backtest-data (0 extra RPC).
 *
 * Voor elke mint: reconstrueer de trade-serie, bereken op elk mogelijke
 * entry-tijdstip (binnen een venster sinds launch) een set ROBUSTE features en
 * bepaal de TP/SL-uitkomst van een trade die daar zou zijn ingegaan. Daarna:
 *   - per feature: win-rate + expectancy van trades gesplitst op drempel (decile)
 *   - identificeer welke single-feature echte discriminatie toont i.p.v.
 *     absolute surge-gates (die leverden geen edge op).
 *
 * Features (allen uit beschikbare per-txn data, geen wallets nodig):
 *   la:  launch-age (sec sinds eerste txn)
 *   mom: momentum sinds launch (priceNow / priceAtStart - 1)
 *   mom5m: momentum over laatste 5 min (priceNow / price5mAgo - 1)
 *   buyp: buy-druk (cumulatieve SOL buys vs sells, ratio)
 *   volmin: txn-intensiteit (aantal txns per min sinds launch)
 *   whale: >=1 whale-txn in laatste 5 min
 *   imbalance: netto flow (buys-sells in SOL) over laatste 5 min
 *
 * TP/SL met default 12%/8%/60m — de best-gekalibreerde combinatie uit de
 * eerdere runs. Rapport toont per feature de top-decile vs bottom-decile
 * expectancy, zodat we zien of er ECHT discriminatie is.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE = path.join(__dirname, '..', '.backtest-cache');
const MIN = Number(process.argv.find((a) => a.startsWith('--min='))?.split('=')[1] ?? 20);
const TP = Number(process.argv.find((a) => a.startsWith('--tp='))?.split('=')[1] ?? 0.12);
const SL = Number(process.argv.find((a) => a.startsWith('--sl='))?.split('=')[1] ?? 0.08);
const MAXHOLD = 60 * 60;

const files = fs.readdirSync(CACHE).filter((f) => f.endsWith('.json'));
let mints = 0, entries = 0;

// verzamel alle trades met hun features + uitkomst
const trades = []; // { mint, entryT, la, mom, mom5m, buyp, volmin, whale, imbalance, pnl }

for (const file of files) {
  let pts;
  try { pts = JSON.parse(fs.readFileSync(path.join(CACHE, file), 'utf8')); } catch { continue; }
  if (!Array.isArray(pts) || pts.length < MIN) continue;
  pts.sort((a, b) => a.t - b.t);
  const launchT = pts[0].t;
  const p0 = pts[0].priceSol;
  mints += 1;
  const n = pts.length;
  // pre-compute cumulatieve series
  for (let i = Math.max(2, n - 20); i < n; i++) {
    const e = pts[i];
    const la = e.t - launchT;
    if (la < 60 || la > MAXHOLD / 2) continue; // entry binnen 1-30 min na launch
    const pNow = e.priceSol;
    // momentum sinds launch
    const mom = p0 > 0 ? pNow / p0 - 1 : 0;
    // momentum over 5 min: zoek de prijs 5 min eerder
    const t5 = e.t - 300;
    let pi = i - 1;
    while (pi >= 0 && pts[pi].t > t5) pi -= 1;
    const mom5m = pi >= 0 && pts[pi].priceSol > 0 ? pNow / pts[pi].priceSol - 1 : 0;
    // buy-druk (cumulatief sinds launch)
    const inNow = pts.slice(1, i + 1);
    let bSol = 0, sSol = 0;
    for (const p of inNow) { if (p.kind === 'buy') bSol += p.solSwapped || 0; else sSol += p.solSwapped || 0; }
    const buyp = (bSol + sSol) > 0 ? bSol / (bSol + sSol) : 0.5;
    // volmin: txns per min sinds launch
    const mins = Math.max(1, la / 60);
    const volmin = (i + 1) / mins;
    // whale in laatste 5 min + netto flow over 5 min
    let w5 = false, net5 = 0;
    for (let j = i; j >= 0 && pts[j].t > t5; j--) { if (pts[j].whale) w5 = true; net5 += pts[j].kind === 'buy' ? (pts[j].solSwapped || 0) : -(pts[j].solSwapped || 0); }
    // TP/SL-sim van hier (alleen die perdit future trades gebruiken)
    let pnl = null;
    for (let j = i + 1; j < n; j++) {
      const pj = pts[j].priceSol;
      const rel = pj / pNow - 1;
      if (rel >= TP) { pnl = TP; break; }
      if (rel <= -SL) { pnl = -SL; break; }
      if (pts[j].t - e.t > MAXHOLD) { pnl = rel; break; }
    }
    if (pnl === null) continue;
    entries += 1;
    trades.push({ mint: path.basename(file, '.json').slice(0, 10), la, mom, mom5m, buyp, volmin, whale: w5, imbalance: net5, pnl });
  }
}

console.log(`[feature-edge] mints=${mints} entries=${entries} TP/SL=${TP}/${SL}`);

// Per feature: splitsen op drempel (decile) en win-rate + expectancy
const features = ['la', 'mom', 'mom5m', 'buyp', 'volmin', 'imbalance'];
for (const feat of features) {
  const vals = trades.map((t, i) => [t[feat], i]).sort((a, b) => a[0] - b[0]);
  const q1 = vals[Math.floor(vals.length * 0.25)][0];
  const q3 = vals[Math.floor(vals.length * 0.75)][0];
  const lo = trades.filter((t) => t[feat] <= q1);
  const hi = trades.filter((t) => t[feat] >= q3);
  const summ = (arr) => {
    if (!arr.length) return '–';
    const wr = arr.filter((t) => t.pnl > 0).length / arr.length;
    const exp = arr.reduce((s, t) => s + t.pnl, 0) / arr.length;
    return `n=${arr.length} win=${(wr * 100).toFixed(0)}% exp=${(exp * 100).toFixed(2)}%`;
  };
  console.log(`${feat.padEnd(8)} laag(≤${q1.toFixed(3)}) ${summ(lo)}  |   hoog(≥${q3.toFixed(3)}) ${summ(hi)}`);
}

// Whale-aan-uit
const wY = trades.filter((t) => t.whale), wN = trades.filter((t) => !t.whale);
const summW = (arr) => arr.length ? `n=${arr.length} win=${(arr.filter((t) => t.pnl > 0).length / arr.length * 100).toFixed(0)}% exp=${(arr.reduce((s, t) => s + t.pnl, 0) / arr.length * 100).toFixed(2)}%` : '–';
console.log(`whale   aan ${summW(wY)}  |   uit ${summW(wN)}`);

console.log(`\n[totaal] expectancy over alle ${entries} entries: ${(trades.reduce((s, t) => s + t.pnl, 0) / entries * 100).toFixed(2)}% win${(trades.filter((t) => t.pnl > 0).length / entries * 100).toFixed(0)}%`);