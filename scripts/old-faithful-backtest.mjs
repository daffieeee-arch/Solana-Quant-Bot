#!/usr/bin/env node
/**
 * Old Faithful / Hydrant backtest — validate the sniper gates on REAL historical
 * pump.fun launches, entirely via Triton JSON-RPC (no Birdeye/Gecko, no stream).
 *
 * Flow per candidate mint:
 *   1. getSignaturesForAddress(pump.program)  → recent pump.fun launch txns
 *      (create = launch; extract mint + bonding curve from the txn).
 *   2. For a candidate mint: getSignaturesForAddress(mint) → buy/sell txns.
 *   3. getTransaction per signature → decode bonding-curve virtual reserves
 *      → price series, buys/min, sells/min, volume, first-seen age.
 *   4. Evaluate the current gates at every 60s point after launch and measure
 *      forward returns (+20% within 5/15 min) → win-rate per gate threshold set.
 *
 * Usage: node scripts/old-faithful-backtest.mjs [--limit 20] [--minutes 30]
 * Reads the Triton token from secrets.txt (same layout as the bot).
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
const token = loadToken();
const rpcBase = `https://johnb-mainnet-2781.mainnet.rpcpool.com/${token}`;

let rpcCalls = 0;
async function rpc(method, params) {
  rpcCalls += 1;
  const res = await fetch(rpcBase, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: rpcCalls, method, params }),
    signal: AbortSignal.timeout(20_000),
  });
  const data = await res.json();
  if (data.error) throw new Error(`RPC ${method}: ${data.error.code} ${data.error.message}`);
  return data.result;
}

/** Decode u64 little-endian from a byte offset. */
function readU64LE(buf, offset) {
  let n = 0n;
  for (let i = offset + 7; i >= offset; i -= 1) n = (n << 8n) | BigInt(buf[i]);
  return n;
}

/** Decode bonding-curve virtual reserves from a base64 account. */
function decodeCurve(base64) {
  const buf = Buffer.from(base64, 'base64');
  if (buf.length < 88) return undefined;
  const virtualToken = readU64LE(buf, 64);
  const virtualSol = readU64LE(buf, 72);
  const totalSupply = readU64LE(buf, 80);
  if (virtualToken <= 0n || virtualSol <= 0n) return undefined;
  // price = virtualSol / virtualToken (SOL per token), token has 6 decimals, SOL 9
  const priceSol = Number(virtualSol) / Number(virtualToken) * (10 ** (6 - 9));
  return {
    virtualToken: Number(virtualToken),
    virtualSol: Number(virtualSol),
    totalSupply: Number(totalSupply),
    priceSol,
    liquidityUsd: 2 * (Number(virtualSol) / 1e9) * SOL_PRICE_USD,
  };
}

const SOL_PRICE_USD = Number(process.env.SOL_PRICE_USD || 74);
const WSOL_MINT = 'So11111111111111111111111111111111111111112';

/** Resolv de echte token-naam via Triton DAS getAsset (gecached per mint). */
const symbolCache = new Map(); // mint -> Promise<string|null>
async function resolveSymbol(mint) {
  if (!symbolCache.has(mint)) {
    symbolCache.set(mint, (async () => {
      try {
        const asset = await rpc('getAsset', { id: mint });
        const sym = asset?.content?.metadata?.symbol ?? asset?.token_info?.symbol ?? asset?.token_info?.symbol ?? undefined;
        return sym && typeof sym === 'string' && sym.length > 0 && sym.length <= 24 ? sym : null;
      } catch {
        return null;
      }
    })());
  }
  return symbolCache.get(mint);
}

/** Real swap metrics from a txn: price from WSOL-token-delta (gevestigde AMMs swap via
 * WSOL-ATA, native SOL-delta is vaak ~0), SOL volume, whale flag. */
function txnMetrics(tx) {
  const meta = tx?.meta;
  if (!meta) return undefined;
  const pre = meta.preTokenBalances ?? [];
  const post = meta.postTokenBalances ?? [];
  const byOwner = new Map(); // owner -> {token:{mint,pre,post}, wsol:{pre,post}, native:0}
  const touch = (owner, key, delta) => {
    const o = byOwner.get(owner) ?? { token: { mint: undefined, pre: undefined, post: undefined }, wsol: { pre: undefined, post: undefined }, native: 0 };
    if (key === 'native') o.native += delta;
    else if (key === 'wsol-pre') o.wsol.pre = delta;
    else if (key === 'wsol-post') o.wsol.post = delta;
    else if (key === 'token-pre') o.token.pre = delta;
    else if (key === 'token-post') o.token.post = delta;
    byOwner.set(owner, o);
  };
  for (const b of pre) {
    const owner = String(b.owner ?? '');
    const mint = String(b.mint ?? '');
    if (mint === WSOL_MINT) touch(owner, 'wsol-pre', b.uiTokenAmount?.uiAmount);
    else { touch(owner, 'token-pre', b.uiTokenAmount?.uiAmount); const cur = byOwner.get(owner); if (cur) cur.token.mint = mint; }
  }
  for (const b of post) {
    const owner = String(b.owner ?? '');
    const mint = String(b.mint ?? '');
    if (mint === WSOL_MINT) touch(owner, 'wsol-post', b.uiTokenAmount?.uiAmount);
    else { touch(owner, 'token-post', b.uiTokenAmount?.uiAmount); const cur = byOwner.get(owner); if (cur && !cur.token.mint) cur.token.mint = mint; }
  }
  const preBal = meta.preBalances ?? [];
  const postBal = meta.postBalances ?? [];
  for (let i = 1; i < Math.min(preBal.length, postBal.length); i += 1) {
    touch(String(i), 'native', (postBal[i] ?? 0) - (preBal[i] ?? 0));
  }
  // Grootste |token-delta| per owner; prijs = WSOL-delta / token-delta van die owner.
  let best;
  for (const [owner, o] of byOwner) {
    if (o.token.pre === undefined || o.token.post === undefined) continue;
    if (!o.token.mint) continue;
    const dt = o.token.post - o.token.pre;
    const absDt = Math.abs(dt);
    const wsolDelta = o.wsol.pre !== undefined && o.wsol.post !== undefined ? o.wsol.post - o.wsol.pre : 0;
    const nativeDelta = o.native / 1e9;
    const solDelta = Math.abs(wsolDelta) >= Math.abs(nativeDelta) ? wsolDelta : nativeDelta;
    const solSwapped = Math.abs(solDelta);
    if (absDt < 1_000 || solSwapped < 0.0001) continue;
    const priceSol = Number((solSwapped / absDt).toFixed(12));
    if (!Number.isFinite(priceSol) || priceSol <= 0 || priceSol > 1) continue;
    if (!best || absDt > best.absDt) best = { owner, mint: o.token.mint, priceSol, solSwapped, whale: solSwapped >= 1.0, tokenDeltaSign: Math.sign(dt) };
  }
  if (!best) return undefined;
  return { priceSol: best.priceSol, solSwapped: best.solSwapped, whale: best.whale, kind: best.tokenDeltaSign >= 0 ? 'buy' : 'sell' };
}

/** Detecteer een swap txn (pump Buy/Sell of Raydium/Orca Swap) via LOG-MARKERS.
 * Richting + prijs komen uit txnMetrics (algemeen, venue-agnostisch). */
function parseSwapTxn(tx) {
  const log = tx?.meta?.logMessages ?? [];
  const isSwapLike = log.some((l) => /Program log: Instruction: (Buy|Sell|Swap|Swap2|SharedAccountsRouteV2|Route)/.test(l));
  const isCreate = log.some((l) => /Program log: (Instruction: )?Create/.test(l));
  if (!isSwapLike && !isCreate) return undefined;
  if (isCreate) return { kind: 'create', slot: tx?.slot, blockTime: tx?.blockTime };
  return { kind: 'swap', slot: tx?.slot, blockTime: tx?.blockTime, signature: typeof tx?.transaction?.signatures?.[0] === 'string' ? tx.transaction.signatures[0] : undefined };
}

/** Reconstruct the trade timeline for one mint over `hours` of history, cached on
 * disk so multi-config evaluation runs cost ZERO additional RPC calls. */
async function reconstructTrades(mint, curve, hours = 48) {
  const cacheDir = path.join(__dirname, '..', '.backtest-cache');
  const cacheFile = path.join(cacheDir, `${mint}.json`);
  fs.mkdirSync(cacheDir, { recursive: true });
  if (fs.existsSync(cacheFile)) {
    try {
      const cached = JSON.parse(fs.readFileSync(cacheFile, 'utf8'));
      if (Array.isArray(cached) && cached.length > 0) return cached;
    } catch { /* hernieuwen */ }
  }
  const until = Math.floor(Date.now() / 1000) - hours * 3600;
  // Diepe pagination: haal sigs op in pagina's van 1000 met `before` tot we (a) de
  // historie-grens overschrijden, (b) een budget aan pagina's raken, of (c) een
  // lege/onveranderde pagina zien. `before` is de oudste sig van de vorige pagina.
  // Budget: max 400 pagina's per token (400k sigs ≈ 23 dagen op actieve coins zoals
  // BONK ~17k tx/dag) — een praktische bovengrens die we in één nacht-run kunnen
  // doorlopen zonder de RPC-quota structureel te raken.
  const MAX_PAGES = 400;
  const MAX_TX_FETCH = 600; // alleen de relevantste txns ophalen (budget-zuinig)
  const sigsCollected = [];
  let before;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const params = [{ limit: 1000 }];
    if (before) params[0].before = before;
    let pageSigs;
    try { pageSigs = await rpc('getSignaturesForAddress', [mint, params[0]]); }
    catch { break; }
    if (!Array.isArray(pageSigs) || pageSigs.length === 0) break;
    const fresh = pageSigs.filter((s) => !(s.blockTime && s.blockTime < until));
    sigsCollected.push(...fresh);
    const oldest = pageSigs[pageSigs.length - 1];
    if (!oldest || oldest.signature === before) break;
    // als de hele pagina ouder is dan `until`, zijn we klaar
    const oldestBt = oldest.blockTime;
    if (oldestBt && oldestBt < until) break;
    before = oldest.signature;
    // stop als we al ver genoeg terug zijn en de pagina klein was (einde van de keten)
    if (pageSigs.length < 1000) break;
  }
  const points = [];
  // Spreid de tx-fetches over ALLE pagina's (nieuwste→oudst) zodat de ~600 fetches
  // de hele 90-dagen periode dekken i.p.v. alleen de recentste dagen.
  const step = Math.max(1, Math.floor(sigsCollected.length / MAX_TX_FETCH));
  for (const [pageIdx, sig] of sigsCollected.entries()) {
    if (pageIdx % step !== 0) continue;
    if (typeof sig?.signature !== 'string') continue;
    if (sig.blockTime && sig.blockTime < until) continue;
    if (sig.err) continue;
    const tx = await rpc('getTransaction', [sig.signature, { encoding: 'json', maxSupportedTransactionVersion: 0 }]);
    const p = parseSwapTxn(tx);
    if (!p || p.kind === 'create') continue;
    const metrics = txnMetrics(tx);
    if (!metrics) continue;
    points.push({
      t: p.blockTime ?? sig.blockTime,
      kind: metrics.kind,
      priceSol: metrics.priceSol,
      solSwapped: metrics.solSwapped,
      whale: metrics.whale,
    });
  }
  points.sort((a, b) => (a.t ?? 0) - (b.t ?? 0));
  try { fs.writeFileSync(cacheFile, JSON.stringify(points)); } catch { /* niet-kritiek */ }
  return points;
}

/** Build price/buys/sells/volume/whale series using REAL per-txn metrics. */
function buildSeries(points, curve) {
  let lastPrice = undefined;
  const series = [];
  for (const p of points) {
    if (p.priceSol !== undefined && p.priceSol > 0) lastPrice = p.priceSol;
    series.push({
      t: p.t,
      kind: p.kind,
      priceSol: lastPrice,
      buys: p.kind === 'buy' ? 1 : 0,
      sells: p.kind === 'sell' ? 1 : 0,
      usdVol: (p.solSwapped ?? 0) * SOL_PRICE_USD,
      whaleBuys: p.kind === 'buy' && p.whale ? 1 : 0,
    });
  }
  return series;
}

/** Bucket the series into 5-minute bins (efficiënt; forward-tick = 5 min).
 * 12 bins/uur → 90 dagen = 25.920 bins; cap bij 120 dagen om mem safe te houden. */
function bucket(series, hours) {
  const start = series[0]?.t ?? Math.floor(Date.now() / 1000);
  const totalBins = Math.min(120 * 12, hours * 12); // 12 bins/uur (5-min)
  const bins = [];
  for (let m = 0; m < totalBins; m += 1) {
    const from = start + m * 300;
    const to = from + 300;
    const slice = series.filter((p) => p.t >= from && p.t < to);
    const price = slice.find((p) => p.priceSol !== undefined)?.priceSol;
    bins.push({
      minute: m,
      buys: slice.filter((p) => p.buys).length,
      sells: slice.filter((p) => p.sells).length,
      usdVol: slice.reduce((a, x) => a + (x.usdVol ?? 0), 0),
      whaleBuys: slice.reduce((a, x) => a + (x.whaleBuys ?? 0), 0),
      priceSol: price,
    });
  }
  return bins;
}

/** Simuleer het ECHTE paper-profiel per signaal: entry @ priceNow, TP/SL/tijdstop
 * op 1-min-resolutie (forward bins zijn 5-min; interpoleren op 5-min-stappen is
 * conservatief). TP raakt eerst → winst +tp%; SL eerst → verlies −sl%; geen van
 * beide binnen maxBins → −0 (mark-to-model exit op laatste prijs).
 * Returns {tp, sl, flat, netPnlPct, last} */
function simulateTrade(bins, entryIdx, config) {
  const entry = bins[entryIdx].priceSol;
  const maxBins = config.maxHoldBins ?? 12; // 12 × 5min = 60min max hold
  let exit = 'flat';
  let last = bins[entryIdx].priceSol;
  for (let k = 1; k <= maxBins && entryIdx + k < bins.length; k += 1) {
    const b = bins[entryIdx + k];
    if (!b.priceSol) continue;
    last = b.priceSol;
    const ret = ((b.priceSol - entry) / entry) * 100;
    if (ret >= config.tpPercent) { exit = 'tp'; break; }
    if (ret <= -config.slPercent) { exit = 'sl'; break; }
  }
  const net = exit === 'tp' ? config.tpPercent : exit === 'sl' ? -config.slPercent : ((last - entry) / entry) * 100;
  return { exit, netPnlPct: net };
}

/** Evaluate our gates at each tick with REAL TP/SL expectancy. */
function evaluateGates(bins, config) {
  const results = [];
  const firstPrice = bins.find((x) => x.priceSol !== undefined && x.priceSol > 0)?.priceSol;
  for (let i = 0; i < bins.length; i += 1) {
    const b = bins[i];
    if (!b.priceSol) continue;
    const window = bins.slice(Math.max(0, i - 4), i + 1);
    const buys = window.reduce((acc, x) => acc + x.buys, 0);
    const sells = window.reduce((acc, x) => acc + x.sells, 0);
    const usdVol5 = window.reduce((acc, x) => acc + (x.usdVol ?? 0), 0);
    const whaleBuys5 = window.reduce((acc, x) => acc + (x.whaleBuys ?? 0), 0);
    const priceNow = window.find((x) => x.priceSol !== undefined)?.priceSol;
    if (!priceNow) continue;
    const priceStart = bins[Math.max(0, i - 5)]?.priceSol ?? priceNow;
    const change5 = priceStart > 0 ? ((priceNow - priceStart) / priceStart) * 100 : 0;
    const momSinceLaunch = firstPrice && firstPrice > 0 ? ((priceNow - firstPrice) / firstPrice) * 100 : 0;
    const pressure = buys + sells > 0 ? buys / (buys + sells) : 0;
    const surgeOk = buys >= config.minBuySurgeCount;
    const pressureOk = pressure >= config.minBuyPressure;
    const momentumOk = config.minMomentum5 === undefined ? true : change5 >= config.minMomentum5;
    const launchOk = config.minMomentumSinceLaunch === undefined ? true : momSinceLaunch >= config.minMomentumSinceLaunch;
    const ageOk = config.maxAgeBins === undefined ? true : i <= config.maxAgeBins;
    const volOk = config.minVolUsd5 === undefined ? true : usdVol5 >= config.minVolUsd5;
    const whaleOk = config.minWhaleBuys5 === undefined ? true : whaleBuys5 >= config.minWhaleBuys5;
    const gatePass = surgeOk && pressureOk && momentumOk && launchOk && ageOk && volOk && whaleOk;
    const trade = gatePass ? simulateTrade(bins, i, { tpPercent: config.tpPercent ?? 8, slPercent: config.slPercent ?? 12, maxHoldBins: config.maxHoldBins ?? 12 }) : undefined;
    const fwd = (steps) => {
      const future = bins[i + steps];
      return future?.priceSol && priceNow > 0 ? ((future.priceSol - priceNow) / priceNow) * 100 : 0;
    };
    results.push({ minute: i, buys, sells, pressure, change5, momSinceLaunch, usdVol5, whaleBuys5, gatePass, trade, forward5: fwd(5), forward15: fwd(15), forward60: fwd(60), priceNow });
  }
  return results;
}

/** CLI */
const limit = Number(process.argv.find((a) => a.startsWith('--limit='))?.split('=')[1] || 20);
const hours = Number(process.argv.find((a) => a.startsWith('--hours='))?.split('=')[1] || 48);

async function main() {
  console.log(`[backtest] Triton Hydrant backtest · limit=${limit} hours=${hours} · rpc=${rpcBase.slice(0, 48)}…`);
  console.log(`[backtest] RPC-calls budget: ~205 per mint (1 sigs + 200 tx) — ${limit} mints ≈ ${limit * 205}`);
  const configs = [
      // Baseline (huidige gates, al getest)
      { key: 'strict', label: 'strict 50/0.55', minBuySurgeCount: 50, minBuyPressure: 0.55, minLiquidityUsd: 10_000 },
      { key: 'relaxed', label: 'versoepeld 20/0.5', minBuySurgeCount: 20, minBuyPressure: 0.5, minLiquidityUsd: 10_000 },
      { key: 'snipe', label: 'sniping 10/0.45', minBuySurgeCount: 10, minBuyPressure: 0.45, minLiquidityUsd: 5_000 },
      // HYPOTHESE: volume-in-$ + whale-buys + vroege-fase momentum combineren
      { key: 'volWhale', label: 'vol≥5k$ + whale≥1 (≤60m)', minBuySurgeCount: 10, minBuyPressure: 0.45, minVolUsd5: 5_000, minWhaleBuys5: 1, maxAgeBins: 12 },
      { key: 'volWhaleMom', label: 'vol≥5k$ + whale≥1 + mom≥20% (≤120m)', minBuySurgeCount: 10, minBuyPressure: 0.45, minVolUsd5: 5_000, minWhaleBuys5: 1, minMomentumSinceLaunch: 20, maxAgeBins: 24 },
      { key: 'whaleOnly', label: 'whale≥2 (≤60m)', minBuySurgeCount: 5, minBuyPressure: 0.4, minWhaleBuys5: 2, maxAgeBins: 12 },
      { key: 'vol10k', label: 'vol≥10k$ (≤60m)', minBuySurgeCount: 10, minBuyPressure: 0.45, minVolUsd5: 10_000, maxAgeBins: 12 },
      // TP/SL-kalibratie op de versoepelde config (de beste win-rate-basis):
      //   break-even bij win-rate p: p·TP = (1−p)·SL ⇔ p = SL/(TP+SL)
      //   huidig 8/12: nodig 60% win → 53,7% verliest. Test 8/8, 10/10, 12/8, 8/10.
      { key: 'relaxedTP8SL8', label: 'versoepeld TP8/SL8', minBuySurgeCount: 20, minBuyPressure: 0.5, tpPercent: 8, slPercent: 8 },
      { key: 'relaxedTP10SL10', label: 'versoepeld TP10/SL10', minBuySurgeCount: 20, minBuyPressure: 0.5, tpPercent: 10, slPercent: 10 },
      { key: 'relaxedTP12SL8', label: 'versoepeld TP12/SL8', minBuySurgeCount: 20, minBuyPressure: 0.5, tpPercent: 12, slPercent: 8 },
      { key: 'relaxedTP8SL10', label: 'versoepeld TP8/SL10', minBuySurgeCount: 20, minBuyPressure: 0.5, tpPercent: 8, slPercent: 10 },
      { key: 'relaxedTP10SL6', label: 'versoepeld TP10/SL6', minBuySurgeCount: 20, minBuyPressure: 0.5, tpPercent: 10, slPercent: 6 },
  ];

  // 1. Mints: uit --mints=<file> (aanbevolen; echte verse mints uit de live bot)
  //    óf uit recente pump-program-txns (adres-niveau, maar vaak errors → minder betrouwbaar).
  const mintsFile = process.argv.find((a) => a.startsWith('--mints='))?.split('=')[1];
  const mints = new Map(); // mint -> { curve }
  if (mintsFile) {
    const lines = fs.readFileSync(mintsFile, 'utf8').split('\n').map((l) => l.trim()).filter((l) => l.length >= 32);
    for (const m of lines.slice(0, limit)) mints.set(m, { curve: undefined });
    console.log(`[backtest] ${mints.size} mints uit ${mintsFile} geladen`);
  } else {
    const launchSigs = await rpc('getSignaturesForAddress', [PUMP_PROGRAM, { limit: 300 }]);
    console.log(`[backtest] ${launchSigs.length} recente pump-program txns gevonden op adres-niveau`);
    for (const sig of launchSigs.slice(0, limit)) {
      let tx;
      try { tx = await rpc('getTransaction', [sig.signature, { encoding: 'json', maxSupportedTransactionVersion: 0 }]); }
      catch { continue; }
      const p = parsePumpTxn(tx);
      if (p?.kind === 'create' && p.mint && p.curve && !mints.has(p.mint)) {
        mints.set(p.mint, { curve: p.curve });
      }
    }
    console.log(`[backtest] ${mints.size} unieke verse pump-launches gevonden`);
  }

  // 2. Per mint: naam-resolutie (1× getAsset per mint, gecached) + trades reconstructeren
  const winners = {}; // key -> rows
  for (const cfg of configs) winners[cfg.key] = [];
  let analyzed = 0;
  for (const [mint, { curve }] of mints) {
    if (analyzed >= limit) break;
    analyzed += 1;
    const symbol = (await resolveSymbol(mint)) ?? mint.slice(0, 8);
    let points;
    try { points = await reconstructTrades(mint, curve, hours); }
    catch { continue; }
    if (points.length < 4) continue;
    const series = buildSeries(points, curve);
    const bins = bucket(series, hours);
    const buysTot = bins.reduce((a, x) => a + x.buys, 0);
    const sellsTot = bins.reduce((a, x) => a + x.sells, 0);
    for (const cfg of configs) {
      const evals = evaluateGates(bins, cfg);
      const passed = evals.filter((e) => e.gatePass && e.trade);
      let ntp = 0, nsl = 0, nflat = 0, net = 0;
      for (const e of passed) {
        if (e.trade.exit === 'tp') ntp += 1;
        else if (e.trade.exit === 'sl') nsl += 1;
        else nflat += 1;
        net += e.trade.netPnlPct;
      }
      winners[cfg.key].push({
        mint: symbol, signals: passed.length,
        ntp, nsl, nflat,
        netPnlPct: net,
        expectancy: passed.length ? net / passed.length : 0,
      });
    }
    if (analyzed <= 3) console.log(`[backtest] ${symbol} (${mint.slice(0, 8)}…) txns=${points.length} buys=${buysTot} sells=${sellsTot} bins=${bins.length}`);
    if (analyzed % 5 === 0) console.log(`[backtest] geanalyseerd: ${analyzed}/${limit} mints … (rpc-calls: ${rpcCalls})`);
  }

  // 3. Output — gesorteerd op EXPECTANCY (netto % per trade met TP 8%/SL 12%/60m)
  console.log('\n=== RESULTATEN (48u, TP 8% / SL 12% / 60m max hold) ===');
  const summary = configs.map((cfg) => {
    const rows = winners[cfg.key].filter((w) => w.signals > 0);
    const tot = rows.reduce((a, x) => a + x.signals, 0);
    const ntp = rows.reduce((a, x) => a + x.ntp, 0);
    const nsl = rows.reduce((a, x) => a + x.nsl, 0);
    const exp = tot ? rows.reduce((a, x) => a + x.netPnlPct, 0) / tot : 0;
    const winRate = tot ? (ntp / tot) * 100 : 0;
    return { cfg: cfg.label, tot, ntp, nsl, exp, winRate, rows: rows.slice(0, 5) };
  }).sort((a, b) => b.exp - a.exp);
  for (const s of summary) {
    console.log(`\n[${s.cfg}] trades=${s.tot} TP=${s.ntp} SL=${s.nsl} winRate=${s.winRate.toFixed(1)}% expectancy=${s.exp >= 0 ? '+' : ''}${s.exp.toFixed(2)}%/trade`);
    for (const r of s.rows) console.log(`   ${r.mint}… tr=${r.signals} tp=${r.ntp} sl=${r.nsl} exp=${r.expectancy >= 0 ? '+' : ''}${r.expectancy.toFixed(2)}%`);
  }
  console.log(`\n[backtest] totaal RPC-calls: ${rpcCalls} (cache hergebruikt bij her-run)`);
}

main().catch((e) => { console.error('[backtest] FOUT:', e.message); console.error(e.stack?.split('\n').slice(0, 6).join('\n') ?? ''); process.exit(1); });